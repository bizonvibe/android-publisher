import hashlib
import json
import re
from pathlib import PurePosixPath
from urllib.parse import urlsplit

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .manifests import normalize_manifest
from .models import Retryable, Stop


class R2:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    def get_client(self):
        s = self.settings
        if self.client is None:
            if not all((s.r2_endpoint_url, s.r2_access_key_id, s.r2_secret_access_key)):
                raise Stop("R2_NOT_CONFIGURED")
            url = urlsplit(s.r2_endpoint_url)
            if (
                url.scheme != "https"
                or url.username
                or url.password
                or url.query
                or url.fragment
                or url.path not in ("", "/")
                or url.port not in (None, 443)
                or not re.fullmatch(
                    r"[a-f0-9]{32}(?:\.(?:eu|fedramp))?\.r2\.cloudflarestorage\.com",
                    url.hostname or "",
                )
            ):
                raise Stop("R2_ENDPOINT_INVALID")
            self.client = boto3.client(
                "s3",
                endpoint_url=s.r2_endpoint_url,
                region_name="auto",
                aws_access_key_id=s.r2_access_key_id,
                aws_secret_access_key=s.r2_secret_access_key,
                config=Config(
                    signature_version="s3v4",
                    s3={"addressing_style": "path"},
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                    connect_timeout=15,
                    read_timeout=60,
                    retries={"max_attempts": 2},
                ),
            )
        return self.client

    def read_manifest(self, job):
        manifest_key = job.r2_manifest_key or str(
            PurePosixPath(job.r2_object_key).parent / "manifest.json"
        )
        response = self.get_client().get_object(Bucket=self.settings.r2_bucket, Key=manifest_key)
        with response["Body"] as body:
            raw = body.read(65_537)
        if len(raw) > 65_536:
            raise Stop("MANIFEST_TOO_LARGE")
        try:
            parsed = json.loads(raw)
            manifest, schema = normalize_manifest(parsed, job)
        except (ValueError, UnicodeError):
            raise Stop("INVALID_MANIFEST") from None
        self.last_raw_manifest = parsed
        return manifest, {
            "manifest_key": manifest_key,
            "schema": schema,
            "sha256": manifest.sha256.lower(),
            "size_bytes": manifest.size_bytes,
            "object_key": job.r2_object_key,
        }

    def download(self, job, directory):
        target = directory / "video.mp4"
        temporary = directory / "video.part"
        try:
            manifest, receipt = self.read_manifest(job)
            response = self.get_client().get_object(
                Bucket=self.settings.r2_bucket, Key=job.r2_object_key
            )
            if (
                receipt["schema"] == "factory-r2-v1"
                and response.get("ContentType", "").split(";")[0] != "video/mp4"
            ):
                response["Body"].close()
                raise Stop("VIDEO_MIME_MISMATCH")
            digest, total = hashlib.sha256(), 0
            with response["Body"] as body, temporary.open("wb") as output:
                while chunk := body.read(1024 * 1024):
                    total += len(chunk)
                    if total > manifest.size_bytes:
                        raise Stop("VIDEO_SIZE_MISMATCH")
                    digest.update(chunk)
                    output.write(chunk)
            if total != manifest.size_bytes or digest.hexdigest() != manifest.sha256.lower():
                raise Stop("VIDEO_CHECKSUM_MISMATCH")
            temporary.replace(target)
            (directory / "download.json").write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
            return target
        except (ValueError, UnicodeError):
            raise Stop("INVALID_MANIFEST") from None
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"AccessDenied", "NoSuchKey", "NoSuchBucket", "InvalidAccessKeyId"}:
                raise Stop("R2_OBJECT_UNAVAILABLE") from None
            raise Retryable("R2_TEMPORARY_ERROR") from None
        except BotoCoreError:
            raise Retryable("R2_TEMPORARY_ERROR") from None
        finally:
            temporary.unlink(missing_ok=True)

    def description(self, job):
        # The producer's description asset is verified independently before use.
        _, receipt = self.read_manifest(job)
        if receipt["schema"] != "factory-r2-v1":
            raise Stop("DESCRIPTION_UNAVAILABLE")
        raw = self.last_raw_manifest
        entry = raw.get("assets", {}).get("description", {})
        expected = raw["prefix"] + "/description." + raw.get("language", "") + ".txt"
        if (
            entry.get("key") != expected
            or entry.get("content_type", "").split(";")[0] != "text/plain"
        ):
            raise Stop("DESCRIPTION_UNAVAILABLE")
        from .models import Manifest

        check = Manifest(
            video_id=job.video_id,
            object_key=expected,
            sha256=entry.get("sha256"),
            size_bytes=entry.get("bytes"),
        )
        if check.size_bytes > 16_384:
            raise Stop("DESCRIPTION_TOO_LARGE")
        response = self.get_client().get_object(Bucket=self.settings.r2_bucket, Key=expected)
        with response["Body"] as body:
            data = body.read(16_385)
        if (
            len(data) != check.size_bytes
            or hashlib.sha256(data).hexdigest() != check.sha256.lower()
            or response.get("ContentType", "").split(";")[0] != "text/plain"
        ):
            raise Stop("DESCRIPTION_CHECKSUM_MISMATCH")
        text = data.decode("utf-8").strip()
        if not text or len(text) > 2200:
            raise Stop("DESCRIPTION_TOO_LARGE")
        return text

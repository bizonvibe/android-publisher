import hashlib
import json
from pathlib import PurePosixPath

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import ValidationError

from .models import Manifest, Retryable, Stop


class R2:
    def __init__(self, settings, client=None):
        self.settings = settings
        self.client = client

    def download(self, job, directory):
        s = self.settings
        if self.client is None:
            if not all((s.r2_endpoint_url, s.r2_access_key_id, s.r2_secret_access_key)):
                raise Stop("R2_NOT_CONFIGURED")
            if not s.r2_endpoint_url.startswith("https://"):
                raise Stop("R2_HTTPS_REQUIRED")
            self.client = boto3.client(
                "s3",
                endpoint_url=s.r2_endpoint_url,
                region_name="auto",
                aws_access_key_id=s.r2_access_key_id,
                aws_secret_access_key=s.r2_secret_access_key,
                config=Config(connect_timeout=15, read_timeout=60, retries={"max_attempts": 2}),
            )
        manifest_key = str(PurePosixPath(job.r2_object_key).parent / "manifest.json")
        target = directory / "video.mp4"
        temporary = directory / "video.part"
        try:
            response = self.client.get_object(Bucket=s.r2_bucket, Key=manifest_key)
            with response["Body"] as body:
                raw = body.read(65_537)
            if len(raw) > 65_536:
                raise Stop("MANIFEST_TOO_LARGE")
            manifest = Manifest.model_validate(json.loads(raw))
            if manifest.video_id != job.video_id or manifest.object_key != job.r2_object_key:
                raise Stop("MANIFEST_MISMATCH")
            response = self.client.get_object(Bucket=s.r2_bucket, Key=job.r2_object_key)
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
            return target
        except (ValidationError, ValueError):
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

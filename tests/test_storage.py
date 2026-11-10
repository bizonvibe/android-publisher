import hashlib
import io
import json

import pytest

from android_publisher.models import Stop
from android_publisher.storage import R2


class Client:
    def __init__(self, manifest, data=b"video"):
        self.manifest, self.data, self.calls = manifest, data, []

    def get_object(self, **kwargs):
        self.calls.append(kwargs)
        value = (
            json.dumps(self.manifest).encode()
            if kwargs["Key"].endswith("manifest.json")
            else self.data
        )
        return {"Body": io.BytesIO(value)}


def manifest(job):
    return dict(
        video_id=job.video_id,
        object_key=job.r2_object_key,
        sha256=hashlib.sha256(b"video").hexdigest(),
        size_bytes=5,
    )


def test_private_bucket_and_checksum(settings, job, tmp_path):
    client = Client(manifest(job))
    assert R2(settings, client).download(job, tmp_path).read_bytes() == b"video"
    assert client.calls == [
        {"Bucket": "content-factory-videos", "Key": "v1/manifest.json"},
        {"Bucket": "content-factory-videos", "Key": "v1/video.mp4"},
    ]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"sha256": "0" * 64}, "VIDEO_CHECKSUM_MISMATCH"),
        ({"size_bytes": 2}, "VIDEO_SIZE_MISMATCH"),
        ({"size_bytes": 10}, "VIDEO_CHECKSUM_MISMATCH"),
        ({"video_id": "wrong"}, "MANIFEST_MISMATCH"),
        ({"object_key": "wrong.mp4"}, "MANIFEST_MISMATCH"),
        ({"sha256": "bad"}, "INVALID_MANIFEST"),
    ],
)
def test_reject_corrupt(settings, job, tmp_path, change, code):
    m = manifest(job)
    m.update(change)
    with pytest.raises(Stop, match=code):
        R2(settings, Client(m)).download(job, tmp_path)
    assert not (tmp_path / "video.part").exists()
    assert not (tmp_path / "video.mp4").exists()


def test_access_denied_not_retried_or_leaked(settings, job, tmp_path):
    from botocore.exceptions import ClientError

    class Denied:
        def get_object(self, **kwargs):
            raise ClientError({"Error": {"Code": "AccessDenied", "Message": "secret"}}, "GetObject")

    with pytest.raises(Stop, match="^R2_OBJECT_UNAVAILABLE$"):
        R2(settings, Denied()).download(job, tmp_path)

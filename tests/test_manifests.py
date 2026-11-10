import copy
import hashlib
import io
import json
from pathlib import Path

import pytest

from android_publisher.manifests import normalize_manifest
from android_publisher.models import Job, Stop
from android_publisher.storage import R2

FIXTURES = Path(__file__).parent / "fixtures" / "agent"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def factory_job():
    raw = json.loads(Path("examples/first-reel.json").read_text(encoding="utf-8"))
    return Job(**raw)


def test_actual_factory_r2_contract_and_recorded_verification(factory_job):
    manifest, schema = normalize_manifest(load("factory-r2-manifest.json"), factory_job)
    report = load("r2-verification.json")
    video = next(asset for asset in report["assets"] if asset["role"] == "video")
    assert schema == "factory-r2-v1"
    assert (
        manifest.sha256
        == video["sha256"]
        == "5de5a375c395b646fd372e7dfaf85afc71805eddffb390b11cb1807f687659e0"
    )
    assert manifest.size_bytes == video["bytes"] == 5419698
    assert manifest.object_key == video["key"]


@pytest.mark.parametrize(
    "mutation,code",
    [
        (lambda m: m.update(status="failed"), "MANIFEST_NOT_READY_OR_MISMATCH"),
        (lambda m: m.update(version=2), "MANIFEST_NOT_READY_OR_MISMATCH"),
        (lambda m: m.update(prefix="reels/another"), "MANIFEST_NOT_READY_OR_MISMATCH"),
        (lambda m: m["assets"]["video"].update(key="other/video.mp4"), "MANIFEST_MISMATCH"),
        (lambda m: m["assets"]["video"].update(content_type="image/png"), "VIDEO_MIME_MISMATCH"),
        (lambda m: m["assets"]["video"].update(bytes=0), "INVALID_MANIFEST"),
        (lambda m: m["assets"]["video"].update(sha256="broken"), "INVALID_MANIFEST"),
    ],
)
def test_factory_rejects_invalid_contract(factory_job, mutation, code):
    raw = copy.deepcopy(load("factory-r2-manifest.json"))
    mutation(raw)
    with pytest.raises(Stop, match=code):
        normalize_manifest(raw, factory_job)


@pytest.mark.parametrize(
    "name,key,size",
    [
        ("final-manifest.json", "oct13/01-GitHub-Backup.mp4", 8793547),
        ("qa.json", "fbo/FBO-FBS-Misha2-Fixed-Captions.mp4", 12105982),
    ],
)
def test_real_legacy_fixtures(job, name, key, size):
    job.r2_object_key = key
    normalized, _ = normalize_manifest(load(name), job)
    assert normalized.size_bytes == size


def test_library_ids_are_not_a_video_manifest(job):
    with pytest.raises(Stop, match="MANIFEST_FILE_NOT_FOUND"):
        normalize_manifest(load("storage-manifest.json"), job)


def test_duplicate_filename_is_ambiguous(job):
    record = {"file": "video.mp4", "size_bytes": 5, "sha256": "0" * 64}
    with pytest.raises(Stop, match="MANIFEST_AMBIGUOUS"):
        normalize_manifest([record, record], job)


def test_factory_download_checks_explicit_manifest_key_mime_sha(settings, factory_job, tmp_path):
    raw = load("factory-r2-manifest.json")
    data = b"mock video"
    raw["assets"]["video"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
    factory_job.r2_manifest_key = "metadata/custom.json"
    calls = []

    class Client:
        def get_object(self, Bucket, Key):
            calls.append(Key)
            return {
                "Body": io.BytesIO(json.dumps(raw).encode() if Key.endswith(".json") else data),
                "ContentType": "application/json" if Key.endswith(".json") else "video/mp4",
            }

    r2 = R2(settings, Client())
    assert r2.download(factory_job, tmp_path).read_bytes() == data
    assert calls == ["metadata/custom.json", factory_job.r2_object_key]
    assert json.loads((tmp_path / "download.json").read_text())["schema"] == "factory-r2-v1"


def test_wrong_remote_mime_not_accepted(settings, factory_job, tmp_path):
    raw = load("factory-r2-manifest.json")

    class Client:
        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(json.dumps(raw).encode()), "ContentType": "text/html"}

    with pytest.raises(Stop, match="VIDEO_MIME_MISMATCH"):
        R2(settings, Client()).download(factory_job, tmp_path)


def test_description_is_verified_not_blindly_copied(settings, factory_job):
    raw = load("factory-r2-manifest.json")
    data = "Описание #reels".encode()
    raw["assets"]["description"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    class Client:
        def get_object(self, Bucket, Key):
            is_manifest = Key.endswith(".json")
            return {
                "Body": io.BytesIO(json.dumps(raw).encode() if is_manifest else data),
                "ContentType": "application/json" if is_manifest else "text/plain; charset=utf-8",
            }

    assert R2(settings, Client()).description(factory_job) == data.decode()
    raw["assets"]["description"]["sha256"] = "0" * 64
    with pytest.raises(Stop, match="DESCRIPTION_CHECKSUM_MISMATCH"):
        R2(settings, Client()).description(factory_job)


def test_cli_dry_run_with_real_factory_adapter_and_mock_phone(settings, factory_job):
    from android_publisher.android import MockAndroid
    from android_publisher.local import execute_dry

    raw = load("factory-r2-manifest.json")
    data = b"CI-only-video-not-a-real-MP4"
    raw["assets"]["video"].update(bytes=len(data), sha256=hashlib.sha256(data).hexdigest())

    class Client:
        def get_object(self, Bucket, Key):
            assert Bucket == "content-factory-videos"
            manifest = Key.endswith("manifest.json")
            return {
                "Body": io.BytesIO(json.dumps(raw).encode() if manifest else data),
                "ContentType": "application/json" if manifest else "video/mp4",
            }

    factory_job.account_id = "test_account"
    android = MockAndroid()
    row = execute_dry(
        settings,
        factory_job,
        android=android,
        storage=R2(settings, Client()),
        output=lambda _: None,
    )
    assert row["status"] == "DRY_RUN_READY"
    assert row["result"]["download_sha256"] == hashlib.sha256(data).hexdigest()
    assert android.submissions == 0

import json
from pathlib import Path

import pytest

from android_publisher.android import MockAndroid
from android_publisher.config import load_settings
from android_publisher.local import confirm_live, execute_dry, execute_live, setup
from android_publisher.models import Execution, Stop
from android_publisher.worker import Worker


def test_config_never_loads_secrets_or_live_from_disk(tmp_path, monkeypatch):
    path = tmp_path / "local.json"
    path.write_text(
        json.dumps(
            {
                "allow_live": True,
                "api_token": "secret",
                "r2_secret_access_key": "secret",
                "device_serial": "USB",
            }
        )
    )
    monkeypatch.delenv("PUBLISHER_ALLOW_LIVE", raising=False)
    settings = load_settings(path)
    assert settings.device_serial == "USB"
    assert not settings.allow_live
    assert settings.r2_secret_access_key == ""
    assert settings.api_token == ""


def test_producer_env_aliases_supported_but_publisher_env_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "producer-key")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "producer-secret")
    monkeypatch.setenv("R2_BUCKET_NAME", "content-factory-videos")
    monkeypatch.setenv("R2_ACCOUNT_ID", "a" * 32)
    monkeypatch.setenv("PUBLISHER_R2_ACCESS_KEY_ID", "publisher-key")
    settings = load_settings(tmp_path / "missing")
    assert settings.r2_access_key_id == "publisher-key"
    assert settings.r2_secret_access_key == "producer-secret"
    assert settings.r2_endpoint_url == "https://" + "a" * 32 + ".r2.cloudflarestorage.com"


@pytest.mark.parametrize("mode", ["TRIAL_REEL", "NORMAL_REEL"])
def test_local_dry_run_targets_only_new_job(settings, storage, job, mode):
    from android_publisher.models import Mode

    job.publication_mode = Mode(mode)
    android = MockAndroid()
    row = execute_dry(settings, job, android=android, storage=storage, output=lambda _: None)
    assert row["status"] == "DRY_RUN_READY"
    assert row["result"]["download_sha256"]
    assert android.submissions == 0
    assert not settings.allow_live


def test_local_dry_refuses_live_input(settings, storage, job):
    job.execution_mode = Execution.LIVE
    with pytest.raises(Stop, match="DRY_RUN_ONLY"):
        execute_dry(settings, job, android=MockAndroid(), storage=storage)


def test_live_does_not_even_connect_without_environment_permission(settings):
    with pytest.raises(Stop, match="LIVE_DISABLED"):
        execute_live(settings, "anything")


def test_owner_confirmation_and_changed_dry_run(settings, job, tmp_path):
    result = {
        "download_sha256": "a" * 64,
        "profile_sha256": "b" * 64,
        "device_serial": "USB",
        "app_version": "1",
    }
    with pytest.raises(Stop, match="OWNER_CANCELLED"):
        confirm_live({}, job, result, tmp_path, result, ask=lambda _: "yes", output=lambda _: None)
    assert not (tmp_path / "owner-confirmation.json").exists()
    confirm_live(
        {},
        job,
        result,
        tmp_path,
        result,
        ask=lambda _: "PUBLISH @test_account TRIAL_REEL",
        output=lambda _: None,
    )
    assert (tmp_path / "owner-confirmation.json").exists()
    changed = {**result, "download_sha256": "changed"}
    with pytest.raises(Stop, match="DRY_RUN_CHANGED"):
        confirm_live(
            {}, job, changed, tmp_path, result, ask=lambda _: pytest.fail("must not prompt")
        )


def test_recovered_local_live_never_runs_without_owner_callback(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    job.owner_confirmation_required = True
    jid = store.create(job)["id"]
    store.claim()
    store.recover()
    android = MockAndroid()
    Worker(store, android, storage, settings).tick(jid)
    assert store.get(jid)["error"] == "OWNER_CONFIRMATION_REQUIRED"
    assert android.submissions == 0
    assert android.preparations == 0


def test_hash_dedupe_survives_changed_video_id(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    jid = store.create(job)["id"]
    android = MockAndroid()
    worker = Worker(store, android, storage, settings)
    worker.tick(jid)
    job.video_id = "renamed"
    job.idempotency_key = "new-key"
    second = store.create(job)["id"]
    worker.tick(second)
    assert store.get(second)["error"] == "DUPLICATE_PUBLICATION"
    assert android.submissions == 1
    assert android.preparations == 1


def test_setup_saves_only_nonsecrets_and_never_enables_live(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("profiles").mkdir()
    Path("profiles/instagram-en.example.json").write_text('{"blockers":["captcha"],"selectors":{}}')
    monkeypatch.setattr(
        "android_publisher.local.adb_devices", lambda _: [{"serial": "USB", "state": "device"}]
    )
    monkeypatch.setattr(
        "android_publisher.local.diagnose", lambda *a, **kw: {"ok": False, "checks": []}
    )
    answers = iter(["", "my_account", "a" * 32, "", "", "my caption", "1"])
    secrets = iter(["KEY_ID_PRIVATE", "SECRET_PRIVATE"])
    settings = setup(
        ask=lambda _: next(answers), output=lambda _: None, secret_input=lambda _: next(secrets)
    )
    saved = Path("publisher.local.json").read_text()
    profile = Path("profiles/instagram-local.json").read_text()
    assert "KEY_ID_PRIVATE" not in saved + profile
    assert "SECRET_PRIVATE" not in saved + profile
    assert "allow_live" not in saved
    assert not settings.allow_live
    assert json.loads(profile)["import_method"] == "share_intent"
    assert json.loads(Path("data/first-job.json").read_text())["execution_mode"] == "DRY_RUN"


def test_confirming_owner_still_does_not_tap_if_changed_bytes(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    job.owner_confirmation_required = True
    android = MockAndroid()
    jid = store.create(job)["id"]

    def reject(*args):
        raise Stop("DRY_RUN_CHANGED")

    Worker(store, android, storage, settings, before_submit=reject).tick(jid)
    assert android.submissions == 0
    assert not store.get(jid)["submitted"]


def test_owner_decline_leaves_no_submit_reservation(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    job.owner_confirmation_required = True
    android = MockAndroid()
    jid = store.create(job)["id"]

    def decline(*args):
        raise Stop("OWNER_CANCELLED")

    Worker(store, android, storage, settings, before_submit=decline).tick(jid)
    row = store.get(jid)
    assert row["error"] == "OWNER_CANCELLED"
    assert row["submitted"] == 0
    assert android.submissions == 0
    assert not store.duplicate(job)

import json
from datetime import date
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

from android_publisher.android import MockAndroid
from android_publisher.daily import configure_flow, days_to_check, discover, enable_daily, run_daily
from android_publisher.models import Stop


class DailyStorage:
    def __init__(self, ready):
        self.ready = ready
        self.requested = []

    def read_manifest(self, job):
        self.requested.append(job.r2_manifest_key)
        if job.r2_manifest_key not in self.ready:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return None, {}

    def description(self, job):
        return "Описание " + job.video_id

    def download(self, job, directory):
        path = directory / "video.mp4"
        path.write_bytes(job.video_id.encode())
        return path


def keys(*slots):
    return {f"reels/ru/daily/2026-11-11/{slot:02}/manifest.json" for slot in slots}


def test_real_daily_factory_keys_and_partial_batch():
    storage = DailyStorage(keys(1, 2, 3, 4))
    jobs = discover(storage, day=date(2026, 11, 11), lookback=0)
    assert len(jobs) == 4  # Fifth slot is not ready; batch status is deliberately not read.
    assert len(storage.requested) == 5
    assert jobs[0].account_id == "crabscoder"
    assert jobs[0].publication_mode == "NORMAL_REEL"
    assert all(job.execution_mode == "DRY_RUN" for job in jobs)
    assert jobs[-1].r2_object_key == "reels/ru/daily/2026-11-11/04/video.mp4"


def test_access_denied_is_not_treated_as_empty_day():
    class Denied(DailyStorage):
        def read_manifest(self, job):
            raise ClientError({"Error": {"Code": "AccessDenied"}}, "GetObject")

    with pytest.raises(Stop, match="R2_OBJECT_UNAVAILABLE"):
        discover(Denied(set()), day=date(2026, 11, 11))


def test_empty_day_is_valid_and_yesterday_is_checked():
    storage = DailyStorage(set())
    assert discover(storage, day=date(2026, 11, 12)) == []
    assert len(storage.requested) == 10
    assert storage.requested[0].startswith("reels/ru/daily/2026-11-11/")
    assert days_to_check(date(2026, 11, 12), 0) == [date(2026, 11, 12)]
    with pytest.raises(Stop, match="INVALID_LOOKBACK"):
        days_to_check(lookback=30)


def test_daily_dry_run_prepares_one_never_publishes(settings):
    phone = MockAndroid(account="crabscoder")
    result = run_daily(
        settings,
        day=date(2026, 11, 11),
        lookback=0,
        storage=DailyStorage(keys(1, 2, 3, 4)),
        android=phone,
    )
    assert result["ready"] == 4
    assert len(result["jobs"]) == 1
    assert result["jobs"][0]["status"] == "DRY_RUN_READY"
    assert phone.submissions == 0


def test_daily_live_requires_permission_before_touching_android(settings):
    with pytest.raises(Stop, match="LIVE_DISABLED"):
        run_daily(settings, live=True, android=SimpleNamespace(connect=lambda: pytest.fail("no")))


def test_daily_live_requires_local_policy(settings):
    settings.allow_live = True
    with pytest.raises(Stop, match="DAILY_NOT_ENABLED"):
        run_daily(settings, live=True, android=MockAndroid(account="crabscoder"))


def test_authorized_daily_posts_four_and_never_repeats(settings, monkeypatch):
    settings.allow_live = True
    monkeypatch.setattr("android_publisher.daily.require_policy", lambda *a: {})
    phone = MockAndroid(account="crabscoder")
    storage = DailyStorage(keys(1, 2, 3, 4))
    report = run_daily(
        settings, day=date(2026, 11, 11), lookback=0, live=True, storage=storage, android=phone
    )
    assert [row["status"] for row in report["jobs"]] == ["PUBLISHED"] * 4
    assert phone.submissions == 4
    again = run_daily(
        settings, day=date(2026, 11, 11), lookback=0, live=True, storage=storage, android=phone
    )
    assert [row["status"] for row in again["jobs"]] == ["ALREADY_SUBMITTED"] * 4
    assert phone.submissions == 4


def test_unknown_result_stops_rest_of_batch_and_next_runs(settings, monkeypatch):
    settings.allow_live = True
    monkeypatch.setattr("android_publisher.daily.require_policy", lambda *a: {})

    class Uncertain(MockAndroid):
        def submit(self, prepared):
            self.submissions += 1
            raise Stop("UNVERIFIED_RESULT")

    phone = Uncertain(account="crabscoder")
    storage = DailyStorage(keys(1, 2))
    report = run_daily(
        settings, day=date(2026, 11, 11), lookback=0, live=True, storage=storage, android=phone
    )
    assert len(report["jobs"]) == 1
    assert report["jobs"][0]["status"] == "UNKNOWN_RESULT"
    assert phone.submissions == 1
    with pytest.raises(Stop, match="DEVICE_PAUSED"):
        run_daily(settings, live=True, storage=storage, android=phone)


def test_repeat_discovery_uses_same_idempotency(settings):
    from android_publisher.store import Store

    storage = DailyStorage(keys(1))
    store = Store(settings.data_dir / "test.sqlite3")
    first = discover(storage, day=date(2026, 11, 11), lookback=0)[0]
    second = discover(storage, day=date(2026, 11, 11), lookback=0)[0]
    assert store.create(first)["id"] == store.create(second)["id"]


def test_enable_daily_refuses_dry_run_or_unverified_pilot(settings):
    with pytest.raises(Stop, match="VERIFIED_PILOT_REQUIRED"):
        enable_daily(settings, "missing", ask=lambda _: pytest.fail("must not ask"))


def test_configure_flow_does_not_enable_live_or_reuse_wrong_final_selector(settings):
    settings.selector_profile = settings.data_dir / "profile.json"
    settings.selector_profile.write_text(
        json.dumps({"selectors": {"publish": [{"text": "Далее"}]}, "live_calibrated": True})
    )
    configure_flow(settings)
    profile = json.loads(settings.selector_profile.read_text())
    assert profile["submission_flow"] == "next_then_confirmation"
    assert not profile["live_calibrated"]
    assert profile["selectors"]["publish"] == []
    assert not settings.allow_live


def test_policy_enablement_binds_account_device_profile_and_ui_pilot(settings):
    import hashlib

    from android_publisher.daily import require_policy
    from android_publisher.models import Job
    from android_publisher.store import Store

    settings.selector_profile = settings.data_dir / "profile.json"
    settings.selector_profile.write_text('{"live_calibrated":true}')
    settings.device_serial = "USB"
    sha = hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
    store = Store(settings.data_dir / "publisher.sqlite3")
    pilot = Job(
        video_id="pilot",
        r2_object_key="pilot/video.mp4",
        account_id="crabscoder",
        publication_mode="NORMAL_REEL",
        execution_mode="LIVE",
        idempotency_key="pilot",
    )
    jid = store.create(pilot)["id"]
    store.finish(
        jid,
        "PUBLISHED",
        result={
            "verification": "ui-success-marker",
            "profile_sha256": sha,
            "device_serial": "USB",
            "app_version": "454",
        },
    )
    policy = enable_daily(settings, jid, ask=lambda _: "ENABLE DAILY @crabscoder")
    assert policy["enabled"]
    phone = SimpleNamespace(connect=lambda: None, identity=lambda: {"app_version": "454"})
    assert require_policy(settings, phone)["pilot_job_id"] == jid
    phone.identity = lambda: {"app_version": "455"}
    with pytest.raises(Stop, match="DRY_RUN_CHANGED"):
        require_policy(settings, phone)
    settings.selector_profile.write_text('{"live_calibrated":false}')
    with pytest.raises(Stop, match="DRY_RUN_CHANGED"):
        require_policy(settings, phone)


def test_owner_verified_pilot_cannot_enable_unattended_publication(settings):
    import hashlib

    from android_publisher.models import Job
    from android_publisher.store import Store

    settings.selector_profile = settings.data_dir / "profile.json"
    settings.selector_profile.write_text('{"live_calibrated":true}')
    store = Store(settings.data_dir / "publisher.sqlite3")
    job = Job(
        video_id="pilot",
        r2_object_key="pilot/video.mp4",
        account_id="crabscoder",
        publication_mode="NORMAL_REEL",
        idempotency_key="pilot",
    )
    jid = store.create(job)["id"]
    store.finish(
        jid,
        "PUBLISHED",
        result={
            "verification": "owner-confirmed",
            "device_serial": settings.device_serial,
            "profile_sha256": hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest(),
            "app_version": "454",
        },
    )
    with pytest.raises(Stop, match="VERIFIED_PILOT_REQUIRED"):
        enable_daily(settings, jid, ask=lambda _: pytest.fail("must not ask"))

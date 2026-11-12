import pytest

from android_publisher.models import Stop
from android_publisher.recovery import record_owner_published
from android_publisher.store import Store


def test_owner_record_never_resends_or_releases_reservation(settings, job):
    store = Store(settings.data_dir / "publisher.sqlite3")
    jid = store.create(job)["id"]
    store.claim(jid)
    store.barrier(jid, job, "a" * 64)
    store.pause()
    store.finish(jid, "UNKNOWN_RESULT", error="UNVERIFIED_RESULT")
    row = record_owner_published(
        settings, jid, "https://www.instagram.com/reel/Real123/?igsh=tracking"
    )
    assert row["status"] == "PUBLISHED"
    assert row["submitted"] == 1
    assert row["result"]["verification"] == "owner-confirmed"
    assert row["result"]["permalink"] == "https://www.instagram.com/reel/Real123/"
    assert store.paused()
    assert store.duplicate(job, "a" * 64)
    assert not (settings.data_dir / "daily-authorization.json").exists()


def test_cannot_mark_unattempted_job_as_published(settings, job):
    store = Store(settings.data_dir / "publisher.sqlite3")
    jid = store.create(job)["id"]
    with pytest.raises(Stop, match="RECONCILIATION_REQUIRES_UNKNOWN_SUBMITTED"):
        record_owner_published(settings, jid, "https://www.instagram.com/reel/Real123/")
    assert store.get(jid)["status"] == "QUEUED"


def test_bad_link_does_not_change_state(settings, job):
    store = Store(settings.data_dir / "publisher.sqlite3")
    jid = store.create(job)["id"]
    with pytest.raises(Stop, match="INVALID_PERMALINK"):
        record_owner_published(settings, jid, "https://evil.example/reel/Real123/")
    assert store.get(jid)["status"] == "QUEUED"

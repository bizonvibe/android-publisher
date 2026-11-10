from concurrent.futures import ThreadPoolExecutor

import pytest

from android_publisher.models import Job, Stop


def test_idempotency_conflict(store, job):
    row = store.create(job)
    assert store.create(job)["id"] == row["id"]
    job.caption = "changed"
    with pytest.raises(Stop, match="IDEMPOTENCY_CONFLICT"):
        store.create(job)


def test_default_schedule_idempotency(store, job):
    payload = job.model_dump(exclude={"scheduled_at"})
    assert store.create(Job(**payload))["id"] == store.create(Job(**payload))["id"]


def test_parallel_claim(store, job):
    store.create(job)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: store.claim(), range(4)))
    assert sum(row is not None for row in results) == 1


@pytest.mark.parametrize("submitted", [False, True])
def test_recovery(store, job, submitted):
    jid = store.create(job)["id"]
    store.claim()
    if submitted:
        store.barrier(jid, job)
    store.recover()
    assert store.get(jid)["status"] == ("UNKNOWN_RESULT" if submitted else "QUEUED")
    assert store.paused() == submitted


def test_future_schedule(store, job):
    job.scheduled_at = job.scheduled_at.replace(year=2099)
    store.create(job)
    assert store.claim() is None


def test_restarts_do_not_allow_unbounded_attempts(store, job):
    jid = store.create(job)["id"]
    store.claim()
    store.recover(max_attempts=1)
    assert store.get(jid)["status"] == "FAILED"
    assert store.get(jid)["error"] == "ATTEMPTS_EXHAUSTED"
    assert store.claim() is None

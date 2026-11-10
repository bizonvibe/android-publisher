import pytest

from android_publisher.android import MockAndroid
from android_publisher.models import Execution, Mode, Retryable, Stop
from android_publisher.worker import Worker


def run(store, settings, storage, job, android=None):
    android = android or MockAndroid()
    row = store.create(job)
    worker = Worker(store, android, storage, settings)
    worker.tick()
    return store.get(row["id"]), worker, android


@pytest.mark.parametrize("mode", ["TRIAL_REEL", "NORMAL_REEL"])
def test_dry_run_never_submits(store, settings, storage, job, mode):
    job.publication_mode = Mode(mode)
    result, _, android = run(store, settings, storage, job)
    assert result["status"] == "DRY_RUN_READY"
    assert result["result"]["trial_available"]
    assert result["artifacts"]
    assert android.submissions == 0
    assert not result["submitted"]


def test_live_disabled(store, settings, storage, job):
    job.execution_mode = Execution.LIVE
    result, _, android = run(store, settings, storage, job)
    assert result["error"] == "LIVE_DISABLED"
    assert android.preparations == 0
    assert storage.downloads == 0


def test_live_and_duplicate_other_key(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    result, _, android = run(store, settings, storage, job)
    assert result["status"] == "PUBLISHED"
    assert result["submitted"]
    job.idempotency_key = "key2"
    result, _, _ = run(store, settings, storage, job, android)
    assert result["error"] == "DUPLICATE_PUBLICATION"
    assert android.submissions == 1


@pytest.mark.parametrize(
    "failure,code",
    [
        (Stop("ACCOUNT_MISMATCH"), "ACCOUNT_MISMATCH"),
        (Stop("HUMAN_INTERVENTION_REQUIRED"), "HUMAN_INTERVENTION_REQUIRED"),
        (Stop("FEATURE_UNAVAILABLE"), "FEATURE_UNAVAILABLE"),
    ],
)
def test_stops(store, settings, storage, job, failure, code):
    result, _, android = run(store, settings, storage, job, MockAndroid(failure=failure))
    assert result["error"] == code
    assert android.submissions == 0
    assert store.paused() == (code == "HUMAN_INTERVENTION_REQUIRED")


def test_normal_does_not_require_trial(store, settings, storage, job):
    job.publication_mode = Mode.NORMAL_REEL
    result, _, _ = run(store, settings, storage, job, MockAndroid(trial=False))
    assert result["status"] == "DRY_RUN_READY"


def test_unknown_never_retried_and_pauses_device(store, settings, storage, job):
    class LostResponse(MockAndroid):
        def submit(self, prepared):
            self.submissions += 1
            raise Retryable("connection lost after tap")

    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    result, worker, android = run(store, settings, storage, job, LostResponse())
    assert result["status"] == "UNKNOWN_RESULT"
    assert store.paused()
    store.recover()
    assert not worker.tick()
    assert android.submissions == 1
    store.pause(False)
    assert not worker.tick()


def test_barrier_persisted_before_tap(store, settings, storage, job):
    class Inspect(MockAndroid):
        def submit(self, prepared):
            assert store.get(jid)["submitted"] == 1
            assert store.get(jid)["status"] == "SUBMITTING"
            return super().submit(prepared)

    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    jid = store.create(job)["id"]
    Worker(store, Inspect(), storage, settings).tick()
    assert store.get(jid)["status"] == "PUBLISHED"


def test_bounded_retry(store, settings, storage, job):
    settings.max_attempts = 2
    result, worker, android = run(
        store, settings, storage, job, MockAndroid(failure=Retryable("offline"))
    )
    assert result["status"] == "QUEUED"
    assert not worker.tick()  # backoff
    with store.db() as db:
        db.execute("UPDATE jobs SET due='2000'")
    worker.tick()
    assert store.get(result["id"])["status"] == "FAILED"
    assert android.preparations == 2


def test_promotion_requires_published_trial(store, settings, storage, job):
    job.publication_mode = Mode.SHARE_TRIAL_TO_EVERYONE
    job.source_job_id = "missing"
    result, _, android = run(store, settings, storage, job)
    assert result["error"] == "INVALID_TRIAL_SOURCE"
    assert android.submissions == 0


def test_explicit_promotion_only_and_once(store, settings, storage, job):
    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    result, worker, android = run(store, settings, storage, job)
    assert android.submissions == 1
    assert not worker.tick()  # no automatic sharing
    job.publication_mode = Mode.SHARE_TRIAL_TO_EVERYONE
    job.source_job_id = result["id"]
    job.idempotency_key = "promote-1"
    result, _, _ = run(store, settings, storage, job, android)
    assert result["status"] == "PUBLISHED"
    assert storage.downloads == 1
    job.idempotency_key = "promote-2"
    result, _, _ = run(store, settings, storage, job, android)
    assert result["error"] == "DUPLICATE_PUBLICATION"
    assert android.submissions == 2


def test_unverified_result_is_unknown(store, settings, storage, job):
    class Unverified(MockAndroid):
        def submit(self, prepared):
            return {"verified": False}

    settings.allow_live = True
    job.execution_mode = Execution.LIVE
    result, _, _ = run(store, settings, storage, job, Unverified())
    assert result["status"] == "UNKNOWN_RESULT"


def test_pause_survives_restart_and_blocks_other_jobs(store, settings, storage, job):
    result, worker, android = run(
        store, settings, storage, job, MockAndroid(failure=Stop("HUMAN_INTERVENTION_REQUIRED"))
    )
    job.idempotency_key = "next"
    jid = store.create(job)["id"]
    store.recover()
    assert not worker.tick()
    assert store.get(jid)["status"] == "QUEUED"
    assert android.preparations == 1


def test_unavailable_trial_status(store, settings, storage, job):
    result, _, android = run(store, settings, storage, job, MockAndroid(trial=False))
    assert result["status"] == "FEATURE_UNAVAILABLE"
    assert android.submissions == 0


def test_readable_stop_report_with_last_step(store, settings, storage, job):
    import json

    android = MockAndroid(failure=Stop("UI_NOT_FOUND:caption"))
    android.last_step = "caption"
    result, _, _ = run(store, settings, storage, job, android)
    path = settings.data_dir / "jobs" / result["id"] / "stop.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    assert report["step"] == "caption"
    assert "calibrate --step caption" in report["fix"]
    assert report["submitted"] is False

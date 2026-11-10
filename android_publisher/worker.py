import logging
from datetime import UTC, datetime, timedelta

from .models import Execution, Job, Mode, Retryable, Stop

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, android, storage, settings):
        self.store, self.android, self.storage, self.settings = store, android, storage, settings

    def tick(self):
        row = self.store.claim()
        if row is None:
            return False
        jid, job = row["id"], Job.model_validate(row["job"])
        directory = self.settings.data_dir / "jobs" / jid
        directory.mkdir(parents=True, exist_ok=True)
        submitted = False
        try:
            if job.execution_mode == Execution.LIVE and not self.settings.allow_live:
                raise Stop("LIVE_DISABLED")
            source = None
            if job.publication_mode == Mode.SHARE_TRIAL_TO_EVERYONE:
                source = self.store.get(job.source_job_id)
                if not source or not (
                    source["status"] == "PUBLISHED"
                    and source["job"]["publication_mode"] == Mode.TRIAL_REEL
                    and source["job"]["execution_mode"] == Execution.LIVE
                    and source["job"]["account_id"].casefold() == job.account_id.casefold()
                    and source["job"]["video_id"] == job.video_id
                    and source["job"]["r2_object_key"] == job.r2_object_key
                ):
                    raise Stop("INVALID_TRIAL_SOURCE")
                video = None
            else:
                video = self.storage.download(job, directory)
            prepared = self.android.prepare(job, video, source)
            if job.execution_mode == Execution.DRY_RUN:
                self.store.finish(
                    jid,
                    "DRY_RUN_READY",
                    result=prepared,
                    artifacts=self.android.evidence(directory),
                )
                return True
            self.store.barrier(jid, job)
            submitted = True
            result = self.android.submit(prepared)
            if result.get("verified") is not True:
                raise Stop("UNVERIFIED_RESULT")
            self.store.finish(
                jid, "PUBLISHED", result=result, artifacts=self.android.evidence(directory)
            )
        except Exception as exc:
            # Never persist raw exception strings: SDK errors may contain credentials.
            error = (
                exc.code
                if isinstance(exc, Stop)
                else ("TEMPORARY_FAILURE" if isinstance(exc, Retryable) else "INTERNAL_ERROR")
            )
            status = (
                "UNKNOWN_RESULT"
                if submitted
                else ("BLOCKED" if error == "HUMAN_INTERVENTION_REQUIRED" else "FAILED")
            )
            if not submitted and error == "FEATURE_UNAVAILABLE":
                status = "FEATURE_UNAVAILABLE"
            due = None
            if (
                not submitted
                and isinstance(exc, Retryable)
                and row["attempts"] < self.settings.max_attempts
            ):
                status = "QUEUED"
                due = (
                    datetime.now(UTC)
                    + timedelta(seconds=self.settings.retry_seconds * row["attempts"])
                ).isoformat()
            if submitted or error == "HUMAN_INTERVENTION_REQUIRED":
                self.store.pause()
            self.store.finish(
                jid, status, error=error, artifacts=self.android.evidence(directory), due=due
            )
            logger.warning("job=%s status=%s code=%s", jid, status, error)
        return True

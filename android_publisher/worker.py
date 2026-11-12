import hashlib
import logging
from datetime import UTC, datetime, timedelta

from .models import Execution, Job, Mode, Retryable, Stop

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, store, android, storage, settings, before_submit=None):
        self.store, self.android, self.storage, self.settings = store, android, storage, settings
        self.before_submit = before_submit

    def tick(self, jid=None):
        row = self.store.claim(jid)
        if row is None:
            return False
        jid, job = row["id"], Job.model_validate(row["job"])
        directory = self.settings.data_dir / "jobs" / jid
        directory.mkdir(parents=True, exist_ok=True)
        if hasattr(self.android, "set_artifact_dir"):
            self.android.set_artifact_dir(directory)
        submitted = False
        try:
            if job.owner_confirmation_required and self.before_submit is None:
                raise Stop("OWNER_CONFIRMATION_REQUIRED")
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
            sha256 = None
            if video is not None:
                with video.open("rb") as stream:
                    sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
            if self.store.duplicate(job, sha256):
                raise Stop("DUPLICATE_PUBLICATION")
            prepared = self.android.prepare(job, video, source)
            prepared["download_sha256"] = sha256
            if job.execution_mode == Execution.DRY_RUN:
                self.store.finish(
                    jid,
                    "DRY_RUN_READY",
                    result=prepared,
                    artifacts=self.android.evidence(directory),
                )
                return True
            if self.before_submit is not None:
                self.before_submit(row, job, prepared, directory)
            self.store.barrier(jid, job, sha256)
            submitted = True
            result = self.android.submit(prepared)
            if result.get("verified") is not True:
                raise Stop("UNVERIFIED_RESULT")
            result.update(
                {
                    key: prepared.get(key)
                    for key in ("device_serial", "app_version", "profile_sha256", "download_sha256")
                }
            )
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
            import json

            from .guidance import guidance

            report = {
                "status": status,
                "error": error,
                "submitted": submitted,
                "step": getattr(self.android, "last_step", None),
                "fix": guidance("UNKNOWN_RESULT" if submitted else error),
            }
            (directory / "stop.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            self.store.finish(
                jid,
                status,
                result={"instructions": report["fix"]},
                error=error,
                artifacts=self.android.evidence(directory) + [str(directory / "stop.json")],
                due=due,
            )
            logger.warning("job=%s status=%s code=%s", jid, status, error)
        return True

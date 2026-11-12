"""One noninteractive Windows invocation: discover complete daily manifests and process new jobs."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from botocore.exceptions import ClientError

from .android import Android
from .locking import device_session
from .models import Execution, Job, Mode, Stop
from .storage import R2
from .store import Store
from .success import configured as success_configured
from .worker import Worker

MOSCOW = ZoneInfo("Europe/Moscow")


def days_to_check(day=None, lookback=1):
    if not 0 <= lookback <= 7:
        raise Stop("INVALID_LOOKBACK")
    first = day or datetime.now(MOSCOW).date()
    return [first - timedelta(days=offset) for offset in range(lookback, -1, -1)]


def discover(storage, *, day=None, lookback=1, execution=Execution.DRY_RUN):
    """Known producer paths, no ListBucket or stale batch-status.json dependency."""
    jobs = []
    for current in days_to_check(day, lookback):
        for slot in range(1, 6):
            prefix = f"reels/ru/daily/{current.isoformat()}/{slot:02}"
            job = Job(
                video_id=f"daily-{current:%Y%m%d}-{slot:02}",
                account_id="crabscoder",
                r2_object_key=prefix + "/video.mp4",
                r2_manifest_key=prefix + "/manifest.json",
                publication_mode=Mode.NORMAL_REEL,
                execution_mode=execution,
                idempotency_key=f"daily:crabscoder:{current}:{slot:02}:{execution}",
            )
            try:
                storage.read_manifest(job)  # Validates ready, exact prefix/key, hash, size, MIME.
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
                    continue  # Not ready yet. Never remember a missing slot as completed.
                raise Stop("R2_OBJECT_UNAVAILABLE") from None
            job.caption = storage.description(job)
            jobs.append(Job.model_validate(job.model_dump(exclude={"scheduled_at"})))
    return jobs


def policy_path(settings):
    return settings.data_dir / "daily-authorization.json"


def require_policy(settings, android):
    path = policy_path(settings)
    if not path.exists():
        raise Stop("DAILY_NOT_ENABLED")
    policy = json.loads(path.read_text(encoding="utf-8"))
    expected = hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
    if (
        policy.get("enabled") is not True
        or policy.get("account_id") != "crabscoder"
        or policy.get("publication_mode") != "NORMAL_REEL"
        or policy.get("device_serial") != settings.device_serial
        or policy.get("profile_sha256") != expected
    ):
        raise Stop("DRY_RUN_CHANGED")
    # Only read metadata. The worker checks username again before every import.
    android.connect()
    if android.identity().get("app_version") != policy.get("app_version"):
        raise Stop("DRY_RUN_CHANGED")
    return policy


def run_daily(settings, *, day=None, lookback=1, live=False, storage=None, android=None):
    """Never prompts or calibrates. A single bad/unknown job stops this invocation."""
    if live and (not settings.allow_live or settings.android_backend != "uiautomator2"):
        raise Stop("LIVE_DISABLED")
    storage = storage or R2(settings)
    android = android or Android(settings)
    with device_session(settings):
        store = Store(settings.data_dir / "publisher.sqlite3")
        store.recover(settings.max_attempts)
        if store.paused():
            raise Stop("DEVICE_PAUSED")
        if live:
            require_policy(settings, android)
        jobs = discover(
            storage,
            day=day,
            lookback=lookback,
            execution=Execution.LIVE if live else Execution.DRY_RUN,
        )
        if jobs and not live:
            (settings.data_dir / "daily-first-job.json").write_text(
                jobs[0].model_dump_json(indent=2), encoding="utf-8"
            )
        report = {
            "started_at": datetime.now(UTC).isoformat(),
            "mode": "LIVE" if live else "DRY_RUN",
            "account_id": "crabscoder",
            "ready": len(jobs),
            "jobs": [],
        }
        worker = Worker(store, android, storage, settings)
        for job in jobs:
            # Never queue a second posting of a video published through API/manual CLI.
            if store.duplicate(job):
                report["jobs"].append({"video_id": job.video_id, "status": "ALREADY_SUBMITTED"})
                continue
            row = store.create(job)
            if row["status"] == "QUEUED":
                worker.tick(row["id"])
            row = store.get(row["id"])
            report["jobs"].append(
                {
                    "id": row["id"],
                    "video_id": job.video_id,
                    "status": row["status"],
                    "error": row["error"],
                }
            )
            if row.get("error") == "DUPLICATE_PUBLICATION" and not row["submitted"]:
                report["jobs"][-1]["status"] = "ALREADY_SUBMITTED"
                continue
            if row["status"] not in {"PUBLISHED", "DRY_RUN_READY"}:
                break
            # DRY_RUN leaves a composer open. Do not accumulate unsubmitted drafts.
            if not live:
                break
        report["completed_at"] = datetime.now(UTC).isoformat()
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        (settings.data_dir / "daily-last-run.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report


def configure_flow(settings):
    """One-time profile migration, no Android interaction and no LIVE enablement."""
    from .calibration import save_profile

    profile = json.loads(settings.selector_profile.read_text(encoding="utf-8"))
    profile.update(
        submission_flow="next_then_confirmation",
        composer_account_verification="profile_before_import",
        live_calibrated=False,
    )
    selectors = profile.setdefault("selectors", {})
    for name in ("publish_next", "publication_confirmation", "publish", "publication_success"):
        selectors[name] = []  # Never reinterpret the old publish selector as a different action.
    save_profile(settings.selector_profile, profile)
    policy_path(settings).unlink(missing_ok=True)


def enable_daily(settings, pilot_id, *, ask=input):
    """Authorizes a recurring account/mode/device/profile only after a verified local pilot."""
    with device_session(settings):
        store = Store(settings.data_dir / "publisher.sqlite3")
        pilot = store.get(pilot_id)
        if not pilot or pilot["status"] != "PUBLISHED":
            raise Stop("VERIFIED_PILOT_REQUIRED")
        job, result = pilot["job"], pilot["result"] or {}
        digest = hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
        if (
            job.get("execution_mode") != "LIVE"
            or job["account_id"] != "crabscoder"
            or job["publication_mode"] != "NORMAL_REEL"
            or result.get("verification") != "ui-success-marker"
            or result.get("profile_sha256") != digest
            or result.get("device_serial") != settings.device_serial
            or not result.get("app_version")
        ):
            raise Stop("VERIFIED_PILOT_REQUIRED")
        profile = json.loads(settings.selector_profile.read_text(encoding="utf-8"))
        if profile.get("live_calibrated") is not True:
            raise Stop("PROFILE_NOT_CALIBRATED_FOR_LIVE")
        if (
            ask(
                "Разрешить ежедневную публикацию новых готовых NORMAL_REEL в @crabscoder? Введите ENABLE DAILY @crabscoder: "
            ).strip()
            != "ENABLE DAILY @crabscoder"
        ):
            raise Stop("OWNER_CANCELLED")
        policy = {
            "enabled": True,
            "account_id": "crabscoder",
            "publication_mode": "NORMAL_REEL",
            "device_serial": settings.device_serial,
            "profile_sha256": digest,
            "app_version": result["app_version"],
            "pilot_job_id": pilot_id,
            "enabled_at": datetime.now(UTC).isoformat(),
        }
        policy_path(settings).write_text(json.dumps(policy, indent=2), encoding="utf-8")
        return policy


def approve_profile(settings, *, ask=input):
    """Freeze the calibrated two-stage contract before a one-off physical LIVE pilot."""
    from .calibration import save_profile

    with device_session(settings):
        profile = json.loads(settings.selector_profile.read_text(encoding="utf-8"))
        required = (
            "profile_tab",
            "account_username",
            "caption",
            "normal_mode_marker",
            "publish_next",
            "publication_confirmation",
            "publish",
        )
        if (
            profile.get("submission_flow") != "next_then_confirmation"
            or any(not profile.get("selectors", {}).get(name) for name in required)
            or not success_configured(profile)
        ):
            raise Stop("DAILY_PROFILE_INCOMPLETE")
        if (
            ask(
                "Проверены Next, отдельное подтверждение Share и маркер успеха? Введите APPROVE @crabscoder: "
            ).strip()
            != "APPROVE @crabscoder"
        ):
            raise Stop("OWNER_CANCELLED")
        profile["live_calibrated"] = True
        save_profile(settings.selector_profile, profile)
        policy_path(settings).unlink(missing_ok=True)

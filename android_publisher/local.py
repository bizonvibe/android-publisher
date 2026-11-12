"""Small local setup and execution workflow, sharing the API's store and safety barrier."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from getpass import getpass
from pathlib import Path
from uuid import uuid4

from .android import Android
from .calibration import Calibrator, GuidedAndroid, save_profile
from .config import LOCAL_FIELDS, load_settings
from .doctor import adb_devices, diagnose
from .guidance import guidance
from .locking import device_session
from .models import Execution, Job, Stop
from .storage import R2
from .store import Store
from .worker import Worker

FIRST_VIDEO = "reels/ru/reel-20261109-92645916/video.mp4"
FIRST_MANIFEST = "reels/ru/reel-20261109-92645916/manifest.json"


def read_job(path):
    return Job.model_validate_json(Path(path).read_text(encoding="utf-8-sig"))


def credentials(settings, secret_input=getpass):
    # Process memory only. Supports the producer's R2_* variables too.
    if not settings.r2_access_key_id:
        settings.r2_access_key_id = secret_input(
            "R2_ACCESS_KEY_ID (не сохраняется; пусто=позже): "
        ).strip()
    if not settings.r2_secret_access_key:
        settings.r2_secret_access_key = secret_input(
            "R2_SECRET_ACCESS_KEY (не сохраняется; пусто=позже): "
        ).strip()


def print_report(report, output=print):
    for check in report["checks"]:
        output(
            ("OK " if check["ok"] else "НУЖНО ДЕЙСТВИЕ ")
            + check["check"]
            + ": "
            + str(check["detail"])
        )
        if check.get("fix"):
            output(check["fix"])


def setup(config_path=Path("publisher.local.json"), ask=input, output=print, secret_input=getpass):
    settings = load_settings(config_path)
    settings.allow_live = False
    output("Мастер Windows/Android. Только настройка и DRY_RUN. LIVE не включается.")
    output(
        "Включите USB debugging, подключите кабель, разблокируйте телефон и подтвердите RSA. Установите Instagram и войдите вручную."
    )
    settings.adb_path = (
        ask(f"Путь к adb.exe [Enter={settings.adb_path}]: ").strip() or settings.adb_path
    )
    try:
        devices = adb_devices(settings.adb_path)
    except Stop:
        output(
            "Установите официальные Platform Tools: https://developer.android.com/tools/releases/platform-tools"
        )
        output(
            "Запустите мастер снова и укажите полный путь к adb.exe. Секреты ещё не запрашивались."
        )
        return None
    ready = [device["serial"] for device in devices if device["state"] == "device"]
    output("ADB устройства: " + str(devices))
    if len(ready) == 1:
        settings.device_serial = ready[0]
    else:
        settings.device_serial = ask("Serial устройства со статусом device: ").strip()
    if settings.device_serial not in ready:
        output(guidance("DEVICE_UNAVAILABLE"))
        return None
    account = ask("Точный Instagram username (без @): ").strip().lstrip("@")
    endpoint = ask(
        f"R2 endpoint HTTPS или account ID [Enter={settings.r2_endpoint_url or 'не задан'}]: "
    ).strip()
    if endpoint:
        settings.r2_endpoint_url = (
            endpoint
            if endpoint.startswith("https://")
            else "https://" + endpoint + ".r2.cloudflarestorage.com"
        )
    video_key = ask(f"MP4 key [Enter={FIRST_VIDEO}]: ").strip() or FIRST_VIDEO
    manifest_key = ask(f"Manifest key [Enter={FIRST_MANIFEST}]: ").strip() or FIRST_MANIFEST
    caption = ask("Описание и хештеги (Enter=загрузить проверенное описание из R2): ").strip()
    selected_mode = ask("Режим: 1=TRIAL_REEL (по умолчанию), 2=NORMAL_REEL: ").strip()
    if selected_mode not in {"", "1", "2"}:
        raise Stop("INVALID_MODE")
    job = Job(
        video_id=Path(video_key).parent.name,
        r2_object_key=video_key,
        r2_manifest_key=manifest_key,
        account_id=account,
        caption=caption,
        publication_mode="NORMAL_REEL" if selected_mode == "2" else "TRIAL_REEL",
        execution_mode=Execution.DRY_RUN,
        idempotency_key="dry-" + uuid4().hex,
    )
    settings.selector_profile = Path("profiles/instagram-local.json")
    if not settings.selector_profile.exists():
        example = json.loads(Path("profiles/instagram-en.example.json").read_text(encoding="utf-8"))
        example.update(import_method="share_intent", live_calibrated=False)
        save_profile(settings.selector_profile, example)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    job_path = settings.data_dir / "first-job.json"
    # Never serialize Settings wholesale: that would leak keys and persist LIVE.
    nonsecret = {key: str(getattr(settings, key)) for key in LOCAL_FIELDS}
    config_path.write_text(json.dumps(nonsecret, indent=2), encoding="utf-8")
    credentials(settings, secret_input)
    if not caption and settings.r2_access_key_id and settings.r2_secret_access_key:
        try:
            job.caption = R2(settings).description(job)
            output("Описание скачано из R2, SHA256 проверен. Проверьте его перед DRY_RUN:")
            output(job.caption)
        except Exception:
            output(
                "Описание из R2 не загружено. Введите вручную; пустое описание не подставляется автоматически."
            )
            job.caption = ask("Описание и хештеги: ").strip()
            if not job.caption:
                raise Stop("CAPTION_REQUIRED")
    job = Job.model_validate(job.model_dump())
    job_path.write_text(job.model_dump_json(indent=2), encoding="utf-8")
    with device_session(settings):
        report = diagnose(settings, job, download=True)
    print_report(report, output)
    output(f"Готово: {config_path}, {job_path}. LIVE не включён; секреты в файлы не записаны.")
    if (
        report["ok"]
        and ask("Запустить сейчас интерактивный DRY_RUN? Введите DRY_RUN: ").strip() == "DRY_RUN"
    ):
        execute_dry(settings, job, interactive=True, ask=ask, output=output)
    else:
        output(f"Следующий шаг: python -m android_publisher dry-run --job {job_path} --interactive")
    return settings


def execute_dry(
    settings, job, *, interactive=False, ask=input, output=print, android=None, storage=None
):
    if job.execution_mode != Execution.DRY_RUN:
        raise Stop("DRY_RUN_ONLY")
    # New explicit run; never reuse the example's old schedule/key.
    job = job.model_copy(
        update={"scheduled_at": datetime.now(UTC), "idempotency_key": "dry-" + uuid4().hex}
    )
    android = android or (
        GuidedAndroid(settings, ask=ask, output=output) if interactive else Android(settings)
    )
    with device_session(settings):
        store = Store(settings.data_dir / "publisher.sqlite3")
        store.recover(settings.max_attempts)
        if store.paused():
            raise Stop("DEVICE_PAUSED")
        row = store.create(job)
        Worker(store, android, storage or R2(settings), settings).tick(row["id"])
        row = store.get(row["id"])
    output("Задание " + row["id"] + ": " + row["status"])
    output("Скриншоты: " + str(settings.data_dir / "jobs" / row["id"]))
    if row["error"]:
        output(guidance(row["error"]))
    elif row["status"] == "DRY_RUN_READY":
        output(
            "Остановлено ПЕРЕД финальной кнопкой. LIVE не включался. Проверьте ролик, аккаунт, описание и режим на телефоне."
        )
    return row


def confirm_live(row, job, prepared, directory, dry_result, *, ask=input, output=print):
    for field in ("download_sha256", "profile_sha256", "device_serial", "app_version"):
        if not dry_result.get(field) or prepared.get(field) != dry_result[field]:
            raise Stop("DRY_RUN_CHANGED")
    output("\nПЕРЕД РЕАЛЬНОЙ ПУБЛИКАЦИЕЙ. Проверьте экран и screenshot ready-before-final.")
    output(f"Аккаунт: @{job.account_id}; режим: {job.publication_mode}; video_id: {job.video_id}")
    output("SHA256: " + prepared["download_sha256"])
    output("Описание:\n" + job.caption)
    output("Артефакты: " + str(directory))
    phrase = f"PUBLISH @{job.account_id} {job.publication_mode}"
    if ask("Для ОДНОГО финального нажатия введите точно: " + phrase + "\n> ").strip() != phrase:
        raise Stop("OWNER_CANCELLED")
    # Audit without secrets; Store.barrier still happens afterwards.
    (directory / "owner-confirmation.json").write_text(
        json.dumps(
            {
                "confirmed_at": datetime.now(UTC).isoformat(),
                "account_id": job.account_id,
                "publication_mode": job.publication_mode,
                "sha256": prepared["download_sha256"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def execute_live(settings, dry_id, *, learn_success=False, ask=input, output=print):
    if not settings.allow_live:
        raise Stop("LIVE_DISABLED")
    if settings.android_backend != "uiautomator2":
        raise Stop("LIVE_DISABLED")
    with device_session(settings):
        store = Store(settings.data_dir / "publisher.sqlite3")
        store.recover(settings.max_attempts)
        if store.paused():
            raise Stop("DEVICE_PAUSED")
        dry = store.get(dry_id)
        if not dry or dry["status"] != "DRY_RUN_READY" or dry["job"]["execution_mode"] != "DRY_RUN":
            raise Stop("DRY_RUN_REQUIRED")
        if datetime.now(UTC) - datetime.fromisoformat(dry["updated"]) > timedelta(minutes=30):
            raise Stop("DRY_RUN_EXPIRED")
        if learn_success and dry["job"]["publication_mode"] != "NORMAL_REEL":
            raise Stop("LEARNING_REQUIRES_NORMAL_REEL")
        result = dry["result"] or {}
        if (
            result.get("device_serial") != settings.device_serial
            or result.get("profile_sha256")
            != hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
        ):
            raise Stop("DRY_RUN_CHANGED")
        job = Job.model_validate(
            {
                **dry["job"],
                "execution_mode": "LIVE",
                "scheduled_at": datetime.now(UTC),
                "owner_confirmation_required": True,
                "idempotency_key": "owner-live:" + dry_id,
            }
        )
        android = GuidedAndroid(settings, calibrate=False, ask=ask, output=output)
        android.owner_session = True  # This single pilot still requires exact owner confirmation.
        android.learn_success = learn_success

        def confirm(row, job, prepared, directory):
            confirm_live(row, job, prepared, directory, result, ask=ask, output=output)

        row = store.create(job)
        Worker(store, android, R2(settings), settings, before_submit=confirm).tick(row["id"])
        row = store.get(row["id"])
        if learn_success and row["status"] == "PUBLISHED":
            from .calibration import save_profile
            from .success import explicit_success

            evidence = (row["result"] or {}).get("success_evidence") or {}
            if evidence.get("kind") in {"selector", "toast"} and explicit_success(
                evidence.get("text")
            ):
                # Only add the actually observed acknowledgement; do not touch submission controls.
                if (
                    hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
                    != result["profile_sha256"]
                ):
                    raise Stop("DRY_RUN_CHANGED")
                profile = android.profile
                profile["success_evidence"] = evidence
                if evidence["kind"] == "selector":
                    profile.setdefault("selectors", {})["publication_success"] = [
                        evidence["selector"]
                    ]
                profile["live_calibrated"] = True
                save_profile(settings.selector_profile, profile)
                (settings.data_dir / "daily-authorization.json").unlink(missing_ok=True)
                verified = row["result"]
                verified["tested_profile_sha256"] = result["profile_sha256"]
                verified["profile_sha256"] = hashlib.sha256(
                    settings.selector_profile.read_bytes()
                ).hexdigest()
                store.finish(row["id"], "PUBLISHED", result=verified, artifacts=row["artifacts"])
                row = store.get(row["id"])
                output(
                    "Явное сообщение Instagram об успехе сохранено. Ежедневный LIVE ещё не включён."
                )
    output(json.dumps(row, ensure_ascii=False, indent=2))
    return row


def calibrate(settings, step, *, ask=input, output=print):
    with device_session(settings):
        android = Android(settings)
        if not android.health()["available"]:
            raise Stop("DEVICE_UNAVAILABLE")
        import uiautomator2 as u2

        android.d = u2.connect_usb(settings.device_serial)
        directory = settings.data_dir / "calibration" / uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        android.set_artifact_dir(directory)
        Calibrator(android, ask, output).capture(step, optional=step == "trial_toggle")
        output("Селектор сохранён. Ни один элемент не нажат. Скриншоты: " + str(directory))

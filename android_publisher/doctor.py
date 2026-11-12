"""Read-only diagnostics. No test uploads, no publication, no secret dumps."""

import importlib.metadata
import platform
import subprocess
import sys
from datetime import UTC, datetime

from .guidance import guidance
from .models import Retryable, Stop
from .storage import R2

PACKAGES = ("fastapi", "uvicorn", "pydantic-settings", "boto3", "uiautomator2", "portalocker")


def adb_devices(adb_path):
    try:
        output = subprocess.run(
            [adb_path, "devices", "-l"], capture_output=True, text=True, check=True, timeout=15
        ).stdout
    except (OSError, subprocess.SubprocessError):
        raise Stop("ADB_UNAVAILABLE") from None
    return [
        {"serial": fields[0], "state": fields[1]}
        for line in output.splitlines()[1:]
        if len(fields := line.split()) >= 2 and not line.startswith("*")
    ]


def safe_code(exc):
    if isinstance(exc, Stop):
        return exc.code
    if isinstance(exc, Retryable):
        return "TEMPORARY_FAILURE"
    code = getattr(exc, "response", {}).get("Error", {}).get("Code")
    return (
        "R2_OBJECT_UNAVAILABLE"
        if code
        in {
            "AccessDenied",
            "NoSuchKey",
            "NoSuchBucket",
            "InvalidAccessKeyId",
            "403",
            "404",
            "SignatureDoesNotMatch",
        }
        else "DIAGNOSTIC_FAILED"
    )


def diagnose(settings, job=None, *, download=False, android=None, storage=None):
    checks = []

    def add(name, ok, detail, code=None):
        checks.append(
            {"check": name, "ok": ok, "detail": detail, "fix": guidance(code) if code else None}
        )

    add("python", sys.version_info >= (3, 11), platform.python_version())
    for package in PACKAGES:
        try:
            add("dependency:" + package, True, importlib.metadata.version(package))
        except importlib.metadata.PackageNotFoundError:
            add("dependency:" + package, False, "python -m pip install -e .")
    try:
        devices = adb_devices(settings.adb_path)
        ready = any(
            d["serial"] == settings.device_serial and d["state"] == "device" for d in devices
        )
        add(
            "adb",
            ready,
            {"devices": devices, "selected": settings.device_serial},
            None if ready else "DEVICE_UNAVAILABLE",
        )
        if ready:

            def shell(*args):
                return subprocess.run(
                    [settings.adb_path, "-s", settings.device_serial, "shell", *args],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=15,
                ).stdout

            installed = "package:" in shell("pm", "path", "com.instagram.android")
            add(
                "instagram",
                installed,
                "installed" if installed else "Установите Instagram и войдите вручную",
            )
            if installed:
                import uiautomator2 as u2

                device = u2.connect_usb(settings.device_serial) if android is None else android.d
                info = device.info
                add(
                    "uiautomator2",
                    True,
                    {
                        "screen_on": info.get("screenOn"),
                        "instagram_version": device.app_info("com.instagram.android").get(
                            "versionName"
                        ),
                    },
                )
                add(
                    "screen",
                    info.get("screenOn") is True,
                    "Экран включён; убедитесь, что телефон разблокирован"
                    if info.get("screenOn")
                    else "Включите и разблокируйте экран Android",
                )
                # Read-only: doctor does not launch or navigate Instagram.
                device.dump_hierarchy()
    except Exception as exc:
        code = safe_code(exc)
        add("android_diagnostics", False, code, code)
    try:
        profile_exists = settings.selector_profile.is_file()
        add("selector_profile", profile_exists, str(settings.selector_profile))
        if job is None:
            configured = all(
                (settings.r2_endpoint_url, settings.r2_access_key_id, settings.r2_secret_access_key)
            )
            add(
                "r2",
                False,
                "Credentials заданы, но для проверки GetObject нужен --job"
                if configured
                else "R2_NOT_CONFIGURED",
                None if configured else "R2_NOT_CONFIGURED",
            )
        else:
            r2 = storage or R2(settings)
            directory = (
                settings.data_dir / "doctor" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
            )
            directory.mkdir(parents=True, exist_ok=True)
            if download:
                r2.download(job, directory)
                add(
                    "r2_download_sha256_mime",
                    True,
                    {"directory": str(directory), "object_key": job.r2_object_key},
                )
            else:
                _, receipt = r2.read_manifest(job)
                add("r2_manifest", True, receipt)
                add(
                    "r2_video",
                    False,
                    "Не проверен: добавьте --download для проверки MP4/SHA256/MIME",
                )
    except Exception as exc:
        code = safe_code(exc)
        add("r2", False, code, code)
    return {
        "ok": all(check["ok"] for check in checks),
        "live_enabled": settings.allow_live,
        "checks": checks,
    }

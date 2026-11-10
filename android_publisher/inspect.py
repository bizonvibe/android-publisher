"""Capture the current UI for manual selector calibration, without clicking anything."""

from datetime import UTC, datetime

from .android import Android
from .config import Settings


def main():
    import uiautomator2 as u2

    settings = Settings()
    android = Android(settings)
    if not android.health()["available"]:
        raise SystemExit("Device unavailable. Check PUBLISHER_DEVICE_SERIAL and adb devices -l")
    android.d = u2.connect_usb(settings.device_serial)
    directory = settings.data_dir / "inspection" / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory.mkdir(parents=True, exist_ok=True)
    for artifact in android.evidence(directory):
        print(artifact)


if __name__ == "__main__":
    main()

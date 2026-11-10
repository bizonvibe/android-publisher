from types import SimpleNamespace

from android_publisher.doctor import diagnose
from android_publisher.models import Stop


def test_doctor_reports_missing_adb_and_r2_without_secret_leaks(settings, job, monkeypatch):
    def missing(_):
        raise Stop("ADB_UNAVAILABLE")

    monkeypatch.setattr("android_publisher.doctor.adb_devices", missing)
    report = diagnose(settings, job, download=True)
    assert not report["ok"]
    assert any(item["detail"] == "ADB_UNAVAILABLE" for item in report["checks"])
    assert any(item["detail"] == "R2_NOT_CONFIGURED" for item in report["checks"])


def test_doctor_does_not_leak_raw_errors(settings, job, monkeypatch):
    monkeypatch.setattr("android_publisher.doctor.adb_devices", lambda _: [])

    class Storage:
        def download(self, *args):
            raise RuntimeError("my-private-key-is-secret")

    report = diagnose(settings, job, download=True, storage=Storage())
    assert "my-private-key" not in str(report)
    assert "DIAGNOSTIC_FAILED" in str(report)


def test_doctor_success_never_launches_instagram_or_uploads(settings, storage, job, monkeypatch):
    settings.device_serial = "USB"
    monkeypatch.setattr(
        "android_publisher.doctor.adb_devices", lambda _: [{"serial": "USB", "state": "device"}]
    )
    calls = []

    def subprocess_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(stdout="package:/data/app/instagram.apk")

    monkeypatch.setattr("android_publisher.doctor.subprocess.run", subprocess_run)
    device = SimpleNamespace(
        info={"screenOn": True},
        app_info=lambda _: {"versionName": "123"},
        dump_hierarchy=lambda: "<hierarchy/>",
    )
    report = diagnose(
        settings, job, download=True, android=SimpleNamespace(d=device), storage=storage
    )
    assert report["ok"]
    assert storage.downloads == 1
    assert all("start" not in call and "tap" not in call for call in calls)

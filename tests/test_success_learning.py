import hashlib
import json
from types import SimpleNamespace

import pytest

from android_publisher.android import Android, MockAndroid
from android_publisher.local import execute_live
from android_publisher.models import Mode
from android_publisher.store import Store
from android_publisher.success import configured, explicit_success, from_hierarchy


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Ваше видео Reels опубликовано.", True),
        ("Your reel has been shared", True),
        ("  Reel shared! ", True),
        ("Готово", False),
        ("Загрузка видео Reels", False),
        ("Ваше видео Reels будет опубликовано", False),
        ("Поделиться", False),
        (None, False),
    ],
)
def test_only_explicit_publication_acknowledgement(text, expected):
    assert explicit_success(text) is expected


def test_actual_node_is_learned_only_for_instagram():
    xml = '<hierarchy><node package="com.instagram.android" resource-id="app:id/success" text="Ваше видео Reels опубликовано." class="android.widget.TextView"/></hierarchy>'
    assert from_hierarchy(xml)["selector"] == {"resourceId": "app:id/success"}
    assert from_hierarchy(xml.replace("com.instagram.android", "another.app")) is None
    assert from_hierarchy('<hierarchy><node text="Готово"/></hierarchy>') is None


class Toast:
    def __init__(self):
        self.message = "Your reel has been shared"
        self.resets = 0

    def reset(self):
        self.resets += 1
        self.message = None

    def get_message(self, **kwargs):
        return self.message


def observer(settings, learning=False):
    phone = Android(settings)
    phone.learn_success = learning
    phone.profile = {
        "selectors": {},
        "success_evidence": {"kind": "toast", "text": "Your reel has been shared"},
    }
    phone.d = SimpleNamespace(toast=Toast(), dump_hierarchy=lambda: "<hierarchy/>")
    phone.selector = lambda *args, **kwargs: None
    phone.guard = lambda: None
    return phone


def test_cached_toast_is_cleared_before_any_submission(settings):
    phone = observer(settings)
    phone.start_success_probe()
    assert phone.d.toast.resets == 2
    assert not phone.success_seen()
    phone.d.toast.message = "Your reel has been shared"
    assert phone.success_seen()
    assert phone.observed_success == {"kind": "toast", "text": "Your reel has been shared"}


def test_future_automation_requires_exact_learned_message(settings):
    phone = observer(settings)
    phone.start_success_probe()
    phone.d.toast.message = "Reel shared!"
    assert not phone.success_seen()
    phone.d.toast.message = "Готово"
    assert not phone.success_seen()
    assert not configured({"success_evidence": {"kind": "toast", "text": "Готово"}})
    assert configured(phone.profile)


@pytest.mark.parametrize("published", [True, False])
def test_successful_pilot_saves_only_observed_ack_without_enabling_daily(
    settings, storage, job, monkeypatch, published
):
    settings.allow_live = True
    settings.device_serial = "USB"
    settings.selector_profile = settings.data_dir / "profile.json"
    settings.selector_profile.write_text('{"live_calibrated":false,"selectors":{}}')
    original = hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
    job.publication_mode = Mode.NORMAL_REEL
    sha = hashlib.sha256(b"mock-mp4").hexdigest()
    store = Store(settings.data_dir / "publisher.sqlite3")
    source = store.create(job)
    store.finish(
        source["id"],
        "DRY_RUN_READY",
        result={
            "profile_sha256": original,
            "device_serial": "USB",
            "app_version": "454",
            "download_sha256": sha,
        },
    )

    class Phone(MockAndroid):
        def __init__(self, settings, **kwargs):
            super().__init__()
            self.profile = json.loads(settings.selector_profile.read_text())

        def prepare(self, *args):
            result = super().prepare(*args)
            result.update(profile_sha256=original, device_serial="USB", app_version="454")
            return result

        def submit(self, prepared):
            if not published:
                from android_publisher.models import Stop

                raise Stop("UNVERIFIED_RESULT")
            result = super().submit(prepared)
            result.update(
                verification="ui-success-marker",
                success_evidence={"kind": "toast", "text": "Your reel has been shared"},
            )
            return result

    monkeypatch.setattr("android_publisher.local.GuidedAndroid", Phone)
    monkeypatch.setattr("android_publisher.local.R2", lambda _: storage)
    result = execute_live(
        settings,
        source["id"],
        learn_success=True,
        ask=lambda _: "PUBLISH @test_account NORMAL_REEL",
        output=lambda _: None,
    )
    if not published:
        assert result["status"] == "UNKNOWN_RESULT"
        assert hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest() == original
        assert store.paused()
        return
    assert result["status"] == "PUBLISHED"
    profile = json.loads(settings.selector_profile.read_text())
    assert configured(profile)
    assert profile["live_calibrated"] is True
    assert result["result"]["tested_profile_sha256"] == original
    assert (
        result["result"]["profile_sha256"]
        == hashlib.sha256(settings.selector_profile.read_bytes()).hexdigest()
    )
    assert not (settings.data_dir / "daily-authorization.json").exists()

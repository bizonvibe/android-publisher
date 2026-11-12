import json
from types import SimpleNamespace

import pytest

from android_publisher.android import Android
from android_publisher.calibration import Calibrator, candidates, literal
from android_publisher.models import Stop

XML = """<hierarchy>
<node resource-id="app:id/trial_row" text="Trial" class="Row">
  <node resource-id="app:id/switch" text="" class="android.widget.Switch" checkable="true" checked="true" bounds="[0,0][40,40]"/>
</node>
<node resource-id="app:id/auto_row" text="Automatically share" class="Row">
  <node resource-id="app:id/switch" text="" class="android.widget.Switch" checkable="true" checked="false" bounds="[0,50][40,90]"/>
</node>
<node resource-id="app:id/share" text="Share" class="Button" bounds="[50,50][80,80]"/>
</hierarchy>"""


def test_stable_ancestor_xpath_for_identical_switches():
    switches = [item for item in candidates(XML) if item["checkable"]]
    assert len(switches) == 2
    assert switches[0]["selector"] == {
        "xpath": "//*[@resource-id='app:id/trial_row']//*[name()='android.widget.Switch' and @checkable='true']"
    }
    assert "bounds" not in json.dumps(switches[0]["selector"])
    assert "[1]" not in json.dumps(switches[0]["selector"])


def test_xpath_literal_handles_quotes():
    assert literal("normal") == "'normal'"
    assert literal("it's") == '"it\'s"'
    assert literal("both'\"").startswith("concat(")


def fake_android(settings):
    if not settings.selector_profile.exists():
        settings.selector_profile.write_text(
            '{"selectors":{},"blockers":["captcha"]}', encoding="utf-8"
        )
    android = Android(settings)
    android.profile = {"selectors": {}, "blockers": ["captcha"]}
    android.guard = lambda: None
    android.checkpoint = lambda _: None
    android.d = SimpleNamespace(
        dump_hierarchy=lambda: XML, app_info=lambda _: {"versionName": "123"}
    )
    return android


def test_capture_saves_selector_but_never_clicks(settings, monkeypatch):
    settings.selector_profile = settings.data_dir / "profile.json"
    android = fake_android(settings)
    node = SimpleNamespace(
        exists=True, count=1, click=lambda: pytest.fail("capture must never click")
    )
    monkeypatch.setattr("android_publisher.calibration.resolve", lambda *a: node)
    answers = iter(["5", "SAVE"])
    selected = Calibrator(android, ask=lambda _: next(answers), output=lambda _: None).capture(
        "publish"
    )
    assert selected is node
    profile = json.loads(settings.selector_profile.read_text())
    assert profile["selectors"]["publish"] == [{"resourceId": "app:id/share"}]
    assert profile["live_calibrated"] is False
    assert profile["calibration"]["app_version"] == "123"


def test_optional_trial_absence_saved_without_enabling_live(settings):
    settings.selector_profile = settings.data_dir / "profile.json"
    android = fake_android(settings)
    assert (
        Calibrator(android, ask=lambda _: "absent", output=lambda _: None).capture(
            "trial_toggle", optional=True
        )
        is None
    )
    assert json.loads(settings.selector_profile.read_text())["unavailable"] == ["trial_toggle"]


def test_final_button_cannot_be_skipped(settings):
    android = fake_android(settings)
    answers = iter(["skip", "q"])
    with pytest.raises(Stop, match="OWNER_CANCELLED"):
        Calibrator(android, ask=lambda _: next(answers), output=lambda _: None).capture("publish")
    assert "skipped_steps" not in android.profile


def test_preparatory_click_refuses_share_even_if_miscalibrated(settings):
    android = fake_android(settings)
    node = SimpleNamespace(
        info={"text": "Share"}, click=lambda: pytest.fail("must not click Share")
    )
    android.selector = lambda _: node
    with pytest.raises(Stop, match="FINAL_CONTROL_AT_PREPARATION"):
        android.click("editor_next")


def test_exact_mediastore_uri_is_used_without_publish(settings, monkeypatch):
    android = fake_android(settings)
    calls = []

    def adb(*args):
        calls.append(args)
        return "Row: 0 _id=42" if "query" in args else ""

    android.adb = adb
    monkeypatch.setattr("android_publisher.android.time.sleep", lambda _: None)
    android.share_video("/sdcard/Movies/Publisher/exact-uuid.mp4")
    assert "exact-uuid.mp4" in calls[0][-1]
    assert "content://media/external/video/media/42" in calls[1]
    assert "android.intent.action.SEND" in calls[1]
    assert "com.instagram.android" in calls[1]
    assert not any("tap" in call for call in calls)


def test_ambiguous_mediastore_uri_is_not_imported(settings):
    android = fake_android(settings)
    android.adb = lambda *a: "Row: 0 _id=42\nRow: 1 _id=43"
    with pytest.raises(Stop, match="MEDIASTORE_NOT_FOUND"):
        android.share_video("/sdcard/Movies/Publisher/exact-uuid.mp4")


def test_generated_xpath_matches_uiautomator2_actual_page_source():
    from uiautomator2.xpath import PageSource

    switches = [item for item in candidates(XML) if item["checkable"]]
    page = PageSource(XML)
    for switch in switches:
        elements = page.find_elements(switch["selector"]["xpath"])
        assert len(elements) == 1
        assert elements[0].info["checked"] == switch["checked"]


def test_phone_checksum_checked_before_import(settings, tmp_path):
    import hashlib

    android = fake_android(settings)
    video = tmp_path / "video.mp4"
    video.write_bytes(b"verified")
    digest = hashlib.sha256(b"verified").hexdigest()
    android.adb = lambda *args: digest + "  /sdcard/Movies/Publisher/exact.mp4"
    android.verify_remote(video, "/sdcard/Movies/Publisher/exact.mp4")
    android.adb = lambda *args: "0" * 64 + "  wrong"
    with pytest.raises(Stop, match="DEVICE_CHECKSUM_MISMATCH"):
        android.verify_remote(video, "/sdcard/Movies/Publisher/exact.mp4")

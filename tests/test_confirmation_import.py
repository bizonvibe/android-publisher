import json
import xml.etree.ElementTree as ET

import pytest

from android_publisher.confirmation_import import (
    SHARE_ID,
    TITLE_ID,
    extract_confirmation,
    import_confirmation,
)
from android_publisher.models import Stop


def snapshot():
    root = ET.Element("hierarchy")
    ET.SubElement(
        root,
        "node",
        {
            "resource-id": TITLE_ID,
            "text": "Обновление оригинального аудио",
            "package": "com.instagram.android",
        },
    )
    ET.SubElement(
        root,
        "node",
        {
            "resource-id": SHARE_ID,
            "content-desc": "Поделиться",
            "class": "android.widget.Button",
            "package": "com.instagram.android",
            "clickable": "true",
            "enabled": "true",
        },
    )
    ET.SubElement(
        root,
        "node",
        {
            "resource-id": "com.instagram.android:id/clips_original_audio_nux_sheet_turn_off_and_share_button",
            "content-desc": "Выключить и поделиться",
            "clickable": "true",
        },
    )
    return ET.tostring(root, encoding="utf-8")


def test_extracts_exact_resource_ids_and_never_turn_off_button():
    result = extract_confirmation(snapshot())
    assert result["publish"] == [{"resourceId": SHARE_ID}]
    assert result["publication_confirmation"] == [{"resourceId": TITLE_ID}]
    assert "turn_off" not in json.dumps(result)


def test_import_from_existing_evidence_preserves_next_and_disables_live(settings):
    settings.selector_profile = settings.data_dir / "profile.json"
    settings.selector_profile.write_text(
        json.dumps(
            {
                "submission_flow": "next_then_confirmation",
                "live_calibrated": True,
                "selectors": {"publish_next": [{"resourceId": "next"}]},
            }
        )
    )
    evidence = settings.data_dir / "jobs" / "old-job"
    evidence.mkdir(parents=True)
    (evidence / "hierarchy.xml").write_bytes(snapshot())
    (settings.data_dir / "daily-authorization.json").write_text('{"enabled":true}')
    result = import_confirmation(settings)
    profile = json.loads(settings.selector_profile.read_text())
    assert profile["selectors"]["publish_next"] == [{"resourceId": "next"}]
    assert profile["selectors"]["publish"] == [{"resourceId": SHARE_ID}]
    assert not profile["live_calibrated"]
    assert not (settings.data_dir / "daily-authorization.json").exists()
    assert result["success_selector_configured"] is False


@pytest.mark.parametrize(
    "raw",
    [b"<broken", b"<hierarchy/>", snapshot().replace(b'clickable="true"', b'clickable="false"')],
)
def test_rejects_invalid_evidence(raw):
    with pytest.raises(Stop, match="INVALID_CONFIRMATION_SNAPSHOT"):
        extract_confirmation(raw)

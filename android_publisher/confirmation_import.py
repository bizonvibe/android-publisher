"""Import known Instagram confirmation controls from saved evidence, without touching Android."""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from .calibration import save_profile
from .locking import device_session
from .models import Stop

TITLE_ID = "com.instagram.android:id/clips_original_audio_nux_sheet_title"
SHARE_ID = "com.instagram.android:id/clips_original_audio_nux_sheet_share_button"


def extract_confirmation(raw):
    if len(raw) > 2_097_152:
        raise Stop("INVALID_CONFIRMATION_SNAPSHOT")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise Stop("INVALID_CONFIRMATION_SNAPSHOT") from None
    nodes = list(root.iter("node"))
    title = [node for node in nodes if node.get("resource-id") == TITLE_ID]
    share = [node for node in nodes if node.get("resource-id") == SHARE_ID]
    if len(title) != 1 or len(share) != 1:
        raise Stop("INVALID_CONFIRMATION_SNAPSHOT")
    if (
        title[0].get("package") != "com.instagram.android"
        or title[0].get("text") != "Обновление оригинального аудио"
        or share[0].get("package") != "com.instagram.android"
        or share[0].get("class") != "android.widget.Button"
        or share[0].get("content-desc") != "Поделиться"
        or share[0].get("clickable") != "true"
        or share[0].get("enabled") != "true"
    ):
        raise Stop("INVALID_CONFIRMATION_SNAPSHOT")
    return {
        "publication_confirmation": [{"resourceId": TITLE_ID}],
        "publish": [{"resourceId": SHARE_ID}],
    }


def import_confirmation(settings, snapshot=None):
    with device_session(settings):
        profile = json.loads(settings.selector_profile.read_text(encoding="utf-8"))
        if profile.get("submission_flow") != "next_then_confirmation":
            raise Stop("DAILY_PROFILE_INCOMPLETE")
        paths = (
            [Path(snapshot)]
            if snapshot
            else sorted(
                (settings.data_dir / "jobs").rglob("hierarchy.xml"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
        )
        chosen = None
        for path in paths:
            try:
                with path.open("rb") as stream:
                    raw = stream.read(2_097_153)
                selectors = extract_confirmation(raw)
            except (OSError, Stop):
                if snapshot:
                    raise Stop("INVALID_CONFIRMATION_SNAPSHOT") from None
                continue
            chosen = path
            break
        if chosen is None:
            raise Stop("CONFIRMATION_SNAPSHOT_NOT_FOUND")
        profile.setdefault("selectors", {}).update(selectors)
        profile["live_calibrated"] = False
        profile["confirmation_snapshot"] = {
            "path": str(chosen),
            "sha256": hashlib.sha256(raw).hexdigest(),
        }
        save_profile(settings.selector_profile, profile)
        (settings.data_dir / "daily-authorization.json").unlink(missing_ok=True)
        return {
            "source": str(chosen),
            "saved": list(selectors),
            "live_enabled": False,
            "success_selector_configured": bool(profile["selectors"].get("publication_success")),
        }

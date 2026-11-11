import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from .models import Mode, Retryable, Stop
from .selectors import resolve

PACKAGE = "com.instagram.android"


class Android:
    """Selectors are a versioned, locally calibrated UI contract; ambiguity stops execution."""

    def __init__(self, settings):
        self.settings = settings
        self.d = None
        self.profile = json.loads(settings.selector_profile.read_text(encoding="utf-8"))
        self.trace_dir = None
        self.last_step = "initialization"
        self.trace_counter = 0

    def adb(self, *args):
        try:
            return subprocess.run(
                [self.settings.adb_path, "-s", self.settings.device_serial, *args],
                check=True,
                capture_output=True,
                text=True,
                timeout=90,
            ).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            raise Retryable("ADB_UNAVAILABLE") from None

    def health(self):
        if not self.settings.device_serial:
            return {"available": False, "reason": "DEVICE_SERIAL_REQUIRED"}
        try:
            return {"available": self.adb("get-state") == "device"}
        except Retryable:
            return {"available": False, "reason": "ADB_UNAVAILABLE"}

    def connect(self):
        if not self.health()["available"]:
            raise Retryable("DEVICE_UNAVAILABLE")
        import uiautomator2 as u2

        self.d = u2.connect_usb(self.settings.device_serial)
        self.d.app_start(PACKAGE, stop=True)
        time.sleep(2)
        self.guard()

    def guard(self):
        xml = self.d.dump_hierarchy().lower()
        blockers = self.profile.get("blockers", [])
        if not blockers:
            raise Stop("BLOCKER_PROFILE_REQUIRED")
        if any(word.lower() in xml for word in blockers):
            raise Stop("HUMAN_INTERVENTION_REQUIRED")
        if self.d.app_current().get("package") != PACKAGE:
            raise Stop("UNEXPECTED_APPLICATION")

    def selector(self, name, required=True):
        self.guard()
        alternatives = self.profile.get("selectors", {}).get(name, [])
        matches = []
        for selector in alternatives:
            try:
                obj = resolve(self.d, selector)
            except ValueError:
                raise Stop("INVALID_SELECTOR_PROFILE") from None
            if obj.exists:
                if obj.count != 1:
                    raise Stop("AMBIGUOUS_UI:" + name)
                matches.append(obj)
        if len(matches) > 1:
            raise Stop("AMBIGUOUS_UI:" + name)
        if not matches:
            if required:
                raise Stop("UI_NOT_FOUND:" + name)
            return None
        return matches[0]

    def click(self, name):
        self.checkpoint("before-" + name)
        if name in self.profile.get("skipped_steps", []) and name in {
            "reel",
            "editor_next",
            "gallery_next",
            "create",
            "gallery",
        }:
            return
        obj = self.selector(name)
        if obj is None and name in self.profile.get("skipped_steps", []):
            return
        if obj is None:
            raise Stop("UI_NOT_FOUND:" + name)
        self.tap_once(obj)
        time.sleep(0.7)
        self.guard()
        self.checkpoint("after-" + name)

    def wait(self, name, seconds=15):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            obj = self.selector(name, required=False)
            if obj is not None:
                return obj
            time.sleep(0.5)
        raise Stop("UI_NOT_FOUND:" + name)

    def keyboard_visible(self):
        # Query Android's IME state, not keyboard labels or a fixed screen area.
        output = self.adb("shell", "dumpsys", "input_method")
        for field in ("mInputShown", "isInputViewShown", "mIsInputViewShown"):
            values = re.findall(r"\b" + field + r"=(true|false)\b", output)
            if values:
                if len(set(values)) != 1:
                    raise Stop("KEYBOARD_STATE_UNKNOWN")
                return values[0] == "true"
        values = re.findall(r"\bmImeWindowVis=(0x[0-9a-fA-F]+|[0-9]+)\b", output)
        if len(values) == 1:
            return bool(int(values[0], 16 if values[0].startswith("0x") else 10) & 2)
        raise Stop("KEYBOARD_STATE_UNKNOWN")

    def dismiss_keyboard(self, timeout=3):
        self.guard()
        if not self.keyboard_visible():
            return
        self.checkpoint("before-hide-keyboard")
        # Exactly one Back, only after Android confirms IME visibility.
        # Retrying Back could leave the composer, so timeout means stop.
        self.adb("shell", "input", "keyevent", "KEYCODE_BACK")
        deadline = time.monotonic() + timeout
        while self.keyboard_visible():
            if time.monotonic() >= deadline:
                raise Stop("KEYBOARD_NOT_HIDDEN")
            time.sleep(0.15)
        self.guard()
        self.checkpoint("keyboard-hidden")

    def account(self, expected):
        self.click("profile_tab")
        actual = self.wait("account_username").get_text().strip().lstrip("@")
        if actual.casefold() != expected.casefold():
            raise Stop("ACCOUNT_MISMATCH")

    def toggle(self, name, desired):
        self.checkpoint("toggle-" + name)
        obj = self.selector(name)
        info = obj.info
        if not info.get("checkable") or "checked" not in info:
            raise Stop("UNVERIFIABLE_TOGGLE:" + name)
        if info["checked"] != desired:
            self.tap_once(obj)
            time.sleep(0.5)
        self.guard()
        if self.selector(name).info.get("checked") != desired:
            raise Stop("TOGGLE_FAILED:" + name)

    def prepare(self, job, video, source=None):
        self.connect()
        self.account(job.account_id)
        if (
            job.execution_mode == "LIVE"
            and not getattr(self, "owner_session", False)
            and self.profile.get("live_calibrated") is not True
        ):
            raise Stop("PROFILE_NOT_CALIBRATED_FOR_LIVE")
        if (
            job.execution_mode == "LIVE"
            and not getattr(self, "owner_session", False)
            and not self.profile.get("selectors", {}).get("publication_success")
        ):
            raise Stop("SUCCESS_SELECTOR_REQUIRED")
        if job.publication_mode == Mode.SHARE_TRIAL_TO_EVERYONE:
            url = (source or {}).get("result", {}).get("permalink")
            if not valid_permalink(url):
                raise Stop("SOURCE_PERMALINK_UNAVAILABLE")
            self.adb(
                "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", url, "-p", PACKAGE
            )
            self.wait("existing_trial_marker")
            if (
                self.wait("existing_reel_owner").get_text().strip().lstrip("@").casefold()
                != job.account_id.casefold()
            ):
                raise Stop("ACCOUNT_MISMATCH")
            # A separate menu action opens a confirmation. Never clicks its final button here.
            self.click("trial_options")
            if self.selector("share_everyone_prepare", required=False) is None:
                raise Stop("FEATURE_UNAVAILABLE")
            self.click("share_everyone_prepare")
            self.wait("share_everyone_confirm")
            return {"trial_available": True, "final_action": "share_everyone_confirm"}
        remote = "/sdcard/Movies/Publisher/" + video.parent.name + ".mp4"
        self.adb("shell", "mkdir", "-p", "/sdcard/Movies/Publisher")
        self.adb("push", str(video), remote)
        self.verify_remote(video, remote)
        self.adb(
            "shell",
            "am",
            "broadcast",
            "-a",
            "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
            "-d",
            "file://" + remote,
        )
        if self.profile.get("import_method") == "share_intent":
            self.share_video(remote)
            self.click("reel")
        else:
            self.click("create")
            self.click("reel")
            self.click("gallery")
            # Select exact filename, never newest thumbnail: stale gallery content is unsafe.
            self.guard()
            media = self.d(description=Path(remote).name)
            if not media.wait(timeout=15) or media.count != 1:
                raise Stop("EXACT_MEDIA_NOT_FOUND")
            self.tap_once(media)
            self.click("gallery_next")
        self.click("editor_next")
        self.wait("caption").set_text(job.caption)
        if self.wait("caption").get_text() != job.caption:
            raise Stop("CAPTION_MISMATCH")
        self.checkpoint("caption-entered")
        self.dismiss_keyboard()
        if self.wait("caption").get_text() != job.caption:
            raise Stop("CAPTION_MISMATCH")
        self.guard()
        available = self.selector("trial_toggle", required=False) is not None
        if job.publication_mode == Mode.TRIAL_REEL:
            if not available:
                raise Stop("FEATURE_UNAVAILABLE")
            self.toggle("trial_toggle", True)
            # Disable Instagram's optional automatic sharing, never opt into it.
            self.toggle("automatic_share_toggle", False)
            self.wait("trial_mode_marker")
        else:
            if available:
                self.toggle("trial_toggle", False)
            self.wait("normal_mode_marker")
        self.confirm_composer_account(job.account_id)
        self.wait("publish")
        self.checkpoint("ready-before-final")
        return {"trial_available": available, "final_action": "publish", **self.identity()}

    def tap_final(self, prepared):
        self.last_step = "final-confirmation"
        if self.selector("publication_success", required=False) is not None:
            raise Stop("STALE_SUCCESS_MARKER")
        # Caller must persist its irreversible barrier BEFORE entering this method.
        self.tap_once(self.selector(prepared["final_action"]), final=True)

    def tap_once(self, obj, *, final=False):
        # A repeated navigation RPC might hit Share after Next changed the screen.
        info = obj.info
        label = str(info.get("text", "") or info.get("contentDescription", "")).strip().casefold()
        if not final and label in {
            "share",
            "publish",
            "post",
            "share now",
            "поделиться",
            "опубликовать",
        }:
            raise Stop("FINAL_CONTROL_AT_PREPARATION")
        bounds = info.get("bounds", {})
        if not info.get("enabled") or not all(
            k in bounds for k in ("left", "top", "right", "bottom")
        ):
            raise Stop("INVALID_FINAL_CONTROL" if final else "INVALID_UI_CONTROL")
        if bounds["right"] <= bounds["left"] or bounds["bottom"] <= bounds["top"]:
            raise Stop("INVALID_FINAL_CONTROL" if final else "INVALID_UI_CONTROL")
        self.guard()
        self.adb(
            "shell",
            "input",
            "tap",
            str((bounds["left"] + bounds["right"]) // 2),
            str((bounds["top"] + bounds["bottom"]) // 2),
        )

    def submit(self, prepared):
        self.tap_final(prepared)
        self.wait("publication_success", seconds=60)
        # Toast/feed navigation alone is deliberately insufficient; profile must select
        # an explicit success confirmation from this submission.
        link = self.selector("published_permalink", required=False)
        permalink = link.get_text().strip() if link is not None else None
        return {"verified": True, "permalink": canonical_permalink(permalink)}

    def confirm_composer_account(self, expected):
        if (
            self.wait("composer_account").get_text().strip().lstrip("@").casefold()
            != expected.casefold()
        ):
            raise Stop("ACCOUNT_MISMATCH")

    def identity(self):
        # Binding DRY_RUN to the phone, Instagram version and exact saved profile.
        raw = self.settings.selector_profile.read_bytes()
        version = self.d.app_info(PACKAGE).get("versionName", "")
        return {
            "device_serial": self.settings.device_serial,
            "app_version": version,
            "profile_sha256": hashlib.sha256(raw).hexdigest(),
        }

    def verify_remote(self, video, remote):
        with video.open("rb") as stream:
            expected = hashlib.file_digest(stream, "sha256").hexdigest()
        actual = self.adb("shell", "sha256sum", remote).split()
        if not actual or actual[0].lower() != expected:
            raise Stop("DEVICE_CHECKSUM_MISMATCH")
        self.checkpoint("phone-sha256-verified")

    def share_video(self, remote):
        # Use Android's content URI grant rather than guessing the newest gallery thumbnail.
        filename = Path(remote).name
        deadline = time.monotonic() + 15
        ids = []
        while time.monotonic() < deadline:
            output = self.adb(
                "shell",
                "content",
                "query",
                "--uri",
                "content://media/external/video/media",
                "--projection",
                "_id",
                "--where",
                f"\"_display_name='{filename}'\"",
            )
            ids = re.findall(r"\b_id=(\d+)", output)
            if ids:
                break
            time.sleep(0.5)
        if len(ids) != 1:
            raise Stop("MEDIASTORE_NOT_FOUND")
        uri = "content://media/external/video/media/" + ids[0]
        self.checkpoint("media-import")
        self.adb(
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.SEND",
            "-t",
            "video/mp4",
            "--eu",
            "android.intent.extra.STREAM",
            uri,
            "--grant-read-uri-permission",
            "-p",
            PACKAGE,
        )
        time.sleep(1)
        self.guard()

    def set_artifact_dir(self, directory):
        self.trace_dir = directory
        self.trace_counter = 0

    def checkpoint(self, label):
        self.last_step = label
        if self.trace_dir is not None:
            self.trace_counter += 1
            directory = self.trace_dir / "steps" / f"{self.trace_counter:03d}-{label}"
            directory.mkdir(parents=True, exist_ok=True)
            self.evidence(directory)

    def evidence(self, directory):
        artifacts = []
        if self.d is not None:
            for filename, operation in [
                ("screen.png", lambda p: self.d.screenshot(str(p))),
                (
                    "hierarchy.xml",
                    lambda p: p.write_text(self.d.dump_hierarchy(), encoding="utf-8"),
                ),
            ]:
                try:
                    path = directory / filename
                    operation(path)
                    artifacts.append(str(path))
                except Exception:
                    pass  # Evidence failure must never change submission uncertainty.
        return artifacts


def valid_permalink(url):
    if not isinstance(url, str):
        return False
    parsed = urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.netloc in {"www.instagram.com", "instagram.com"}
        and re.fullmatch(r"/reel/[A-Za-z0-9_-]+/?", parsed.path) is not None
        and not parsed.query
        and not parsed.fragment
    )


def canonical_permalink(url):
    if not isinstance(url, str):
        return None
    parsed = urlparse(url.strip())
    cleaned = urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", "", ""))
    return cleaned if valid_permalink(cleaned) else None


class MockAndroid:
    """CI adapter: no ADB, network or Instagram; failures are injectable."""

    def __init__(self, *, trial=True, account="test_account", failure=None):
        self.trial, self.username, self.failure = trial, account, failure
        self.submissions = 0
        self.preparations = 0

    def health(self):
        return {"available": True, "mock": True}

    def prepare(self, job, video, source=None):
        self.preparations += 1
        if self.failure:
            raise self.failure
        if job.account_id != self.username:
            raise Stop("ACCOUNT_MISMATCH")
        if job.publication_mode != Mode.NORMAL_REEL and not self.trial:
            raise Stop("FEATURE_UNAVAILABLE")
        return {"trial_available": self.trial, "final_action": "mock"}

    def submit(self, prepared):
        self.submissions += 1
        return {"verified": True, "permalink": "https://www.instagram.com/reel/Mock123/"}

    def evidence(self, directory):
        path = directory / "mock-evidence.json"
        path.write_text('{"mock":true}', encoding="utf-8")
        return [str(path)]

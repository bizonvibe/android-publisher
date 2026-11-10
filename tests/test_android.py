import pytest

from android_publisher.android import Android, valid_permalink
from android_publisher.models import Stop


class Node:
    exists = True
    count = 1

    def __init__(self, checked=False):
        self.checked = checked

    @property
    def info(self):
        return {"checkable": True, "checked": self.checked}

    def click(self):
        self.checked = not self.checked


class Device:
    def __init__(self, node=None, xml="<node />"):
        self.node, self.xml = node or Node(), xml

    def __call__(self, **kwargs):
        return self.node

    def dump_hierarchy(self):
        return self.xml

    def app_current(self):
        return {"package": "com.instagram.android"}


def adapter(settings, device):
    a = Android(settings)
    a.d = device
    a.profile = {"blockers": ["captcha"], "selectors": {"switch": [{"text": "Trial"}]}}
    return a


def test_challenge_stops_before_selector(settings):
    a = adapter(settings, Device(xml="<node text='CAPTCHA'/ >"))
    with pytest.raises(Stop, match="HUMAN_INTERVENTION_REQUIRED"):
        a.selector("switch")


def test_ambiguous_selector(settings):
    node = Node()
    node.count = 2
    with pytest.raises(Stop, match="AMBIGUOUS_UI"):
        adapter(settings, Device(node)).selector("switch")


def test_automatic_share_disabled_and_no_second_click(settings):
    node = Node(checked=True)
    a = adapter(settings, Device(node))
    a.toggle("switch", False)
    assert not node.checked
    a.toggle("switch", False)
    assert not node.checked


def test_unknown_screen_stops(settings):
    node = Node()
    node.exists = False
    with pytest.raises(Stop, match="UI_NOT_FOUND"):
        adapter(settings, Device(node)).selector("switch")


@pytest.mark.parametrize(
    "url,valid",
    [
        ("https://www.instagram.com/reel/abc123/", True),
        ("https://instagram.com/reel/abc123", True),
        ("https://evil.example/reel/abc/", False),
        ("https://instagram.com/reel/abc/?x=1", False),
        ("http://instagram.com/reel/abc/", False),
        (None, False),
    ],
)
def test_permalink_validation(url, valid):
    assert valid_permalink(url) == valid


def test_final_tap_uses_selector_bounds_once_and_never_u2_click(settings):
    from types import SimpleNamespace

    class FinalAndroid(Android):
        def __init__(self):
            self.calls = []

        def selector(self, name, required=True):
            if name in {"publication_success", "published_permalink"}:
                return None
            return SimpleNamespace(
                info={"enabled": True, "bounds": {"left": 10, "right": 30, "top": 40, "bottom": 60}}
            )

        def guard(self):
            pass

        def adb(self, *args):
            self.calls.append(args)

        def wait(self, name, seconds=15):
            pass

    a = FinalAndroid()
    assert a.submit({"final_action": "publish"})["verified"]
    assert a.calls == [("shell", "input", "tap", "20", "50")]


def test_final_adb_error_is_not_retried(settings):
    from types import SimpleNamespace

    from android_publisher.models import Retryable

    a = adapter(settings, Device())
    calls = []

    def selector(name, required=True):
        if name == "publication_success":
            return None
        return SimpleNamespace(
            info={"enabled": True, "bounds": {"left": 1, "right": 5, "top": 1, "bottom": 5}}
        )

    def adb(*args):
        calls.append(args)
        raise Retryable("lost adb response")

    a.selector, a.adb = selector, adb
    with pytest.raises(Retryable):
        a.submit({"final_action": "publish"})
    assert len(calls) == 1


def test_owner_copied_link_can_drop_tracking_but_never_accept_other_hosts():
    from android_publisher.android import canonical_permalink

    assert (
        canonical_permalink("https://www.instagram.com/reel/Abc123/?igsh=tracking")
        == "https://www.instagram.com/reel/Abc123/"
    )
    assert canonical_permalink("https://evil.example/reel/Abc123/?igsh=tracking") is None
    assert canonical_permalink("https://instagram.com@evil.example/reel/Abc123/") is None

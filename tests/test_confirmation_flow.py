from android_publisher.android import Android


class Flow(Android):
    def __init__(self, *, direct=False):
        self.taps = []
        self.direct = direct

    def tap_final(self, prepared):
        self.taps.append(prepared["final_action"])

    def checkpoint(self, name):
        pass

    def selector(self, name, required=True):
        if name == "publication_success":
            return object() if self.direct or len(self.taps) == 2 else None
        if name == "publication_confirmation":
            return object()
        return None

    def wait(self, name, seconds=15):
        assert name == "publication_success" and len(self.taps) == 2
        return object()


def test_two_stage_confirmation_is_not_confused_with_next():
    phone = Flow()
    result = phone.submit({"final_action": "publish_next"})
    assert phone.taps == ["publish_next", "publish"]
    assert result["verification"] == "ui-success-marker"


def test_direct_success_never_causes_second_submission():
    phone = Flow(direct=True)
    assert phone.submit({"final_action": "publish_next"})["verified"]
    assert phone.taps == ["publish_next"]


def test_unknown_confirmation_does_not_press_any_share_button(monkeypatch):
    import pytest

    from android_publisher.models import Stop

    phone = Flow()
    phone.selector = lambda name, required=True: object() if name == "publish" else None
    ticks = iter([0, 61])
    monkeypatch.setattr("android_publisher.android.time.monotonic", lambda: next(ticks))
    with pytest.raises(Stop, match="UNVERIFIED_RESULT"):
        phone.submit({"final_action": "publish_next"})
    assert phone.taps == ["publish_next"]

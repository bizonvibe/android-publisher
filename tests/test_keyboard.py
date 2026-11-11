import pytest

from android_publisher.android import Android
from android_publisher.models import Stop


@pytest.mark.parametrize(
    "output,expected",
    [
        ("mInputShown=true", True),
        ("mInputShown=false", False),
        ("mInputShown=false mShowRequested=true", False),
        ("isInputViewShown=true", True),
        ("mIsInputViewShown=false", False),
        ("mImeWindowVis=0x3", True),
        ("mImeWindowVis=0x1", False),
        ("mImeWindowVis=2", True),
    ],
)
def test_android_ime_state(settings, output, expected):
    android = Android(settings)
    android.adb = lambda *args: output
    assert android.keyboard_visible() is expected


@pytest.mark.parametrize(
    "output", ["", "mShowRequested=true", "mInputShown=true mInputShown=false"]
)
def test_unknown_state_does_not_guess(settings, output):
    android = Android(settings)
    android.adb = lambda *args: output
    with pytest.raises(Stop, match="KEYBOARD_STATE_UNKNOWN"):
        android.keyboard_visible()


def session(settings, states):
    android = Android(settings)
    android.guard = lambda: None
    android.checkpoint = lambda _: None
    values = iter(states)
    android.keyboard_visible = lambda: next(values)
    calls = []
    android.adb = lambda *args: calls.append(args)
    return android, calls


def test_closed_keyboard_never_sends_back(settings):
    android, calls = session(settings, [False])
    android.dismiss_keyboard()
    assert calls == []


def test_open_keyboard_sends_exactly_one_back(settings):
    android, calls = session(settings, [True, False])
    android.dismiss_keyboard()
    assert calls == [("shell", "input", "keyevent", "KEYCODE_BACK")]


def test_does_not_retry_back_when_keyboard_stays_open(settings):
    android, calls = session(settings, [True, True])
    with pytest.raises(Stop, match="KEYBOARD_NOT_HIDDEN"):
        android.dismiss_keyboard(timeout=0)
    assert len(calls) == 1


def test_unknown_state_never_sends_back(settings):
    android, calls = session(settings, [])

    def unknown():
        raise Stop("KEYBOARD_STATE_UNKNOWN")

    android.keyboard_visible = unknown
    with pytest.raises(Stop, match="KEYBOARD_STATE_UNKNOWN"):
        android.dismiss_keyboard()
    assert calls == []

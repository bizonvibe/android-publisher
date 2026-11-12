from types import SimpleNamespace

import pytest

from android_publisher.android import Android
from android_publisher.models import Stop


def phone(settings, label="Нет", message="Поделиться этим видео в истории WhatsApp?"):
    instance = Android(settings)
    instance.profile = {
        "selectors": {
            "whatsapp_share_prompt": [{"resourceId": "prompt"}],
            "whatsapp_share_decline": [{"resourceId": "decline"}],
        }
    }
    marker = SimpleNamespace(info={"text": message})
    decline = SimpleNamespace(info={"text": label})
    instance.visible = True
    instance.taps = []
    instance.checkpoint = lambda _: None
    instance.selector = lambda name, required=True: (
        (marker if instance.visible else None) if name == "whatsapp_share_prompt" else decline
    )

    def tap(obj):
        instance.taps.append(obj)
        instance.visible = False

    instance.tap_once = tap
    return instance


def test_declines_only_verified_whatsapp_prompt_once(settings):
    instance = phone(settings)
    assert instance.dismiss_whatsapp_prompt()
    assert len(instance.taps) == 1
    assert not instance.dismiss_whatsapp_prompt()
    assert len(instance.taps) == 1


def test_unknown_dialog_is_never_dismissed(settings):
    instance = phone(settings, message="Сохранить черновик?")
    with pytest.raises(Stop, match="UNEXPECTED_OPTIONAL_DIALOG"):
        instance.dismiss_whatsapp_prompt()
    assert instance.taps == []


def test_never_clicks_share_as_refusal(settings):
    instance = phone(settings, label="Поделиться")
    with pytest.raises(Stop, match="UNEXPECTED_OPTIONAL_DIALOG"):
        instance.dismiss_whatsapp_prompt()
    assert instance.taps == []


def test_unconfigured_handler_is_noop(settings):
    instance = phone(settings)
    instance.profile = {}
    instance.selector = lambda *a, **kw: pytest.fail("must not search for generic No")
    assert not instance.dismiss_whatsapp_prompt()


def test_not_retried_if_refusal_does_not_close_prompt(settings):
    instance = phone(settings)
    instance.tap_once = lambda node: instance.taps.append(node)
    with pytest.raises(Stop, match="WHATSAPP_PROMPT_NOT_DISMISSED"):
        instance.dismiss_whatsapp_prompt(timeout=0)
    assert len(instance.taps) == 1

import xml.etree.ElementTree as ET
from types import SimpleNamespace

from android_publisher.android import Android
from android_publisher.whatsapp import discover_refusal


def xml(*, modal=True, label="Не сейчас", package="com.instagram.android", second_no=False):
    root = ET.Element("hierarchy")
    container = ET.SubElement(
        root,
        "node",
        {
            "resource-id": "com.instagram.android:id/bottom_sheet_content_container"
            if modal
            else "app:id/composer"
        },
    )
    ET.SubElement(
        container,
        "node",
        {
            "resource-id": "app:id/offer",
            "package": package,
            "text": "Поделиться этим видео в статусе WhatsApp?",
            "class": "android.widget.TextView",
        },
    )
    button = {
        "resource-id": "app:id/decline",
        "package": package,
        "content-desc": label,
        "class": "android.widget.Button",
        "clickable": "true",
        "enabled": "true",
    }
    ET.SubElement(container, "node", button)
    if second_no:
        ET.SubElement(container, "node", {**button, "resource-id": "app:id/another_decline"})
    return ET.tostring(root, encoding="unicode")


def test_discovers_only_same_modal_refusal_without_guessing_ids():
    result = discover_refusal(xml())
    assert result["whatsapp_share_prompt"] == [{"resourceId": "app:id/offer"}]
    assert result["whatsapp_share_decline"] == [{"resourceId": "app:id/decline"}]


def test_background_no_and_wrong_provider_or_action_are_rejected():
    assert discover_refusal(xml(modal=False)) is None
    assert discover_refusal(xml(package="com.whatsapp")) is None
    assert discover_refusal(xml(label="Поделиться")) is None
    assert discover_refusal(xml(second_no=True)) is None


def test_refusal_in_another_overlay_is_not_misidentified():
    root = ET.fromstring(xml())
    main = root.find("node")
    button = main[-1]
    main.remove(button)
    another = ET.SubElement(main, "node", {"resource-id": "app:id/alert_dialog_container"})
    another.append(button)
    assert discover_refusal(ET.tostring(root, encoding="unicode")) is None


def test_runtime_discovery_captures_and_refuses_once(settings):
    phone = Android(settings)
    phone.profile = {"selectors": {}}
    phone.decline_unknown_whatsapp = True
    phone.guard = lambda: None
    phone.checkpoint = lambda _: None
    phone.d = SimpleNamespace(dump_hierarchy=lambda: xml())
    phone.visible = True
    phone.taps = []
    marker = SimpleNamespace(info={"text": "Поделиться этим видео в статусе WhatsApp?"})
    refusal = SimpleNamespace(info={"contentDescription": "Не сейчас"})
    phone.selector = lambda name, required=True: (
        (marker if phone.visible else None) if name == "whatsapp_share_prompt" else refusal
    )

    def tap(node):
        phone.taps.append(node)
        phone.visible = False

    phone.tap_once = tap
    assert phone.dismiss_whatsapp_prompt()
    assert phone.taps == [refusal]
    assert phone.profile["selectors"]["whatsapp_share_decline"] == [
        {"resourceId": "app:id/decline"}
    ]
    assert not phone.settings.allow_live

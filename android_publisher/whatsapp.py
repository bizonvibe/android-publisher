"""Identify an explicit Instagram WhatsApp offer and a single refusal in its modal.

No discovery from an arbitrary 'No', a crossposting toggle, or the WhatsApp app itself.
"""

import xml.etree.ElementTree as ET

REFUSALS = {
    "нет",
    "не сейчас",
    "не делиться",
    "нет, спасибо",
    "пропустить",
    "no",
    "not now",
    "no thanks",
    "no, thanks",
    "skip",
}


def offer(text):
    text = text.casefold()
    return "whatsapp" in text and any(
        word in text for word in ("поделиться", "поделитесь", "делиться", "делитесь", "share")
    )


def discover_refusal(xml):
    root = ET.fromstring(xml)
    nodes = list(root.iter("node"))
    parents = {child: parent for parent in root.iter() for child in parent}

    def spec(node):
        rid = node.get("resource-id")
        if rid and sum(other.get("resource-id") == rid for other in nodes) == 1:
            return {"resourceId": rid}
        for attr, key in (("text", "text"), ("content-desc", "description")):
            value = node.get(attr)
            if (
                value
                and sum(
                    other.get(attr) == value and other.get("class") == node.get("class")
                    for other in nodes
                )
                == 1
            ):
                return {key: value, "className": node.get("class", "")}
        return None

    def is_modal(node):
        rid = node.get("resource-id", "").casefold().split("/")[-1]
        return "button" not in rid and (
            rid in {"parentpanel", "dialog", "alert"}
            or (
                any(
                    token in rid
                    for token in ("bottom_sheet", "bottomsheet", "dialog", "popup", "alert")
                )
                and any(group in rid for group in ("container", "content", "root", "panel"))
            )
        )

    def nearest_modal(node):
        node = parents.get(node)
        while node is not None:
            if is_modal(node):
                return node
            node = parents.get(node)
        return None

    results = []
    for marker in nodes:
        if (
            marker.get("package") != "com.instagram.android"
            or marker.get("visible-to-user") == "false"
            or not offer(marker.get("text", "") + " " + marker.get("content-desc", ""))
        ):
            continue
        ancestor = parents.get(marker)
        while ancestor is not None:
            # No must belong to the same nearest modal, never a different overlay.
            if is_modal(ancestor) and nearest_modal(marker) is ancestor:
                buttons = [
                    node
                    for node in ancestor.iter("node")
                    if node.get("package") == "com.instagram.android"
                    and node.get("visible-to-user") != "false"
                    and node.get("clickable") == "true"
                    and node.get("enabled") == "true"
                    and node.get("checkable") != "true"
                    and nearest_modal(node) is ancestor
                    and (node.get("text") or node.get("content-desc", "")).strip().casefold()
                    in REFUSALS
                ]
                if len(buttons) == 1 and spec(marker) and spec(buttons[0]):
                    results.append(
                        {
                            "whatsapp_share_prompt": [spec(marker)],
                            "whatsapp_share_decline": [spec(buttons[0])],
                        }
                    )
                    break
                if len(buttons) > 1:
                    return None
            ancestor = parents.get(ancestor)
    unique = {str(result): result for result in results}
    return next(iter(unique.values())) if len(unique) == 1 else None

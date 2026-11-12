"""Conservative recognition of explicit publication acknowledgements, not generic 'Done'."""

import xml.etree.ElementTree as ET

MESSAGES = {
    "ваше видео reels опубликовано",
    "видео reels опубликовано",
    "видео опубликовано",
    "ролик опубликован",
    "ваш ролик опубликован",
    "вы поделились видео reels",
    "your reel has been shared",
    "your reel was shared",
    "reel shared",
    "reel posted",
    "your reel has been published",
    "your reel is published",
}


def explicit_success(text):
    return isinstance(text, str) and " ".join(text.split()).rstrip(".! ").casefold() in MESSAGES


def configured(profile):
    evidence = profile.get("success_evidence", {})
    return bool(
        profile.get("selectors", {}).get("publication_success")
        or (evidence.get("kind") == "toast" and explicit_success(evidence.get("text")))
    )


def from_hierarchy(xml):
    root = ET.fromstring(xml)
    nodes = list(root.iter("node"))
    for node in nodes:
        if node.get("package") != "com.instagram.android":
            continue
        for attribute, key in (("text", "text"), ("content-desc", "description")):
            message = node.get(attribute, "")
            if not explicit_success(message):
                continue
            rid = node.get("resource-id")
            if rid and sum(other.get("resource-id") == rid for other in nodes) == 1:
                spec = {"resourceId": rid}
            elif sum(other.get(attribute) == message for other in nodes) == 1:
                spec = {key: message}
                if node.get("class"):
                    spec["className"] = node.get("class")
            else:
                continue
            return {"kind": "selector", "text": message, "selector": spec}
    return None

"""Stable accessibility selectors, including switches identified by a labelled ancestor."""

KEYS = {
    "resourceId",
    "text",
    "description",
    "className",
    "textMatches",
    "descriptionMatches",
    "checked",
    "checkable",
}


class XPathNode:
    def __init__(self, selector):
        self.selector = selector

    @property
    def exists(self):
        return self.selector.exists

    @property
    def count(self):
        return len(self.selector.all())

    @property
    def info(self):
        return self.selector.get().info

    def click(self):
        self.selector.click()

    def get_text(self):
        return self.selector.get_text()

    def set_text(self, value):
        self.selector.set_text(value)


def resolve(device, spec):
    if set(spec) == {"xpath"} and isinstance(spec["xpath"], str):
        return XPathNode(device.xpath(spec["xpath"]))
    if spec and all(key in KEYS for key in spec):
        return device(**spec)
    raise ValueError("Invalid selector")

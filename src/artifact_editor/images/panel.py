"""Adapt workshop controls for independently expanded chapter image panels."""
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup


def panel_controls(markup, image_index, image_url, editor_url):
    prefix = f"timeline-prompt-{int(image_index)}-"
    soup = BeautifulSoup(markup, "html.parser")
    for element in soup.find_all(True):
        if element.has_attr("id"):
            element["id"] = prefix + element["id"]
        for attr in ("hx-target", "hx-include", "hx-indicator", "hx-disabled-elt"):
            if element.has_attr(attr):
                element[attr] = re.sub(r"#([\w-]+)", lambda match: "#" + prefix + match[1], element[attr])
        for attr in ("for", "aria-controls", "aria-labelledby", "aria-describedby"):
            if element.has_attr(attr):
                element[attr] = " ".join(prefix + value for value in element[attr].split())
        for attr in ("hx-get", "hx-post", "hx-put", "hx-delete", "hx-patch"):
            if element.has_attr(attr):
                element[attr] = urljoin(image_url, element[attr])
        action = None
        if element.get("id") == prefix + "workflow-run-button":
            action = "generate"
        elif element.get("hx-target") == "#" + prefix + "strip-centerpiece":
            action = "metadata"
        if action:
            element["hx-post"] = editor_url
            element["hx-vals"] = '{"action":"' + action + '"}'
            element["hx-target"] = f"#timeline-image-editor-{int(image_index)}"
            element["hx-swap"] = "innerHTML"
            element["hx-include"] = "closest .chapter-mini-image-editor"
            element["hx-on::before-request"] = ""
            element["hx-on::after-request"] = ""
    return str(soup)

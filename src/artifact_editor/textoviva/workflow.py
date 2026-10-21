"""Resolve animation workflow selections to names accepted by the loader."""

from pathlib import Path


def animation_workflow_name(template):
    name = Path(template).name.removesuffix(".json")
    for interface in ("ui.", "api."):
        if name.startswith(interface):
            name = name.removeprefix(interface)
            break
    return name.removeprefix("i2v.")

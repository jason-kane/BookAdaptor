"""Resolve animation workflow selections to names accepted by the loader."""

from pathlib import Path


def animation_workflow_name(template):
    name = Path(template).name.removesuffix(".json")
    for interface in ("ui.", "api."):
        if name.startswith(interface):
            name = name.removeprefix(interface)
            break
    return name.removeprefix("i2v.")


def image_workflow_name(template):
    """Remove selector prefixes while preserving dots in image model names."""
    name = Path(template).name.removesuffix(".json")
    for prefix in ("ui.", "api."):
        name = name.removeprefix(prefix)
    for mode in ("t2i", "ti2i", "ref2i", "i2v", "flf2v"):
        if name.startswith(mode + "."):
            return name[len(mode) + 1:]
    return name

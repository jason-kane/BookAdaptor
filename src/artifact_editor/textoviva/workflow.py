"""Discover image engines and resolve workflow selections for generation."""

from pathlib import Path


def default_image_workflow(chapter, fallback):
    """Use the inherited book setting when an image has no explicit workflow."""
    return (getattr(chapter, 'config', {}) or {}).get('default_image_template') or fallback


def image_engine_choices():
    """List installed still-image workflows using the workflow selector's IDs."""
    import const
    root = Path(const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR'])
    choices = []
    for path in sorted(root.glob('ui.t2i.*.json')):
        identity, _, label = path.stem.removeprefix('ui.').partition('_')
        if identity == 't2i.style':
            continue
        label = label.replace('_', ' ').removeprefix('sampler-')
        choices.append((identity, label or identity.removeprefix('t2i.')))
    return choices


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

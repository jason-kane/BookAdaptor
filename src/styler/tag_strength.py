"""Weighted tag evidence from current and previous style sample generations."""
import json
from pathlib import Path
import threading
import time


PREVIOUS_GENERATION_WEIGHT = 0.25
_CACHE_SECONDS = 30
_cache_lock = threading.Lock()
_cache = {}


def generated_tag_strengths(client, library_dir):
    """Sum current sample tag links at 1.0 and previous links at 0.25."""
    cache_key = str(Path(library_dir).resolve())
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(cache_key)
        if cached and now - cached[0] < _CACHE_SECONDS:
            return dict(cached[1])
        result = _calculate_generated_tag_strengths(client, library_dir)
        _cache[cache_key] = (time.monotonic(), result)
        return dict(result)


def _calculate_generated_tag_strengths(client, library_dir):
    from styler.tag_consensus import prompt_version

    folder = Path(library_dir) / 'style_exploration'
    strengths = {}
    if not folder.is_dir():
        return strengths

    current_versions = {}
    for sidecar in folder.glob('*.json'):
        if sidecar.name.endswith('.tags.json') or not sidecar.with_suffix('.png').is_file():
            continue
        try:
            metadata = json.loads(sidecar.read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict):
            continue
        style = metadata.get('style_tag')
        tags = metadata.get('tags')
        if not isinstance(style, str) or not isinstance(tags, list):
            continue
        if style not in current_versions:
            current_versions[style] = prompt_version(client, style)
        weight = (1.0 if metadata.get('prompt_sha256') == current_versions[style]
                  else PREVIOUS_GENERATION_WEIGHT)
        for tag in {tag.strip() for tag in tags
                    if isinstance(tag, str) and tag.strip()}:
            key = ' '.join(tag.casefold().split())
            strengths[key] = strengths.get(key, 0.0) + weight
    return strengths

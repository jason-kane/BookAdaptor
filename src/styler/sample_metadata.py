"""Portable metadata belonging to one generated sample image."""
import hashlib
import json
import logging
from pathlib import Path
import tempfile

log = logging.getLogger(__name__)


def image_tags(path):
    """Read display tags from this image's canonical JSON sidecar."""
    path = Path(path)
    try:
        metadata = json.loads(path.with_suffix('.json').read_text())
        if not isinstance(metadata, dict):
            return []
        digest = metadata.get('sha256')
        if digest and digest != hashlib.sha256(path.read_bytes()).hexdigest():
            return []
        tags = metadata.get('tags', [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            return []
        return tags[:8]
    except (OSError, ValueError):
        return []


def character_placement(path):
    """Return the saved character/artwork relationship for this image."""
    path = Path(path)
    try:
        metadata = json.loads(path.with_suffix('.json').read_text())
        if not isinstance(metadata, dict):
            return None
        digest = metadata.get('sha256')
        if digest and digest != hashlib.sha256(path.read_bytes()).hexdigest():
            return None
        placement = metadata.get('character_placement')
        if placement == 'in_front':  # Legacy v5 metadata.
            placement = 'outside_image'
        return placement if placement in ('in_image', 'outside_image', 'not_visible', 'uncertain') else None
    except (OSError, ValueError):
        return None


def embedded_prompt(path):
    from PIL import Image
    try:
        with Image.open(path) as image:
            graph = json.loads(image.info.get('prompt', '{}'))
        prompts = [node.get('inputs', {}).get('value') for node in graph.values()
                   if node.get('_meta', {}).get('title') == 'PROMPT']
        prompts = [value for value in prompts if isinstance(value, str)]
        if len(set(prompts)) == 1:
            return prompts[0]
        # Older exported samplers inline the exact prompt in the positive
        # encoder instead of retaining the named PROMPT input.
        prompts = []
        for node in graph.values():
            positive = node.get('inputs', {}).get('positive')
            if not isinstance(positive, list) or len(positive) != 2:
                continue
            encoder = graph.get(str(positive[0]), {})
            if 'TextEncode' not in encoder.get('class_type', ''):
                continue
            inputs = encoder.get('inputs', {})
            value = inputs.get('text', inputs.get('prompt'))
            if isinstance(value, str):
                prompts.append(value)
        if len(set(prompts)) == 1:
            return prompts[0]
    except (OSError, ValueError, AttributeError):
        pass
    return None


def write_metadata(path, task, *, prompt=None, record=None, digest=None):
    """Never infer a historical generation prompt from today's style settings."""
    path = Path(path)
    digest = digest or hashlib.sha256(path.read_bytes()).hexdigest()
    sidecar = path.with_suffix('.json')
    metadata = {}
    if sidecar.is_file():
        try:
            previous = json.loads(sidecar.read_text())
            if isinstance(previous, dict) and previous.get('sha256', digest) == digest:
                metadata = previous
        except (OSError, ValueError):
            log.warning('Unable to read sample metadata: %s', sidecar)
    if prompt is None:
        prompt = metadata.get('prompt')
    if prompt is None:
        prompt = embedded_prompt(path)
    if record is None:
        legacy = path.with_suffix('.tags.json')
        if legacy.is_file():
            try:
                record = json.loads(legacy.read_text())
            except (OSError, ValueError):
                log.warning('Unable to read sample tags: %s', legacy)
    metadata.update({key: task[key] for key in ('job_id', 'style_tag', 'engine', 'scan_version')
                     if key in task})
    metadata.update(prompt=prompt, sha256=digest)
    metadata.setdefault('tags', [])
    if isinstance(record, dict) and record.get('sha256') == digest:
        metadata.update({key: record[key] for key in (
            'tags', 'analysis_version', 'prompt_sha256', 'raw_tags', 'tag_matches',
            'character_placement', 'image_tier',
            'tag_matching_version', 'tag_matching_tags_sha256', 'tag_vocabulary_sha256') if key in record})
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=sidecar.parent,
                                         prefix=sidecar.name + '.', suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(metadata, indent=2, ensure_ascii=False) + '\n')
        temporary.chmod(0o644)
        temporary.replace(sidecar)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return metadata

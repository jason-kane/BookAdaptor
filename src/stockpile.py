"""Scan stock images and complete their sidecars on the serial image worker."""
from styler.queue_history import publish_queue_event
import hashlib
import json
import logging
from pathlib import Path
import shutil
import tempfile
import threading
import time
import uuid

from PIL import Image

from styler.image_tags import EVENTS, analyze, fingerprint, parse_tags, tag_payload
from tag_matching import matched, match_tags

ANALYSIS_VERSION = 'stockpile-image-metadata-v1'
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif'}
log = logging.getLogger(__name__)
_TAG_INDEX_CACHE_SECONDS = 30
_tag_index_lock = threading.Lock()
_tag_index_cache = {}


def read_metadata(path):
    try:
        value = json.loads(path.with_suffix('.json').read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def valid_description(value):
    return isinstance(value, str) and bool(value.strip())


def metadata_images(library_dir):
    root = Path(library_dir) / 'stockpile'
    if not root.is_dir():
        return
    for path in sorted(root.rglob('*'), key=lambda path: path.as_posix().casefold()):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            continue
        yield path.relative_to(root).as_posix(), read_metadata(path) or {}


def _tag_index(library_dir):
    cache_key = str(Path(library_dir).resolve())
    now = time.monotonic()
    with _tag_index_lock:
        cached = _tag_index_cache.get(cache_key)
        if cached and now - cached[0] < _TAG_INDEX_CACHE_SECONDS:
            return cached[1]
        index = {}
        for filename, metadata in metadata_images(library_dir):
            tags = metadata.get('tags', [])
            if not isinstance(tags, list):
                continue
            image_tags = {' '.join(value.casefold().split())
                          for value in tags if isinstance(value, str) and value.strip()}
            description = metadata.get('description')
            image = dict(filename=filename,
                         description=description if valid_description(description) else Path(filename).stem)
            for tag in image_tags:
                index.setdefault(tag, []).append(image)
        _tag_index_cache[cache_key] = (time.monotonic(), index)
        return index


def tag_counts(library_dir):
    return {tag: len(images) for tag, images in _tag_index(library_dir).items()}


def tagged_images(library_dir, tag):
    """Find stock images with this tag in their JSON metadata."""
    wanted = ' '.join(tag.casefold().split())
    if not wanted:
        return []
    return list(_tag_index(library_dir).get(wanted, ()))


def valid_tags(value):
    return (isinstance(value, list) and bool(value)
            and all(isinstance(tag, str) and tag.strip() and len(tag) <= 80 for tag in value))


def complete(metadata, dimensions, digest):
    return (isinstance(metadata, dict)
            and metadata.get('width') == dimensions[0] and metadata.get('height') == dimensions[1]
            and valid_description(metadata.get('description')) and valid_tags(metadata.get('tags'))
            and metadata.get('sha256', digest) == digest)


def parse_analysis(text):
    result = tag_payload(text)
    if not isinstance(result, dict) or not valid_description(result.get('description')):
        raise ValueError('Stockpile analysis must include an image description')
    tags = parse_tags(json.dumps(result))
    if not tags:
        raise ValueError('Stockpile analysis must include image tags')
    return dict(description=result['description'].strip(), tags=tags)


def scan_stockpile(client, library_dir, queue='video_render_jobs'):
    root = Path(library_dir) / 'stockpile'
    result = dict(state='stockpile_queued', scanned=0, complete=0, jobs=[], errors=[])
    if not root.is_dir():
        return result
    for path in sorted(root.rglob('*')):
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue
        try:
            path.resolve().relative_to(root.resolve())
            result['scanned'] += 1
            with Image.open(path) as image:
                dimensions = image.size
            digest = fingerprint(path)
            metadata = read_metadata(path)
            if complete(metadata, dimensions, digest) and matched(metadata):
                result['complete'] += 1
                continue
            name = path.relative_to(root).as_posix()
            job_id = 'stockpile:' + hashlib.sha256((name + ':' + digest).encode()).hexdigest()
            task = dict(action='stockpile_image_metadata', job_id=job_id, filename=name, sha256=digest)
            pending = 'stockpile:pending:' + job_id
            if client.set(pending, '1', nx=True, ex=86400):
                try:
                    client.lpush(queue, json.dumps(task))
                    client.publish(EVENTS, json.dumps(dict(
                        state='job_queued', job_id=job_id, job_type='Stockpile tags',
                        action='stockpile_image_metadata', filename=name)))
                except Exception:
                    client.delete(pending)
                    raise
            result['jobs'].append(job_id)
        except (OSError, ValueError) as exc:
            log.warning('Unable to scan stock image %s: %s', path, exc)
            result['errors'].append(path.relative_to(root).as_posix())
    return result


def run_job(client, task, library_dir):
    event = dict(job_id=task['job_id'], filename=task['filename'])
    client.publish(EVENTS, json.dumps(dict(
        **event, state='running', job_type='Stockpile tags')))
    temporary = None
    try:
        root = (Path(library_dir) / 'stockpile').resolve()
        path = (root / task['filename']).resolve()
        path.relative_to(root)
        if path.suffix.lower() not in EXTENSIONS:
            raise ValueError('Unsupported stock image')
        digest = fingerprint(path)
        if digest != task['sha256']:
            raise ValueError('Stock image changed; scan again')
        with Image.open(path) as image:
            width, height = image.size
        metadata = read_metadata(path) or {}
        if metadata.get('sha256', digest) != digest:
            metadata = {key: value for key, value in metadata.items()
                        if key not in ('description', 'tags')}
        refresh_analysis = bool(task.get('analysis_version')
                                and metadata.get('analysis_version') != task['analysis_version'])
        if refresh_analysis or not valid_description(metadata.get('description')) or not valid_tags(metadata.get('tags')):
            analysis = analyze(path, workflow_name='stockpile-image-metadata', parser=parse_analysis)
            # Keep other metadata and any valid fields added during analysis.
            current = read_metadata(path) or metadata
            if current.get('sha256', digest) == digest:
                metadata = current
            if not valid_description(metadata.get('description')):
                metadata['description'] = analysis['description']
            if refresh_analysis or not valid_tags(metadata.get('tags')):
                metadata['tags'] = analysis['tags']
                metadata['raw_tags'] = analysis['tags']
                for key in ('tag_matching_version', 'tag_matching_tags_sha256', 'tag_vocabulary_sha256'):
                    metadata.pop(key, None)
            metadata['analysis_version'] = ANALYSIS_VERSION
        if fingerprint(path) != digest:
            raise ValueError('Stock image changed during analysis; scan again')
        if not matched(metadata):
            source_tags = metadata.get('raw_tags')
            if not valid_tags(source_tags):
                source_tags = metadata['tags']
            metadata.update(match_tags(client, library_dir, source_tags))
        if fingerprint(path) != digest:
            raise ValueError('Stock image changed during tag matching; scan again')
        metadata.update(width=width, height=height, sha256=digest)
        sidecar = path.with_suffix('.json')
        if sidecar.exists() and read_metadata(path) is None:
            shutil.copy2(sidecar, sidecar.with_name(sidecar.name + '.invalid-' + uuid.uuid4().hex))
        with tempfile.NamedTemporaryFile(mode='w', dir=sidecar.parent, prefix=sidecar.name + '.',
                                         suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        temporary.chmod(0o644)
        temporary.replace(sidecar)
        event.update(state='stockpile_complete')
    except Exception as exc:
        log.exception('Stockpile analysis failed for %s', task['filename'])
        event.update(state='stockpile_failed', message=str(exc))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        client.delete('stockpile:pending:' + task['job_id'])
    publish_queue_event(client, EVENTS, event)
    return event

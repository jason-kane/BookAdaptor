"""# Stockpile image metadata pipeline

This module indexes stock images for the Styler pages, queues incomplete
sidecars, and completes those sidecars on the serial image worker. It keeps
metadata portable beside each image so both the web UI and background worker
can use the same source of truth.

## Callers and contracts

- ``styler.views`` uses :func:`tagged_images`, :func:`tag_counts`, and
  :data:`EXTENSIONS` to browse stock images and expose safe image paths.
- ``tag_matching`` uses :func:`metadata_images` to build the vocabulary from
  stockpile sidecars.
- ``styler.idle_maintenance`` uses :data:`ANALYSIS_VERSION`, :data:`EXTENSIONS`,
  :func:`complete`, and :func:`read_metadata` to decide what needs work.
- ``websocket.server`` calls :func:`scan_stockpile` and :func:`run_job`. The
  scanner returns ``state``, ``scanned``, ``complete``, ``jobs``, and ``errors``;
  a worker returns a ``stockpile_complete`` or ``stockpile_failed`` event.

The input library directory contains a ``stockpile/`` tree. Each image may have
an adjacent ``.json`` sidecar. Queue clients are Redis-compatible objects with
``set(nx=True, ex=...)``, ``lpush``, ``publish``, and ``delete`` methods. Worker
jobs require ``job_id``, relative ``filename``, and the queued ``sha256``; the
optional ``analysis_version`` asks the worker to refresh visual analysis. A
scan uses ``video_render_jobs`` by default; the websocket server supplies its
configured queue explicitly.

## Why this module owns this work

The UI needs tag-indexed image lookup, while analysis and tag normalization are
slow asynchronous operations. This module shares one sidecar contract between
those paths, prevents duplicate queued work, detects images changed after
scheduling, and writes successful metadata atomically. The queue server remains
a transport and dispatcher; this module owns stockpile-specific policy and
metadata transitions.

## Extension guide

Keep paths relative to the stockpile root in jobs and events. Keep the queue
payload and event fields stable for the websocket client. If analysis or
normalization changes, update the version constants and preserve unrelated
sidecar fields. ``TODO(MONITOR)`` marks aggregate production measurements still
needed; this module does not emit telemetry. Never record filenames, job IDs,
raw descriptions, or tag text for monitoring.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from PIL import Image

from styler.image_tags import EVENTS, analyze, fingerprint, parse_tags, tag_payload
from styler.queue_history import publish_queue_event
from tag_matching import match_tags, matched

# Increment this when the visual-analysis workflow or its output contract changes.
ANALYSIS_VERSION = 'stockpile-image-metadata-v2-comprehensive-tags'

# Keep this set aligned with image formats the viewers and worker can decode.
EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff', '.gif'}

# Preserve tags from broad visual analysis while rejecting malformed sidecars.
MAX_TAG_LENGTH = 80

# A pending marker prevents duplicate scans while a queued job waits for a worker.
# TODO(MONITOR): observe marker age at job start/expiry and duplicate-scan hits
# before tuning this one-day lease; record durations/counts, never job identities.
PENDING_TTL_SECONDS = 86_400

# Cache tag indexes briefly because image sidecars can change while pages are open.
# TODO(MONITOR): aggregate index cache hit/miss and rebuild rates before tuning TTL.
TAG_INDEX_CACHE_SECONDS = 30

log = logging.getLogger(__name__)
_tag_index_lock = threading.Lock()
_tag_index_cache: dict[str, tuple[float, dict[str, list[dict[str, str]]]]] = {}


def read_metadata(path: str | Path) -> dict[str, Any] | None:
    """Read an image's adjacent JSON sidecar, returning ``None`` if unusable."""
    sidecar = Path(path).with_suffix('.json')
    try:
        metadata = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return None
    return metadata if isinstance(metadata, dict) else None


def valid_description(value: object) -> bool:
    """Return whether a sidecar description contains non-whitespace text."""
    return isinstance(value, str) and bool(value.strip())


def _image_paths(root: Path, *, casefold_sort: bool = False) -> Iterator[Path]:
    """Yield supported regular files under ``root`` in a stable order."""
    if not root.is_dir():
        return
    paths = root.rglob('*')
    if casefold_sort:
        ordered_paths = sorted(paths, key=lambda path: path.as_posix().casefold())
    else:
        ordered_paths = sorted(paths)
    for path in ordered_paths:
        if path.is_file() and path.suffix.lower() in EXTENSIONS:
            yield path


def metadata_images(library_dir: str | Path) -> Iterator[tuple[str, dict[str, Any]]]:
    """Yield ``(relative_filename, metadata)`` for safe stockpile images."""
    root = Path(library_dir) / 'stockpile'
    resolved_root = root.resolve()
    for path in _image_paths(root, casefold_sort=True):
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError:
            continue
        yield path.relative_to(root).as_posix(), read_metadata(path) or {}


def _normalized_tag(value: str) -> str:
    """Normalize spacing and case for matching a requested tag to an index key."""
    return ' '.join(value.casefold().split())


def _tag_index(library_dir: str | Path) -> dict[str, list[dict[str, str]]]:
    """Build or reuse a short-lived index from normalized tag to image rows."""
    cache_key = str(Path(library_dir).resolve())
    now = time.monotonic()
    with _tag_index_lock:
        cached = _tag_index_cache.get(cache_key)
        if cached and now - cached[0] < TAG_INDEX_CACHE_SECONDS:
            return cached[1]

        index: dict[str, list[dict[str, str]]] = {}
        for filename, metadata in metadata_images(library_dir):
            tags = metadata.get('tags', [])
            if not isinstance(tags, list):
                continue
            # TODO(MONITOR): measure tag counts and normalized lengths in buckets;
            # do not retain or report tag values.
            image_tags = {
                _normalized_tag(value)
                for value in tags
                if isinstance(value, str) and value.strip()
            }
            description = metadata.get('description')
            image = {
                'filename': filename,
                'description': description if valid_description(description) else Path(filename).stem,
            }
            for tag in image_tags:
                index.setdefault(tag, []).append(image)

        _tag_index_cache[cache_key] = (time.monotonic(), index)
        return index


def tag_counts(library_dir: str | Path) -> dict[str, int]:
    """Return the number of distinct stockpile images containing each tag."""
    return {tag: len(images) for tag, images in _tag_index(library_dir).items()}


def tagged_images(library_dir: str | Path, tag: str) -> list[dict[str, str]]:
    """Find stock images whose JSON metadata contains ``tag``."""
    wanted = _normalized_tag(tag)
    if not wanted:
        return []
    return list(_tag_index(library_dir).get(wanted, ()))


def valid_tags(value: object) -> bool:
    """Return whether tags are a nonempty list of short, nonblank strings."""
    # TODO(MONITOR): observe tag-length and rejected-list-size buckets before
    # changing this existing limit; never collect the tag contents themselves.
    return (
        isinstance(value, list)
        and bool(value)
        and all(
            isinstance(tag, str) and tag.strip() and len(tag) <= MAX_TAG_LENGTH
            for tag in value
        )
    )


def complete(metadata: object, dimensions: tuple[int, int], digest: str) -> bool:
    """Check whether a sidecar has current dimensions, description, tags, and digest."""
    if not isinstance(metadata, dict):
        return False
    return (
        metadata.get('width') == dimensions[0]
        and metadata.get('height') == dimensions[1]
        and valid_description(metadata.get('description'))
        and valid_tags(metadata.get('tags'))
        and metadata.get('sha256', digest) == digest
    )


def parse_analysis(text: str) -> dict[str, Any]:
    """Validate model output and return the sidecar's description and tags."""
    result = tag_payload(text)
    if not isinstance(result, dict) or not valid_description(result.get('description')):
        raise ValueError('Stockpile analysis must include an image description')
    tags = parse_tags(json.dumps(result))
    if not tags:
        raise ValueError('Stockpile analysis must include image tags')
    return {'description': result['description'].strip(), 'tags': tags}


def _queue_job(client: Any, queue: str, task: dict[str, str]) -> None:
    """Claim a pending marker, enqueue the task, and publish its queue notice."""
    job_id = task['job_id']
    pending_key = f'stockpile:pending:{job_id}'
    if not client.set(pending_key, '1', nx=True, ex=PENDING_TTL_SECONDS):
        return

    try:
        client.lpush(queue, json.dumps(task))
        client.publish(EVENTS, json.dumps({
            'state': 'job_queued',
            'job_id': job_id,
            'job_type': 'Stockpile tags',
            'action': 'stockpile_image_metadata',
            'filename': task['filename'],
        }))
    except Exception:
        client.delete(pending_key)
        raise


def scan_stockpile(
    client: Any,
    library_dir: str | Path,
    queue: str = 'video_render_jobs',
) -> dict[str, Any]:
    """Queue incomplete stockpile images and report scan counts and errors."""
    root = Path(library_dir) / 'stockpile'
    result: dict[str, Any] = {
        'state': 'stockpile_queued',
        'scanned': 0,
        'complete': 0,
        'jobs': [],
        'errors': [],
    }
    if not root.is_dir():
        return result

    # TODO(MONITOR): count image extensions, scan errors by category, and per-run
    # scanned/completed/queued totals; do not retain filenames or job IDs.
    for path in _image_paths(root):
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

            filename = path.relative_to(root).as_posix()
            job_id = 'stockpile:' + hashlib.sha256(f'{filename}:{digest}'.encode()).hexdigest()
            task = {
                'action': 'stockpile_image_metadata',
                'job_id': job_id,
                'filename': filename,
                'sha256': digest,
            }
            _queue_job(client, queue, task)
            result['jobs'].append(job_id)
        except (OSError, ValueError) as exc:
            log.warning('Unable to scan stock image %s: %s', path, exc)
            result['errors'].append(path.relative_to(root).as_posix())
    return result


def _resolve_job_image(root: Path, filename: str) -> Path:
    """Resolve and validate a queued relative image path within the stockpile."""
    path = (root / filename).resolve()
    path.relative_to(root)
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError('Unsupported stock image')
    return path


def _metadata_after_analysis(
    path: Path,
    metadata: dict[str, Any],
    task: dict[str, Any],
    digest: str,
) -> dict[str, Any]:
    """Refresh incomplete analysis while preserving valid concurrent sidecar data."""
    requested_version = task.get('analysis_version')
    refresh_analysis = bool(
        requested_version and metadata.get('analysis_version') != requested_version
    )
    missing_description = not valid_description(metadata.get('description'))
    missing_tags = not valid_tags(metadata.get('tags'))
    if not (refresh_analysis or missing_description or missing_tags):
        return metadata

    analysis = analyze(path, workflow_name='stockpile-image-metadata', parser=parse_analysis)

    # Analysis may take a while; merge fields another writer added meanwhile.
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
    return metadata


def _metadata_after_matching(client: Any, library_dir: str | Path, metadata: dict[str, Any]) -> dict[str, Any]:
    """Normalize source tags when the stored tag-matching record is stale."""
    if matched(metadata):
        return metadata
    source_tags = metadata.get('raw_tags')
    if not valid_tags(source_tags):
        source_tags = metadata['tags']
    metadata.update(match_tags(client, library_dir, source_tags))
    return metadata


def _ensure_image_unchanged(path: Path, digest: str, phase: str) -> None:
    """Abort a job if its image changed after the queued fingerprint was made."""
    if fingerprint(path) != digest:
        raise ValueError(f'Stock image changed during {phase}; scan again')


def _write_metadata_atomically(sidecar: Path, metadata: dict[str, Any]) -> None:
    """Replace a sidecar atomically, preserving malformed content as a backup."""
    if sidecar.exists() and read_metadata(sidecar) is None:
        backup = sidecar.with_name(f'{sidecar.name}.invalid-{uuid.uuid4().hex}')
        shutil.copy2(sidecar, backup)

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w',
            dir=sidecar.parent,
            prefix=f'{sidecar.name}.',
            suffix='.tmp',
            delete=False,
        ) as output:
            temporary = Path(output.name)
            output.write(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
        temporary.chmod(0o644)
        temporary.replace(sidecar)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_job(client: Any, task: dict[str, Any], library_dir: str | Path) -> dict[str, Any]:
    """Analyze one queued image, atomically update its sidecar, and publish its result."""
    event = {'job_id': task['job_id'], 'filename': task['filename']}
    client.publish(EVENTS, json.dumps({
        **event,
        'state': 'running',
        'job_type': 'Stockpile tags',
    }))

    try:
        root = (Path(library_dir) / 'stockpile').resolve()
        path = _resolve_job_image(root, task['filename'])
        digest = fingerprint(path)
        if digest != task['sha256']:
            raise ValueError('Stock image changed; scan again')
        with Image.open(path) as image:
            dimensions = image.size

        metadata = read_metadata(path) or {}
        if metadata.get('sha256', digest) != digest:
            metadata = {
                key: value
                for key, value in metadata.items()
                if key not in ('description', 'tags')
            }
        metadata = _metadata_after_analysis(path, metadata, task, digest)
        _ensure_image_unchanged(path, digest, 'analysis')
        metadata = _metadata_after_matching(client, library_dir, metadata)
        _ensure_image_unchanged(path, digest, 'tag matching')

        metadata.update(width=dimensions[0], height=dimensions[1], sha256=digest)
        _write_metadata_atomically(path.with_suffix('.json'), metadata)
        event['state'] = 'stockpile_complete'
    except Exception as exc:
        # A failed job remains retryable; the old sidecar is left untouched.
        log.exception('Stockpile analysis failed for %s', task['filename'])
        event.update(state='stockpile_failed', message=str(exc))
    finally:
        client.delete(f"stockpile:pending:{task['job_id']}")

    publish_queue_event(client, EVENTS, event)
    return event

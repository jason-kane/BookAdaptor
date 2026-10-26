"""Queue versioned normalization passes over existing Styler sample metadata."""
from styler.queue_history import publish_queue_event
import hashlib
import json
from pathlib import Path

from styler.image_tags import EVENTS
from styler.jobs import sample_task

STATE = 'styler:styles-scan:state'


def pop_scan_job(client, queue):
    """Atomically take the oldest style scan before background generation."""
    return client.eval('''
        local jobs = redis.call('LRANGE', KEYS[1], 0, -1)
        for i = #jobs, 1, -1 do
            local job = cjson.decode(jobs[i])
            if job.action == 'styler_scan_image_tags' then
                redis.call('LREM', KEYS[1], -1, jobs[i])
                return jobs[i]
            end
        end
        return nil
    ''', 1, queue)


def _read_state(client):
    raw = client.get(STATE)
    try:
        value = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
    except (TypeError, ValueError):
        value = {}
    return value if isinstance(value, dict) else {}


def scan_styles(client, library_dir, queue='video_render_jobs'):
    """Queue one normalization pass, resuming its version until every job succeeds."""
    folder = Path(library_dir) / 'style_exploration'
    state = _read_state(client)
    version = state.get('version', 0)
    if state.get('status') != 'running':
        version = int(version) + 1
        state = {'version': version, 'status': 'running'}
        client.set(STATE, json.dumps(state))

    remaining_key = f'styler:styles-scan:{version}:remaining'
    candidates = []
    sample_folders = ((folder, 'classic'), (folder / 'kid_friendly', 'kid_friendly'))
    for sample_folder, base_style_prompt in sample_folders:
        if not sample_folder.is_dir():
            continue
        for path in sorted(sample_folder.glob('*.png'), key=lambda value: value.name.casefold()):
            engine = next((value for value in ('krea2', 'qwen', 'zit')
                           if path.stem.endswith('_' + value)), None)
            if engine is None:
                continue
            style_tag = path.stem[:-(len(engine) + 1)]
            from styler.tag_consensus import style_key
            key = style_key(style_tag)
            from styler.style_duplicates import style_merge_locked
            if client.sismember('ABYSS/tags', key) or client.hexists(key, 'merged_into') or style_merge_locked(client, style_tag):
                continue
            sidecar = path.with_suffix('.json')
            try:
                metadata = json.loads(sidecar.read_text())
                if not isinstance(metadata, dict):
                    continue
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except (OSError, ValueError):
                continue
            if metadata.get('sha256') and metadata['sha256'] != digest:
                continue
            tags = metadata.get('tags')
            if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
                continue
            if metadata.get('scan_version') == version:
                continue
            sample = sample_task({'style_tag': style_tag, 'engine': engine,
                                  'base_style_prompt': base_style_prompt})
            job_id = f"style-scan:{version}:{sample['job_id']}"
            candidates.append(dict(action='styler_scan_image_tags', job_id=job_id,
                                   sample_job_id=sample['job_id'], style_tag=style_tag,
                                   engine=engine, base_style_prompt=base_style_prompt,
                                   sha256=digest, scan_version=version))

    candidates.sort(key=lambda task: (task['style_tag'].casefold(), task['base_style_prompt'], task['engine']))
    # Reconcile the durable remaining set on every click. This also recovers
    # from a worker restart or a failed job without advancing the scan version.
    desired = {task['job_id'] for task in candidates}
    existing = client.smembers(remaining_key)
    for job_id in existing:
        job_id = job_id.decode() if isinstance(job_id, bytes) else job_id
        if job_id not in desired:
            client.srem(remaining_key, job_id)
    if desired:
        client.sadd(remaining_key, *desired)

    for task in candidates:
        pending = 'styler:styles-scan:pending:' + task['job_id']
        if client.set(pending, '1', nx=True, ex=86400):
            try:
                client.lpush(queue, json.dumps(task))
                client.publish(EVENTS, json.dumps(dict(
                    state='job_queued', job_id=task['job_id'], job_type='Tag normalization',
                    action='styler_scan_image_tags', style_tag=task['style_tag'],
                    engine=task['engine'])))
            except Exception:
                client.delete(pending)
                raise

    outstanding = client.scard(remaining_key)
    if not outstanding:
        state['status'] = 'complete'
        client.set(STATE, json.dumps(state))
    return dict(state='styles_scan_queued' if outstanding else 'styles_scan_complete',
                version=version, scanned=len(candidates), jobs=sorted(desired))


def run_job(client, task, library_dir):
    """Normalize a sample's existing tags and persist the scan version atomically."""
    from styler.image_tags import fingerprint
    from styler.sample_metadata import write_metadata
    from tag_matching import match_tags

    event = {key: task[key] for key in ('job_id', 'style_tag', 'engine', 'scan_version')}
    client.publish(EVENTS, json.dumps(dict(
        job_id=task['job_id'], style_tag=task['style_tag'], engine=task['engine'],
        state='running', job_type='Tag normalization')))
    pending = 'styler:styles-scan:pending:' + task['job_id']
    from styler.tag_consensus import style_key
    if client.sismember('ABYSS/tags', style_key(task['style_tag'])):
        event.update(state='styles_scan_image_complete', image_tags=[],
                     message='Skipped: style is in ABYSS for manual tag repair')
        client.delete(pending)
        publish_queue_event(client, EVENTS, event)
        _finish_scan_job(client, task)
        return event
    base_style_prompt = task.get('base_style_prompt', 'classic')
    folder = Path(library_dir) / 'style_exploration'
    if base_style_prompt == 'kid_friendly':
        folder /= base_style_prompt
    path = folder / f"{task['style_tag']}_{task['engine']}.png"
    try:
        if fingerprint(path) != task['sha256']:
            raise ValueError('Style sample changed; scan again')
        sidecar = path.with_suffix('.json')
        metadata = json.loads(sidecar.read_text())
        if not isinstance(metadata, dict) or not isinstance(metadata.get('tags'), list):
            raise ValueError('Style sample has no image tags to normalize')
        source_tags = metadata.get('raw_tags', metadata['tags'])
        if not isinstance(source_tags, list) or not all(isinstance(tag, str) for tag in source_tags):
            source_tags = metadata['tags']
        matching = match_tags(client, library_dir, source_tags,
                              style_name=task['style_tag'], engine=task['engine'])
        if fingerprint(path) != task['sha256']:
            raise ValueError('Style sample changed during tag matching; scan again')

        sample = sample_task({'style_tag': task['style_tag'], 'engine': task['engine'],
                              'base_style_prompt': base_style_prompt})
        record_key = 'styler:image-tags:' + sample['job_id']
        old = client.get(record_key)
        old_record = json.loads(old) if isinstance(old, (str, bytes)) else {}
        old_tags = old_record.get('tags', []) if isinstance(old_record, dict) else []
        record = dict(old_record) if isinstance(old_record, dict) else {}
        for key in ('analysis_version', 'prompt_sha256'):
            if key not in record and key in metadata:
                record[key] = metadata[key]
        record.update(job_id=sample['job_id'], style_tag=task['style_tag'], engine=task['engine'],
                      base_style_prompt=base_style_prompt,
                      sha256=task['sha256'], tags=matching['tags'], scan_version=task['scan_version'])
        record.update({key: value for key, value in matching.items() if key != 'tags'})
        with client.pipeline() as pipe:
            for tag in old_tags:
                pipe.srem('styler:image-tag:' + tag, sample['job_id'])
            pipe.set(record_key, json.dumps(record))
            if matching['tags']:
                pipe.sadd('styler:image-tag-names', *matching['tags'])
            for tag in matching['tags']:
                pipe.sadd('styler:image-tag:' + tag, sample['job_id'])
            pipe.execute()
        write_metadata(path, {**sample, 'scan_version': task['scan_version']},
                       record=record, digest=task['sha256'])
        agreement = None
        try:
            from styler.tag_consensus import reconcile
            agreement = reconcile(client, task['style_tag'], library_dir)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Unable to refresh style tags after scan')
        event.update(state='styles_scan_image_complete', image_tags=matching['tags'])
        if agreement:
            event.update(agreement)
    except Exception as exc:
        import logging
        logging.getLogger(__name__).exception('Style tag scan failed for %s/%s',
                                              task['style_tag'], task['engine'])
        event.update(state='styles_scan_image_failed', message=str(exc))
    finally:
        client.delete(pending)

    publish_queue_event(client, EVENTS, event)
    if event['state'] == 'styles_scan_image_complete':
        _finish_scan_job(client, task)
    return event


def _finish_scan_job(client, task):
    remaining_key = f"styler:styles-scan:{task['scan_version']}:remaining"
    client.srem(remaining_key, task['job_id'])
    if not client.scard(remaining_key):
        state = _read_state(client)
        if state.get('version') == task['scan_version']:
            state['status'] = 'complete'
            client.set(STATE, json.dumps(state))
            client.publish(EVENTS, json.dumps(dict(
                job_id=task['job_id'], state='styles_scan_complete',
                scan_version=task['scan_version'])))

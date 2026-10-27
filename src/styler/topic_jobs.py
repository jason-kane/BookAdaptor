"""Run category taxonomy prompts through Styler's visible serial worker queue."""
import hashlib
import json
import time
import uuid

EVENTS = 'styler_sample_events'
QUEUE = 'video_render_jobs'
IDLE_QUEUE = 'styler_idle_jobs'
BUILD_INTERVAL = 7 * 24 * 60 * 60
BUILD_RETRY_INTERVAL = 15 * 60


def _result_key(job_id):
    return f'styler:topic-llm-result:{job_id}'


def queued_prompt(client, prompt, system_prompt, *, force=False, timeout=300,
                  topic_level='taxonomy', topic_parent=None):
    """Queue a prompt as a visible Styler task and wait for its worker result."""
    identity = hashlib.sha256((system_prompt + '\0' + prompt).encode()).hexdigest()
    if force:
        identity = hashlib.sha256((identity + uuid.uuid4().hex).encode()).hexdigest()
    job_id = f'styler-topic:{identity}'
    result_key = _result_key(job_id)
    existing = client.get(result_key)
    if existing:
        result = json.loads(existing)
        if result.get('state') == 'complete':
            return result['text']
        if result.get('state') == 'failed':
            raise RuntimeError(result.get('error') or 'Category LLM task failed')

    pending_key = f'styler:topic-llm-pending:{job_id}'
    task = dict(action='styler_topic_llm', job_id=job_id, prompt=prompt,
                system_prompt=system_prompt, force=bool(force), topic_level=topic_level,
                provider='comfyui')
    if topic_parent:
        task['topic_parent'] = topic_parent
    if client.set(pending_key, '1', nx=True, ex=timeout + 60):
        try:
            client.lpush(QUEUE, json.dumps(task, ensure_ascii=False))
            client.publish(EVENTS, json.dumps(dict(
                state='job_queued', job_id=job_id, job_type='Category grouping',
                action='styler_topic_llm', topic_level=topic_level,
                topic_parent=topic_parent)))
        except Exception:
            client.delete(pending_key)
            raise

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = client.get(result_key)
        if raw:
            result = json.loads(raw)
            if result.get('state') == 'complete':
                return result['text']
            if result.get('state') == 'failed':
                raise RuntimeError(result.get('error') or 'Category LLM task failed')
        time.sleep(0.2)
    raise TimeoutError('Timed out waiting for the queued category LLM task')


def run_job(client, task):
    """Worker entry point for one queued taxonomy or membership prompt."""
    event = dict(job_id=task['job_id'], action='styler_topic_llm',
                 topic_level=task.get('topic_level', 'taxonomy'),
                 job_type='Category grouping')
    if task.get('topic_parent'):
        event['topic_parent'] = task['topic_parent']
    result_key = _result_key(task['job_id'])
    try:
        from artifact_editor.llm import str_prompt
        text = str_prompt(task['prompt'], system_prompt=task['system_prompt'],
                          force=task.get('force', False))
        client.set(result_key, json.dumps({'state': 'complete', 'text': text}), ex=3600)
        event.update(state='complete', message='Category grouping ready')
    except Exception as exc:
        client.set(result_key, json.dumps({'state': 'failed', 'error': str(exc)}), ex=3600)
        event.update(state='failed', message=str(exc))
    finally:
        client.delete(f"styler:topic-llm-pending:{task['job_id']}")
        from styler.queue_history import publish_queue_event
        publish_queue_event(client, EVENTS, event)
    return event


def enqueue_taxonomy_rebuild(client, *, queue=IDLE_QUEUE, reason='weekly', now=None):
    """Queue one whole-tree rebuild for the idle worker and report deduplicated state."""
    now = time.time() if now is None else now
    existing = client.get('styler:topic-tree:pending')
    if existing:
        return dict(state='already_queued', job_id=_decode(existing))
    try:
        last_attempt = float(client.get('styler:topic-tree:last-attempt') or 0)
    except (TypeError, ValueError):
        last_attempt = 0
    if reason == 'weekly' and now - last_attempt < BUILD_RETRY_INTERVAL:
        return dict(state='retry_later')
    job_id = f"styler-topic-tree:{int(now)}:{uuid.uuid4().hex[:8]}"
    task = dict(action='styler_topic_taxonomy_rebuild', job_id=job_id,
                reason=reason, idle_maintenance=True, provider='comfyui')
    if not client.set('styler:topic-tree:pending', job_id, nx=True, ex=24 * 60 * 60):
        return dict(state='already_queued', job_id=_decode(client.get('styler:topic-tree:pending')))
    try:
        client.rpush(queue, json.dumps(task))
        client.publish(EVENTS, json.dumps({**task, 'state': 'job_queued',
                                           'job_type': 'Category taxonomy rebuild'}))
    except Exception:
        client.delete('styler:topic-tree:pending')
        raise
    return dict(state='queued', job_id=job_id)


def run_taxonomy_rebuild(client, task):
    """Build the entire taxonomy on the worker, atomically publishing only a complete tree."""
    from styler.topic_taxonomy import BUILD_LAST_ATTEMPT, BUILD_LAST_SUCCESS, build_full_tree
    event = dict(job_id=task['job_id'], action=task['action'],
                 reason=task.get('reason', 'weekly'),
                 job_type='Category taxonomy rebuild')
    now = time.time()
    client.set(BUILD_LAST_ATTEMPT, str(now))
    try:
        from artifact_editor.llm import str_prompt
        request_count = 0

        def prompt_fn(prompt, *, system_prompt, force=False):
            nonlocal request_count
            request_count += 1
            message = f'Category tree rebuild: LLM request {request_count}'
            progress = json.dumps({'percent': 0, 'message': message})
            client.set(f"styler_progress:{task['job_id']}", progress, ex=24 * 60 * 60)
            client.publish(EVENTS, json.dumps({**event, 'state': 'running',
                                               'percent': 0, 'message': message}))
            result = str_prompt(prompt, system_prompt=system_prompt, force=force)
            message = f'Category tree rebuild: completed {request_count} LLM requests'
            client.set(f"styler_progress:{task['job_id']}",
                       json.dumps({'percent': 0, 'message': message}), ex=24 * 60 * 60)
            client.publish(EVENTS, json.dumps({**event, 'state': 'running',
                                               'percent': 0, 'message': message}))
            return result

        tree = build_full_tree(client, force=True, prompt_fn=prompt_fn)
        client.set(BUILD_LAST_SUCCESS, str(time.time()))
        event.update(state='complete', tag_count=tree['tag_count'],
                     topic_count=len(tree['topics']), message='Complete topic tree rebuilt')
    except Exception as exc:
        event.update(state='failed', message=str(exc))
        import logging
        logging.getLogger(__name__).exception('Full Styler topic tree rebuild failed')
    finally:
        client.delete('styler:topic-tree:pending')
        client.delete(f"styler_progress:{task['job_id']}")
        from styler.queue_history import publish_queue_event
        publish_queue_event(client, EVENTS, event)
    return event


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value

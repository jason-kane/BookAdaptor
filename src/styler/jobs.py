"""Styler sample jobs on the shared, serial render worker."""
from styler.queue_history import publish_queue_event
import hashlib
from html import escape
from urllib.parse import quote
import json
import os

EVENTS = 'styler_sample_events'
BASE_STYLE_PROMPTS = ('classic', 'kid_friendly')


def pop_engine_sample(client, queue, engine):
    """Take the oldest queued sample using the currently loaded engine."""
    return client.eval('''
        local length = redis.call('LLEN', KEYS[1])
        -- Scan from the FIFO end in bounded chunks. Keep the FIFO order within
        -- this engine before switching to another non-priority task/model.
        local finish = length - 1
        while finish >= 0 do
            local start = math.max(0, finish - 255)
            local jobs = redis.call('LRANGE', KEYS[1], start, finish)
            for i = #jobs, 1, -1 do
                local ok, task = pcall(cjson.decode, jobs[i])
                if not ok or type(task) ~= 'table' then
                    return false
                end
                if task.action == 'styler_sample' and task.engine == ARGV[1] and not task.priority_removed then
                    redis.call('LREM', KEYS[1], -1, jobs[i])
                    return jobs[i]
                end
            end
            finish = start - 1
        end
        return false
    ''', 1, queue, engine)


def pop_interactive_job(client, queue='video_render_jobs'):
    """Take the oldest UI request ahead of queued background work."""
    return client.eval('''
        local jobs = redis.call('LRANGE', KEYS[1], 0, -1)
        for i = #jobs, 1, -1 do
            local ok, task = pcall(cjson.decode, jobs[i])
            if ok and type(task) == 'table' then
                local action = task.action
                local background = action == 'styler_scan_image_tags'
                    or action == 'stockpile_image_metadata'
                    or (action == 'styler_sample' and task.background_category ~= nil)
                if not background then
                    redis.call('LREM', KEYS[1], 1, jobs[i])
                    return jobs[i]
                end
            end
        end
        return false
    ''', 1, queue)


def clear_queued_samples(client, queue='video_render_jobs'):
    # One Redis operation prevents the worker or another socket from changing
    # the queue between filtering jobs and releasing their deduplication keys.
    return client.eval('''
        local jobs = redis.call('LRANGE', KEYS[1], 0, -1)
        local removed = 0
        redis.call('DEL', KEYS[1])
        for _, payload in ipairs(jobs) do
            local ok, task = pcall(cjson.decode, payload)
            if ok and type(task) == 'table' and task.action == 'styler_sample' then
                if type(task.job_id) == 'string' then
                    redis.call('DEL', 'styler_pending:' .. task.job_id)
                    redis.call('DEL', 'styler_progress:' .. task.job_id)
                end
                removed = removed + 1
            else
                redis.call('RPUSH', KEYS[1], payload)
            end
        end
        return removed
    ''', 1, queue)


def sample_task(message):
    tag, engine = message['style_tag'], message['engine']
    base_style_prompt = message.get('base_style_prompt', 'classic')
    if (not isinstance(tag, str) or not tag or len(tag) > 200
            or any(c in tag for c in ('/', '\\', '\x00')) or tag in ('.', '..')
            or engine not in ('zit', 'krea2', 'qwen')
            or base_style_prompt not in BASE_STYLE_PROMPTS):
        raise ValueError('Invalid style sample')
    identity = hashlib.sha256(f'{base_style_prompt}:{engine}:{tag}'.encode()).hexdigest()
    return dict(action='styler_sample', job_id=identity, style_tag=tag, engine=engine,
                base_style_prompt=base_style_prompt)


def sample_response(task, state):
    event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
    event['base_style_prompt'] = task.get('base_style_prompt', 'classic')
    event['state'] = state
    if isinstance(task.get('style_tags'), list):
        event['tags'] = task['style_tags']
    event['id'] = f"styler-sample-{task['job_id']}"
    if state in ('complete', 'loaded'):
        filename = f"{task['style_tag']}_{task['engine']}_sampler.png"
        event['filename'] = ('/styles/show_sample/' + quote(filename, safe='')
                             + '?base_style_prompt=' + quote(task.get('base_style_prompt', 'classic'), safe=''))
    elif state == 'queued':
        event['wrapper'] = (
            f'<span id="{event["id"]}" '
            f'data-style-sample="{escape(task["style_tag"], quote=True)}" '
            f'data-base-style-prompt="{escape(task.get("base_style_prompt", "classic"), quote=True)}" '
            f'data-engine="{task["engine"]}" role="status">'
            '<wa-spinner aria-label="Generating sample"></wa-spinner></span>'
        )
    return event


def request_sample(client, task, queue, library_dir):
    from styler.style_duplicates import style_merge_locked
    if style_merge_locked(client, task['style_tag']):
        raise ValueError('This style is being merged; try again when the scan finishes')
    sample_dir = os.path.join(library_dir, 'style_exploration')
    if task.get('base_style_prompt') == 'kid_friendly':
        sample_dir = os.path.join(sample_dir, 'kid_friendly')
    path = os.path.join(sample_dir,
                        f"{task['style_tag']}_{task['engine']}.png")
    if os.path.isfile(path):
        response = sample_response(task, 'loaded')
        from styler.image_tags import schedule
        from styler.sample_metadata import image_tags
        schedule(client, task, path)
        response['image_tags'] = image_tags(path)
        from styler.tag_consensus import saved_tags
        response['prompt_tags'] = saved_tags(client, task['style_tag'])
        return response
    enqueue(client, task, queue)
    progress = client.get(f"styler_progress:{task['job_id']}")
    if isinstance(progress, (str, bytes)):
        return json.loads(progress)
    return sample_response(task, 'queued')


def enqueue(client, task, queue):
    from styler.style_duplicates import style_merge_locked
    style_tag = task.get('style_tag')
    if style_tag and style_merge_locked(client, style_tag):
        raise ValueError(f'Style {style_tag} is being merged; try again shortly')
    # Redis makes requests from multiple pages and socket processes coalesce.
    key = f"styler_pending:{task['job_id']}"
    if client.set(key, '1', nx=True, ex=86400):
        try:
            client.lpush(queue, json.dumps(task))
            client.publish(EVENTS, json.dumps(dict(
                state='job_queued', job_id=task['job_id'], job_type='Sample image',
                action='styler_sample', style_tag=task['style_tag'], engine=task['engine'],
                base_style_prompt=task.get('base_style_prompt', 'classic'))))
        except Exception:
            client.delete(key)
            raise


def run_sample_job(client, task):
    from comfy import workflow_progress
    from styler.styles import Style
    from styler.views import draw_sample
    task.setdefault('base_style_prompt', 'classic')
    event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
    event['base_style_prompt'] = task['base_style_prompt']
    style = Style(task['style_tag'])
    task['style_tags'] = list(style.tags)
    client.publish(EVENTS, json.dumps(dict(
        **event, state='running', job_type='Sample image', tags=task['style_tags'])))
    from styler.tag_consensus import style_key
    if client.sismember('ABYSS/tags', style_key(task['style_tag'])):
        event.update(state='failed', message='Style is in ABYSS and needs manual tag repair')
        client.delete(f"styler_progress:{task['job_id']}")
        client.delete(f"styler_pending:{task['job_id']}")
        publish_queue_event(client, EVENTS, event)
        return
    def progress(percent, message):
        update = sample_response(task, 'running')
        update.update(percent=max(0, min(100, int(percent))), message=message)
        payload = json.dumps(update)
        client.set(f"styler_progress:{task['job_id']}", payload, ex=86400)
        client.publish(EVENTS, payload)

    token = workflow_progress.set(progress)
    try:
        base_style_prompt = task.get('base_style_prompt', 'classic')
        path = style.get_image_path(task['engine'], base_style_prompt)
        if not os.path.exists(path):
            progress(0, 'Starting sample')
            path = draw_sample(task['style_tag'], engine=task['engine'],
                               base_style_prompt=base_style_prompt)
        if not os.path.exists(path):
            raise RuntimeError('Sample workflow produced no image')
        event = sample_response(task, 'complete')
        from styler.sample_metadata import image_tags
        event['image_tags'] = image_tags(path)
    except Exception as exc:
        event.update(state='failed', message=str(exc))
    finally:
        workflow_progress.reset(token)
        client.delete(f"styler_progress:{task['job_id']}")
        client.delete(f"styler_pending:{task['job_id']}")
    publish_queue_event(client, EVENTS, event)
    if event['state'] == 'complete' and not (task.get('idle_maintenance') or task.get('background_category')):
        from styler.image_tags import schedule
        try:
            schedule(client, task, path)
        except Exception:
            import logging
            logging.getLogger(__name__).exception('Unable to queue image tagging')

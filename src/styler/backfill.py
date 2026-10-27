"""Resumable category-by-category sample backfill, below interactive jobs."""
import json
from pathlib import Path
import uuid

STATE = 'styler:backfill:state'
LOCK = 'styler:backfill:lock'
ENGINES = ('zit', 'krea2', 'qwen')
BASE_STYLE_PROMPTS = ('classic', 'kid_friendly')


def stop(client):
    with client.lock(LOCK, timeout=15, blocking_timeout=2):
        old = client.get(STATE)
        if isinstance(old, (str, bytes)):
            client.delete('styler:backfill:seen:' + json.loads(old)['id'])
        client.delete(STATE)


def restrict_lost_queue(client, visible_styles, queue='video_render_jobs'):
    """Drop queued background work for LOST styles outside the visible page."""
    from styler.image_tags import QUEUE as TAG_QUEUE
    return client.eval('''
        local visible = {}
        for _, style in ipairs(cjson.decode(ARGV[1])) do visible[style] = true end
        local removed = 0
        local function style_key(tag)
            return string.gsub(string.lower(string.gsub(tag, '^%s*(.-)%s*$', '%1')), '[^%-a-z0-9_]', '_')
        end
        local function prune(queue, kind)
            local jobs = redis.call('LRANGE', queue, 0, -1)
            redis.call('DEL', queue)
            for _, payload in ipairs(jobs) do
                local ok, task = pcall(cjson.decode, payload)
                local tag = ok and type(task) == 'table' and task.style_tag or nil
                local key = type(tag) == 'string' and style_key(tag) or nil
                local discard = key and redis.call('SISMEMBER', 'LOST/tags', key) == 1
                    and not visible[key]
                    and ((kind == 'tags' and task.action == 'styler_tag_sample')
                         or (kind == 'render' and (task.action == 'styler_scan_image_tags'
                             or task.action == 'styler_sample')))
                if discard then
                    if task.action == 'styler_tag_sample' then
                        redis.call('DEL', task.pending_key or
                            ('styler:tag-pending:' .. task.job_id .. ':' .. task.sha256 .. ':' .. task.analysis_version))
                    elseif task.action == 'styler_scan_image_tags' then
                        redis.call('DEL', 'styler:styles-scan:pending:' .. task.job_id)
                        if task.scan_version then
                            local remaining = 'styler:styles-scan:' .. task.scan_version .. ':remaining'
                            redis.call('SREM', remaining, task.job_id)
                            redis.call('PUBLISH', 'styler_sample_events', cjson.encode({
                                job_id=task.job_id, style_tag=task.style_tag, engine=task.engine,
                                scan_version=task.scan_version, state='styles_scan_image_complete',
                                image_tags={}, message='Skipped outside the visible LOST page'}))
                            if redis.call('SCARD', remaining) == 0 then
                                redis.call('PUBLISH', 'styler_sample_events', cjson.encode({
                                    job_id=task.job_id, scan_version=task.scan_version,
                                    state='styles_scan_complete'}))
                            end
                        end
                    elseif task.action == 'styler_sample' then
                        redis.call('DEL', 'styler_pending:' .. task.job_id)
                        redis.call('DEL', 'styler_progress:' .. task.job_id)
                    end
                    removed = removed + 1
                else
                    redis.call('RPUSH', queue, payload)
                end
            end
        end
        prune(KEYS[1], 'render')
        prune(KEYS[2], 'tags')
        return removed
    ''', 2, queue, TAG_QUEUE, json.dumps(visible_styles))


def start(client, category):
    with client.lock(LOCK, timeout=15, blocking_timeout=2):
        old = client.get(STATE)
        if isinstance(old, (str, bytes)):
            old = json.loads(old)
            client.delete('styler:backfill:seen:' + old['id'])
        categories = sorted(client.smembers('tags'))
        categories = [value.decode() if isinstance(value, bytes) else value for value in categories]
        categories = [category] + [value for value in categories if value != category]
        state = dict(id=uuid.uuid4().hex, categories=categories, category=0,
                     engines=list(ENGINES), engine=0, base_style_index=0,
                     base_style_prompts=list(BASE_STYLE_PROMPTS),
                     cursor=0, members=[], scanned=False)
        client.set(STATE, json.dumps(state))


def next_sample(client, queue, library_dir, preferred_engine=None, budget=256):
    """Claim at most one missing sample, only while the interactive queue is empty."""
    from styler.jobs import sample_task
    with client.lock(LOCK, timeout=15, blocking_timeout=2):
        raw = client.get(STATE)
        if not isinstance(raw, (str, bytes)) or client.llen(queue):
            return None
        state = json.loads(raw)
        if state.get('base_style_prompts') != list(BASE_STYLE_PROMPTS):
            # Resume an older one-prompt backfill from the current category so
            # every style gets a kid_friendly sample before tag analysis.
            state.update(phase='images', engines=list(ENGINES), engine=0,
                         base_style_index=0, base_style_prompts=list(BASE_STYLE_PROMPTS),
                         cursor=0, members=[], scanned=False)
        if state.get('phase', 'images') == 'tags':
            return None
        seen = 'styler:backfill:seen:' + state['id']
        for _ in range(budget):
            if client.llen(queue):
                break
            if state['category'] >= len(state['categories']):
                client.delete(STATE, seen)
                return None
            if not state['scanned'] and not state['members'] and state['engine'] == 0:
                if preferred_engine in ENGINES:
                    state['engines'] = [preferred_engine] + [engine for engine in ENGINES if engine != preferred_engine]
            if not state['members']:
                if state['scanned'] and state['cursor'] == 0:
                    state['base_style_index'] = state.get('base_style_index', 0) + 1
                    if state['base_style_index'] >= len(BASE_STYLE_PROMPTS):
                        state['base_style_index'] = 0
                        state['engine'] += 1
                    state.update(scanned=False, cursor=0)
                    if state['engine'] == len(state['engines']):
                        state.update(engine=0, base_style_index=0, phase='tags')
                        client.set(STATE, json.dumps(state))
                        return None
                    continue
                cursor, members = client.sscan(state['categories'][state['category']] + '/tags',
                                                cursor=state['cursor'], count=128)
                state.update(cursor=cursor, members=[value.decode() if isinstance(value, bytes) else value for value in members], scanned=True)
                if not state['members']:
                    continue
            tag = state['members'].pop()
            engine = state['engines'][state['engine']]
            base_style_prompt = BASE_STYLE_PROMPTS[state.get('base_style_index', 0)]
            try:
                task = sample_task(dict(style_tag=tag, engine=engine,
                                        base_style_prompt=base_style_prompt))
            except ValueError:
                continue
            sample_dir = Path(library_dir) / 'style_exploration'
            if base_style_prompt == 'kid_friendly':
                sample_dir /= base_style_prompt
            path = sample_dir / f'{tag}_{engine}.png'
            if path.is_file() or client.sismember(seen, task['job_id']):
                continue
            # Registry keys may also occur as members; only render actual styles.
            if client.type(tag) not in ('hash', b'hash'):
                continue
            if client.sismember('ABYSS/tags', tag):
                continue
            client.sadd(seen, task['job_id'])
            client.expire(seen, 604800)
            if not client.set('styler_pending:' + task['job_id'], '1', nx=True, ex=86400):
                continue
            task['background_category'] = state['categories'][state['category']]
            client.set(STATE, json.dumps(state))
            return task
        client.set(STATE, json.dumps(state))
        return None


def next_tag_sample(client, queue, library_dir, budget=256):
    """Queue one style/prompt's available engine images as a shared tag job."""
    from styler.jobs import sample_task
    from styler.image_tags import (
        QUEUE as TAG_QUEUE,
        _hold_tag_group_build_locks,
        pop_tag_job,
        schedule,
    )

    with client.lock(LOCK, timeout=15, blocking_timeout=2):
        raw = client.get(STATE)
        if not isinstance(raw, (str, bytes)) or client.llen(queue):
            return None
        state = json.loads(raw)
        if state.get('phase') != 'tags':
            return None
        # Reset an engine-first scan left by an older worker.
        if state.get('tag_order') != 'style':
            state.update(tag_order='style', cursor=0, members=[], scanned=False,
                         tag_style=None, tag_engine=0, tag_base_style_index=0)
        for _ in range(budget):
            if client.llen(queue):
                break
            if not state.get('tag_style'):
                if not state['members']:
                    if state['scanned'] and state['cursor'] == 0:
                        state.update(category=state['category'] + 1, engine=0,
                                     base_style_index=0, phase='images', scanned=False, cursor=0)
                        state.pop('tag_order', None)
                        break
                    cursor, members = client.sscan(state['categories'][state['category']] + '/tags',
                                                    cursor=state['cursor'], count=128)
                    state.update(cursor=cursor, members=[value.decode() if isinstance(value, bytes) else value
                                                         for value in members], scanned=True)
                    if not state['members']:
                        continue
                state.update(tag_style=state['members'].pop(), tag_engine=0,
                             tag_base_style_index=0)
            tag = state['tag_style']
            if client.sismember('ABYSS/tags', tag):
                state['tag_style'] = None
                state['tag_base_style_index'] = 0
                continue

            prompt_index = state.get('tag_base_style_index', 0)
            base_style_prompt = BASE_STYLE_PROMPTS[prompt_index]
            state['tag_base_style_index'] = prompt_index + 1
            if state['tag_base_style_index'] == len(BASE_STYLE_PROMPTS):
                state.update(tag_style=None, tag_base_style_index=0)

            sample_dir = Path(library_dir) / 'style_exploration'
            if base_style_prompt == 'kid_friendly':
                sample_dir /= base_style_prompt
            ready = []
            for engine in ENGINES:
                try:
                    task = sample_task(dict(style_tag=tag, engine=engine,
                                            base_style_prompt=base_style_prompt))
                except ValueError:
                    continue
                path = sample_dir / f'{tag}_{engine}.png'
                if path.is_file():
                    ready.append((task, path))

            if ready:
                with _hold_tag_group_build_locks(
                    client, tag, [base_style_prompt]
                ):
                    for task, path in ready:
                        schedule(client, task, path)
                job = pop_tag_job(client, TAG_QUEUE, tag)
            else:
                job = None

            if job:
                client.set(STATE, json.dumps(state))
                return job
        client.set(STATE, json.dumps(state))
        return None

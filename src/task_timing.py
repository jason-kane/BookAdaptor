"""Durable timing of comparable work, independent of short-lived queue history.

Statistics are successful execution durations per work unit, not queue wait or
provider percentages. Raw prompts, chapter names, job IDs and output paths are
excluded from profiles. Dedupe markers expire; accumulated statistics do not.
"""
import hashlib
import heapq
import json
import logging
import math
import os
import time
from collections import OrderedDict

PREFIX = 'task-timing:v1'
SAMPLE_LIMIT = 128
_seen = OrderedDict()


def number(value, default=None):
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (ValueError, TypeError):
        return default


def _workflow_shape(workflow):
    models, settings, classes = set(), set(), set()
    model_keys = {'ckpt_name', 'checkpoint', 'model_name', 'unet_name', 'vae_name', 'clip_name', 'lora_name',
                  'model', 'model_id', 'pretrained_model_name'}
    shape_keys = {'width', 'height', 'steps', 'batch_size', 'sampler_name', 'scheduler'}
    frames = 0
    def visit(value):
        nonlocal frames
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            kind = value.get('class_type') or value.get('type')
            if isinstance(kind, str) and not (len(kind) == 36 and kind.count('-') == 4):
                classes.add(kind)
            inputs = value.get('widgets_values_named') or value.get('inputs') or {}
            if isinstance(inputs, dict):
                for key, item in inputs.items():
                    if key in model_keys and isinstance(item, str):
                        models.add((key, item))
                    if key in shape_keys and isinstance(item, (str, int, float)):
                        settings.add((key, str(item)))
                    if key in ('frames', 'num_frames', 'frame_count', 'length'):
                        frames = max(frames, number(item, 0))
            for key, item in value.items():
                if isinstance(item, (dict, list)) and key not in ('inputs', 'widgets_values_named', 'widgets_values'):
                    visit(item)
    visit(workflow)
    return {'classes': sorted(classes), 'models': sorted(models), 'settings': sorted(settings)}, frames


def prepare(task, *, queue=None, units=None, unit=None):
    """Copy a task and assign a stable profile before its execution starts."""
    task = dict(task)
    if task.get('timing_key') and units is None and unit is None:
        return task
    action = task.get('action') or 'video_render'
    arguments = task.get('arguments') or {}
    role = queue or task.get('execution_queue') or ('cpu' if action in ('draw_frame', 'encode_video', 'typeset_latex') else 'gpu')
    from task_queue import task_provider
    shape = {'version': 1, 'queue': role, 'action': action,
             'provider': 'none' if role in ('cpu', 'render') else task_provider(task), 'unit': unit or 'task'}
    measured_units = units or 1
    if action in ('gpu_workflow', 'gpu_llama'):
        if action == 'gpu_workflow':
            shape['workflow'], frames = _workflow_shape(arguments.get('workflow', {}))
            seconds = number(arguments.get('duration_seconds'), 0)
            if seconds > 0:
                measured_units, unit = seconds, 'video_second'
            elif frames > 1:
                measured_units, unit = frames, 'frame'
        else:
            # Text and image requests have different runtimes. Prompt content
            # and request-specific filenames must never become profile keys.
            shape.update(vision=bool(arguments.get('image_path')), max_tokens=arguments.get('max_tokens', 1024))
    elif action == 'draw_frame':
        image = arguments.get('image_dict') or {}
        shape.update(aspect=arguments.get('aspect'), fullscreen=bool(image.get('fullscreen')),
                     mode=image.get('mode'), cached=bool(not arguments.get('force') and os.path.isfile(arguments.get('frame_fn', ''))))
    elif action == 'encode_video':
        measured_units, unit = max(1, number(arguments.get('max_frames'), 1)), 'frame'
        shape.update(fps=arguments.get('fps'), aspect=(task.get('chapter') or {}).get('aspect'))
    elif action == 'typeset_latex':
        shape.update(aspect=(task.get('chapter') or {}).get('aspect'), source=arguments.get('source', 'xml'))
    else:
        for key in ('engine', 'base_style_prompt', 'operation', 'service_to'):
            if task.get(key) is not None:
                shape[key] = task[key]
        if action == 'styler_tag_group':
            measured_units, unit = max(1, len(task.get('items') or task.get('engines') or [])), 'image'
    shape['context'] = task.get('timing_context') or {}
    shape['unit'] = unit or 'task'
    profile = json.dumps(shape, sort_keys=True, separators=(',', ':'))
    task.update(timing_key=hashlib.sha256(profile.encode()).hexdigest(),
                timing_units=max(0.000001, float(measured_units)), timing_unit=shape['unit'])
    return task


_RECORD = '''
if not redis.call('SET', KEYS[3], '1', 'NX', 'EX', 604800) then return 0 end
if ARGV[1] ~= 'complete' then
    redis.call('HINCRBY', KEYS[1], 'failed_count', 1)
    return 1
end
local duration = tonumber(ARGV[2])
local minimum = tonumber(redis.call('HGET', KEYS[1], 'min'))
local maximum = tonumber(redis.call('HGET', KEYS[1], 'max'))
redis.call('HINCRBY', KEYS[1], 'count', 1)
redis.call('HINCRBYFLOAT', KEYS[1], 'sum', duration)
if not minimum or duration < minimum then redis.call('HSET', KEYS[1], 'min', duration) end
if not maximum or duration > maximum then redis.call('HSET', KEYS[1], 'max', duration) end
redis.call('HSET', KEYS[1], 'updated_at', ARGV[3])
redis.call('LPUSH', KEYS[2], ARGV[2])
redis.call('LTRIM', KEYS[2], 0, 127)
return 1
'''


def record(client, task, seconds, *, state='complete', started_at=None):
    seconds = number(seconds)
    started_at = started_at or task.get('queue_started_at') or task.get('started_at')
    if seconds is None or seconds < 0 or started_at is None or not task.get('job_id'):
        return
    task = prepare(task)
    stamp = hashlib.sha256(f"{task['job_id']}:{started_at}".encode()).hexdigest()
    client.eval(_RECORD, 3, f"{PREFIX}:stats:{task['timing_key']}",
                f"{PREFIX}:samples:{task['timing_key']}", f'{PREFIX}:seen:{stamp}',
                state, seconds / number(task.get('timing_units'), 1), time.time())


def learn_outcomes(client, outcomes):
    """Import retained outcomes, including completions from workers running older code."""
    for task in outcomes:
        start = task.get('queue_started_at') or task.get('started_at')
        duration = task.get('duration_seconds')
        if start is None or duration is None:
            continue
        identity = (PREFIX, task.get('job_id'), str(start))
        if identity in _seen:
            continue
        record(client, task, duration, state=task.get('state', 'complete'), started_at=start)
        _seen[identity] = True
        if len(_seen) > 4096:
            _seen.popitem(last=False)


class Estimator:
    def __init__(self, client, now=None):
        self.client = client
        self.now = time.time() if now is None else now
        self.profiles = {}

    def stats(self, key):
        if key not in self.profiles:
            stats = self.client.hgetall(f'{PREFIX}:stats:{key}')
            samples = self.client.lrange(f'{PREFIX}:samples:{key}', 0, SAMPLE_LIMIT - 1)
            self.profiles[key] = stats, samples
        return self.profiles[key]

    def attach(self, task, *, wait=None):
        task = prepare(task)
        stats, samples = self.stats(task['timing_key'])
        units = number(task.get('timing_units'), 1)
        count = int(number(stats.get('count'), 0))
        start = number(task.get('queue_started_at') or task.get('started_at'))
        elapsed = max(0, self.now - start) if start is not None else None
        duration = number(task.get('duration_seconds'))
        state = task.get('state', 'queued')
        samples = [number(value) for value in samples]
        samples = [value * units for value in samples if value is not None]
        average = number(stats.get('sum'), 0) / count * units if count else None
        minimum = number(stats.get('min'))
        maximum = number(stats.get('max'))
        remaining = None
        status = 'learning'
        if average is not None:
            if elapsed is None or elapsed < average:
                remaining = max(0, average - (elapsed or 0))
                status = 'estimated'
            else:
                survivors = [value for value in samples if value > elapsed]
                remaining = sum(survivors) / len(survivors) - elapsed if survivors else None
                status = 'estimated' if survivors else 'overrun'
        if state in ('complete', 'failed'):
            elapsed = duration if duration is not None else elapsed
            remaining = 0 if state == 'complete' else None
            status = state
        queued = state == 'queued'
        timing = dict(count=count, unit=task['timing_unit'],
                      min_seconds=minimum * units if minimum is not None else None,
                      avg_seconds=average, max_seconds=maximum * units if maximum is not None else None,
                      samples_seconds=samples, elapsed_seconds=elapsed, remaining_seconds=remaining,
                      wait_seconds=wait, eta_seconds=(wait + average if wait is not None and average is not None else None) if queued else remaining,
                      status=status, as_of=self.now)
        return {**task, 'timing': timing}

    def serial(self, running, jobs):
        running = self.attach(running) if running else None
        delay = running['timing']['remaining_seconds'] if running else 0
        result = []
        for task in jobs:
            task = self.attach(task, wait=delay)
            result.append(task)
            duration = task['timing']['avg_seconds']
            delay = delay + duration if delay is not None and duration is not None else None
        return running, result

    def parallel(self, running, jobs, workers):
        active = [self.attach(task) for task in running]
        slots = [(task['timing']['remaining_seconds'] if task['timing']['remaining_seconds'] is not None else math.inf) for task in active]
        slots.extend([0] * max(0, int(workers) - len(active)))
        heapq.heapify(slots)
        result = []
        for task in jobs:
            available = heapq.heappop(slots) if slots else math.inf
            task = self.attach(task, wait=available if math.isfinite(available) else None)
            result.append(task)
            duration = task['timing']['avg_seconds']
            heapq.heappush(slots, available + duration if duration is not None else math.inf)
        return active, result


def safe_record(client, task, seconds, **kwargs):
    try:
        record(client, task, seconds, **kwargs)
    except Exception:
        logging.getLogger(__name__).exception('Unable to retain task timing')


def enrich(client, event):
    """Add public timing without leaking the worker's workflow arguments."""
    source = dict(event)
    if not source.get('timing_key') or not source.get('queue_started_at'):
        raw = client.get('styler:worker-running')
        try:
            current = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
            if isinstance(current, dict) and current.get('job_id') == event.get('job_id'):
                source = {**current, **source}
        except (TypeError, ValueError):
            pass
    timed = Estimator(client).attach(source)
    fields = ('timing', 'timing_key', 'timing_units', 'timing_unit', 'queue_started_at', 'duration_seconds')
    return {**event, **{key: timed[key] for key in fields if key in timed}}


def format_seconds(value):
    value = number(value)
    if value is None:
        return 'unknown'
    if 0 < value < 1:
        return f'{max(1, round(value * 1000))}ms'
    seconds = max(0, math.ceil(value))
    if seconds < 60:
        return f'{seconds}s'
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f'{minutes}m' + (f' {seconds}s' if seconds else '')
    hours, minutes = divmod(minutes, 60)
    return f'{hours}h' + (f' {minutes}m' if minutes else '')


def eta_label(event):
    timing = event.get('timing') or {}
    state = event.get('state', 'running')
    if state in ('complete', 'failed'):
        duration = number(event.get('duration_seconds'), number(timing.get('elapsed_seconds')))
        return ('Done' if state == 'complete' else 'Failed') + (f' · {format_seconds(duration)}' if duration is not None else '')
    remaining = number(timing.get('eta_seconds'))
    if remaining is not None and remaining > 0:
        return ('Queued · ' if state == 'queued' else '') + f'~{format_seconds(remaining)} ' + ('to finish' if state == 'queued' else 'left')
    if timing.get('status') == 'overrun':
        return f"Longer than observed · {format_seconds(timing.get('elapsed_seconds'))} elapsed"
    return 'Queued · learning duration' if state == 'queued' else 'Learning duration'

"""Bounded, resumable repairs that enter the worker only after sustained idleness."""
import hashlib
import json
import logging
from pathlib import Path
import time

QUEUE = 'styler_idle_jobs'
STATE = 'styler:idle:state'
ACTIVITY = 'styler:idle:last-activity'
IDLE_SECONDS = 30
MODE = 'styler:idle:mode'
MODE_INTERVALS = {'off': None, 'walk': IDLE_SECONDS, 'run': 5}
MODE_BATCH_SIZES = {'off': 0, 'walk': 1, 'run': 2}
RESCAN_SECONDS = 300
RETRY_SECONDS = 900
ENGINES = ('zit', 'krea2', 'qwen')
BASE_STYLE_PROMPTS = ('classic', 'kid_friendly')
log = logging.getLogger(__name__)


def get_mode(client):
    value = client.get(MODE)
    value = value.decode() if isinstance(value, bytes) else value
    return value if value in MODE_INTERVALS else 'walk'


def set_mode(client, mode):
    if not isinstance(mode, str) or mode not in MODE_INTERVALS:
        raise ValueError('Unknown background task rate')
    client.set(MODE, mode)
    client.publish('styler_sample_events', json.dumps(dict(state='maintenance_mode', mode=mode)))
    return mode


def background_ready(client, now=None):
    interval = MODE_INTERVALS[get_mode(client)]
    if interval is None:
        return False
    now = time.time() if now is None else now
    try:
        last_activity = float(client.get(ACTIVITY))
    except (TypeError, ValueError):
        mark_activity(client, now)
        return False
    return now - last_activity >= interval


def mark_activity(client, now=None):
    client.set(ACTIVITY, str(time.time() if now is None else now))


def pop_idle_job(client, video_queue, priority_queue, tag_queue):
    raw = client.eval('''
        if redis.call('EXISTS', KEYS[5]) == 1 then return nil end
        for i = 1, 3 do
            if redis.call('LLEN', KEYS[i]) > 0 then return nil end
        end
        return redis.call('LPOP', KEYS[4])
    ''', 5, video_queue, priority_queue, tag_queue, QUEUE, 'styler:worker-running')
    return json.loads(raw) if raw else None


def _json(value):
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _metadata(path):
    try:
        return _json(path.with_suffix('.json').read_text())
    except (OSError, UnicodeError):
        return {}


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def _image_paths(root):
    """Walk in stable order without reading images or materializing the whole tree."""
    from stockpile import EXTENSIONS
    if not root.is_dir():
        return
    try:
        entries = sorted(root.iterdir(), key=lambda path: (path.name.casefold(), path.name))
    except OSError:
        log.warning('Unable to list stockpile directory %s', root)
        return
    for path in entries:
        if path.is_symlink():
            continue
        if path.is_dir():
            yield from _image_paths(path)
        elif path.is_file() and path.suffix.lower() in EXTENSIONS:
            yield path


class IdleMaintenance:
    def __init__(self, client, library_dir, video_queue, priority_queue, tag_queue, *, budget=24):
        self.client = client
        self.library = Path(library_dir)
        self.queues = (video_queue, priority_queue, tag_queue)
        self.budget = budget
        self.state = _json(client.get(STATE))
        self.stockpile_paths = None
        self.batch_reserved = set()

    def _style_candidate(self, tag, engine, base_style_prompt):
        from styler.image_tags import ANALYSIS_VERSION, fingerprint
        from styler.jobs import sample_task
        from styler.tag_consensus import prompt_version
        from tag_matching import VERSION, matched
        task = sample_task(dict(style_tag=tag, engine=engine,
                                base_style_prompt=base_style_prompt))
        sample_dir = self.library / 'style_exploration'
        if base_style_prompt == 'kid_friendly':
            sample_dir /= base_style_prompt
        path = sample_dir / f'{tag}_{engine}.png'
        if path.is_symlink():
            return None
        if not path.is_file():
            task['background_category'] = 'idle maintenance'
            return task, 'styler_pending:' + task['job_id']
        digest = fingerprint(path)
        metadata = _metadata(path)
        record = _json(self.client.get('styler:image-tags:' + task['job_id']))
        def analyzed(value):
            return (value.get('sha256') == digest and value.get('analysis_version') == ANALYSIS_VERSION
                    and isinstance(value.get('tags'), list) and all(isinstance(tag, str) for tag in value['tags']))
        if not analyzed(metadata) or not analyzed(record):
            task.update(action='styler_tag_sample', sha256=digest, analysis_version=ANALYSIS_VERSION,
                        prompt_sha256=prompt_version(self.client, tag))
            return task, f"styler:tag-pending:{task['job_id']}:{digest}:{ANALYSIS_VERSION}"
        if not matched(metadata) or not matched(record) or metadata['tags'] != record['tags']:
            version = 'idle:' + VERSION
            task.update(action='styler_scan_image_tags', sample_job_id=task['job_id'],
                        job_id=f"style-scan:{version}:{task['job_id']}:{digest[:16]}",
                        sha256=digest, scan_version=version)
            return task, 'styler:styles-scan:pending:' + task['job_id']
        return None

    def _next_style(self, now):
        if now < self.state.get('styles_after', 0):
            return None
        for _ in range(self.budget):
            tag = self.state.get('style')
            if tag and any(value is None for value in self.client.hmget(tag, 'name', 'prompt')):
                self.state['style'] = None
                continue
            if not tag:
                keys = self.state.get('style_keys', [])
                if not keys:
                    if self.state.get('style_started') and self.state.get('style_cursor', 0) == 0:
                        self.state.update(style_started=False, styles_after=now + RESCAN_SECONDS)
                        return None
                    cursor, keys = self.client.scan(cursor=self.state.get('style_cursor', 0), count=512, _type='hash')
                    keys = [_text(key) for key in keys]
                    self.state.update(style_cursor=int(cursor), style_started=True, style_keys=keys)
                    if not keys:
                        continue
                tag = self.state['style_keys'].pop(0)
                fields = self.client.hmget(tag, 'name', 'prompt')
                if any(value is None for value in fields) or self.client.sismember('ABYSS/tags', tag):
                    continue
                self.state.update(style=tag, engine=0, style_base_index=0)
            if self.client.sismember('ABYSS/tags', tag):
                self.state['style'] = None
                continue
            engine_index = self.state.get('engine', 0)
            engine = ENGINES[engine_index]
            base_index = self.state.get('style_base_index', 0)
            base_style_prompt = BASE_STYLE_PROMPTS[base_index]
            try:
                candidate = self._style_candidate(tag, engine, base_style_prompt)
                if (candidate and self._retry_key(candidate[0]) not in self.batch_reserved
                        and not self.client.exists(self._retry_key(candidate[0]))):
                    return candidate
            except (OSError, ValueError):
                log.warning('Unable to inspect idle style sample %s/%s', tag, engine, exc_info=True)
            # Stay on this image through generation, analysis, and normalization.
            # Advance once it is healthy, or back off a failed stage without starving other work.
            self.state['engine'] = engine_index + 1
            if self.state['engine'] >= len(ENGINES):
                self.state['engine'] = 0
                self.state['style_base_index'] = base_index + 1
                if self.state['style_base_index'] >= len(BASE_STYLE_PROMPTS):
                    self.state.update(style=None, style_base_index=0)
        return None

    def _next_stockpile(self, now):
        from PIL import Image
        from stockpile import ANALYSIS_VERSION, complete, read_metadata
        from styler.image_tags import fingerprint
        from tag_matching import matched
        if now < self.state.get('stockpile_after', 0):
            return None
        root = self.library / 'stockpile'
        if self.stockpile_paths is None:
            self.stockpile_paths = iter(_image_paths(root))
        for _ in range(self.budget):
            try:
                path = next(self.stockpile_paths)
            except StopIteration:
                self.stockpile_paths = None
                self.state.update(stockpile_cursor='', stockpile_after=now + RESCAN_SECONDS)
                return None
            name = path.relative_to(root).as_posix()
            def path_order(value):
                return tuple((part.casefold(), part) for part in Path(value).parts)
            if self.state.get('stockpile_cursor') and path_order(name) <= path_order(self.state['stockpile_cursor']):
                continue
            self.state['stockpile_cursor'] = name
            try:
                with Image.open(path) as image:
                    dimensions = image.size
                digest = fingerprint(path)
                metadata = read_metadata(path) or {}
                if (complete(metadata, dimensions, digest) and metadata.get('sha256') == digest
                        and matched(metadata) and metadata.get('analysis_version') == ANALYSIS_VERSION):
                    continue
                job_id = 'stockpile:' + hashlib.sha256((name + ':' + digest).encode()).hexdigest()
                task = dict(action='stockpile_image_metadata', job_id=job_id, filename=name,
                            sha256=digest, analysis_version=ANALYSIS_VERSION)
                if self._retry_key(task) not in self.batch_reserved and not self.client.exists(self._retry_key(task)):
                    return task, 'stockpile:pending:' + job_id
            except (OSError, ValueError):
                log.warning('Unable to inspect idle stockpile image %s', name, exc_info=True)
        return None

    @staticmethod
    def _retry_key(task):
        # A new image or analysis/normalization version gets a fresh attempt immediately.
        from tag_matching import VERSION
        signature = json.dumps([task['job_id'], task.get('sha256'), task.get('analysis_version'), VERSION])
        return 'styler:idle:retry:' + hashlib.sha256(signature.encode()).hexdigest()

    def tick(self, now=None):
        now = time.time() if now is None else now
        mode = get_mode(self.client)
        interval = MODE_INTERVALS[mode]
        if interval is None:
            return None
        if self.client.exists('styler:worker-running') or any(self.client.llen(queue) for queue in (*self.queues, QUEUE)):
            mark_activity(self.client, now)
            return None
        try:
            last_activity = float(self.client.get(ACTIVITY))
        except (TypeError, ValueError):
            mark_activity(self.client, now)
            return None
        if now - last_activity < interval:
            return None
        try:
            last_topic_build = float(self.client.get('styler:topic-tree:last-success') or 0)
        except (TypeError, ValueError):
            last_topic_build = 0
        if now - last_topic_build >= 7 * 24 * 60 * 60:
            from styler.topic_jobs import enqueue_taxonomy_rebuild
            queued = enqueue_taxonomy_rebuild(self.client, reason='weekly', now=now)
            if queued.get('state') == 'queued':
                return dict(action='styler_topic_taxonomy_rebuild', job_id=queued['job_id'])
        with self.client.lock('styler:idle:lock', timeout=30, blocking_timeout=1):
            # A checkpoint survives worker restarts; scanning is bounded even on a healthy catalog.
            checkpoint = json.loads(json.dumps(self.state))
            candidates = []
            checkpoints = []
            self.batch_reserved = set()
            try:
                for _ in range(MODE_BATCH_SIZES[mode]):
                    order = ('stockpile', 'styles') if self.state.get('turn') == 'stockpile' else ('styles', 'stockpile')
                    candidate = None
                    for domain in order:
                        # Looking ahead for a second style must not skip the first
                        # image's remaining analysis/normalization stages on the next batch.
                        preserve = domain == 'styles' and any('style_tag' in task for task, _ in candidates)
                        style_checkpoint = {key: value for key, value in json.loads(json.dumps(self.state)).items()
                                            if key.startswith('style') or key == 'engine'} if preserve else None
                        candidate = self._next_style(now) if domain == 'styles' else self._next_stockpile(now)
                        if style_checkpoint is not None:
                            for key in list(self.state):
                                if key.startswith('style') or key == 'engine':
                                    self.state.pop(key)
                            self.state.update(style_checkpoint)
                        if candidate:
                            self.state['turn'] = 'stockpile' if domain == 'styles' else 'styles'
                            break
                    if not candidate:
                        break
                    task, pending_key = candidate
                    task['idle_maintenance'] = True
                    candidates.append((task, pending_key))
                    self.batch_reserved.add(self._retry_key(task))
                    checkpoints.append(json.loads(json.dumps(self.state)))
            finally:
                self.batch_reserved.clear()
            if not candidates:
                self.client.set(STATE, json.dumps(self.state))
                return None
            checkpoints[-1] = json.loads(json.dumps(self.state))
            tasks = [task for task, _ in candidates]
            leases = [key for task, pending in candidates for key in (pending, self._retry_key(task))]
            accepted = int(self.client.eval('''
                if redis.call('EXISTS', KEYS[5]) == 1 then return 0 end
                for i = 1, 4 do
                    if redis.call('LLEN', KEYS[i]) > 0 then return 0 end
                end
                local mode = redis.call('GET', KEYS[8]) or 'walk'
                if mode == 'off' then return 0 end
                local interval = mode == 'run' and tonumber(ARGV[4]) or tonumber(ARGV[3])
                local active = tonumber(redis.call('GET', KEYS[6]) or ARGV[2])
                if tonumber(ARGV[2]) - active < interval then return 0 end
                local tasks = cjson.decode(ARGV[1])
                local limit = math.min(#tasks, mode == 'run' and 2 or 1)
                for i = 1, limit do
                    if redis.call('EXISTS', KEYS[8 + 2 * i]) == 1 then return 0 end
                end
                local events = cjson.decode(ARGV[7])
                for i = 1, limit do
                    -- Empty queues and no running owner make stale pending leases recoverable.
                    redis.call('SET', KEYS[7 + 2 * i], '1', 'EX', 86400)
                    redis.call('SET', KEYS[8 + 2 * i], '1', 'EX', ARGV[5])
                    redis.call('RPUSH', KEYS[4], cjson.encode(tasks[i]))
                    redis.call('PUBLISH', ARGV[6], cjson.encode(events[i]))
                end
                local checkpoints = cjson.decode(ARGV[8])
                redis.call('SET', KEYS[7], cjson.encode(checkpoints[limit]))
                return limit
            ''', 8 + len(leases), *self.queues, QUEUE, 'styler:worker-running', ACTIVITY, STATE, MODE, *leases,
                json.dumps(tasks), now, IDLE_SECONDS, MODE_INTERVALS['run'], RETRY_SECONDS, 'styler_sample_events',
                json.dumps([{**task, 'state': 'job_queued'} for task in tasks]), json.dumps(checkpoints)))
            if not accepted:
                # User work won the race; revisit this exact candidate after the next quiet period.
                self.state = checkpoint
                self.stockpile_paths = None
                return None
            if accepted < len(candidates):
                # A Run -> Walk change during inspection may admit only the first task.
                self.state = checkpoints[accepted - 1]
                self.stockpile_paths = None
            return tasks[0]

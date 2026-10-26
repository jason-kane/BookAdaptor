"""Per-image evidence for tags promoted only by three-engine consensus."""
from styler.queue_history import publish_queue_event
import ast
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import uuid

ANALYSIS_VERSION = 'visual-style-tags-v7-style-description'
QUEUE = 'styler_image_tag_jobs'
PRIORITY_QUEUE = 'styler_image_tag_priority_jobs'
EVENTS = 'styler_sample_events'
log = logging.getLogger(__name__)


def sample_image_path(library_dir, style_tag, engine, base_style_prompt='classic'):
    directory = Path(library_dir) / 'style_exploration'
    if base_style_prompt == 'kid_friendly':
        directory /= base_style_prompt
    return directory / f'{style_tag}_{engine}.png'


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pop_tag_job(client, queue, style_tag=None):
    """Finish queued images for the current style before taking another style."""
    payload = client.eval('''
        if ARGV[1] ~= '' then
            for _, payload in ipairs(redis.call('LRANGE', KEYS[1], 0, -1)) do
                local job = cjson.decode(payload)
                if job.style_tag == ARGV[1] then
                    redis.call('LREM', KEYS[1], 1, payload)
                    return payload
                end
            end
        end
        return redis.call('LPOP', KEYS[1])
    ''', 1, queue, style_tag or '')
    return json.loads(payload) if payload else None


def tag_payload(text):
    """Read a structured result without repairing or inventing tag contents."""
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip().lstrip('\ufeff')
    # Models sometimes surround the result with Markdown or explanatory prose.
    start = re.search(r'[\[{]', text)
    if not start:
        raise ValueError('Image analysis must return a tags array')
    text = text[start.start():]
    cleaned = []
    quote = None
    escaped = False
    depth = 0
    for index, char in enumerate(text):
        if quote:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in ('"', "'"):
            quote = char
        elif char in '[{':
            depth += 1
        elif char in ']}':
            depth -= 1
        elif char == ',' and text[index + 1:].lstrip().startswith((']', '}')):
            continue
        cleaned.append(char)
        if depth == 0:
            break
    payload = ''.join(cleaned)
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Also accept Python-style single-quoted lists/dicts; never execute text.
        try:
            return ast.literal_eval(payload)
        except (ValueError, SyntaxError):
            raise ValueError('Image analysis must return a valid tags array') from None


def parse_tags(text):
    result = tag_payload(text)
    values = result.get('tags') if isinstance(result, dict) else result
    if not isinstance(values, list) or not all(isinstance(tag, str) for tag in values):
        raise ValueError('Image analysis must return a tags array')
    tags = list(dict.fromkeys(' '.join(tag.lower().split()) for tag in values))
    if len(tags) > 8 or any(not tag or len(tag) > 80 for tag in tags):
        raise ValueError('Image analysis must return up to 8 distinct short tags')
    return tags


def parse_analysis(text):
    """Parse style tags and the character's relationship to the artwork."""
    result = tag_payload(text)
    if not isinstance(result, dict):
        raise ValueError('Image analysis must include character_placement')
    tags = parse_tags(text)
    placement = result.get('character_placement')
    if placement == 'in_front':  # Accept responses from the previous schema.
        placement = 'outside_image'
    if placement not in ('in_image', 'outside_image', 'not_visible', 'uncertain'):
        raise ValueError('Image analysis must classify character_placement')
    return dict(tags=tags, character_placement=placement)


def _current_style_description(style):
    description = str(style.style.get('historical_description', '')).strip()
    if not description:
        return ''
    from styler.style_description import _source
    _, signature = _source(style)
    if style.style.get('historical_description_signature') != signature:
        return ''
    return description


def schedule(client, task, path, *, force=False, priority=False):
    from styler.style_duplicates import style_merge_locked
    if style_merge_locked(client, task['style_tag']):
        return []
    from styler.tag_consensus import prompt_version
    from styler.tag_consensus import style_key
    if client.sismember('ABYSS/tags', style_key(task['style_tag'])):
        return []
    version = prompt_version(client, task['style_tag'])
    digest = fingerprint(path)
    record = client.get('styler:image-tags:' + task['job_id'])
    if isinstance(record, (str, bytes)):
        record = json.loads(record)
    if not force and isinstance(record, dict):
        if (record.get('sha256') == digest and record.get('tags')
                and record.get('analysis_version') == ANALYSIS_VERSION):
            return record['tags']
    pending = f"styler:tag-pending:{task['job_id']}:{digest}:{ANALYSIS_VERSION}"
    job = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
    # Sample identities include the selected base prompt. Preserve it on the
    # queued tag job so validation does not silently assume ``classic``.
    if task.get('base_style_prompt'):
        job['base_style_prompt'] = task['base_style_prompt']
    job.update(action='styler_tag_sample', sha256=digest, prompt_sha256=version, analysis_version=ANALYSIS_VERSION)
    if priority:
        job['priority'] = True
        # Promote an already queued analysis atomically. A pending job absent
        # from both queues is running and must not be submitted twice.
        queued = client.eval('''
            for i = 2, 3 do
                for _, payload in ipairs(redis.call('LRANGE', KEYS[i], 0, -1)) do
                    local job = cjson.decode(payload)
                    if job.job_id == ARGV[2] and job.sha256 == ARGV[3]
                        and job.analysis_version == ARGV[4] then
                        redis.call('LREM', KEYS[i], 1, payload)
                        job.priority = true
                        redis.call('LPUSH', KEYS[3], cjson.encode(job))
                        return 1
                    end
                end
            end
            if redis.call('SET', KEYS[1], '1', 'NX', 'EX', 86400) then
                redis.call('LPUSH', KEYS[3], ARGV[1])
                return 1
            end
            return 0
        ''', 3, pending, QUEUE, PRIORITY_QUEUE, json.dumps(job),
                    task['job_id'], digest, ANALYSIS_VERSION)
        if queued:
            client.publish(EVENTS, json.dumps(dict(
                state='job_queued', job_id=task['job_id'], job_type='Image tags',
                action='styler_tag_sample', style_tag=task['style_tag'],
                engine=task['engine'], priority=True,
                base_style_prompt=task.get('base_style_prompt', 'classic'))))
        if client.exists('styler:worker-running'):
            log.warning('Tag task blocked by running task')
        return []
    if client.set(pending, '1', nx=True, ex=86400):
        try:
            client.rpush(QUEUE, json.dumps(job))
            client.publish(EVENTS, json.dumps(dict(
                state='job_queued', job_id=task['job_id'], job_type='Image tags',
                action='styler_tag_sample', style_tag=task['style_tag'],
                engine=task['engine'],
                base_style_prompt=task.get('base_style_prompt', 'classic'))))
        except Exception:
            client.delete(pending)
            raise
    return []


def request_retag(client, style_tag, library_dir, engine=None, base_style_prompt='classic'):
    from styler.style_duplicates import style_merge_locked
    if style_merge_locked(client, style_tag):
        raise ValueError('This style is being merged; try again when the scan finishes')
    from styler.jobs import sample_task
    from styler.tag_consensus import style_key
    key = style_key(style_tag)
    if client.sismember('ABYSS/tags', key):
        raise ValueError('Style is in ABYSS; add a tag manually to resume processing')
    client.delete('styler:auto-retag:' + key)
    engines = ('zit', 'krea2', 'qwen')
    if engine is not None:
        if engine not in engines:
            raise ValueError('Unknown sample engine')
        engines = (engine,)
    tasks = [sample_task(dict(style_tag=style_tag, engine=name,
                              base_style_prompt=base_style_prompt)) for name in engines]
    existing = []
    for task in reversed(tasks):
        path = sample_image_path(library_dir, style_tag, task['engine'], base_style_prompt)
        if path.is_file():
            schedule(client, task, path, force=True, priority=True)
            existing.insert(0, task)
    if not existing:
        raise ValueError('No sample images found')
    return existing


def reconcile_style_on_detail(client, style_tag, library_dir):
    """Reconcile complete current image-tag evidence when style details open."""
    from styler.jobs import sample_task
    from styler.sample_metadata import image_tags
    from styler.tag_consensus import ENGINES, style_key
    key = style_key(style_tag)
    was_lost = bool(client.sismember('LOST/tags', key))
    if client.sismember('ABYSS/tags', key):
        return None
    for base_style_prompt in ('classic', 'kid_friendly'):
        for engine in ENGINES:
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            task = sample_task(dict(style_tag=style_tag, engine=engine,
                                    base_style_prompt=base_style_prompt))
            if (not path.is_file() or not image_tags(path)
                    or not _analysis_record_current(client, task, path)):
                return None
    from styler.tag_consensus import reconcile
    agreement = reconcile(client, style_tag, library_dir)
    has_tags = client.scard(key + '/tags') > 0
    lost_count = client.scard('LOST/tags')
    if was_lost and has_tags:
        return dict(state='lost_resolved', style_tag=style_tag, lost_count=lost_count,
                    tags=agreement['tags'], added=agreement['added'],
                    removed=agreement['removed'], counts=agreement['counts'])
    if not agreement['added'] and not agreement['removed']:
        return None
    return dict(state='style_tags_reconciled', style_tag=style_tag,
                tags=agreement['tags'], added=agreement['added'],
                removed=agreement['removed'], counts=agreement['counts'],
                lost=bool(client.sismember('LOST/tags', key)), lost_count=lost_count)


def request_missing_tags(client, style_tag, library_dir):
    """Queue missing engine analyses at the head of the UI priority queue."""
    from styler.style_duplicates import style_merge_locked
    if style_merge_locked(client, style_tag):
        raise ValueError('This style is being merged; try again when the scan finishes')
    from styler.jobs import sample_task
    from styler.sample_metadata import image_tags
    from styler.tag_consensus import style_key
    if client.sismember('ABYSS/tags', style_key(style_tag)):
        return []
    tasks = []
    for base_style_prompt in ('classic', 'kid_friendly'):
        for engine in ('qwen', 'krea2', 'zit'):
            task = sample_task(dict(style_tag=style_tag, engine=engine,
                                    base_style_prompt=base_style_prompt))
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file() and (not image_tags(path)
                                   or not _analysis_record_current(client, task, path)):
                schedule(client, task, path, force=True, priority=True)
                tasks.insert(0, task)
    return tasks


def _analysis_record_current(client, task, path):
    """Whether saved, nonempty engine tags still belong to this image."""
    raw = client.get('styler:image-tags:' + task['job_id'])
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return False
    try:
        record = json.loads(raw)
    except ValueError:
        return False
    return (record.get('sha256') == fingerprint(path)
            and record.get('analysis_version') == ANALYSIS_VERSION
            and bool(record.get('tags')))


def auto_retag_if_empty(client, style_tag, library_dir):
    """Prioritize fresh analysis of each visible sample without display tags."""
    from styler.jobs import sample_task
    from styler.tag_consensus import style_key
    from styler.sample_metadata import image_tags

    key = style_key(style_tag)
    if client.sismember('ABYSS/tags', key):
        return False
    samples = []
    for base_style_prompt in ('classic', 'kid_friendly'):
        for engine in ('qwen', 'krea2', 'zit'):
            task = sample_task(dict(style_tag=style_tag, engine=engine,
                                    base_style_prompt=base_style_prompt))
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file() and not image_tags(path):
                marker = f'styler:auto-retag:{key}:{base_style_prompt}:{engine}'
                if not client.set(marker, '1', nx=True, ex=21600):
                    continue
                try:
                    schedule(client, task, path, force=True, priority=True)
                except Exception:
                    client.delete(marker)
                    raise
                samples.append((task, path))
    if not samples:
        return False
    client.publish(EVENTS, json.dumps(dict(
        style_tag=style_tag, state='retag_queued',
        engines=[task['engine'] for task, _ in samples], automatic=True,
        base_style_prompts=sorted({task['base_style_prompt'] for task, _ in samples}))))
    return True


def prioritize_style_tags(client, style_tag, library_dir):
    """Promote stale or missing tag analyses for a chosen style's samples."""
    from styler.jobs import sample_task
    queued = []
    for base_style_prompt in ('classic', 'kid_friendly'):
        for engine in ('qwen', 'krea2', 'zit'):
            task = sample_task(dict(style_tag=style_tag, engine=engine,
                                    base_style_prompt=base_style_prompt))
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file():
                schedule(client, task, path, priority=True)
                queued.insert(0, task)
    return queued


def analyze(path, *, workflow_name='styler-image-tags', parser=parse_analysis, label=None,
            style_name='', style_description='', on_status=None):
    import comfy
    import const
    name = 'styler_tags_' + uuid.uuid4().hex
    image = Path(const.COMFY_DIRS['artifactserver']['INPUT_DIR']) / (name + '.png')
    output_dir = Path(const.COMFY_DIRS['artifactserver']['OUTPUT_DIR'])
    try:
        shutil.copyfile(path, image)
        for attempt in range(2):
            output_prefix = f'{name}_{attempt}'
            workflow = comfy.load_workflow_template('api', 'i2json', workflow_name)
            if attempt:
                graph = workflow.workflow if hasattr(workflow, 'workflow') else workflow
                for node in graph.values():
                    if node.get('class_type') == 'Qwen2_5_VL_GGUF_Describe_Image':
                        inputs = node.get('inputs', {})
                        inputs['temperature'] = 0.2
                        inputs['prompt'] += (
                            ' Reinspect the image carefully. The previous attempt returned no usable tags. '
                            'Return useful tags for clearly visible style or image features; abstain only '
                            'if the image truly cannot be assessed.'
                        )
                        break
            # Qwen GGUF inference bypasses ComfyUI's model manager. Clear
            # resident diffusion/text models first, then clear the GGUF model
            # from the execution cache after the tag request so the next image
            # generation has the GPU back.
            if on_status:
                on_status('clearing_vram', 'Clearing VRAM')
            comfy.free_vram()
            if on_status:
                on_status('running', 'Running image analysis')
            try:
                comfy.run_workflow(workflow, template_environment={
                    'IMAGE': image.name, 'OUTPUT_PREFIX': output_prefix,
                    'STYLE_NAME': style_name,
                    'STYLE_DESCRIPTION': style_description,
                })
            finally:
                if on_status:
                    on_status('clearing_vram', 'Clearing VRAM')
                comfy.free_vram()
            if on_status:
                on_status('running', 'Matching image tags')
            outputs = list(output_dir.glob(output_prefix + '*.txt'))
            if len(outputs) != 1:
                if attempt:
                    raise ValueError('Image tag workflow did not produce one text result')
                log.warning('Image analysis produced no single result for %s; retrying once',
                            label or Path(path).name)
                continue
            try:
                result = parser(outputs[0].read_text())
            except ValueError:
                if attempt:
                    raise
                log.warning('Image analysis returned an invalid result for %s; retrying once',
                            label or Path(path).name)
                continue
            empty = result == [] or (isinstance(result, dict) and result.get('tags') == [])
            if empty and not attempt:
                log.warning('Image analysis returned no tags for %s; retrying once',
                            label or Path(path).name)
                continue
            return result
        return []
    finally:
        image.unlink(missing_ok=True)
        for output in output_dir.glob(name + '*.txt'):
            output.unlink(missing_ok=True)


def run_job(client, task):
    import const
    from styler.jobs import sample_task
    validated = sample_task(task)
    # Older queued jobs omitted this field even though their identity was
    # created from a kid_friendly sample. Infer that legacy identity only
    # when the classic identity does not match.
    if (validated['job_id'] != task['job_id']
            and 'base_style_prompt' not in task):
        validated = sample_task(dict(task, base_style_prompt='kid_friendly'))
    if validated['job_id'] != task['job_id']:
        raise ValueError('Invalid sample identity')
    base_style_prompt = task.get('base_style_prompt', 'classic')
    path = sample_image_path(const.LIBRARY_DIR, task['style_tag'], task['engine'], base_style_prompt)
    pending = f"styler:tag-pending:{task['job_id']}:{task['sha256']}:{task.get('analysis_version', ANALYSIS_VERSION)}"
    deferred = False

    def publish_status(state, message):
        running_key = 'styler:worker-running'
        raw_running = client.get(running_key)
        if raw_running:
            try:
                running_task = json.loads(raw_running)
            except (TypeError, ValueError):
                running_task = None
            if isinstance(running_task, dict) and running_task.get('job_id') == task['job_id']:
                running_task.update(state=state, message=message)
                client.set(running_key, json.dumps(running_task), ex=86400)
        client.publish(EVENTS, json.dumps(dict(
            job_id=task['job_id'], style_tag=task['style_tag'], engine=task['engine'],
            base_style_prompt=base_style_prompt, action='styler_tag_sample',
            state=state, message=message, job_type='Image tags',
            priority=bool(task.get('priority')))))

    try:
        publish_status('preparing', 'Preparing image tags')
        from styler.tag_consensus import style_key
        if client.sismember('ABYSS/tags', style_key(task['style_tag'])):
            event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
            event['base_style_prompt'] = base_style_prompt
            event['priority'] = bool(task.get('priority'))
            event.update(state='tag_abyss', message='Style requires manual tag repair')
            publish_queue_event(client, EVENTS, event)
            return
        if not path.is_file():
            raise FileNotFoundError(f"Sample image no longer exists: {path.name}")
        if fingerprint(path) != task['sha256']:
            schedule(client, task, path)
            event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
            event['base_style_prompt'] = base_style_prompt
            event['priority'] = bool(task.get('priority'))
            event.update(state='tag_failed',
                         message='Sample image changed while tags were being generated; newer analysis queued')
            publish_queue_event(client, EVENTS, event)
            return
        from styler.styles import Style
        style = Style(task['style_tag'])
        style_description = _current_style_description(style)
        if not style_description:
            if task.get('description_waited'):
                raise ValueError('Style description generation failed; image tagging is waiting for a current description')
            from styler.style_description import request as request_description
            dependency = request_description(client, task['style_tag'])
            if dependency.get('state') != 'complete':
                resumed_task = dict(task, description_waited=True)
                target_queue = PRIORITY_QUEUE if task.get('priority') else QUEUE
                client.rpush(target_queue, json.dumps(resumed_task, ensure_ascii=False))
                deferred = True
                client.publish(EVENTS, json.dumps(dict(
                    state='job_queued', job_id=task['job_id'], job_type='Image tags',
                    action='styler_tag_sample', style_tag=task['style_tag'],
                    engine=task['engine'], priority=bool(task.get('priority')),
                    base_style_prompt=base_style_prompt,
                    depends_on=dependency.get('job_id'), dependency_type='Historical description')))
                return
            style = Style(task['style_tag'])
            style_description = _current_style_description(style)
            if not style_description:
                raise ValueError('Style description task completed without a current description')
        from styler.tag_consensus import prompt_version, reconcile
        version = prompt_version(client, task['style_tag'])
        from tag_matching import match_tags
        analysis = analyze(
            path, label=f"{task['style_tag']}/{task['engine']}",
            style_name=style.name, style_description=style_description,
            on_status=publish_status)
        if isinstance(analysis, dict):
            raw_tags = analysis.get('tags', [])
            character_placement = analysis.get('character_placement')
        else:  # Keep custom analyzers and older integrations compatible.
            raw_tags = analysis
            character_placement = None
        matching = match_tags(client, const.LIBRARY_DIR, raw_tags,
                              style_name=task['style_tag'], engine=task['engine'])
        tags = matching['tags']
        # Do not attach an old analysis to a replacement image.
        if fingerprint(path) != task['sha256'] or prompt_version(client, task['style_tag']) != version:
            schedule(client, task, path)
            event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
            event['base_style_prompt'] = base_style_prompt
            event['priority'] = bool(task.get('priority'))
            event.update(state='tag_failed',
                         message='Sample image or tag prompt changed during analysis; newer analysis queued')
            publish_queue_event(client, EVENTS, event)
            return
        image_tier = {'outside_image': 1, 'in_image': 3}.get(character_placement)
        tier_applied = False
        if image_tier is not None:
            style.set_engine_tier(task['engine'], image_tier, source='image-analysis')
            tier_applied = True
        record = {key: task[key] for key in ('job_id', 'style_tag', 'engine', 'sha256')}
        record['base_style_prompt'] = base_style_prompt
        record.update(tags=tags, prompt_sha256=version, analysis_version=ANALYSIS_VERSION)
        record.update(matching)
        if character_placement:
            record['character_placement'] = character_placement
        if image_tier is not None:
            record['image_tier'] = image_tier
        record_key = 'styler:image-tags:' + task['job_id']
        old = client.get(record_key)
        old_tags = json.loads(old).get('tags', []) if isinstance(old, (str, bytes)) else []
        with client.pipeline() as pipe:
            for tag in old_tags:
                pipe.srem('styler:image-tag:' + tag, task['job_id'])
            pipe.set(record_key, json.dumps(record))
            if tags:
                pipe.sadd('styler:image-tag-names', *tags)
            for tag in tags:
                pipe.sadd('styler:image-tag:' + tag, task['job_id'])
            pipe.execute()
        sidecar = path.with_suffix('.tags.json')
        temporary = sidecar.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(record, indent=2) + '\n')
        temporary.chmod(0o644)
        temporary.replace(sidecar)
        from styler.sample_metadata import write_metadata, image_tags
        write_metadata(path, task, record=record, digest=task['sha256'])
        event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
        event['base_style_prompt'] = base_style_prompt
        event['priority'] = bool(task.get('priority'))
        if character_placement:
            event['character_placement'] = character_placement
        if tier_applied:
            event['image_tier'] = image_tier
            event['tier_badge_html'] = style.engine_tier_badge(task['engine'])
        if not tags and client.sismember('LOST/tags', style_key(task['style_tag'])):
            style = style_key(task['style_tag'])
            with client.pipeline() as pipe:
                pipe.srem('LOST/tags', style)
                pipe.zrem('LOST/index', style)
                pipe.sadd('ABYSS/tags', style)
                pipe.sadd('tags', 'ABYSS')
                pipe.execute()
            if client.scard('LOST/tags') == 0:
                client.srem('tags', 'LOST')
                client.lrem('categories', 0, 'LOST')
            event.update(state='tag_abyss', image_tags=[],
                         message='Style moved to ABYSS for manual tag repair')
            publish_queue_event(client, EVENTS, event)
            return
        agreement = reconcile(client, task['style_tag'], const.LIBRARY_DIR)
        lost_style = style_key(task['style_tag'])
        event.update(state='prompt_tagged', image_tags=image_tags(path), **agreement,
                     lost=bool(client.sismember('LOST/tags', lost_style)),
                     lost_count=client.scard('LOST/tags'))
        publish_queue_event(client, EVENTS, event)
    except Exception as exc:
        log.exception('Sample image tagging failed for %s/%s', task['style_tag'], task['engine'])
        event = {key: task[key] for key in ('job_id', 'style_tag', 'engine')}
        event['base_style_prompt'] = base_style_prompt
        event['priority'] = bool(task.get('priority'))
        event.update(state='tag_failed', message=str(exc) or 'Image tagging failed')
        publish_queue_event(client, EVENTS, event)
    finally:
        if not deferred:
            client.delete(pending)

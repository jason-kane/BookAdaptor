"""Durable AI Toolkit training jobs, consumed serially by the local GPU worker."""
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from contextlib import contextmanager

from . import lora_wizard as wizard

ACTIVE = ('queued', 'waiting_for_gpu', 'training')
STEPS_PER_IMAGE = 40
MIN_TRAINING_STEPS = 1200
MAX_TRAINING_STEPS = 2000


def training_steps(image_count):
    return max(MIN_TRAINING_STEPS, min(MAX_TRAINING_STEPS, image_count * STEPS_PER_IMAGE))


@contextmanager
def locked(book, draft_id):
    path = wizard.directory(book, draft_id) / '.lock'
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def training_config(book, draft):
    folder = wizard.directory(book, draft['id']).resolve()
    name = 'book_lora_' + draft['id'].replace('-', '')
    steps = training_steps(len(draft['images']))
    return {'job': 'extension', 'config': {'name': name, 'process': [{
        'type': 'sd_trainer', 'training_folder': str(folder / 'output'), 'device': 'cuda:0',
        'trigger_word': draft['trigger'],
        'network': {'type': 'lora', 'linear': 32, 'linear_alpha': 32},
        'save': {'dtype': 'bf16', 'save_every': 250, 'max_step_saves_to_keep': 2},
        'datasets': [{'folder_path': str(folder / 'dataset'), 'caption_ext': 'txt',
                      'caption_dropout_rate': .05, 'cache_latents_to_disk': True,
                      'cache_text_embeddings': True,
                      'resolution': [768], 'square_crop': True}],
        'train': {'batch_size': 1, 'steps': steps, 'gradient_accumulation_steps': 1,
                  'gradient_checkpointing': True, 'noise_scheduler': 'flowmatch',
                  'timestep_type': 'weighted', 'optimizer': 'adafactor', 'optimizer_params': {
                      'relative_step': False, 'scale_parameter': False, 'warmup_init': False}, 'lr': .0003,
                  'dtype': 'bf16', 'disable_sampling': True},
        'model': {'name_or_path': 'Tongyi-MAI/Z-Image-Turbo', 'arch': 'zimage:turbo',
                  'assistant_lora_path': 'ostris/zimage_turbo_training_adapter/zimage_turbo_training_adapter_v2.safetensors',
                  'quantize': True, 'qtype': 'qfloat8', 'quantize_te': True, 'low_vram': True,
                  'layer_offloading': True, 'layer_offloading_transformer_percent': 1.0,
                  'layer_offloading_text_encoder_percent': 1.0},
        'sample': {'sampler': 'flowmatch', 'sample_every': 250, 'width': 1024,
                   'height': 1024, 'guidance_scale': 1, 'sample_steps': 9,
                   'prompts': [draft['trigger'] + ', ' + draft['concept']]},
    }]}}


def enqueue(book, draft_id):
    with locked(book, draft_id):
        draft = wizard.load(book, draft_id)
        if draft['status'] in (*ACTIVE, 'complete'):
            return draft
        if not wizard.MIN_TRAINING_IMAGES <= len(draft['images']) <= wizard.MAX_TRAINING_IMAGES:
            raise ValueError('Training requires between 30 and 50 source images.')
        if draft.get('step') != 4 or not draft['images'] or any(not i['caption'].strip() for i in draft['images']):
            raise ValueError('Complete all captions before training.')
        folder = wizard.directory(book, draft_id)
        for item in draft['images']:
            image = folder / 'dataset' / item['filename']
            if not image.is_file():
                raise ValueError('A reference image is missing. Add it again before training.')
            image.with_suffix('.txt').write_text(wizard.caption_text(item, draft['trigger']) + '\n')
        (folder / 'training.json').write_text(json.dumps(training_config(book, draft), indent=2))
        from uuid import uuid4
        draft.update(status='queued', queued_at=time.time(), error=None, training_job_id=uuid4().hex)
        draft.pop('started_at', None)
        draft.pop('failed_at', None)
        draft.pop('traceback', None)
        wizard.save(book, draft)
        return draft


def gpu_available():
    """Wait for all other GPU compute processes instead of competing with inference."""
    result = subprocess.run(['nvidia-smi', '--query-compute-apps=pid', '--format=csv,noheader,nounits'],
                            capture_output=True, text=True, check=True, timeout=15)
    return not result.stdout.strip()


def register(book, draft):
    import const
    from artifact_editor import config
    folder = wizard.directory(book, draft['id'])
    job_name = training_config(book, draft)['config']['name']
    # Only the final named adapter is accepted, never an intermediate checkpoint.
    output = folder / 'output' / job_name / (job_name + '.safetensors')
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError('AI Toolkit exited without producing its final LoRA adapter. See the training log.')
    filename = job_name + '.safetensors'
    destination = Path(const.LORA_DIR) / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix('.tmp')
    shutil.copyfile(output, temporary)
    os.replace(temporary, destination)
    destination.with_name(filename + '.json').write_text(json.dumps({
        'name': draft['name'], 'model': 'z-image-turbo', 'weight': 1,
        'trigger_words': [draft['trigger']], 'training_draft': draft['id'],
        'trainer': 'ostris/ai-toolkit'}, indent=2))
    from artifact_editor.loras import catalog
    existing_same_name = {
        item['filename'] for item in catalog()
        if item['filename'] != filename
        and (item.get('name') or '').strip().casefold() == draft['name'].strip().casefold()
    }
    # Re-training a style under the same name replaces its prior default LoRA.
    with (Path(const.LIBRARY_DIR) / book.bookdir / '.lora-defaults.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        settings = config.get_config(book.bookdir)
        defaults = [name for name in settings.get('default_loras', []) if name not in existing_same_name]
        settings['default_loras'] = list(dict.fromkeys([*defaults, filename]))
        config.save_config(book.bookdir, settings)
    catalog()
    # A trained style LoRA is part of that style everywhere it is selected.
    # Style records store their LoRA filenames as a JSON list.
    from styler.styles import Style, as_id
    style_key = as_id(draft['name'])
    try:
        style = Style(style_key)
    except ValueError:
        style = None
    if style is not None:
        linked = style.style.get('loras', '[]')
        try:
            linked = json.loads(linked) if isinstance(linked, str) else linked
        except json.JSONDecodeError:
            linked = []
        if not isinstance(linked, list):
            linked = []
        linked = [name for name in linked if name not in existing_same_name]
        style.style['loras'] = json.dumps(list(dict.fromkeys([*linked, filename])))
        style.save_to_redis()
    draft.update(status='complete', completed_at=time.time(), filename=filename)
    for key in ('error', 'failed_at', 'traceback'):
        draft.pop(key, None)
    wizard.save(book, draft)


def training_failure_details(folder):
    """Keep the trainer traceback (including chained exceptions), bounded for queue payloads."""
    import re
    try:
        log = Path(folder) / 'training.log'
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 65536))
            tail = stream.read().decode('utf-8', errors='replace')
        tail = re.sub(r'\x1b\[[0-9;?]*[ -/]*[@-~]', '', tail)
        start = tail.find('Traceback (most recent call last):')
        details = tail[start:].strip() if start >= 0 else tail.strip()
        errors = re.findall(r'^([\w.]+(?:Error|Exception):[^\r\n]*)', details, re.MULTILINE)
        return (errors[-1] if errors else None), details
    except OSError:
        return None, ''


def run_job(book, draft_id, toolkit):
    with locked(book, draft_id):
        draft = wizard.load(book, draft_id)
        if draft['status'] not in ('queued', 'waiting_for_gpu'):
            return
        try:
            if not gpu_available():
                draft['status'] = 'waiting_for_gpu'
                wizard.save(book, draft)
                return
            draft.update(status='training', started_at=time.time(), error=None)
            wizard.save(book, draft)
            folder = wizard.directory(book, draft_id)
            with (folder / 'training.log').open('w') as log:
                subprocess.run([sys.executable, '-u', str(toolkit / 'run.py'), str(folder / 'training.json')],
                               cwd=toolkit, stdout=log, stderr=subprocess.STDOUT, check=True)
            register(book, draft)
        except Exception as error:
            summary, details = training_failure_details(wizard.directory(book, draft_id))
            draft.update(status='failed', error=summary or str(error), failed_at=time.time(),
                         traceback=details if isinstance(error, subprocess.CalledProcessError) and details else traceback.format_exc())
            wizard.save(book, draft)


def main():
    import const
    import redis
    from types import SimpleNamespace
    toolkit = Path(os.environ.get('AI_TOOLKIT_DIR', '/opt/ai-toolkit')).resolve()
    if not (toolkit / 'run.py').is_file():
        raise SystemExit('AI_TOOLKIT_DIR must point to an installed Ostris AI Toolkit checkout.')
    root = Path(const.LIBRARY_DIR).resolve()
    client = redis.Redis(host=os.environ.get('REDIS_HOST', 'redis'), decode_responses=True)
    # The worker-wide lock prevents multiple training workers from sharing this GPU.
    with (root / '.lora-training-worker.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        recovered = False
        while True:
            for path in sorted(root.glob('*/*/lora_training/*/draft.json')):
                book = SimpleNamespace(bookdir=str(path.parents[2].relative_to(root)))
                try:
                    draft = wizard.load(book, path.parent.name)
                    if not recovered and draft['status'] == 'training':
                        draft.update(status='failed', failed_at=time.time(), error='Training worker restarted. Retry training; saved checkpoints remain in the output folder.')
                        wizard.save(book, draft)
                    if client.get('gpu:active-service') == 'training' and draft['status'] in ('queued', 'waiting_for_gpu'):
                        run_job(book, draft['id'], toolkit)
                except (ValueError, OSError) as error:
                    print(f'Cannot process LoRA draft {path}: {error}', flush=True)
            recovered = True
            time.sleep(5)



def training_progress(folder):
    """Read AI Toolkit's job-named tqdm counter, excluding download/cache counters."""
    import re
    folder = Path(folder)
    result = {'percent': 0, 'message': 'Preparing training: loading model and dataset',
              'current_step': 0, 'total_steps': None}
    try:
        config = json.loads((folder / 'training.json').read_text())['config']
        total = int(config['process'][0]['train']['steps'])
        if total <= 0:
            return result
        result['total_steps'] = total
        log = folder / 'training.log'
        with log.open('rb') as stream:
            stream.seek(max(0, log.stat().st_size - 131072))
            tail = stream.read().decode('utf-8', errors='replace')
        tail = re.sub(r'\x1b\[[0-9;?]*[ -/]*[@-~]', '', tail)
        # ToolkitProgressBar uses the configured job name as its description.
        pattern = re.escape(config['name']) + r':[^\r\n]*?(\d+)\s*/\s*' + str(total) + r'\b([^\r\n]*)'
        matches = list(re.finditer(pattern, tail))
        if matches:
            match = matches[-1]
            step = min(total, int(match.group(1)))
            message = f'Training LoRA: {step:,}/{total:,} steps'
            remaining = re.search(r'\[[^]<>]*<([^],]+)', match.group(2))
            if remaining and '?' not in remaining.group(1):
                message += f' · estimated remaining {remaining.group(1).strip()}'
            if step == total:
                message = f'Finishing training: {total:,}/{total:,} steps · saving adapter'
            result.update(current_step=step, percent=min(99, int(step * 100 / total)), message=message)
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        pass
    return result


def queue_entries(library_dir=None, now=None, bookdir=None):
    """Expose the dedicated trainer's durable jobs in the shared GPU queue."""
    import re
    import tomllib
    if library_dir is None:
        config_path = Path(__file__).resolve().parents[2] / 'configuration.toml'
        configuration = tomllib.loads(config_path.read_text()) if config_path.exists() else {}
        library_dir = configuration.get('LIBRARY_DIR', '/active')
    now = time.time() if now is None else now
    entries = []
    root = Path(library_dir) / bookdir if bookdir is not None else Path(library_dir)
    pattern = 'lora_training/*/draft.json' if bookdir is not None else '*/*/lora_training/*/draft.json'
    for path in root.glob(pattern):
        try:
            draft = json.loads(path.read_text())
            status = draft.get('status')
            if status not in (*ACTIVE, 'complete', 'failed'):
                continue
            finished = draft.get('completed_at') or draft.get('failed_at') or path.stat().st_mtime
            if status in ('complete', 'failed') and now - finished > (90 if status == 'complete' else 300):
                continue
            job_id = draft.get('training_job_id', 'lora-training-' + draft['id'])
            state = {'training': 'running', 'waiting_for_gpu': 'queued'}.get(status, status)
            message = {'queued': 'Waiting for the local AI Toolkit worker',
                       'waiting_for_gpu': 'Waiting for the GPU to be released',
                       'training': 'Training LoRA with AI Toolkit',
                       'complete': 'LoRA trained and selected for this book',
                       'failed': draft.get('error') or 'LoRA training failed'}[status]
            percent = 100 if status == 'complete' else 0
            progress = training_progress(path.parent) if status == 'training' else {}
            if progress:
                percent, message = progress['percent'], progress['message']
            entry = {'job_id': job_id, 'action': 'lora_training', 'job_type': 'Training LoRA',
                     'filename': draft['name'], 'label': 'Training LoRA · ' + draft['name'],
                     'provider': 'training', 'executor': 'ai-toolkit', 'externally_managed': True,
                     'state': state, 'message': message, 'percent': percent, 'priority': False,
                     'draft_id': draft['id'], 'queue_started_at': draft.get('started_at') or draft.get('queued_at')}
            if status == 'failed':
                summary, details = training_failure_details(path.parent)
                entry['message'] = summary or message
                entry['traceback'] = draft.get('traceback') or details or message
            if status in ('complete', 'failed'):
                entry['completed_at'] = finished
            entries.append(entry)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return entries


if __name__ == '__main__':
    main()

import json
import sys
from types import SimpleNamespace

import pytest
from artifact_editor.book import lora_wizard as wizard, lora_training as training


@pytest.fixture
def training_draft(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path), LORA_DIR=str(tmp_path / 'loras')))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Ink', 'concept': 'Ink drawings', 'trigger': 'bookink'})
    folder = wizard.directory(book, draft['id'])
    (folder / 'dataset').mkdir()
    (folder / 'dataset/a.png').write_bytes(b'normalized-image')
    draft.update(step=4, images=[{'id': 'a', 'filename': 'a.png', 'caption': 'bookink, tree'}])
    wizard.save(book, draft)
    return book, draft, folder


def test_enqueue_config_is_idempotent(training_draft):
    book, draft, folder = training_draft
    first = training.enqueue(book, draft['id'])
    second = training.enqueue(book, draft['id'])
    assert first['queued_at'] == second['queued_at']
    config = json.loads((folder / 'training.json').read_text())
    process = config['config']['process'][0]
    assert process['device'] == 'cuda:0'
    assert process['model']['arch'] == 'zimage:turbo'
    assert 'training_adapter_v2' in process['model']['assistant_lora_path']
    assert (folder / 'dataset/a.txt').read_text() == 'bookink, tree\n'


def test_register_recovers_finished_adapter_after_registration_failure(training_draft, monkeypatch):
    book, draft, folder = training_draft
    from artifact_editor import loras
    saved = {}
    monkeypatch.setitem(sys.modules, 'artifact_editor.config', SimpleNamespace(
        get_config=lambda path: {},
        save_config=lambda path, settings: saved.update(settings)))
    monkeypatch.setattr(loras, 'catalog', lambda: [])
    style = SimpleNamespace(style={'loras': '[]'}, save_to_redis=lambda: None)
    monkeypatch.setitem(sys.modules, 'styler.styles', SimpleNamespace(
        Style=lambda key: style, as_id=lambda name: name.lower()))
    job_name = training.training_config(book, draft)['config']['name']
    output = folder / 'output' / job_name
    output.mkdir(parents=True)
    (output / (job_name + '.safetensors')).write_bytes(b'finished adapter')
    draft.update(status='failed', error="No module named 'flask'", failed_at=1, traceback='old failure')
    training.register(book, draft)
    result = wizard.load(book, draft['id'])
    assert result['status'] == 'complete'
    assert not {'error', 'failed_at', 'traceback'} & result.keys()
    assert saved['default_loras'] == [result['filename']]
    assert json.loads(style.style['loras']) == [result['filename']]


def test_training_waits_for_gpu_then_records_failure(training_draft, monkeypatch):
    book, draft, folder = training_draft
    training.enqueue(book, draft['id'])
    monkeypatch.setattr(training, 'gpu_available', lambda: False)
    training.run_job(book, draft['id'], folder)
    assert wizard.load(book, draft['id'])['status'] == 'waiting_for_gpu'
    monkeypatch.setattr(training, 'gpu_available', lambda: True)
    def fail(*args, **kwargs):
        raise RuntimeError('trainer failed')
    monkeypatch.setattr(training.subprocess, 'run', fail)
    training.run_job(book, draft['id'], folder)
    result = wizard.load(book, draft['id'])
    assert result['status'] == 'failed'
    assert result['error'] == 'trainer failed'
    assert (folder / 'training.log').is_file()


def test_success_registers_final_adapter_and_selects_book(training_draft, monkeypatch, tmp_path):
    book, draft, folder = training_draft
    saved = {}
    monkeypatch.setitem(sys.modules, 'artifact_editor.config', SimpleNamespace(
        get_config=lambda path: {'default_loras': ['existing']},
        save_config=lambda path, value: saved.update(value)))
    from artifact_editor import loras
    monkeypatch.setattr(loras, 'catalog', lambda: [])
    monkeypatch.setattr(training, 'gpu_available', lambda: True)
    def train(command, **kwargs):
        name = training.training_config(book, draft)['config']['name']
        output = folder / 'output' / name
        output.mkdir(parents=True)
        (output / (name + '.safetensors')).write_bytes(b'trained adapter')
    monkeypatch.setattr(training.subprocess, 'run', train)
    training.enqueue(book, draft['id'])
    training.run_job(book, draft['id'], folder)
    result = wizard.load(book, draft['id'])
    assert result['status'] == 'complete'
    assert saved['default_loras'] == ['existing', result['filename']]
    assert (tmp_path / 'loras' / result['filename']).read_bytes() == b'trained adapter'
    assert json.loads((tmp_path / 'loras' / (result['filename'] + '.json')).read_text())['trigger_words'] == ['bookink']


def test_training_queue_reports_waiting_running_progress_and_outcome(training_draft):
    book, draft, folder = training_draft
    queued = training.enqueue(book, draft['id'])
    root = folder.parents[3]
    row = training.queue_entries(root)[0]
    assert row['job_id'] == queued['training_job_id']
    assert row['job_type'] == 'Training LoRA'
    assert row['state'] == 'queued'
    queued['status'] = 'waiting_for_gpu'
    wizard.save(book, queued)
    assert 'GPU to be released' in training.queue_entries(root)[0]['message']
    queued['status'] = 'training'
    wizard.save(book, queued)
    (folder / 'training.log').write_text(training.training_config(book, draft)['config']['name'] + ': 50%|###| 1500/3000 [01:00<01:00]\r')
    row = training.queue_entries(root)[0]
    assert row['state'] == 'running'
    assert row['percent'] == 50
    assert '1,500/3,000' in row['message']
    queued.update(status='failed', error='Out of VRAM', failed_at=100)
    wizard.save(book, queued)
    assert training.queue_entries(root, now=101)[0]['message'] == 'Out of VRAM'
    assert training.queue_entries(root, now=401) == []
    queued.update(status='complete', completed_at=500)
    wizard.save(book, queued)
    assert training.queue_entries(root, now=501)[0]['percent'] == 100
    assert training.queue_entries(root, now=591) == []


def test_progress_uses_configured_steps_and_ignores_other_counters(training_draft):
    book, draft, folder = training_draft
    config = training.training_config(book, draft)
    config['config']['process'][0]['train']['steps'] = 800
    (folder / 'training.json').write_text(json.dumps(config))
    log = folder / 'training.log'
    log.write_text('Caching latents: 100%|###| 800/800 [00:10<00:00]')
    assert training.training_progress(folder)['percent'] == 0
    name = config['config']['name']
    log.write_text(f'{name}: 25%|###| 200/800 [01:00<03:00, 3it/s]\r')
    progress = training.training_progress(folder)
    assert progress['percent'] == 25
    assert progress['current_step'] == 200
    assert '03:00' in progress['message']
    log.write_text(f'{name}: 100%|###| 800/800 [04:00<00:00]')
    assert training.training_progress(folder)['percent'] == 99
    assert 'saving adapter' in training.training_progress(folder)['message']


def test_failed_trainer_exposes_underlying_traceback(training_draft, monkeypatch):
    book, draft, folder = training_draft
    training.enqueue(book, draft['id'])
    monkeypatch.setattr(training, 'gpu_available', lambda: True)
    details = 'Traceback (most recent call last):\n  File "trainer.py", line 12, in train\n    load_model()\ntorch.OutOfMemoryError: CUDA out of memory.\n'
    def fail(command, **kwargs):
        kwargs['stdout'].write('Loading model\n' + details)
        kwargs['stdout'].flush()
        raise training.subprocess.CalledProcessError(1, command)
    monkeypatch.setattr(training.subprocess, 'run', fail)
    training.run_job(book, draft['id'], folder)
    saved = wizard.load(book, draft['id'])
    assert saved['error'] == 'torch.OutOfMemoryError: CUDA out of memory.'
    assert saved['traceback'] == details.strip()
    row = training.queue_entries(folder.parents[3])[0]
    assert row['traceback'] == details.strip()
    assert row['message'] == saved['error']
    # Existing failures from before traceback persistence also expose their log.
    saved.pop('traceback')
    saved['error'] = 'returned non-zero exit status 1'
    wizard.save(book, saved)
    row = training.queue_entries(folder.parents[3])[0]
    assert row['traceback'] == details.strip()
    assert row['message'] == 'torch.OutOfMemoryError: CUDA out of memory.'
    retried = training.enqueue(book, draft['id'])
    assert 'traceback' not in retried

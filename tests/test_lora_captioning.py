import sys
from types import SimpleNamespace
from unittest.mock import Mock

from artifact_editor.book import lora_captioning as captions, lora_wizard as wizard


def test_caption_worker_keeps_successes_and_retries_missing_only(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Ink', 'concept': 'Ink drawings', 'trigger': 'bookink'})
    folder = wizard.directory(book, draft['id']) / 'dataset'
    folder.mkdir()
    draft['images'] = [{'id': key, 'filename': key + '.png', 'caption': 'bookink'} for key in ('a', 'b')]
    draft.update(caption_job_id='job', caption_state='queued')
    wizard.save(book, draft)
    completion = Mock(return_value=['A black ink illustration of a tree.', RuntimeError('temporary error')])
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completions=completion))
    task = {'job_id': 'job', 'bookdir': book.bookdir, 'draft_id': draft['id']}
    import pytest
    with pytest.raises(RuntimeError, match='Some images'):
        captions.run_job(None, task)
    saved = wizard.load(book, draft['id'])
    assert saved['caption_state'] == 'failed'
    assert saved['images'][0]['caption_generated'] is True
    assert saved['images'][0]['caption'].startswith('bookink, ')
    assert not saved['images'][1].get('caption_generated')
    completion.return_value = ['bookink, an ink illustration of a bird.']
    captions.run_job(None, task)
    assert len(completion.call_args.args[1]) == 1
    saved = wizard.load(book, draft['id'])
    assert saved['caption_state'] == 'complete'
    assert all(item['caption_generated'] for item in saved['images'])
    assert (folder / 'a.txt').read_text().startswith('bookink, ')
    assert (folder / 'b.txt').read_text().startswith('bookink, ')


def test_stale_caption_job_cannot_overwrite_new_draft(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft['caption_job_id'] = 'new-job'
    wizard.save(book, draft)
    completion = Mock()
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completions=completion))
    captions.run_job(None, {'job_id': 'old-job', 'bookdir': book.bookdir, 'draft_id': draft['id']})
    completion.assert_not_called()


def test_caption_submission_is_idempotent_and_targets_local_vision(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft['images'] = [{'id': 'a', 'filename': 'a.png', 'caption': 'ink'}]
    wizard.save(book, draft)
    push = Mock()
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(enqueue_task=push, PRIORITY_QUEUE='priority'))
    first = captions.enqueue(book, draft['id'], client=Mock())
    second = captions.enqueue(book, draft['id'], client=Mock())
    assert first['caption_job_id'] == second['caption_job_id']
    push.assert_called_once()
    task = push.call_args.args[2]
    assert task['provider'] == 'llama'
    assert task['action'] == 'lora_captions'


def test_removed_image_result_is_ignored_during_caption_generation(tmp_path, monkeypatch):
    from werkzeug.datastructures import MultiDict
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft.update(step=3, caption_job_id='job', caption_state='queued', images=[
        {'id': name, 'filename': name + '.png', 'caption': 'ink'} for name in ('a', 'b')])
    wizard.save(book, draft)
    (wizard.directory(book, draft['id']) / 'dataset').mkdir()
    def generate(*args, **kwargs):
        current = wizard.load(book, draft['id'])
        wizard.remove_image(book, current, 'b', MultiDict(), {})
        return ['ink, a tree', RuntimeError('image removed during inference')]
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completions=generate))
    captions.run_job(None, {'job_id': 'job', 'bookdir': book.bookdir, 'draft_id': draft['id']})
    saved = wizard.load(book, draft['id'])
    assert saved['caption_state'] == 'complete'
    assert [item['id'] for item in saved['images']] == ['a']
    assert saved['images'][0]['caption'] == 'ink, a tree'


def test_wand_regenerates_only_target_with_lora_then_character_tokens(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Denslow', 'concept': 'Book illustrations', 'trigger': '[DENSLOW]'})
    draft.update(step=3, images=[
        {'id': 'a', 'filename': 'a.png', 'caption': 'old caption', 'caption_generated': True,
         'character_tags': ['tip'], 'characters': ['Tip']},
        {'id': 'b', 'filename': 'b.png', 'caption': 'keep this caption', 'caption_generated': True}])
    wizard.save(book, draft)
    folder = wizard.directory(book, draft['id']) / 'dataset'
    folder.mkdir()
    push = Mock()
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(enqueue_task=push, PRIORITY_QUEUE='priority'))
    queued = captions.enqueue(book, draft['id'], client=Mock(), image_id='a')
    assert queued['images'][0]['caption_generated'] is False
    assert queued['images'][1]['caption_generated'] is True
    completion = Mock(return_value=['A boy in a line drawing.'])
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completions=completion))
    task = push.call_args.args[2]
    assert task['image_ids'] == ['a']
    captions.run_job(None, task)
    assert len(completion.call_args.args[1]) == 1
    saved = wizard.load(book, draft['id'])
    assert saved['images'][0]['caption'] == '[DENSLOW] [Tip] A boy in a line drawing.'
    assert (folder / 'a.txt').read_text().strip() == saved['images'][0]['caption']
    assert saved['images'][1]['caption'] == 'keep this caption'
    assert saved['last_modified_image_id'] == 'a'


def test_required_tokens_are_ordered_and_not_duplicated():
    image = {'caption': '[Tip] [DENSLOW], A boy.', 'characters': ['Tip']}
    assert wizard.caption_text(image, '[DENSLOW]') == '[DENSLOW] [Tip] A boy.'
    wizard.set_characters(image, [], {})
    assert wizard.caption_text(image, '[DENSLOW]') == '[DENSLOW] A boy.'


def test_caption_progress_is_published_to_gpu_queue(monkeypatch):
    publisher = Mock()
    monkeypatch.setitem(sys.modules, 'styler.queue_history', SimpleNamespace(publish_queue_event=publisher))
    client = Mock()
    task = {'job_id': 'caption', 'action': 'lora_captions', 'provider': 'llama'}
    captions.report_progress(client, task, 'running', 50, 'Generating captions: 1/2')
    client.set.assert_called_once()
    event = publisher.call_args.args[2]
    assert publisher.call_args.args[1] == 'styler_sample_events'
    assert event['job_type'] == 'Generating Caption'
    assert event['provider'] == 'llama'
    assert event['state'] == 'running'
    assert event['percent'] == 50
    captions.report_progress(client, task, 'complete', 100, 'Caption generated and saved')
    assert publisher.call_args.args[2]['state'] == 'complete'


def test_bulk_captioning_skips_finished_and_manual_but_includes_character_only_edits(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    book = SimpleNamespace(bookdir='Writer/Book')
    draft = wizard.create(book, {'name': 'Denslow', 'concept': 'Illustrations', 'trigger': '[DENSLOW]'})
    draft.update(step=3, images=[
        {'id': 'untouched', 'filename': 'a.png', 'caption': '[DENSLOW]'},
        {'id': 'tagged', 'filename': 'b.png', 'caption': '[DENSLOW]',
         'caption_generated': True, 'characters': ['Tip'], 'character_tags': ['tip']},
        {'id': 'generated', 'filename': 'c.png', 'caption': '[DENSLOW] A finished drawing.', 'caption_generated': True},
        {'id': 'manual', 'filename': 'd.png', 'caption': 'My own caption', 'caption_generated': True, 'caption_edited': True}])
    wizard.save(book, draft)
    (wizard.directory(book, draft['id']) / 'dataset').mkdir()
    push = Mock()
    monkeypatch.setitem(sys.modules, 'task_queue', SimpleNamespace(enqueue_task=push, PRIORITY_QUEUE='priority'))
    captions.enqueue(book, draft['id'], client=Mock())
    task = push.call_args.args[2]
    assert task['image_ids'] == ['untouched', 'tagged']
    complete = Mock(return_value=['A landscape.', 'A boy.'])
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completions=complete))
    captions.run_job(None, task)
    saved = wizard.load(book, draft['id'])
    assert saved['images'][1]['caption'] == '[DENSLOW] [Tip] A boy.'
    assert saved['images'][2]['caption'] == '[DENSLOW] A finished drawing.'
    assert saved['images'][3]['caption'] == 'My own caption'
    push.reset_mock()
    captions.enqueue(book, draft['id'], client=Mock())
    push.assert_not_called()

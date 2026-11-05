"""Image classification must not spend its entire output budget on reasoning."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from styler import image_tags
from styler.jobs import sample_task


@pytest.fixture
def inference(tmp_path, monkeypatch):
    client = Mock()
    client.sismember.return_value = False
    style = SimpleNamespace(name='Artist', style_tag='artist')
    response = json.dumps({'tags': ['ink'], 'character_placement': 'not_visible', 'primary_category': 'Art'})
    single = Mock(return_value=response)
    batch = Mock(side_effect=lambda client, requests, **kw: [response for _ in requests])
    match = Mock(return_value={'tags': ['ink'], 'raw_tags': ['ink'], 'tag_matches': []})
    for name, module in {
        'const': SimpleNamespace(LIBRARY_DIR=str(tmp_path)),
        'llama_inference': SimpleNamespace(chat_completion=single, chat_completions=batch),
        'styler.styles': SimpleNamespace(Style=lambda _: style),
        'styler.topic_taxonomy': SimpleNamespace(cached_tree=lambda _: {'topics': [{'name': 'Art'}]}),
        'styler.tag_consensus': SimpleNamespace(style_key=lambda value: value, prompt_version=lambda *a: 'v1', reconcile=lambda *a: {}),
        'tag_matching': SimpleNamespace(match_tags=match),
        'styler.sample_metadata': SimpleNamespace(image_tags=lambda _: ['ink']),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(image_tags, '_current_style_description', lambda _: 'Historical style')
    monkeypatch.setattr(image_tags, '_ensure_style_description', lambda *a: (style, 'Historical style', False))
    monkeypatch.setattr(image_tags, '_primary_categories_for_style', lambda *a: ['Art'])
    monkeypatch.setattr(image_tags, '_save_analysis_record', Mock())
    monkeypatch.setattr(image_tags, 'publish_queue_event', Mock())
    tasks = []
    for engine in ('zit', 'krea2', 'qwen'):
        path = image_tags.sample_image_path(tmp_path, 'artist', engine)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(engine.encode())
        task = sample_task(dict(engine=engine, style_tag='artist'))
        task['sha256'] = image_tags.fingerprint(path)
        tasks.append(task)
    return SimpleNamespace(client=client, tasks=tasks, root=tmp_path, single=single, batch=batch)


def assert_final_json_budget(request):
    assert request['thinking_budget_tokens'] == 0
    assert request['max_tokens'] == 2048
    assert request['response_format'] == {'type': 'json_object'}


def test_every_batch_slot_reserves_final_json_budget(inference):
    fixture = inference
    results = image_tags.analyze_image_tag_batch(fixture.client, fixture.tasks, fixture.root)
    assert set(results) == {task['job_id'] for task in fixture.tasks}
    requests = fixture.batch.call_args.args[1]
    assert len(requests) == 3
    for request in requests:
        assert_final_json_budget(request)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_individual_retry_uses_same_final_json_budget(inference, index):
    fixture = inference
    assert image_tags.run_job(fixture.client, fixture.tasks[index]) is True
    assert_final_json_budget(fixture.single.call_args.kwargs)


def test_malformed_repetitive_output_retries_with_corrective_prompt(inference):
    fixture = inference
    good = fixture.single.return_value
    fixture.single.side_effect = ['{"tags": ["woodcut style", "woodcut style",', good]
    assert image_tags.run_job(fixture.client, fixture.tasks[0]) is True
    assert fixture.single.call_count == 2
    first, retry = fixture.single.call_args_list
    assert retry.args[1].startswith(first.args[1])
    assert 'do not cycle through repeated' in retry.args[1]
    assert_final_json_budget(retry.kwargs)


def test_invalid_retry_is_bounded_and_keeps_underlying_traceback(inference):
    fixture = inference
    fixture.single.return_value = '{"tags": ['
    errors = {}
    assert image_tags.run_job(fixture.client, fixture.tasks[0], failure_details=errors) is False
    assert fixture.single.call_count == 2
    assert 'ValueError: Model response did not contain valid JSON' in errors['zit']
    event = image_tags.publish_queue_event.call_args.args[2]
    assert event['traceback'] == errors['zit']


def test_partial_group_failure_retains_only_failed_members_and_diagnostic(inference, monkeypatch):
    fixture = inference
    monkeypatch.setattr(image_tags, 'analyze_image_tag_batch', lambda *args: {})
    def run(client, task, **kwargs):
        if task['engine'] == 'zit':
            kwargs['failure_details']['zit'] = 'ValueError: Model response did not contain valid JSON'
            return False
        return True
    monkeypatch.setattr(image_tags, 'run_job', run)
    image_tags._run_tag_group(fixture.client, {
        'job_id': 'group', 'style_tag': 'artist', 'items': fixture.tasks,
        'engines': ['zit', 'krea2', 'qwen'],
    }, fixture.root)
    event = image_tags.publish_queue_event.call_args.args[2]
    assert event['state'] == 'failed'
    assert event['engines'] == ['zit']
    assert event['items'] == [fixture.tasks[0]]
    assert '2 of 3 images completed' in event['message']
    assert 'ValueError: Model response did not contain valid JSON' in event['traceback']

"""WebSocket illustration notifications and read-only reconnect snapshots."""
import importlib.util
import ast
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import time

import pytest
from bs4 import BeautifulSoup
from redis import RedisError


spec = importlib.util.spec_from_file_location(
    'chapter_image_completions',
    Path(__file__).parents[1] / 'src/artifact_editor/images/completions.py',
)
completions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(completions)
IDENTITY = dict(author='An Author', title='A Book & More', chapter_number=3, language='english')


def test_snapshot_includes_only_saved_existing_images_without_modifying_source(tmp_path):
    chapter = tmp_path / IDENTITY['author'] / IDENTITY['title'] / 'chapter/0003'
    (chapter / 'english').mkdir(parents=True)
    paragraph = chapter / 'paragraphs/000001'
    paragraph.mkdir(parents=True)
    (paragraph / 'finished.png').touch()
    xml = '<chapter><paragraph index="1">' \
          '<image index="4" src="finished.png" image_generation_id="generated"/>' \
          '<image index="5" src="absent.png" image_generation_id="missing"/>' \
          '<image index="6" src="finished.png"/>' \
          '</paragraph></chapter>'
    source = chapter / 'english/chapter.xml'
    source.write_text(xml)
    snapshot = completions.chapter_image_snapshot(IDENTITY, tmp_path)
    assert snapshot == {
        'state': 'chapter_image_snapshot', **IDENTITY,
        'images': [{'id': 'generated', 'index': 4,
                    'url': '/library/An%20Author/A%20Book%20%26%20More/3/english/images/640/4.png?v=generated'}],
    }
    assert source.read_text() == xml


@pytest.mark.parametrize('field,value', [
    ('author', '..'), ('title', '../Other book'), ('language', '/etc'),
    ('title', 'Book\\Other'), ('chapter_number', -1), ('chapter_number', True),
])
def test_invalid_chapter_subscriptions_cannot_traverse_paths(tmp_path, field, value):
    with pytest.raises(ValueError):
        completions.chapter_image_snapshot({**IDENTITY, field: value}, tmp_path)


def test_completion_publishes_chapter_identity_and_versioned_image_url():
    from task_queue import CHAPTER_IMAGE_EVENTS
    client = Mock()
    chapter = SimpleNamespace(author=SimpleNamespace(name=IDENTITY['author']),
                              title=IDENTITY['title'], number=3, language='english')
    image = BeautifulSoup('<image index="4" image_generation_id="redraw"/>', 'xml').image
    assert completions.publish_image_completion(chapter, image, client)
    channel, raw = client.publish.call_args.args
    assert channel == CHAPTER_IMAGE_EVENTS
    payload = json.loads(raw)
    assert payload == {
        'state': 'chapter_image_complete', **IDENTITY,
        'image': completions.completed_image(IDENTITY, 4, 'redraw'),
    }


def test_notification_failure_does_not_fail_a_saved_generation():
    client = Mock()
    client.publish.side_effect = RedisError('Disconnected')
    chapter = SimpleNamespace(author=SimpleNamespace(name=IDENTITY['author']),
                              title=IDENTITY['title'], number=3, language='english')
    image = BeautifulSoup('<image index="4" image_generation_id="redraw"/>', 'xml').image
    assert completions.publish_image_completion(chapter, image, client) is False


def test_shared_websocket_snapshots_and_forwards_only_the_watched_chapter(monkeypatch):
    import sys
    from task_queue import CHAPTER_IMAGE_EVENTS, PRIORITY_QUEUE, TAG_QUEUE, VIDEO_EVENTS
    path = Path(__file__).parents[1] / 'src/websocket/server.py'
    function = next(node for node in ast.parse(path.read_text()).body
                    if isinstance(node, ast.FunctionDef) and node.name == 'styler_websocket')
    function.decorator_list = []
    client, socket, pubsub = Mock(), Mock(), Mock()
    client.pubsub.return_value = pubsub
    event = {'state': 'chapter_image_complete', **IDENTITY,
             'image': completions.completed_image(IDENTITY, 4, 'fresh')}
    other = {**event, 'chapter_number': 4}
    pubsub.get_message.side_effect = [
        {'type': 'message', 'data': json.dumps(other)},
        {'type': 'message', 'data': json.dumps(event)}, None,
    ]
    socket.receive.side_effect = [json.dumps({'action': 'watch_chapter_images', **IDENTITY}),
                                  RuntimeError('Connection closed')]
    snapshot = Mock(return_value={'state': 'chapter_image_snapshot', **IDENTITY, 'images': []})
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'styler.jobs', SimpleNamespace(
        EVENTS='style_events', sample_task=Mock(), request_sample=Mock()))
    monkeypatch.setitem(sys.modules, 'cpu_queue', SimpleNamespace(
        snapshot=lambda _: {'state': 'cpu_queue_plan'}))
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.completions', SimpleNamespace(
        chapter_image_snapshot=snapshot,
        normalize_chapter_identity=completions.normalize_chapter_identity))
    namespace = dict(
        json=json, time=time, redis_connection=lambda: client, log=Mock(),
        styler_queue_snapshot=lambda *args: {'running': None},
        CHAPTER_IMAGE_EVENTS=CHAPTER_IMAGE_EVENTS, VIDEO_EVENTS=VIDEO_EVENTS,
        PRIORITY_QUEUE=PRIORITY_QUEUE, TAG_QUEUE=TAG_QUEUE,
    )
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), 'exec'), namespace)
    with pytest.raises(RuntimeError, match='Connection closed'):
        namespace['styler_websocket'](socket)
    sent = [json.loads(call.args[0]) for call in socket.send.call_args_list]
    assert [item['state'] for item in sent] == [
        'queue_plan', 'cpu_queue_plan', 'chapter_image_snapshot', 'chapter_image_complete']
    assert sent[-1] == event
    snapshot.assert_called_once_with(IDENTITY)
    assert CHAPTER_IMAGE_EVENTS in pubsub.subscribe.call_args.args
    pubsub.close.assert_called_once()
    client.close.assert_called_once()

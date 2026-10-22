import sys
import json
import time
from unittest.mock import MagicMock

import pytest

import worker_client
import worker_operations
from websocket import server


def test_video_progress_coalesces_backlog_and_reads_current_status():
    pubsub, client = MagicMock(), MagicMock()
    pubsub.get_message.side_effect = [
        {"type": "message", "data": json.dumps({"job_id": "a", "message": f"Frame {i}"})}
        for i in range(140, 849)
    ] + [None]
    complete = {"job_id": "a", "percent": "100", "state": "complete"}
    client.hgetall.return_value = complete
    assert server.pending_video_progress(pubsub, client) == [complete]
    client.hgetall.assert_called_once_with("video_job:a")


@pytest.mark.parametrize("fails", [False, True])
def test_chapter_import_worker_reports_completion_or_failure(monkeypatch, fails):
    from artifact_editor.chapter import views
    from artifact_editor.chapter.chapter import Chapter

    importer = MagicMock(side_effect=ValueError("audio failed") if fails else None)
    monkeypatch.setattr(views, "import_chapter_content", importer)
    monkeypatch.setattr(Chapter, "__init__", lambda self, **kwargs: None)
    publish = MagicMock()
    monkeypatch.setattr(server, "publish_progress", publish)
    client = MagicMock()
    task = dict(job_id="a" * 32, action="chapter_import", author="Robert Frost",
                title="Selected Poems", chapter_number=1, language="en",
                sections='[{"typesetting_method":"verse","ops":[]}]')
    server.run_video_job(client, task)
    assert publish.call_args_list[0].args == (
        client, task["job_id"], 0, "Preparing chapter import"
    )
    assert importer.call_args.kwargs["sections_json"] == task["sections"]
    assert callable(importer.call_args.kwargs["progress"])
    assert publish.call_args.args[-1] == ("failed" if fails else "complete")
    assert "Chapter import" in publish.call_args.args[-2]


@pytest.mark.parametrize("action", ["chapter_import", "chapter_missing_metadata",
                                   "chapter_missing_images", "chapter_missing_animations"])
def test_chapter_import_websocket_queues_sections_without_running_import(monkeypatch, action):
    client, ws = MagicMock(), MagicMock()
    task = dict(job_id="b" * 32, action=action, author="Robert Frost",
                title="Selected Poems", chapter_number="1", language="en",
                sections='[{"typesetting_method":"verse","ops":[]}]', unwanted=True)
    ws.receive.side_effect = [json.dumps(task), RuntimeError("end test connection")]
    monkeypatch.setattr(server, "redis_connection", lambda: client)
    monkeypatch.setattr(server, "pending_video_progress", lambda *args: [])
    with pytest.raises(RuntimeError, match="end test"):
        server.handle_websocket(ws)
    queue, payload = client.lpush.call_args.args
    queued = json.loads(payload)
    assert queue == server.VIDEO_QUEUE
    if action == "chapter_import":
        assert queued["sections"] == task["sections"]
    else:
        assert "sections" not in queued
    assert queued["chapter_number"] == 1
    assert "unwanted" not in queued


@pytest.mark.parametrize("action", ["chapter_missing_metadata", "chapter_missing_images",
                                   "chapter_missing_animations"])
def test_chapter_batches_never_fall_through_to_full_render(monkeypatch, action):
    from artifact_editor.chapter import chapter as chapter_module
    from artifact_editor.images import images
    from artifact_editor.video import video
    from artifact_editor.textoviva import jobs

    chapter = MagicMock()
    monkeypatch.setattr(chapter_module, "Chapter", MagicMock(return_value=chapter))
    metadata = MagicMock()
    monkeypatch.setattr(images, "generate_missing_metadata", metadata)
    render = MagicMock(side_effect=AssertionError("Unexpected full render"))
    monkeypatch.setattr(video, "book_to_video", render)
    monkeypatch.setattr(jobs, "rerender_chapter", render)
    publish = MagicMock()
    monkeypatch.setattr(server, "publish_progress", publish)
    server.run_video_job(MagicMock(), dict(job_id="c" * 32, action=action,
        author="Robert Frost", title="Selected Poems", chapter_number=1, language="en"))
    assert metadata.call_count == (action == "chapter_missing_metadata")
    assert chapter.textoviva.generate_missing_images.call_count == (action == "chapter_missing_images")
    assert chapter.textoviva.generate_missing_animations.call_count == (action == "chapter_missing_animations")
    render.assert_not_called()
    assert publish.call_args.args[-1] == "complete"


def test_video_progress_preserves_distinct_jobs_and_missing_snapshots():
    pubsub, client = MagicMock(), MagicMock()
    updates = [{"job_id": "a", "percent": 10}, {"job_id": "b", "percent": 20},
               {"job_id": "a", "percent": 100}]
    pubsub.get_message.side_effect = [
        {"type": "message", "data": json.dumps(event)} for event in updates
    ] + [None]
    client.hgetall.return_value = {}
    assert server.pending_video_progress(pubsub, client) == [updates[2], updates[1]]


def test_web_client_correlates_and_closes(monkeypatch):
    connection = MagicMock()
    connection.receive.side_effect = lambda **kwargs: json.dumps({
        "job_id": json.loads(connection.send.call_args.args[0])["job_id"],
        "result": [{"text": "Hello.", "text_with_ws": "Hello. "}],
    })
    monkeypatch.delenv("VIDEO_WORKER_PROCESS", raising=False)
    monkeypatch.setattr("simple_websocket.Client.connect", lambda url: connection)
    doc = worker_client.sentence_pipeline("Hello. ")
    assert doc.sents[0].text_with_ws == "Hello. "
    connection.close.assert_called_once()


@pytest.mark.parametrize("response, error", [(None, TimeoutError), ('{"job_id":"wrong"}', RuntimeError)])
def test_web_client_closes_on_failure(monkeypatch, response, error):
    connection = MagicMock()
    connection.receive.return_value = response
    monkeypatch.delenv("VIDEO_WORKER_PROCESS", raising=False)
    monkeypatch.setattr("simple_websocket.Client.connect", lambda url: connection)
    with pytest.raises(error):
        worker_client.call_worker("sentences", text="hello")
    connection.close.assert_called_once()


def test_worker_calls_locally_without_websocket(monkeypatch):
    monkeypatch.setenv("VIDEO_WORKER_PROCESS", "1")
    execute = MagicMock(return_value=["token"])
    monkeypatch.setattr(worker_operations, "execute", execute)
    assert worker_client.call_worker("t5_tokens", text="hello") == ["token"]
    execute.assert_called_once_with("t5_tokens", {"text": "hello"})


def test_worker_publishes_errors_and_expires_results(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(worker_operations, "execute", MagicMock(side_effect=ValueError("bad input")))
    server.run_worker_rpc(client, {"job_id": "abc", "operation": "sentences", "arguments": {}, "deadline": time.time()+10})
    pipe = client.pipeline.return_value.__enter__.return_value
    key, payload = pipe.rpush.call_args.args
    assert key == "worker_result:abc"
    assert json.loads(payload) == {"job_id": "abc", "error": "bad input"}
    pipe.expire.assert_called_once_with(key, 600)


def test_expired_request_is_not_executed(monkeypatch):
    execute = MagicMock()
    monkeypatch.setattr(worker_operations, "execute", execute)
    server.run_worker_rpc(MagicMock(), {"deadline": time.time()-1})
    execute.assert_not_called()


def test_gateway_queues_and_returns_correlated_result(monkeypatch):
    client, ws = MagicMock(), MagicMock()
    job_id = "a" * 32
    ws.receive.return_value = json.dumps({"job_id": job_id, "operation": "sentences", "arguments": {"text": "Hello."}})
    payload = json.dumps({"job_id": job_id, "result": []})
    client.blpop.return_value = (f"worker_result:{job_id}", payload)
    monkeypatch.setattr(server, "redis_connection", lambda: client)
    server.handle_worker_rpc(ws)
    queue, task = client.lpush.call_args.args
    assert queue == server.VIDEO_QUEUE
    assert json.loads(task)["action"] == "worker_rpc"
    ws.send.assert_called_once_with(payload)
    client.close.assert_called_once()


def test_web_startup_does_not_import_ml_libraries():
    import subprocess
    subprocess.run([sys.executable, "-c", "from artifact_editor.app import create_app; create_app(); import sys; assert not ({'torch', 'spacy', 'transformers', 'diffusers'} & sys.modules.keys())"], check=True)


def test_real_websocket_round_trip(monkeypatch):
    import threading
    from werkzeug.serving import make_server

    client = MagicMock()
    results = {}
    pipe = client.pipeline.return_value.__enter__.return_value
    pipe.rpush.side_effect = lambda key, payload: results.update({key: payload})
    client.blpop.side_effect = lambda key, **kwargs: (key, results.pop(key))
    client.lpush.side_effect = lambda queue, payload: server.run_worker_rpc(client, json.loads(payload))
    monkeypatch.setattr(server, "redis_connection", lambda: client)
    monkeypatch.setattr(worker_operations, "execute", lambda operation, arguments: [
        {"text": arguments["text"].strip(), "text_with_ws": arguments["text"]}
    ])
    monkeypatch.delenv("VIDEO_WORKER_PROCESS", raising=False)
    http = make_server("127.0.0.1", 0, server.app, threaded=True)
    monkeypatch.setenv("WORKER_WEBSOCKET_URL", f"ws://127.0.0.1:{http.server_port}/worker-rpc")
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        assert worker_client.sentence_pipeline("Hello. ").sents[0].text_with_ws == "Hello. "
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=5)


def test_character_image_websocket_captures_current_description(monkeypatch):
    client, ws = MagicMock(), MagicMock()
    task = dict(job_id='c' * 32, action='character_image', author='Robert Frost',
                title='Selected Poems', chapter_number='1', language='en',
                character_tag='Traveler', description='A traveler in a blue coat.', unwanted=True)
    ws.receive.side_effect = [json.dumps(task), RuntimeError('end test connection')]
    monkeypatch.setattr(server, 'redis_connection', lambda: client)
    monkeypatch.setattr(server, 'pending_video_progress', lambda *args: [])
    with pytest.raises(RuntimeError, match='end test'):
        server.handle_websocket(ws)
    queued = json.loads(client.lpush.call_args.args[1])
    assert queued['description'] == task['description']
    assert queued['character_tag'] == 'Traveler'
    assert 'unwanted' not in queued


@pytest.mark.parametrize('fails', [False, True])
def test_character_image_worker_reports_result(monkeypatch, fails):
    from artifact_editor.characters import characters
    from artifact_editor.chapter.chapter import Chapter
    draw = MagicMock(side_effect=ValueError('generation failed') if fails else None)
    monkeypatch.setattr(characters, 'draw_headshot', draw)
    monkeypatch.setattr(Chapter, '__init__', lambda self, **kwargs: None)
    publish = MagicMock()
    monkeypatch.setattr(server, 'publish_progress', publish)
    server.run_video_job(MagicMock(), dict(job_id='d' * 32, action='character_image',
        author='Robert Frost', title='Selected Poems', chapter_number=1, language='en',
        character_tag='Traveler', description='Blue coat'))
    assert draw.call_args.kwargs == dict(force=True, description='Blue coat')
    assert publish.call_args.args[-1] == ('failed' if fails else 'complete')


def test_character_image_uses_full_body_contrasting_background_and_saves_output(monkeypatch, tmp_path):
    import comfy
    from artifact_editor.characters import characters
    character = dict(tag='Traveler', description='A traveler wearing a blue coat')
    monkeypatch.setattr(characters, 'get_character', lambda *args: character.copy())
    save = MagicMock()
    monkeypatch.setattr(characters, 'save_character', save)
    monkeypatch.setattr(characters.const, 'LIBRARY_DIR', str(tmp_path / 'library'))
    monkeypatch.setitem(characters.const.COMFY_DIRS, 'artifactserver', {'OUTPUT_DIR': str(tmp_path)})
    workflow = {'latent': {'class_type': 'EmptySD3LatentImage', 'inputs': {}},
                'sample': {'class_type': 'KSampler', 'inputs': {}}}
    template = MagicMock()
    template.serialize.return_value = workflow
    monkeypatch.setattr(comfy, 'load_workflow_template', lambda *args: template)
    def run(data, template_environment):
        assert 'entire body visible from head to feet' in template_environment['PROMPT']
        assert 'Plain, solid-color background with strong contrast against the character' in template_environment['PROMPT']
        assert character['description'] in template_environment['PROMPT']
        assert data['latent']['inputs'] == dict(width=768, height=1152)
        output = tmp_path / (template_environment['FILENAME_PREFIX'] + '_00001_.png')
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'generated image')
    monkeypatch.setattr(comfy, 'run_workflow', run)
    chapter = MagicMock(chapterdir='author/book/chapter/1')
    result = characters.draw_headshot(chapter, 'Traveler', force=True)
    from pathlib import Path
    assert Path(result).read_bytes() == b'generated image'
    assert save.call_args.args[2]['headshot'].endswith('characters/Traveler.png')


def test_extract_description_excludes_existing_description_and_generated_metadata(monkeypatch):
    from bs4 import BeautifulSoup
    from artifact_editor.characters import characters
    old_description = 'Invented purple hair and golden armor'
    character = dict(name='Traveler', tag='Traveler', description=old_description)
    chapter = MagicMock()
    chapter.get_xml.return_value = BeautifulSoup('''<chapter>
        <paragraph><phrase>Selected Poems by Robert Frost</phrase></paragraph>
        <paragraph><phrase>Traveler wore a blue coat.</phrase>
        <image><description>Invented purple hair and golden armor</description></image>
        </paragraph><paragraph><description>Invented purple hair and golden armor</description>
        </paragraph></chapter>''', 'xml')
    monkeypatch.setattr(characters, 'get_character', lambda *args: character)
    monkeypatch.setattr(characters, 'save_character', MagicMock())
    prompt = MagicMock(side_effect=['Traveler wore a blue coat.', 'A blue coat.'])
    monkeypatch.setattr(characters.llm, 'str_prompt', prompt)
    assert characters.generate_description(chapter, 'Traveler') == 'A blue coat.'
    assert prompt.call_count == 2
    assert 'Traveler wore a blue coat.' in prompt.call_args_list[0].kwargs['prompt']
    source_prompt = prompt.call_args_list[0].kwargs['prompt']
    assert 'Selected Poems by Robert Frost' in source_prompt
    assert 'BEGIN SOURCE TEXT' in source_prompt
    assert source_prompt.endswith('END SOURCE TEXT')
    assert 'entire source text' in source_prompt

    for call in prompt.call_args_list:
        assert old_description not in call.kwargs['prompt']
        assert old_description not in call.kwargs['system_prompt']


def test_prompt_workflow_receives_system_instructions(monkeypatch, tmp_path):
    from artifact_editor.chapter import chapter as chapter_module
    import const
    template_dir = const.COMFY_DIRS['artifactserver']['WORKFLOW_TEMPLATES_DIR']
    monkeypatch.setitem(const.COMFY_DIRS, 'artifactserver', {
        'WORKFLOW_TEMPLATES_DIR': template_dir,
        'WORKFLOWS_DIR': str(tmp_path),
    })
    filename = chapter_module.get_prompt_workflow_by_filename(
        workflow_filename='chat.qwen35-json.Qwen 3.5 4b bf16.json',
        system_prompt='Extract only supported physical traits. No preface.',
        prompt='Target: Colt. Complete source text.', outputfile='result.txt',
    )
    workflow = json.loads((tmp_path / filename).read_text())
    nodes = {node.get('title'): node for node in workflow['nodes']}
    assert nodes['System Prompt']['widgets_values'] == ['Extract only supported physical traits. No preface.']
    assert nodes['System Prompt']['widgets_values_named']['string'] == 'Extract only supported physical traits. No preface.'
    assert nodes['Prompt']['widgets_values'] == ['Target: Colt. Complete source text.']


def test_character_image_template_is_recognized_as_api_workflow():
    import comfy
    workflow = comfy.load_workflow_template('api', 't2i', 'zit_style_sample')
    assert '1' not in workflow.serialize()
    assert comfy.workflow_type(workflow) == 'api'


@pytest.mark.parametrize('data', [{}, {'1': {}}, {'9': {'class_type': 'SaveImage'}}])
def test_invalid_workflow_is_rejected(data):
    import comfy
    with pytest.raises(ValueError, match='Unknown workflow type'):
        comfy.workflow_type(comfy.Workflow(data))


@pytest.mark.parametrize('has_voice', [None, True, False])
def test_character_voice_controls_follow_has_voice(monkeypatch, has_voice):
    from types import SimpleNamespace
    from bs4 import BeautifulSoup
    from artifact_editor.characters import htmx
    monkeypatch.setattr(htmx, 'url_for', lambda endpoint, **kwargs: '/' + endpoint)
    blender = MagicMock(return_value='<div id="voice_selection_panel">Voice mixer</div>')
    monkeypatch.setattr(htmx, 'voice_blender', blender)
    monkeypatch.setattr(htmx, 'character_pronunciation_widget', lambda *args: 'Pronunciation controls')
    character = dict(tag='Colt', name='Colt', description='A small horse')
    if has_voice is not None:
        character['has_voice'] = has_voice
    chapter = SimpleNamespace(kwargs={}, url='/chapter', author=SimpleNamespace(name='Author'), number=1, title='Book', language='english')
    html = htmx.character_data(chapter, character)
    soup = BeautifulSoup(html, 'html.parser')
    assert soup.find('wa-checkbox', attrs={'name': 'has_voice'}).has_attr('checked') == (has_voice is not False)
    assert bool(soup.find('wa-select', attrs={'name': 'tts_engine'})) == (has_voice is not False)
    assert ('Voice mixer' in html) == (has_voice is not False)
    assert ('Pronunciation controls' in html) == (has_voice is not False)
    assert ('Randomize Voice Weights' in html) == (has_voice is not False)
    assert ('Remove Voice' in html) == (has_voice is not False)
    assert 'Generate Image' in html and 'Description' in html and 'Delete' in html


@pytest.mark.parametrize('enabled', [False, True])
def test_has_voice_toggle_persists_without_losing_voice_configuration(monkeypatch, tmp_path, enabled):
    from flask import Flask, g
    from types import SimpleNamespace
    from artifact_editor.characters import characters, views
    monkeypatch.setattr(characters.const, 'LIBRARY_DIR', str(tmp_path))
    chapter = SimpleNamespace(chapterdir='chapter')
    original = dict(tag='Colt', name='Colt', voices=[{'id': 'af_bella', 'strength': 100}],
                    description='A small horse', audio_appearances=0, visual_appearances=1, number_of_appearances=1)
    characters.save_characters(chapter, {'Colt': original})
    monkeypatch.setattr(views.htmx, 'character_data', lambda chapter, character: 'updated card')
    with Flask(__name__).test_request_context(method='POST', data={'has_voice': str(enabled).lower()}):
        g.chapter = chapter
        assert views.set_has_voice('Colt') == 'updated card'
    saved = characters.get_character(chapter, 'Colt')
    assert saved['has_voice'] is enabled
    assert saved['voices'] == original['voices']
    assert saved['description'] == original['description']


@pytest.mark.parametrize("action", ["timeline_image", "timeline_metadata", "unknown_action"])
def test_image_job_never_falls_through_to_video(monkeypatch, action):
    from artifact_editor.chapter.chapter import Chapter
    from artifact_editor.images import jobs
    from artifact_editor.video import video

    generate, render, publish = MagicMock(), MagicMock(), MagicMock()
    monkeypatch.setattr(Chapter, "__init__", lambda self, **kwargs: None)
    monkeypatch.setattr(jobs, "generate_timeline_image", generate)
    monkeypatch.setattr(jobs, "generate_timeline_metadata", generate)
    monkeypatch.setattr(video, "book_to_video", render)
    monkeypatch.setattr(server, "publish_progress", publish)
    task = dict(job_id="a" * 32, action=action, author="Aesop", title="Fables",
                chapter_number=25, language="english", image_index=1)
    server.run_video_job(MagicMock(), task)
    render.assert_not_called()
    if action in ("timeline_image", "timeline_metadata"):
        generate.assert_called_once()
        assert generate.call_args.args[1] == 1
        assert publish.call_args.args[-1] == "complete"
    else:
        generate.assert_not_called()
        assert publish.call_args.args[-1] == "failed"
        assert "Unsupported render job action" in publish.call_args.args[-2]


def test_prop_image_websocket_queues_current_description(monkeypatch):
    client, ws = MagicMock(), MagicMock()
    task = dict(job_id='e' * 32, action='prop_image', author='Robert Frost',
                title='Selected Poems', chapter_number='1', language='en',
                prop_tag='cup', description='A silver cup', unwanted=True)
    ws.receive.side_effect = [json.dumps(task), RuntimeError('end test connection')]
    monkeypatch.setattr(server, 'redis_connection', lambda: client)
    monkeypatch.setattr(server, 'pending_video_progress', lambda *args: [])
    with pytest.raises(RuntimeError, match='end test'):
        server.handle_websocket(ws)
    queued = json.loads(client.lpush.call_args.args[1])
    assert queued['prop_tag'] == 'cup'
    assert queued['description'] == 'A silver cup'
    assert 'unwanted' not in queued


@pytest.mark.parametrize('fails', [False, True])
def test_prop_image_worker_reports_result(monkeypatch, fails):
    from artifact_editor.props import props
    from artifact_editor.chapter.chapter import Chapter
    draw = MagicMock(side_effect=ValueError('generation failed') if fails else None)
    monkeypatch.setattr(props, 'draw_prop', draw)
    monkeypatch.setattr(Chapter, '__init__', lambda self, **kwargs: None)
    publish = MagicMock()
    monkeypatch.setattr(server, 'publish_progress', publish)
    server.run_video_job(MagicMock(), dict(job_id='e' * 32, action='prop_image',
        author='Robert Frost', title='Selected Poems', chapter_number=1, language='en',
        prop_tag='cup', description='A silver cup'))
    assert draw.call_args.args[1] == 'cup'
    assert draw.call_args.kwargs == dict(description='A silver cup')
    assert publish.call_args.args[-1] == ('failed' if fails else 'complete')
    assert 'Prop image' in publish.call_args.args[-2]

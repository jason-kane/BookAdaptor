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

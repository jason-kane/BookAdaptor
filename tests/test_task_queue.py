"""Redis task queue contracts shared by the artifactserver and worker."""

import json
from unittest.mock import Mock

import pytest

from task_queue import (
    DEFAULT_STATUS_TTL_SECONDS,
    enqueue_payload,
    enqueue_task,
    promote_job,
    remove_queued_job,
)


def test_enqueue_task_initializes_status_then_fifo_queue_and_event():
    client = Mock()
    task = {"job_id": "job-1", "action": "render"}
    status = {"job_id": "job-1", "state": "queued"}
    event = {"job_id": "job-1", "state": "job_queued"}

    enqueue_task(
        client,
        "video_render_jobs",
        task,
        status_key="video_job:job-1",
        status=status,
        event_channel="video_render_events",
        event=event,
    )

    assert [call[0] for call in client.method_calls] == ['hset', 'expire', 'lpush', 'publish']
    queued = json.loads(client.lpush.call_args.args[1])
    timing = {key: queued[key] for key in ('timing_key', 'timing_units', 'timing_unit')}
    assert len(timing['timing_key']) == 64
    assert timing['timing_units'] == 1
    assert timing['timing_unit'] == 'task'
    assert queued == {**task, **timing, 'provider': 'comfyui'}
    client.hset.assert_called_once_with('video_job:job-1', mapping={**status, **timing})
    client.expire.assert_called_once_with('video_job:job-1', DEFAULT_STATUS_TTL_SECONDS)
    assert client.lpush.call_args.args[0] == 'video_render_jobs'
    channel, published = client.publish.call_args.args
    assert channel == 'video_render_events'
    assert json.loads(published) == {**event, **timing}
    assert task == {"job_id": "job-1", "action": "render"}
    assert status == {"job_id": "job-1", "state": "queued"}
    assert event == {"job_id": "job-1", "state": "job_queued"}


def test_enqueue_task_supports_right_push_and_unicode_payloads():
    client = Mock()
    task = {"job_id": "one", "name": "café"}

    enqueue_task(client, "queue", task, front=False, ensure_ascii=False)

    client.rpush.assert_called_once()
    queue, payload = client.rpush.call_args.args
    assert queue == 'queue'
    assert 'café' in payload
    queued = json.loads(payload)
    assert queued.pop('provider') == 'comfyui'
    assert len(queued.pop('timing_key')) == 64
    assert queued.pop('timing_units') == 1
    assert queued.pop('timing_unit') == 'task'
    assert queued == task
    client.lpush.assert_not_called()
    client.publish.assert_not_called()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": {"state": "queued"}},
        {"event": {"state": "job_queued"}},
    ],
)
def test_enqueue_task_validates_optional_metadata_before_mutating_redis(kwargs):
    client = Mock()

    with pytest.raises(ValueError):
        enqueue_task(client, "queue", {"job_id": "one"}, **kwargs)

    assert client.method_calls == []


def test_enqueue_payload_preserves_fields_adds_provider_and_respects_fifo_side():
    client = Mock()
    payload = '{"job_id":"already-serialized"}'

    enqueue_payload(client, "queue", payload, front=False)

    client.rpush.assert_called_once()
    queue, serialized = client.rpush.call_args.args
    assert queue == 'queue'
    assert json.loads(serialized) == {'job_id': 'already-serialized', 'provider': 'comfyui'}
    client.lpush.assert_not_called()


def test_promote_job_validates_inputs_before_running_lua():
    client = Mock()
    for invalid_id in (None, "", "x" * 257):
        with pytest.raises(ValueError, match="job ID"):
            promote_job(client, invalid_id, "priority", "video", "tags")
    with pytest.raises(ValueError, match="priority state"):
        promote_job(client, "one", "priority", "video", "tags", enabled=1)
    client.eval.assert_not_called()


def test_promote_job_returns_decoded_lua_result_with_configured_queues():
    client = Mock()
    response = {"state": "queued", "job": {"job_id": "one"}}
    client.eval.return_value = json.dumps(response)

    result = promote_job(
        client, "one", "priority", "video", "tags", "running", "events", False, "idle"
    )

    assert result == response
    args = client.eval.call_args.args
    assert args[1:6] == (5, "priority", "video", "tags", "idle")
    assert args[6:] == ("running", "one", "events", "0")


def test_remove_queued_job_reports_missing_and_decodes_running_results():
    client = Mock()
    client.eval.return_value = None
    assert remove_queued_job(client, "one", "priority", "video", "tags") is None
    client.eval.return_value = json.dumps({"state": "running"})
    assert remove_queued_job(client, "one", "priority", "video", "tags") == {
        "state": "running"
    }
    with pytest.raises(ValueError, match="job ID"):
        remove_queued_job(client, "", "priority", "video", "tags")

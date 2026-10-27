"""Shared Redis task-queue operations for artifactserver and worker services.

## Responsibility and callers

Artifactserver views submit chapter and render jobs, while
``websocket.server`` owns the worker loop and queue controls. They run in
separate processes but share Redis, so both sides must serialize jobs and
perform queue mutations using the same module. Styler producers may also use
these primitives; domain-specific candidate selection and analysis remain in
their owning modules.

## Queue contract

Jobs are JSON mappings with a stable ``job_id``. ``enqueue_task`` writes an
optional status hash and then pushes the serialized job onto a Redis list;
left-push is the default because the worker pops from the right to preserve
FIFO order. ``promote_job`` and ``remove_queued_job`` use Lua scripts so a job
cannot be duplicated, moved after it starts, or partially removed from queues
when the same identifier appears more than once.

Queue names, status schemas, task dispatch, and worker lifecycle belong to the
calling application. This module supplies Redis mechanics and keeps the queue
operations identical across artifactserver and worker processes.
"""

import json
from typing import Any, Mapping

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

VIDEO_QUEUE = "video_render_jobs"
VIDEO_EVENTS = "video_render_events"
TAG_QUEUE = "styler_image_tag_jobs"
PRIORITY_QUEUE = "styler_image_tag_priority_jobs"
IDLE_QUEUE = "styler_idle_jobs"
RUNNING_KEY = "styler:worker-running"
STYLER_EVENTS = "styler_sample_events"
DEFAULT_IDLE_QUEUE = IDLE_QUEUE
DEFAULT_RUNNING_KEY = RUNNING_KEY
DEFAULT_STYLER_EVENTS = STYLER_EVENTS
DEFAULT_STATUS_TTL_SECONDS = 86_400


def enqueue_task(
    client: "Redis",
    queue: str,
    task: Mapping[str, Any],
    *,
    status_key: str | None = None,
    status: Mapping[str, Any] | None = None,
    status_ttl: int = DEFAULT_STATUS_TTL_SECONDS,
    event_channel: str | None = None,
    event: Mapping[str, Any] | None = None,
    front: bool = True,
    ensure_ascii: bool = True,
) -> None:
    """Persist optional status, enqueue one JSON task, and optionally publish it."""
    if status is not None and status_key is None:
        raise ValueError("status_key is required when initializing task status")
    if event is not None and event_channel is None:
        raise ValueError("event_channel is required when publishing a queue event")

    if status is not None:
        client.hset(status_key, mapping=dict(status))
        client.expire(status_key, status_ttl)

    payload = json.dumps(dict(task), ensure_ascii=ensure_ascii)
    enqueue_payload(client, queue, payload, front=front)
    if event is not None:
        client.publish(
            event_channel, json.dumps(dict(event), ensure_ascii=ensure_ascii)
        )


def enqueue_payload(
    client: "Redis",
    queue: str,
    payload: str,
    *,
    front: bool = True,
) -> None:
    """Enqueue an already serialized task without changing its wire payload."""
    if front:
        client.lpush(queue, payload)
    else:
        client.rpush(queue, payload)


def promote_job(
    client: "Redis",
    job_id: str,
    priority_queue: str,
    video_queue: str,
    tag_queue: str,
    running_key: str = DEFAULT_RUNNING_KEY,
    channel: str = DEFAULT_STYLER_EVENTS,
    enabled: bool = True,
    idle_queue: str = DEFAULT_IDLE_QUEUE,
) -> dict[str, Any] | None:
    """Atomically promote or demote a task while preserving its source queue."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        raise ValueError("Invalid job ID")
    if not isinstance(enabled, bool):
        raise ValueError("Invalid priority state")

    raw = client.eval(
        """
        local current = redis.call('GET', KEYS[5])
        if current then
            local ok, task = pcall(cjson.decode, current)
            if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                -- A queued task can start between the click and this operation.
                -- Never requeue or remove its lifecycle highlight once it is running.
                if ARGV[3] == '1' then
                    task.promoted = true
                    redis.call('SET', KEYS[5], cjson.encode(task), 'KEEPTTL')
                    redis.call('PUBLISH', ARGV[2], cjson.encode({state='job_promoted', job_id=ARGV[1]}))
                end
                return cjson.encode({state='running', job=task})
            end
        end
        local selected = nil
        for index = 1, 4 do
            for _, payload in ipairs(redis.call('LRANGE', KEYS[index], 0, -1)) do
                local ok, task = pcall(cjson.decode, payload)
                if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                    if not selected then
                        selected = task
                        if not selected.queue_origin then
                            selected.queue_origin = index == 4 and 'idle' or index == 3 and 'tags' or index == 2 and 'video'
                                or (task.action == 'styler_tag_sample' and 'tags' or 'video')
                        end
                    end
                    redis.call('LREM', KEYS[index], 0, payload)
                end
            end
        end
        if not selected then return nil end
        selected.promoted = ARGV[3] == '1'
        selected.priority = selected.promoted
        selected.priority_removed = not selected.promoted or nil
        local state = selected.promoted and 'job_promoted' or 'job_demoted'
        if selected.promoted then
            redis.call('LPUSH', KEYS[1], cjson.encode(selected))
        else
            local origin = selected.queue_origin
            selected.queue_origin = nil
            if origin == 'idle' or selected.idle_maintenance then
                redis.call('RPUSH', KEYS[4], cjson.encode(selected))
            elseif origin == 'tags' then
                redis.call('RPUSH', KEYS[3], cjson.encode(selected))
            else
                redis.call('LPUSH', KEYS[2], cjson.encode(selected))
            end
        end
        redis.call('PUBLISH', ARGV[2], cjson.encode({state=state, job_id=ARGV[1]}))
        return cjson.encode({state='queued', job=selected})
        """,
        5,
        priority_queue,
        video_queue,
        tag_queue,
        idle_queue,
        running_key,
        job_id,
        channel,
        "1" if enabled else "0",
    )
    return json.loads(raw) if raw else None


def remove_queued_job(
    client: "Redis",
    job_id: str,
    priority_queue: str,
    video_queue: str,
    tag_queue: str,
    running_key: str = DEFAULT_RUNNING_KEY,
    idle_queue: str = DEFAULT_IDLE_QUEUE,
) -> dict[str, Any] | None:
    """Atomically remove a queued task, reporting when it already started."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        raise ValueError("Invalid job ID")

    raw = client.eval(
        """
        local current = redis.call('GET', KEYS[5])
        if current then
            local ok, task = pcall(cjson.decode, current)
            if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                return cjson.encode({state='running'})
            end
        end
        local selected = nil
        for index = 1, 4 do
            for _, payload in ipairs(redis.call('LRANGE', KEYS[index], 0, -1)) do
                local ok, task = pcall(cjson.decode, payload)
                if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                    if not selected then selected = task end
                    redis.call('LREM', KEYS[index], 0, payload)
                end
            end
        end
        if not selected then return nil end
        return cjson.encode({state='queue_removed', task=selected})
        """,
        5,
        priority_queue,
        video_queue,
        tag_queue,
        idle_queue,
        running_key,
        job_id,
    )
    return json.loads(raw) if raw else None

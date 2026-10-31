"""Shared Redis task-queue operations for artifactserver and worker services.

## Responsibility and callers

Artifactserver views submit chapter and render jobs, while
``websocket.server`` owns the worker loop and queue controls. They run in
separate processes but share Redis, so both sides must serialize jobs and
perform queue mutations using the same module. Styler producers may also use
these primitives; domain-specific candidate selection and analysis remain in
their owning modules.

## Queue contract

Jobs are JSON mappings with a stable ``job_id`` and a ``provider`` identifying
``llama``, ``comfyui``, or ``none`` for control work that needs no model API.
Queue helpers infer this attribute for older call sites that omit it.
``enqueue_task`` writes an optional status hash and then pushes the serialized
job onto a Redis list; left-push is the default because the worker pops from
the right to preserve FIFO order. ``promote_job`` and ``remove_queued_job`` use
Lua scripts so a job cannot be duplicated, moved after it starts, or partially
removed from queues when the same identifier appears more than once.

Chapter render commands are routed to RENDER_QUEUE so the coordinator can wait
for GPU dependencies without blocking the GPU consumer. Per-frame CPU work is
owned by cpu_queue. Other dispatch and worker lifecycle remain with callers.
"""

import json
from typing import Any, Mapping

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis import Redis

VIDEO_QUEUE = "video_render_jobs"
VIDEO_EVENTS = "video_render_events"
CHAPTER_IMAGE_EVENTS = "chapter_image_events"
FRAME_EVENTS = "chapter_frame_events"
RENDER_QUEUE = "chapter_render_jobs"
RENDER_ACTIONS = frozenset({
    "video_render", "textoviva_rerender", "textoviva_regenerate_animation",
    "textoviva_missing_animation", "timeline_metadata", "chapter_import",
    "chapter_missing_metadata",
    "chapter_missing_images", "chapter_missing_animations",
    "chapter_missing_characters", "chapter_missing_props", "chapter_add_all",
    "chapter_character_appearances",
})
TAG_QUEUE = "styler_image_tag_jobs"
PRIORITY_QUEUE = "styler_image_tag_priority_jobs"
IDLE_QUEUE = "styler_idle_jobs"
RUNNING_KEY = "styler:worker-running"
STYLER_EVENTS = "styler_sample_events"
DEFAULT_IDLE_QUEUE = IDLE_QUEUE
DEFAULT_RUNNING_KEY = RUNNING_KEY
DEFAULT_STYLER_EVENTS = STYLER_EVENTS
DEFAULT_STATUS_TTL_SECONDS = 86_400
LLAMA_TASK_ACTIONS = {
    "styler_tag_sample",
    "styler_tag_group",
    "styler_scan_image_tags",
}


def task_provider(task: Mapping[str, Any]) -> str:
    """Return the persisted target API provider for one queued task."""
    if task.get("action") in RENDER_ACTIONS or (
        not task.get("action") and "aspect" in task
    ):
        return "none"
    provider = task.get("provider")
    if provider in ("llama", "comfyui", "none"):
        return provider
    if provider == "comfy":
        return "comfyui"
    if task.get("action") == "gpu_service_switch":
        return "llama" if task.get("service_to") == "Llama" else "comfyui"
    if task.get("action") == "worker_reset":
        return "none"
    return "llama" if task.get("action") in LLAMA_TASK_ACTIONS else "comfyui"


def with_task_provider(task: Mapping[str, Any]) -> dict[str, Any]:
    """Copy a task and fill its target provider, including for legacy payloads."""
    result = dict(task)
    result["provider"] = task_provider(result)
    return result


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

    from task_timing import prepare
    task = prepare(task, queue='render' if task.get('action') in RENDER_ACTIONS else None)

    if queue == VIDEO_QUEUE and (task.get("action") in RENDER_ACTIONS or (
        not task.get("action") and "aspect" in task
    )):
        queue = RENDER_QUEUE
        task = {**task, "action": task.get("action") or "video_render",
                "execution_queue": "render", "provider": "none"}
        identity = {key: task[key] for key in (
            "author", "title", "chapter_number", "language") if key in task}
        render_job_label = task.get("job_type") or "chapter video render"
        render_status = {"job_id": task["job_id"], "state": "queued", "percent": 0,
                         "message": f"Queued for {render_job_label}",
                         "job_type": render_job_label,
                         **(dict(status) if status else {}), **identity,
                         **{key: task[key] for key in ("theater_project_id", "theater_stage") if key in task},
                         **{key: task[key] for key in ('timing_key', 'timing_units', 'timing_unit')},
                         "execution_queue": "render", "action": task["action"]}
        client.hset(f"video_job:{task['job_id']}", mapping=render_status)
        client.expire(f"video_job:{task['job_id']}", status_ttl)
        if event is not None:
            event = {**event, "execution_queue": "render"}
    elif status is not None:
        status = {**status, **{key: task[key] for key in ('timing_key', 'timing_units', 'timing_unit')}}
        client.hset(status_key, mapping=dict(status))
        client.expire(status_key, status_ttl)
    payload = json.dumps(with_task_provider(task), ensure_ascii=ensure_ascii)
    enqueue_payload(client, queue, payload, front=front)
    if event is not None:
        event = {**event, **{key: task[key] for key in ('timing_key', 'timing_units', 'timing_unit')}}
        client.publish(
            event_channel, json.dumps(dict(event), ensure_ascii=ensure_ascii)
        )


def pop_gpu_service_switch(client: "Redis", queue: str) -> dict[str, Any] | None:
    """Atomically pop a GPU switch control task regardless of target provider."""
    payload = client.eval(
        """
        for _, payload in ipairs(redis.call('LRANGE', KEYS[1], 0, -1)) do
            local ok, task = pcall(cjson.decode, payload)
            if ok and type(task) == 'table'
                and task.action == 'gpu_service_switch' then
                redis.call('LREM', KEYS[1], 1, payload)
                return payload
            end
        end
        return nil
        """,
        1,
        queue,
    )
    if not payload:
        return None
    if isinstance(payload, bytes):
        payload = payload.decode()
    task = json.loads(payload)
    return task if isinstance(task, dict) else None


def enqueue_payload(
    client: "Redis",
    queue: str,
    payload: str,
    *,
    front: bool = True,
) -> None:
    """Enqueue serialized JSON after adding the task's provider if it is absent."""
    try:
        decoded = json.loads(payload)
    except (TypeError, ValueError):
        decoded = None
    if isinstance(decoded, dict):
        payload = json.dumps(with_task_provider(decoded), ensure_ascii=False)
        if decoded.get('action') in ('styler_tag_sample', 'styler_tag_group'):
            # Provider deferrals and retries must move a task, not append a
            # second copy. Keep distinct image/prompt revisions of legacy
            # sample tasks, whose job_id alone does not identify a revision.
            queues = list(dict.fromkeys((queue, TAG_QUEUE, PRIORITY_QUEUE, VIDEO_QUEUE, IDLE_QUEUE)))
            client.eval(
                """
                local incoming = cjson.decode(ARGV[1])
                for _, queue in ipairs(KEYS) do
                    for _, payload in ipairs(redis.call('LRANGE', queue, 0, -1)) do
                        local ok, task = pcall(cjson.decode, payload)
                        if ok and type(task) == 'table'
                            and task.action == incoming.action
                            and task.job_id == incoming.job_id then
                            local same = incoming.action == 'styler_tag_group'
                                or (task.sha256 == incoming.sha256
                                    and task.prompt_sha256 == incoming.prompt_sha256
                                    and task.analysis_version == incoming.analysis_version)
                            if same then redis.call('LREM', queue, 0, payload) end
                        end
                    end
                end
                redis.call(ARGV[2], KEYS[1], ARGV[1])
                """,
                len(queues),
                *queues,
                payload,
                'LPUSH' if front else 'RPUSH',
            )
            return
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

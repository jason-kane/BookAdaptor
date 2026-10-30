"""GPU requests made by the video coordinator, executed by the serial GPU worker."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import time
import traceback
import uuid

import redis
from redis.maint_notifications import MaintNotificationsConfig

from task_queue import PRIORITY_QUEUE, VIDEO_QUEUE, STYLER_EVENTS, enqueue_task

render_context = ContextVar("render_context", default={})
gpu_label = ContextVar("gpu_label", default="Video generation")
gpu_task_priority = ContextVar("gpu_task_priority", default=False)


@contextmanager
def task_label(label):
    token = gpu_label.set(label)
    try:
        yield
    finally:
        gpu_label.reset(token)


@contextmanager
def prioritize_gpu_tasks():
    """Keep a sequence of dependent GPU requests ahead of background Llama work."""
    token = gpu_task_priority.set(True)
    try:
        yield
    finally:
        gpu_task_priority.reset(token)


def enqueue(action, arguments, *, provider="comfyui", label=None, timeout=3600,
            priority=False, queue_deadline=None, prepare_vram=False):
    """Queue one GPU request and return its result handle without waiting."""
    if os.environ.get("VIDEO_WORKER_PROCESS") == "1":
        raise RuntimeError("A GPU worker cannot wait on its own queue")
    client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True,
                         maint_notifications_config=MaintNotificationsConfig(enabled=False))
    job_id = uuid.uuid4().hex
    from cpu_queue import cancellation_requested, TaskCancelled
    if cancellation_requested(client, render_context.get()):
        client.close()
        raise TaskCancelled("Parent task cancelled by user")
    job_type = label or gpu_label.get()
    task = {**render_context.get(), "job_id": job_id, "action": action,
            "arguments": arguments, "provider": provider,
            "job_type": job_type, "deadline": time.time() + (queue_deadline or timeout)}
    if prepare_vram:
        task["prepare_vram"] = True
    # A render coordinator can mark the whole multi-provider render flow as
    # priority. Apply that to Llama as well as ComfyUI so provider handoffs
    # cannot strand narration or metadata behind unrelated queue work.
    priority = bool(priority or gpu_task_priority.get())
    if priority:
        task["priority"] = True
    queue = PRIORITY_QUEUE if priority else VIDEO_QUEUE
    try:
        enqueue_task(client, queue, task, event_channel=STYLER_EVENTS,
                     event={**{k: v for k, v in task.items() if k != "arguments"},
                            "state": "job_queued"})
        return {"job_id": job_id, "result_key": f"gpu_result:{job_id}",
                "job_type": job_type, "timeout": queue_deadline or timeout}
    finally:
        client.close()


def wait(request):
    """Wait for one previously enqueued GPU request and return its result."""
    client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True,
                         maint_notifications_config=MaintNotificationsConfig(enabled=False))
    deadline = time.monotonic() + request["timeout"]
    try:
        while time.monotonic() < deadline:
            result = client.blpop(request["result_key"], timeout=5)
            if result:
                response = json.loads(result[1])
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result")
        raise TimeoutError(f"GPU task timed out: {request['job_type']}")
    finally:
        client.close()


def submit(action, arguments, *, provider="comfyui", label=None, timeout=3600,
           priority=False, prepare_vram=False):
    """Queue one GPU request and wait outside the GPU worker, without holding its lock."""
    request = enqueue(action, arguments, provider=provider, label=label,
                      timeout=timeout, priority=priority,
                      prepare_vram=prepare_vram)
    return wait(request)


def submit_many(requests, *, on_complete=None, return_exceptions=False):
    """Enqueue independent GPU requests together, then collect their results in order.

    The single GPU worker remains responsible for sequencing. A callback can
    collect each finished artifact promptly while later requests remain queued.
    """
    requests = list(requests)
    if not requests:
        return []
    batch_timeout = sum(float(item.get("timeout", 3600)) for item in requests) + 3600
    handles = []
    for item in requests:
        options = {key: item[key] for key in (
            "provider", "label", "priority", "prepare_vram"
        ) if key in item}
        handles.append(enqueue(
            item["action"], item.get("arguments", {}),
            timeout=item.get("timeout", 3600), queue_deadline=batch_timeout,
            **options,
        ))

    results = [None] * len(handles)
    pending = {handle["result_key"]: index for index, handle in enumerate(handles)}
    client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True,
                         maint_notifications_config=MaintNotificationsConfig(enabled=False))
    deadline = time.monotonic() + max(handle["timeout"] for handle in handles)
    callback_failures = []

    def complete(index, result):
        results[index] = result
        if on_complete:
            try:
                on_complete(index, result)
            except Exception as error:
                callback_failures.append(error)
                results[index] = error

    try:
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                for result_key, index in list(pending.items()):
                    error = TimeoutError(f"GPU task timed out: {handles[index]['job_type']}")
                    complete(index, error)
                    del pending[result_key]
                break
            result = client.blpop(list(pending), timeout=min(5, max(1, int(remaining))))
            if not result:
                continue
            result_key, raw = result
            index = pending.pop(result_key)
            response = json.loads(raw)
            value = RuntimeError(response["error"]) if "error" in response else response.get("result")
            complete(index, value)
    finally:
        client.close()
    failures = callback_failures + [result for result in results if isinstance(result, Exception)]
    if failures and not return_exceptions:
        raise failures[0]
    return results


def run_job(client, task):
    """Execute a queued workflow or Llama call and persist its outcome for the caller."""
    from comfy import workflow_progress
    from styler.queue_history import publish_queue_event

    public = {k: v for k, v in task.items() if k not in ("arguments", "deadline")}

    def report(percent, message):
        detail = {"percent": percent, "message": message}
        client.set(f"styler_progress:{task['job_id']}", json.dumps(detail), ex=86400)
        client.publish(STYLER_EVENTS, json.dumps({**public, **detail, "state": "running"}))

    token = workflow_progress.set(report)
    response = {}
    try:
        from cpu_queue import cancellation_requested, TaskCancelled
        if cancellation_requested(client, task):
            raise TaskCancelled("Parent task cancelled by user")
        if time.time() >= task["deadline"]:
            raise TimeoutError("GPU request expired before execution")
        report(0, "Starting " + task.get("job_type", "GPU task"))
        if task["action"] == "gpu_workflow":
            import comfy
            if task.get("prepare_vram"):
                from gpu_service import use_gpu_service
                report(0, "Preparing GPU memory for animation")
                with use_gpu_service(client, "comfy"):
                    comfy.free_vram()
                    response["result"] = comfy.run_workflow(**task["arguments"])
            else:
                response["result"] = comfy.run_workflow(**task["arguments"])
        elif task["action"] == "gpu_llama":
            from llama_inference import chat_completion
            response["result"] = chat_completion(client, **task["arguments"])
        else:
            raise ValueError("Unknown GPU request")
        publish_queue_event(client, STYLER_EVENTS, {**public, "state": "complete", "percent": 100})
    except Exception as exc:
        response["error"] = str(exc)
        publish_queue_event(client, STYLER_EVENTS, {
            **public,
            "state": "failed",
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
    finally:
        workflow_progress.reset(token)
        with client.pipeline() as pipe:
            pipe.rpush(f"gpu_result:{task['job_id']}", json.dumps(response))
            pipe.expire(f"gpu_result:{task['job_id']}", 86_400)
            pipe.execute()

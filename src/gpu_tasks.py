"""GPU requests made by the video coordinator, executed by the serial GPU worker."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import time
import uuid

import redis
from redis.maint_notifications import MaintNotificationsConfig

from task_queue import VIDEO_QUEUE, STYLER_EVENTS, enqueue_task

render_context = ContextVar("render_context", default={})
gpu_label = ContextVar("gpu_label", default="Video generation")


@contextmanager
def task_label(label):
    token = gpu_label.set(label)
    try:
        yield
    finally:
        gpu_label.reset(token)


def submit(action, arguments, *, provider="comfyui", label=None, timeout=3600):
    """Queue real GPU work and wait outside the GPU worker, without holding its lock."""
    if os.environ.get("VIDEO_WORKER_PROCESS") == "1":
        raise RuntimeError("A GPU worker cannot wait on its own queue")
    client = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True,
                         maint_notifications_config=MaintNotificationsConfig(enabled=False))
    job_id = uuid.uuid4().hex
    result_key = f"gpu_result:{job_id}"
    task = {**render_context.get(), "job_id": job_id, "action": action,
            "arguments": arguments, "provider": provider,
            "job_type": label or gpu_label.get(), "deadline": time.time() + timeout}
    try:
        enqueue_task(client, VIDEO_QUEUE, task, event_channel=STYLER_EVENTS,
                     event={**{k: v for k, v in task.items() if k != "arguments"},
                            "state": "job_queued"})
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            result = client.blpop(result_key, timeout=5)
            if result:
                response = json.loads(result[1])
                if "error" in response:
                    raise RuntimeError(response["error"])
                return response.get("result")
        raise TimeoutError(f"GPU task timed out: {task['job_type']}")
    finally:
        client.close()


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
        if time.time() >= task["deadline"]:
            raise TimeoutError("GPU request expired before execution")
        report(0, "Starting " + task.get("job_type", "GPU task"))
        if task["action"] == "gpu_workflow":
            import comfy
            response["result"] = comfy.run_workflow(**task["arguments"])
        elif task["action"] == "gpu_llama":
            from llama_inference import chat_completion
            response["result"] = chat_completion(client, **task["arguments"])
        else:
            raise ValueError("Unknown GPU request")
        publish_queue_event(client, STYLER_EVENTS, {**public, "state": "complete", "percent": 100})
    except Exception as exc:
        response["error"] = str(exc)
        publish_queue_event(client, STYLER_EVENTS, {**public, "state": "failed", "message": str(exc)})
    finally:
        workflow_progress.reset(token)
        with client.pipeline() as pipe:
            pipe.rpush(f"gpu_result:{task['job_id']}", json.dumps(response))
            pipe.expire(f"gpu_result:{task['job_id']}", 600)
            pipe.execute()

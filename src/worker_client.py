"""Lightweight WebSocket calls to ML operations owned by the video worker."""
import json
import os
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace

_task_context = ContextVar('worker_task_context', default={})


@contextmanager
def worker_task_context(**fields):
    """Attach chapter progress to RPC metadata without changing ML arguments."""
    token = _task_context.set({**_task_context.get(), **fields})
    try:
        yield
    finally:
        _task_context.reset(token)


def sentence_task_label(chapter_number=None, text_block=None, text_blocks=None):
    """Describe which chapter and input block sentence detection is processing."""
    chapter = f'chapter {chapter_number}' if chapter_number is not None else 'chapter text'
    label = f'Splitting {chapter} into sentences'
    if text_block is not None and text_blocks is not None:
        label += f' · block {text_block} of {text_blocks}'
    return label


def call_worker(operation, **arguments):
    if os.environ.get("VIDEO_WORKER_PROCESS") == "1":
        from worker_operations import execute_isolated
        from gpu_service import use_gpu_service
        if operation == "t5_tokens":
            return execute_isolated(operation, arguments)
        with use_gpu_service(None, "python"):
            return execute_isolated(operation, arguments)

    if os.environ.get("RENDER_WORKER_PROCESS") == "1":
        return call_worker_via_queue(operation, arguments)

    from simple_websocket import Client
    request_id = uuid.uuid4().hex
    connection = Client.connect(os.environ.get("WORKER_WEBSOCKET_URL", "ws://websocket:5001/worker-rpc"))
    try:
        task = {"job_id": request_id, "operation": operation, "arguments": arguments}
        if _task_context.get():
            task['context'] = _task_context.get()
        connection.send(json.dumps(task))
        response = connection.receive(timeout=600)
        if response is None:
            raise TimeoutError(f"Video worker timed out: {operation}")
        result = json.loads(response)
        if result.get("job_id") != request_id:
            raise RuntimeError("Unexpected video worker response")
        if "error" in result:
            raise RuntimeError(f"Video worker {operation} failed: {result['error']}")
        return result["result"]
    finally:
        connection.close()


def call_worker_via_queue(operation, arguments):
    """Keep internal chapter work independent of browser gateway restarts."""
    from cpu_queue import connection, cancellation_requested, TaskCancelled
    from gpu_tasks import render_context
    from task_queue import VIDEO_QUEUE, enqueue_task

    client = connection()
    job_id = uuid.uuid4().hex
    result_key = f"worker_result:{job_id}"
    deadline = time.time() + 590
    context = _task_context.get()
    task = {**render_context.get(), **context, "job_id": job_id,
            "action": "worker_rpc", "operation": operation,
            "arguments": arguments, "deadline": deadline}
    if operation == "sentences":
        task["job_type"] = sentence_task_label(**context)
    try:
        enqueue_task(client, VIDEO_QUEUE, task)
        while time.time() < deadline:
            if cancellation_requested(client, task):
                raise TaskCancelled("Parent task cancelled by user")
            response = client.blpop(result_key, timeout=5)
            if response:
                result = json.loads(response[1])
                if result.get("job_id") != job_id:
                    raise RuntimeError("Unexpected video worker response")
                if "error" in result:
                    raise RuntimeError(f"Video worker {operation} failed: {result['error']}")
                return result["result"]
        raise TimeoutError(f"Video worker timed out: {operation}")
    finally:
        client.delete(result_key)
        client.close()


@dataclass
class Sentence:
    text: str
    text_with_ws: str


def sentence_pipeline(text):
    """Keep only the sentence fields used by chapter import and rendering."""
    return SimpleNamespace(sents=[Sentence(**item) for item in call_worker("sentences", text=text)])

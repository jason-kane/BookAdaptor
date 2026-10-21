"""Lightweight WebSocket calls to ML operations owned by the video worker."""
import json
import os
import uuid
from dataclasses import dataclass
from types import SimpleNamespace


def call_worker(operation, **arguments):
    if os.environ.get("VIDEO_WORKER_PROCESS") == "1":
        from worker_operations import execute
        return execute(operation, arguments)

    from simple_websocket import Client
    request_id = uuid.uuid4().hex
    connection = Client.connect(os.environ.get("WORKER_WEBSOCKET_URL", "ws://websocket:5001/worker-rpc"))
    try:
        connection.send(json.dumps({"job_id": request_id, "operation": operation, "arguments": arguments}))
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


@dataclass
class Sentence:
    text: str
    text_with_ws: str


def sentence_pipeline(text):
    """Keep only the sentence fields used by chapter import and rendering."""
    return SimpleNamespace(sents=[Sentence(**item) for item in call_worker("sentences", text=text)])

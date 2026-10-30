import json

import gpu_tasks
from task_queue import PRIORITY_QUEUE, STYLER_EVENTS


def test_priority_gpu_task_uses_worker_priority_queue(monkeypatch):
    captured = {}

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

        def blpop(self, key, timeout):
            return key, json.dumps({"result": "read complete"})

        def close(self):
            captured["closed"] = True

    def fake_enqueue(client, queue, task, **kwargs):
        captured.update(queue=queue, task=task, event_channel=kwargs["event_channel"])

    monkeypatch.delenv("VIDEO_WORKER_PROCESS", raising=False)
    monkeypatch.setattr(gpu_tasks.redis, "Redis", FakeRedis)
    monkeypatch.setattr(gpu_tasks, "enqueue_task", fake_enqueue)

    result = gpu_tasks.submit(
        "gpu_llama", {"prompt": "Read the story"}, provider="llama",
        label="Director story reading", priority=True,
    )

    assert result == "read complete"
    assert captured["queue"] == PRIORITY_QUEUE
    assert captured["task"]["priority"] is True
    assert captured["task"]["provider"] == "llama"
    assert captured["event_channel"] == STYLER_EVENTS
    assert captured["closed"] is True

"""Shared CPU work queue and a supervised process for each available CPU core.

The render coordinator submits immutable frame descriptions, never a live Chapter
or multiprocessing lock. Workers reconstruct the chapter once per render batch.
Pending and claimed work remain in Redis across a worker-service restart.
"""
import json
import multiprocessing as mp
import os
import traceback
import time
import uuid

import redis
from redis.maint_notifications import MaintNotificationsConfig

CPU_QUEUE = "cpu_jobs"
PROCESSING = "cpu_jobs:processing"
RUNNING = "cpu_jobs:running"
POOL = "cpu_jobs:pool"
FAILURES = "cpu_jobs:failures"
RECENT = "cpu_jobs:recent"
RENDER_RUNNING = "chapter_render:running"


def connection():
    return redis.Redis(host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True,
                       maint_notifications_config=MaintNotificationsConfig(enabled=False))


def core_count():
    try:
        return len(os.sched_getaffinity(0)) or 1
    except (AttributeError, OSError):
        return os.cpu_count() or 1


def batch_key(batch_id):
    return f"cpu_batch:{batch_id}"


def execute(task, chapters):
    arguments = task["arguments"]
    if task["action"] == "draw_frame":
        from artifact_editor.author.author import Author
        from artifact_editor.chapter.chapter import Chapter
        from artifact_editor.frames.frames import draw_frame

        batch_id = task["batch_id"]
        if batch_id not in chapters:
            chapters.clear()
            identity = task["chapter"]
            chapters[batch_id] = Chapter(Author(identity["author"]), identity["title"],
                                          identity["chapter_number"], identity["language"])
        draw_frame(chapters[batch_id], animate_lock=None, **arguments)
    elif task["action"] == "encode_video":
        from artifact_editor.tools import assemble_mp4
        assemble_mp4(**arguments)
    elif task["action"] == "typeset_latex":
        from artifact_editor.chapter.typesetting import execute_typesetting
        execute_typesetting(task)
    else:
        raise ValueError(f"Unknown CPU task: {task['action']}")


def run_task(client, payload, worker_id, chapters, processing_key=PROCESSING):
    task = json.loads(payload)
    from task_timing import prepare, safe_record
    task = prepare(task)
    key = batch_key(task["batch_id"])
    public = {k: v for k, v in task.items() if k != "arguments"}
    public.update(worker_id=worker_id, state="running", started_at=time.time())
    client.hset(RUNNING, worker_id, json.dumps(public))
    if task.get('track_status'):
        client.set(f"cpu_job:{task['job_id']}", json.dumps(public), ex=86400)
    error = None
    try:
        # A timed-out coordinator cancels its unstarted work, rather than
        # allowing an old batch to overwrite frames from the next render.
        if client.hget(key, "cancelled"):
            raise RuntimeError("Render batch was cancelled")
        execute(task, chapters)
    except Exception as exc:
        error = str(exc)
        public.update(state="failed", message=str(exc), debug_details=traceback.format_exc(),
                      duration_seconds=time.time() - public['started_at'])
    finally:
        elapsed = time.time() - public['started_at']
        public.update(state='failed' if error else 'complete', duration_seconds=elapsed,
                      message=error if error else 'LaTeX typeset successfully.' if task['action'] == 'typeset_latex' else 'Complete')
        safe_record(client, task, elapsed,
                    state='failed' if error else 'complete', started_at=public['started_at'])
        # Ack the claim and count the outcome in one Redis transaction, so a
        # restarted worker cannot count the same frame twice.
        with client.pipeline() as pipe:
            if task.get('track_status'):
                pipe.set(f"cpu_job:{task['job_id']}", json.dumps(public), ex=86400)
                if not error:
                    pipe.lpush(RECENT, json.dumps({**public, 'completed_at': time.time()}))
                    pipe.ltrim(RECENT, 0, 9)
                    pipe.expire(RECENT, 90)
            pipe.hincrby(key, "failed" if error else "completed", 1)
            if error:
                pipe.hset(key, "error", error)
                pipe.lpush(FAILURES, json.dumps(public))
                pipe.ltrim(FAILURES, 0, 9)
                pipe.expire(FAILURES, 86400)
            pipe.expire(key, 86400)
            pipe.hdel(RUNNING, worker_id)
            pipe.lrem(processing_key, 1, payload)
            pipe.execute()


def worker_loop(worker_id):
    client = connection()
    chapters = {}
    processing_key = f"{PROCESSING}:{worker_id}"
    while True:
        payload = client.brpoplpush(CPU_QUEUE, processing_key, timeout=5)
        if payload:
            run_task(client, payload, worker_id, chapters, processing_key)


def run_pool():
    """A single Compose service supervises all CPU workers and restarts dead ones."""
    client = connection()
    # The container has stopped its previous processes before this entrypoint
    # starts. Recover claims before creating any replacement workers.
    for processing_key in client.scan_iter(PROCESSING + "*"):
        while client.rpoplpush(processing_key, CPU_QUEUE):
            pass
    client.delete(RUNNING)
    count = core_count()
    workers = {}
    try:
        while True:
            for slot in range(count):
                worker_id = str(slot + 1)
                process = workers.get(worker_id)
                if process is not None and not process.is_alive():
                    process.join()
                    # Recover the last claimed task for a process that crashed.
                    processing_key = f"{PROCESSING}:{worker_id}"
                    while client.rpoplpush(processing_key, CPU_QUEUE):
                        pass
                    client.hdel(RUNNING, worker_id)
                    process = None
                if process is None:
                    process = mp.Process(target=worker_loop, args=(worker_id,))
                    process.start()
                    workers[worker_id] = process
            client.set(POOL, json.dumps({"workers": count}), ex=15)
            time.sleep(2)
    finally:
        for process in workers.values():
            process.terminate()
        for process in workers.values():
            process.join()
        client.delete(POOL)


class Batch:
    def __init__(self, client=None, *, parent_job_id=None, chapter=None, track_status=False):
        self.client = client or connection()
        self.batch_id = uuid.uuid4().hex
        self.key = batch_key(self.batch_id)
        self.parent_job_id = parent_job_id
        self.chapter = chapter
        self.track_status = track_status
        self.total = 0
        self.client.hset(self.key, mapping={"completed": 0, "failed": 0, "total": 0})
        self.client.expire(self.key, 86400)

    def submit(self, action, arguments, label):
        task = {"job_id": uuid.uuid4().hex, "batch_id": self.batch_id,
                "parent_job_id": self.parent_job_id, "action": action,
                "label": label, "chapter": self.chapter, "arguments": arguments}
        if self.track_status:
            task['track_status'] = True
        from task_timing import prepare
        task = prepare(task, queue='cpu')
        # Increment before making the task visible to consumers.
        with self.client.pipeline() as pipe:
            if self.track_status:
                public = {key: value for key, value in task.items() if key != 'arguments'}
                pipe.set(f"cpu_job:{task['job_id']}", json.dumps({**public, 'state': 'queued'}), ex=86400)
            pipe.hincrby(self.key, "total", 1)
            pipe.lpush(CPU_QUEUE, json.dumps(task))
            pipe.execute()
        self.total += 1
        return task['job_id']

    def wait(self, progress=None, timeout=86400):
        deadline = time.monotonic() + timeout
        last_count = -1
        while time.monotonic() < deadline:
            status = self.client.hgetall(self.key)
            complete = int(status.get("completed", 0))
            failed = int(status.get("failed", 0))
            if progress and complete != last_count:
                progress(complete)
                last_count = complete
            if complete + failed >= self.total:
                if failed:
                    raise RuntimeError(f"{failed} CPU tasks failed: {status.get('error', '')}")
                return
            time.sleep(0.2)
        self.client.hset(self.key, "cancelled", "1")
        raise TimeoutError("CPU render tasks did not finish before the deadline")


def snapshot(client, capacity=12):
    """Bound the view independently of the potentially very large frame queue."""
    from task_queue import RENDER_QUEUE

    pool = json.loads(client.get(POOL) or "{}")
    active = [json.loads(raw) for raw in client.hvals(RUNNING)]
    pending_count = client.llen(CPU_QUEUE)
    queued = [json.loads(raw) for raw in client.lrange(CPU_QUEUE, -capacity, -1)][::-1]
    renders = [json.loads(raw) for raw in client.lrange(RENDER_QUEUE, -5, -1)][::-1]
    current = client.get(RENDER_RUNNING)
    render_count = client.llen(RENDER_QUEUE) + int(current is not None)
    if current:
        renders.insert(0, json.loads(current))
    for task in renders:
        task.update(client.hgetall(f"video_job:{task['job_id']}"))
    renders.extend(json.loads(raw) for raw in client.lrange("chapter_render:recent", 0, 2))
    from task_timing import Estimator
    estimator = Estimator(client)
    active, queued = estimator.parallel(active, queued, pool.get('workers', 0))
    renders = [estimator.attach(task) for task in renders]
    # Never send frame arguments or complete chapter/workflow data to the browser.
    fields = ("job_id", "batch_id", "parent_job_id", "label", "action", "worker_id",
              "state", "message", "started_at", "duration_seconds", "chapter",
              "debug_details",
              "percent", "title", "chapter_number", "timing", "timing_key",
              "timing_units", "timing_unit")
    public = lambda task: {k: task[k] for k in fields if k in task}
    return {"state": "cpu_queue_plan", "workers": pool.get("workers", 0),
            "render_count": render_count,
            "running": [public(task) for task in active[:capacity]],
            "running_count": len(active), "queued_count": pending_count,
            "jobs": [public(task) for task in queued],
            "renders": [public(task) for task in renders],
            "completed": [public(estimator.attach(task)) for raw in client.lrange(RECENT, 0, 9)
                          if (task := json.loads(raw)).get('completed_at', 0) >= time.time() - 90],
            "failed": [public(estimator.attach(json.loads(raw))) for raw in client.lrange(FAILURES, 0, 2)]}


if __name__ == "__main__":
    run_pool()

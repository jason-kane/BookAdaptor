"""Shared CPU work queue and a supervised process for each available CPU core.

The render coordinator submits immutable frame descriptions, never a live Chapter
or multiprocessing lock. Workers reconstruct the chapter once per render batch.
Pending and claimed work remain in Redis across a worker-service restart.
"""
import json
import multiprocessing as mp
import os
import signal
import traceback
import time
import uuid
from werkzeug.routing import BuildError

import redis
from redis.maint_notifications import MaintNotificationsConfig

CPU_QUEUE = "cpu_jobs"
PROCESSING = "cpu_jobs:processing"
RUNNING = "cpu_jobs:running"
POOL = "cpu_jobs:pool"
FAILURES = "cpu_jobs:failures"
RECENT = "cpu_jobs:recent"
RENDER_RUNNING = "chapter_render:running"
PAYLOAD_PREFIX = "cpu_job_payload:"
CANCEL_PREFIX = "task_cancelled:"
RENDER_PROCESSING = "chapter_render_jobs:processing"


class TaskCancelled(Exception):
    pass


def cancellation_requested(client, task):
    return any(client.get(CANCEL_PREFIX + identity) == "1"
               for identity in (task.get("job_id"), task.get("parent_job_id")) if identity)


def settle_cpu_task(client, job_id, write_outcome):
    """Commit exactly one outcome even if completion races with cancellation."""
    key = f"cpu_job_settled:{job_id}"
    while True:
        try:
            with client.pipeline() as pipe:
                pipe.watch(key)
                if pipe.get(key) == "1":
                    return False
                pipe.multi()
                pipe.set(key, "1", ex=86400)
                write_outcome(pipe)
                pipe.execute()
                return True
        except redis.WatchError:
            continue


def finish_cancelled_cpu(client, task, payload, processing_key=None, public=None):
    now = time.time()
    result = {k: v for k, v in (public or task).items() if k != "arguments"}
    result.update(state="cancelled", message="Cancelled by user", completed_at=now,
                  duration_seconds=max(0, now - result.get("started_at", now)))
    def write_outcome(pipe):
        if task.get("track_status"):
            pipe.set(f"cpu_job:{task['job_id']}", json.dumps(result), ex=86400)
        pipe.hincrby(batch_key(task["batch_id"]), "failed", 1)
        pipe.hset(batch_key(task["batch_id"]), "error", "Task cancelled by user")
        pipe.expire(batch_key(task["batch_id"]), 86400)
        pipe.lpush(RECENT, json.dumps(result))
        pipe.ltrim(RECENT, 0, 9)
        pipe.expire(RECENT, 90)
        if processing_key:
            pipe.lrem(processing_key, 1, payload)
        if result.get("worker_id"):
            pipe.hdel(RUNNING, result["worker_id"])
    return settle_cpu_task(client, task["job_id"], write_outcome)


def finish_cancelled_render(client, task):
    now = time.time()
    status = client.hgetall(f"video_job:{task['job_id']}")
    started = float(status.get("queue_started_at") or task.get("queue_started_at") or now)
    fields = ("job_id", "action", "label", "title", "chapter_number", "job_type",
              "author", "language", "theater_project_id", "theater_stage", "queue_started_at")
    result = {k: task[k] for k in fields if k in task}
    result.update(state="cancelled", message="Cancelled by user", completed_at=now,
                  duration_seconds=max(0, now - started))
    with client.pipeline() as pipe:
        pipe.hset(f"video_job:{task['job_id']}", mapping={
            "state": "cancelled", "message": result["message"],
            "duration_seconds": result["duration_seconds"], "percent": 0})
        pipe.expire(f"video_job:{task['job_id']}", 86400)
        pipe.lpush("chapter_render:recent", json.dumps(result))
        pipe.ltrim("chapter_render:recent", 0, 2)
        pipe.expire("chapter_render:recent", 300)
        pipe.delete(f"styler:retry-pending:{task['job_id']}")
        pipe.publish("styler_sample_events", json.dumps(result))
        pipe.publish("video_render_events", json.dumps(result))
        pipe.execute()


def cancel_dependents(client, parent_job_id):
    from task_queue import PRIORITY_QUEUE, VIDEO_QUEUE

    for raw in client.lrange(CPU_QUEUE, 0, -1):
        task = json.loads(raw)
        if task.get("parent_job_id") == parent_job_id:
            cancel_task(client, task["job_id"], allow_running=True)
    for raw in client.hvals(RUNNING):
        task = json.loads(raw)
        if task.get("parent_job_id") == parent_job_id:
            client.set(CANCEL_PREFIX + task["job_id"], "1", ex=86400)
    for queue in (PRIORITY_QUEUE, VIDEO_QUEUE):
        for raw in client.lrange(queue, 0, -1):
            task = json.loads(raw)
            if task.get("parent_job_id") == parent_job_id and client.lrem(queue, 1, raw):
                response = json.dumps({"error": "Parent task cancelled by user"})
                with client.pipeline() as pipe:
                    pipe.rpush(f"gpu_result:{task['job_id']}", response)
                    pipe.expire(f"gpu_result:{task['job_id']}", 86400)
                    pipe.publish("styler_sample_events", json.dumps({
                        "job_id": task["job_id"], "state": "cancelled",
                        "message": "Parent task cancelled by user"}))
                    pipe.execute()


def cancel_task(client, job_id, *, allow_running=False):
    """Remove pending work or ask its owning supervisor to stop the active job."""
    from task_queue import RENDER_QUEUE

    rejected = {"state": "cpu_cancel_rejected", "job_id": job_id}
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        return {**rejected, "message": "Invalid task ID"}
    for queue in (CPU_QUEUE, RENDER_QUEUE):
        for raw in client.lrange(queue, 0, -1):
            task = json.loads(raw)
            if task.get("job_id") != job_id:
                continue
            if client.lrem(queue, 1, raw):
                client.set(CANCEL_PREFIX + job_id, "1", ex=86400)
                if queue == CPU_QUEUE:
                    finish_cancelled_cpu(client, task, raw)
                else:
                    finish_cancelled_render(client, task)
                return {"state": "cpu_cancelled", "job_id": job_id}

    active = [json.loads(raw) for raw in client.hvals(RUNNING)]
    current = client.get(RENDER_RUNNING)
    if current:
        active.append(json.loads(current))
    # Include the claim-to-running gap so a click cannot lose a just-started job.
    for key in [RENDER_PROCESSING, *client.scan_iter(PROCESSING + ":*")]:
        active.extend(json.loads(raw) for raw in client.lrange(key, 0, -1))
    task = next((item for item in active if item.get("job_id") == job_id), None)
    if task is None:
        return {**rejected, "message": "Task has already finished or is no longer queued"}
    if not allow_running:
        return {**rejected, "message": "Task has started. Confirm cancellation of the running task."}
    client.set(CANCEL_PREFIX + job_id, "1", ex=86400)
    if "batch_id" not in task:
        client.hset(f"video_job:{job_id}", mapping={"state": "cancelling", "message": "Cancelling task…"})
        cancel_dependents(client, job_id)
    return {"state": "cpu_cancelling", "job_id": job_id}


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
    cancelled = False
    try:
        if cancellation_requested(client, task):
            raise TaskCancelled()
        # A timed-out coordinator cancels its unstarted work, rather than
        # allowing an old batch to overwrite frames from the next render.
        if client.hget(key, "cancelled"):
            raise RuntimeError("Render batch was cancelled")
        execute(task, chapters)
    except TaskCancelled:
        cancelled = True
    except Exception as exc:
        error = str(exc)
        public.update(state="failed", message=str(exc), debug_details=traceback.format_exc(),
                      duration_seconds=time.time() - public['started_at'])
    finally:
        if cancelled:
            finish_cancelled_cpu(client, task, payload, processing_key, public)
        else:
            elapsed = time.time() - public['started_at']
            public.update(state='failed' if error else 'complete', duration_seconds=elapsed,
                          completed_at=time.time(),
                          message=error if error else 'LaTeX typeset successfully.' if task['action'] == 'typeset_latex' else 'Complete')
            safe_record(client, task, elapsed,
                        state='failed' if error else 'complete', started_at=public['started_at'])
            # Ack the claim and count the outcome in one Redis transaction, so a
            # restarted worker cannot count the same frame twice.
            def write_outcome(pipe):
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
            settle_cpu_task(client, task["job_id"], write_outcome)


def worker_loop(worker_id):
    os.setsid()
    client = connection()
    chapters = {}
    processing_key = f"{PROCESSING}:{worker_id}"
    while True:
        payload = client.brpoplpush(CPU_QUEUE, processing_key, timeout=5)
        if payload:
            run_task(client, payload, worker_id, chapters, processing_key)


def stop_cancelled_worker(client, worker_id, process):
    raw = client.hget(RUNNING, worker_id)
    if not raw or not cancellation_requested(client, json.loads(raw)):
        return False
    # Freeze the worker before rechecking its identity: it must not advance
    # from the selected job to another job between checking and killing it.
    try:
        os.kill(process.pid, signal.SIGSTOP)
    except ProcessLookupError:
        return False
    killed = False
    try:
        current = client.hget(RUNNING, worker_id)
        if not current or json.loads(current)["job_id"] != json.loads(raw)["job_id"]:
            return False
        os.killpg(process.pid, signal.SIGKILL)
        killed = True
    except ProcessLookupError:
        return False
    finally:
        if not killed:
            try:
                os.kill(process.pid, signal.SIGCONT)
            except ProcessLookupError:
                pass
    process.join()
    public = json.loads(current)
    processing_key = f"{PROCESSING}:{worker_id}"
    for payload in client.lrange(processing_key, 0, -1):
        task = json.loads(payload)
        if task["job_id"] == public["job_id"]:
            finish_cancelled_cpu(client, task, payload, processing_key, public)
            break
    return True


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
                if process is not None and process.is_alive() and stop_cancelled_worker(client, worker_id, process):
                    process = None
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
        if self.parent_job_id and cancellation_requested(self.client, {"job_id": self.parent_job_id}):
            raise TaskCancelled("Parent task cancelled by user")
        task = {"job_id": uuid.uuid4().hex, "batch_id": self.batch_id,
                "parent_job_id": self.parent_job_id, "action": action,
                "label": label, "chapter": self.chapter, "arguments": arguments}
        if self.track_status:
            task['track_status'] = True
        from task_timing import prepare
        task = prepare(task, queue='cpu')
        # Increment before making the task visible to consumers.
        with self.client.pipeline() as pipe:
            pipe.set(f"{PAYLOAD_PREFIX}{task['job_id']}", json.dumps(task), ex=86400)
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


def retry_task(client, job_id):
    """Queue a fresh CPU task with the original action and arguments."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        return {"state": "cpu_retry_rejected", "job_id": job_id, "message": "Invalid task ID"}
    outcome = None
    for key in (FAILURES, RECENT):
        for raw in client.lrange(key, 0, 49):
            try:
                candidate = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if candidate.get("job_id") == job_id:
                outcome = candidate
                break
        if outcome:
            break
    if not outcome or outcome.get("state") not in ("failed", "complete", "cancelled"):
        return {"state": "cpu_retry_rejected", "job_id": job_id,
                "message": "Only a recent completed, failed, or cancelled CPU task can be retried"}
    raw_payload = client.get(f"{PAYLOAD_PREFIX}{job_id}")
    if not raw_payload:
        return {"state": "cpu_retry_rejected", "job_id": job_id,
                "message": "Original task inputs have expired"}
    try:
        original = json.loads(raw_payload)
    except (TypeError, ValueError):
        return {"state": "cpu_retry_rejected", "job_id": job_id,
                "message": "Original task inputs are unavailable"}
    if original.get("action") not in {"draw_frame", "encode_video", "typeset_latex"}:
        return {"state": "cpu_retry_rejected", "job_id": job_id,
                "message": "This CPU task cannot be retried"}

    from task_timing import prepare
    task = dict(original)
    task.update(job_id=uuid.uuid4().hex, batch_id=uuid.uuid4().hex)
    task.pop("parent_job_id", None)
    task.pop("state", None)
    for field in ("worker_id", "started_at", "completed_at", "duration_seconds", "message",
                  "debug_details", "percent", "timing"):
        task.pop(field, None)
    # A one-task batch keeps the original render's counters and cancellation
    # state untouched while giving this retry its own progress record.
    task["track_status"] = True
    task = prepare(task, queue="cpu")
    batch = batch_key(task["batch_id"])
    public = {key: value for key, value in task.items() if key != "arguments"}
    with client.pipeline() as pipe:
        pipe.hset(batch, mapping={"completed": 0, "failed": 0, "total": 1})
        pipe.expire(batch, 86400)
        pipe.set(f"{PAYLOAD_PREFIX}{task['job_id']}", json.dumps(task), ex=86400)
        pipe.set(f"cpu_job:{task['job_id']}", json.dumps({**public, "state": "queued"}), ex=86400)
        pipe.lpush(CPU_QUEUE, json.dumps(task))
        pipe.execute()
    return {"state": "cpu_retry_queued", "job_id": job_id,
            "retry_job_id": task["job_id"], "job": {**public, "state": "queued"}}


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
        task.setdefault("state", "queued")
        task.update(client.hgetall(f"video_job:{task['job_id']}"))
    renders.extend(json.loads(raw) for raw in client.lrange("chapter_render:recent", 0, 2))
    from task_timing import Estimator
    estimator = Estimator(client)
    active, queued = estimator.parallel(active, queued, pool.get('workers', 0))
    renders = [estimator.attach(task) for task in renders]
    # Never send frame arguments or complete chapter/workflow data to the browser.
    fields = ("job_id", "batch_id", "parent_job_id", "label", "action", "worker_id",
              "state", "message", "started_at", "duration_seconds", "chapter",
              "author", "title", "chapter_number", "language", "image_index",
              "thumbnail_image_index",
              "debug_details",
              "percent", "timing", "timing_key",
              "timing_units", "timing_unit")
    def public(task):
        item = {k: task[k] for k in fields if k in task}
        state = item.get("state", "queued")
        if item.get("action") in {
            "chapter_missing_animations",
            "textoviva_regenerate_animation",
            "textoviva_missing_animation",
        }:
            chapter = item.get("chapter") or {}
            image_index = item.get("image_index", item.get("thumbnail_image_index"))
            author = item.get("author") or chapter.get("author")
            title = item.get("title") or chapter.get("title")
            chapter_number = item.get("chapter_number") or chapter.get("chapter_number")
            language = item.get("language") or chapter.get("language")
            if image_index is not None and author and title and chapter_number and language:
                try:
                    from flask import url_for

                    item["thumbnail_url"] = url_for(
                        "library.book.chapter.images.show_image_by_index",
                        author=author,
                        title=title,
                        chapter_number=int(chapter_number),
                        language=language,
                        height=128,
                        image_index=int(image_index),
                    )
                except (BuildError, RuntimeError, ValueError):
                    pass
        item["cancellable"] = state not in ("failed", "complete", "cancelled", "cancelling")
        if item["cancellable"] and cancellation_requested(client, task):
            item.update(state="cancelling", cancellable=False, message="Cancelling task…")
        return item
    def with_retry(task):
        item = public(task)
        item["retryable"] = bool(
            task.get("state") in ("failed", "complete", "cancelled")
            and client.exists(f"{PAYLOAD_PREFIX}{task.get('job_id', '')}")
        )
        return item
    return {"state": "cpu_queue_plan", "workers": pool.get("workers", 0),
            "render_count": render_count,
            "running": [public(task) for task in active[:capacity]],
            "running_count": len(active), "queued_count": pending_count,
            "jobs": [public(task) for task in queued],
            "renders": [public(task) for task in renders],
            "completed": [with_retry(estimator.attach(task)) for raw in client.lrange(RECENT, 0, 9)
                          if (task := json.loads(raw)).get('completed_at', 0) >= time.time() - 90],
            "failed": [with_retry(estimator.attach(task)) for raw in client.lrange(FAILURES, 0, 2)
                       if (task := json.loads(raw)).get('completed_at', 0) >= time.time() - 300]}


if __name__ == "__main__":
    run_pool()

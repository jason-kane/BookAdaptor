"""Coordinate chapter renders outside both the serial GPU and parallel CPU workers."""
import json
import os
import multiprocessing as mp
import signal
import time

from filelock import FileLock

from cpu_queue import (RENDER_RUNNING, connection, cancellation_requested,
                       cancel_dependents, finish_cancelled_render)
from task_queue import RENDER_QUEUE

PROCESSING = "chapter_render_jobs:processing"


def run_job(client, task):
    from artifact_editor.author.author import Author
    from artifact_editor.chapter.chapter import Chapter
    from gpu_tasks import gpu_task_priority, render_context
    from websocket.server import run_video_job
    import const
    from task_timing import prepare, safe_record

    task = {**task, 'queue_started_at': time.time()}

    identity = {key: task[key] for key in ("author", "title", "chapter_number", "language")}
    token = render_context.set({**identity, "parent_job_id": task["job_id"]})
    # A render is an active, user-requested pipeline. Its Llama and ComfyUI
    # substeps should both request the GPU promptly, rather than inheriting
    # background queue behavior from the render task's original enqueue flag.
    priority_token = gpu_task_priority.set(True)
    client.set(RENDER_RUNNING, json.dumps(task))
    client.hset(f"video_job:{task['job_id']}", mapping={
        "execution_queue": "render", "action": task["action"],
        **({"job_type": task["job_type"]} if task.get("job_type") else {}),
        "job_id": task["job_id"], "state": "running", "queue_started_at": task['queue_started_at'],
        **{key: task[key] for key in ("theater_project_id", "theater_stage") if key in task}})
    try:
        chapter = Chapter(Author(task["author"]), task["title"],
                          int(task["chapter_number"]), task["language"])
        xml = chapter.get_xml()
        duration = sum(float(tag.get('duration', 0) or 0) for tag in xml.find_all(['phrase', 'silence']))
        from artifact_editor.images.animation_source import animation_filename
        prepared = all(animation_filename(image, os.path.join(const.LIBRARY_DIR,
                       chapter.get_paragraph_dir(int(image.find_parent('paragraph')['index']))))
                       for image in xml.find_all('image')) and bool(xml.find('image'))
        task['timing_context'] = {'aspect': chapter.get_aspect(), 'assets_present': prepared}
        task = prepare(task, queue='render', units=duration or max(1, len(xml.find_all('phrase'))),
                       unit='narration_second' if duration else 'phrase')
        client.hset(f"video_job:{task['job_id']}", mapping={
            key: task[key] for key in ('timing_key', 'timing_units', 'timing_unit')})
        client.set(RENDER_RUNNING, json.dumps(task))
        # Only one render may write a chapter's output files at a time.
        with FileLock(os.path.join(const.LIBRARY_DIR, chapter.chapterdir, ".render.lock")):
            run_video_job(client, task)
    except Exception as exc:
        from websocket.server import publish_progress
        publish_progress(client, task["job_id"], 0, str(exc), "failed", task["action"])
    finally:
        render_context.reset(token)
        gpu_task_priority.reset(priority_token)
        status = client.hgetall(f"video_job:{task['job_id']}")
        if status.get("state") in ("complete", "failed"):
            elapsed = time.time() - task['queue_started_at']
            safe_record(client, task, elapsed, state=status['state'], started_at=task['queue_started_at'])
            status['duration_seconds'] = round(elapsed, 3)
            client.hset(f"video_job:{task['job_id']}", 'duration_seconds', status['duration_seconds'])
            summary = {key: task[key] for key in ("job_id", "title", "chapter_number", "action")}
            summary.update(status)
            client.lpush("chapter_render:recent", json.dumps(summary))
            client.ltrim("chapter_render:recent", 0, 2)
            client.expire("chapter_render:recent", 300)
        client.delete(RENDER_RUNNING)
        client.delete(f"styler:retry-pending:{task['job_id']}")


def main():
    os.environ["RENDER_WORKER_PROCESS"] = "1"
    client = connection()
    from websocket.server import publish_progress

    # Recover an interrupted coordinator before accepting another chapter.
    while client.rpoplpush(PROCESSING, RENDER_QUEUE):
        pass
    client.delete(RENDER_RUNNING)
    while True:
        payload = client.brpoplpush(RENDER_QUEUE, PROCESSING, timeout=5)
        if not payload:
            continue
        task = json.loads(payload)
        try:
            if cancellation_requested(client, task):
                finish_cancelled_render(client, task)
                continue
            exitcode = run_supervised_job(client, task)
            if exitcode and not cancellation_requested(client, task):
                publish_progress(client, task["job_id"], 0,
                                 f"Chapter worker exited with code {exitcode}",
                                 "failed", task.get("action"))
                client.delete(RENDER_RUNNING)
        except Exception as exc:
            publish_progress(client, task["job_id"], 0, str(exc), "failed", task.get("action"))
        finally:
            client.lrem(PROCESSING, 1, payload)


def run_job_process(task):
    os.setsid()
    client = connection()
    try:
        run_job(client, task)
    finally:
        client.close()


def run_supervised_job(client, task):
    process = mp.Process(target=run_job_process, args=(task,))
    process.start()
    try:
        while process.is_alive():
            process.join(timeout=0.25)
            if cancellation_requested(client, task):
                break
    finally:
        if process.is_alive():
            # Only this coordinator and its local subprocesses belong to the
            # group. No unrelated worker or GPU service is terminated.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                process.kill()
        process.join()
    if cancellation_requested(client, task):
        cancel_dependents(client, task["job_id"])
        finish_cancelled_render(client, task)
        client.delete(RENDER_RUNNING)
    return process.exitcode


if __name__ == "__main__":
    main()

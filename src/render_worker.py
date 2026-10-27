"""Coordinate chapter renders outside both the serial GPU and parallel CPU workers."""
import json
import os
import time

from filelock import FileLock

from cpu_queue import RENDER_RUNNING, connection
from task_queue import RENDER_QUEUE

PROCESSING = "chapter_render_jobs:processing"


def run_job(client, task):
    from artifact_editor.author.author import Author
    from artifact_editor.chapter.chapter import Chapter
    from gpu_tasks import render_context
    from websocket.server import run_video_job
    import const
    from task_timing import prepare, safe_record

    task = {**task, 'queue_started_at': time.time()}

    identity = {key: task[key] for key in ("author", "title", "chapter_number", "language")}
    token = render_context.set({**identity, "parent_job_id": task["job_id"]})
    client.set(RENDER_RUNNING, json.dumps(task))
    client.hset(f"video_job:{task['job_id']}", mapping={
        "execution_queue": "render", "action": task["action"],
        "job_id": task["job_id"], "state": "running", "queue_started_at": task['queue_started_at']})
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
            run_job(client, task)
        except Exception as exc:
            publish_progress(client, task["job_id"], 0, str(exc), "failed", task.get("action"))
        finally:
            client.lrem(PROCESSING, 1, payload)


if __name__ == "__main__":
    main()

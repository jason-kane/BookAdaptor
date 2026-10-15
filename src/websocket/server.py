"""WebSocket progress delivery and the Redis-backed video render worker."""

import json
import os
import time

import redis
from redis.maint_notifications import MaintNotificationsConfig
from flask import Flask
from flask_sock import Sock

import logger

log = logger.log(__name__)
app = Flask(__name__)
sock = Sock(app)
app.config["SOCK_SERVER_OPTIONS"] = {"ping_interval": 25}

REDIS_HOST = os.environ.get("REDIS_HOST", "redis")
VIDEO_QUEUE = "video_render_jobs"
VIDEO_EVENTS = "video_render_events"
LEGACY_PREFIX = "websocket_"


def redis_connection():
    return redis.Redis(
        host=REDIS_HOST, 
        # BRPOP waits five seconds; keep the client timeout longer so Redis
        # can return its empty-queue response before the socket times out.
        socket_timeout=10,
        decode_responses=True,
        # protocol=3,
        maint_notifications_config=MaintNotificationsConfig(enabled=False)
    )


def publish_progress(client, job_id, percent, message, state="running"):
    event = {
        "job_id": job_id,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "state": state,
    }
    client.hset(f"video_job:{job_id}", mapping=event)
    client.expire(f"video_job:{job_id}", 86400)
    client.publish(VIDEO_EVENTS, json.dumps(event))


def progress_html(event):
    job_id = event["job_id"]
    percent = event["percent"]
    message = event["message"]
    state = event["state"]
    escaped_message = (
        str(message).replace("&", "&amp;").replace("<", "&lt;")
        .replace(">", "&gt;").replace('"', "&quot;")
    )
    status = "success" if state == "complete" else "danger" if state == "failed" else "brand"
    return (
        f'<div id="video-progress-{job_id}" '
        f'hx-swap-oob="outerHTML:#video-progress-{job_id}" class="wa-stack">'
        f'<wa-progress-bar value="{percent}" variant="{status}"></wa-progress-bar>'
        f'<div>{percent}% — {escaped_message}</div></div>'
    )


def run_video_job(client, task):
    job_id = task["job_id"]
    publish_progress(client, job_id, 0, "Preparing video render")
    try:
        from artifact_editor.author.author import Author
        from artifact_editor.chapter.chapter import Chapter
        from artifact_editor.video.video import book_to_video

        chapter = Chapter(
            author=Author(task["author"]),
            title=task["title"],
            number=int(task["chapter_number"]),
            language=task["language"],
        )
        book_to_video(
            chapter,
            aspect=task["aspect"],
            force=bool(task.get("force", False)),
            progress=lambda percent, message: publish_progress(
                client, job_id, percent, message
            ),
        )
        publish_progress(client, job_id, 100, "Video render complete", "complete")
    except Exception as exc:
        log.exception("Video render job %s failed", job_id)
        publish_progress(client, job_id, 100, f"Video render failed: {exc}", "failed")


def video_worker():
    client = redis_connection()
    log.info("Video render worker started; waiting on Redis queue %s", VIDEO_QUEUE)
    while True:
        try:
            queued = client.brpop(VIDEO_QUEUE, timeout=5)
            if queued is None:
                continue
            _, payload = queued
            run_video_job(client, json.loads(payload))
        except Exception:
            log.exception("Video render worker loop failed")
            time.sleep(2)


@sock.route("/ws")
def websocket(ws):
    client = redis_connection()
    pubsub = client.pubsub()
    pubsub.subscribe(VIDEO_EVENTS)
    try:
        while True:
            incoming = ws.receive(timeout=0.1)
            if incoming:
                # The htmx WebSocket extension may send client messages. This
                # service only publishes server-side progress updates.
                continue

            event = pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
            if event and event["type"] == "message":
                try:
                    ws.send(progress_html(json.loads(event["data"])))
                except (redis.RedisError, ValueError, TypeError):
                    log.exception("Could not deliver video progress event")

            # Preserve the existing image-generation progress protocol.
            for key in client.scan_iter(f"{LEGACY_PREFIX}*"):
                message = client.lpop(key)
                if message is None:
                    continue
                queue_name = key.removeprefix(LEGACY_PREFIX)
                try:
                    chapter_key, image_index = queue_name.rsplit("_", 1)
                    value = client.incr(queue_name + "_counter")
                    if value == 1:
                        client.set(queue_name + "_steps", int(float(message)))
                    max_value = int(client.get(queue_name + "_steps") or 100)
                    percent = min(100, int(value / max_value * 100))
                    ws.send(
                        f'<wa-progress-ring hx-swap-oob="outerHTML:.image_{image_index}" '
                        f'class="image_{image_index}" value="{percent}"></wa-progress-ring>'
                    )
                    if message == "Complete":
                        client.delete(key, queue_name + "_steps", queue_name + "_counter")
                        author, title, chapter_number, language = json.loads(chapter_key)
                        img_url = os.path.join(
                            "library", author, title, str(chapter_number), language,
                            "images", "512", f"{image_index}.png",
                        )
                        ws.send(
                            f'<img hx-swap-oob="outerHTML:.image_{image_index}" '
                            f'class="image_{image_index}" style="height: 50%; width: 50%; '
                            f'align-self: center;" src="/{img_url}"/>'
                        )
                except (ValueError, TypeError, redis.RedisError):
                    log.exception("Invalid legacy WebSocket queue entry %s", key)
                    client.delete(key)
    finally:
        pubsub.close()


if __name__ == "__main__":
    # Compose runs this module in a dedicated worker container. The regular
    # Flask invocation imports the module and serves only WebSocket clients.
    video_worker()

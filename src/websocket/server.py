"""WebSocket progress delivery and the Redis-backed video render worker."""

import json
import os
import time
import uuid

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
    previous = client.hgetall(f"video_job:{job_id}")
    event.update({key: value for key, value in previous.items() if key.startswith("comfy_")})
    client.hset(f"video_job:{job_id}", mapping=event)
    client.expire(f"video_job:{job_id}", 86400)
    client.publish(VIDEO_EVENTS, json.dumps(event))


def publish_comfy_progress(client, job_id, percent, message):
    key = f"video_job:{job_id}"
    event = client.hgetall(key)
    event.update({
        "job_id": job_id, "comfy_percent": max(0, min(100, int(percent))),
        "comfy_message": message,
    })
    client.hset(key, mapping=event)
    client.expire(key, 86400)
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
    comfy_html = ""
    if "comfy_percent" in event:
        from html import escape
        comfy_percent = int(event["comfy_percent"])
        comfy_message = escape(str(event.get("comfy_message", "ComfyUI")))
        comfy_html = (
            f'<div class="wa-stack" aria-label="ComfyUI task progress">'
            f'<wa-progress-bar value="{comfy_percent}" aria-label="ComfyUI task progress"></wa-progress-bar>'
            f'<div>{comfy_percent}% — {comfy_message}</div></div>'
        )
    return (
        f'<div id="video-progress-{job_id}" '
        f'hx-swap-oob="outerHTML:#video-progress-{job_id}" class="wa-stack" '
        f'data-video-job="{job_id}" data-video-state="{state}">'
        f'<wa-progress-bar value="{percent}" variant="{status}"></wa-progress-bar>'
        f'<div>{percent}% — {escaped_message}</div>{comfy_html}</div>'
    )


def pending_video_progress(pubsub, client):
    """Coalesce queued notifications and send each job's current snapshot."""
    latest = {}
    # Bound each batch so a busy publisher cannot starve other socket work.
    for _ in range(1000):
        event = pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
        if event is None:
            break
        if event["type"] != "message":
            continue
        try:
            payload = json.loads(event["data"])
            latest[payload["job_id"]] = payload
        except (ValueError, TypeError, KeyError):
            log.exception("Invalid video progress event")
    return [client.hgetall(f"video_job:{job_id}") or payload
            for job_id, payload in latest.items()]


def run_video_job(client, task):
    job_id = task["job_id"]
    message = {"timeline_metadata": "Preparing image metadata", "timeline_image": "Preparing timeline image", "character_image": "Preparing character image", "prop_image": "Preparing prop image",
               "chapter_import": "Preparing chapter import",
               "chapter_missing_metadata": "Preparing missing metadata",
               "chapter_missing_images": "Preparing missing images",
               "chapter_missing_animations": "Preparing missing animations"}.get(
                   task.get("action"), "Preparing video render")
    publish_progress(client, job_id, 0, message)
    from comfy import workflow_progress
    progress_token = workflow_progress.set(
        lambda percent, message: publish_comfy_progress(client, job_id, percent, message)
    )
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
        if task.get("action") == "timeline_metadata":
            from artifact_editor.images.jobs import generate_timeline_metadata
            generate_timeline_metadata(chapter, task["image_index"], progress=lambda percent, message: publish_progress(
                client, job_id, percent, message
            ))
            publish_progress(client, job_id, 100, "Image metadata complete", "complete")
            return
        if task.get("action") == "timeline_image":
            from artifact_editor.images.jobs import generate_timeline_image
            generate_timeline_image(chapter, task["image_index"])
            publish_progress(client, job_id, 100, "Image generation complete", "complete")
            return
        if task.get("action") == "prop_image":
            from artifact_editor.props.props import draw_prop
            draw_prop(chapter, task["prop_tag"], description=task["description"])
            publish_progress(client, job_id, 100, "Prop image complete", "complete")
            return
        if task.get("action") == "character_image":
            from artifact_editor.characters.characters import draw_headshot
            draw_headshot(chapter, task["character_tag"], force=True, description=task["description"])
            publish_progress(client, job_id, 100, "Character image complete", "complete")
            return
        if task.get("action") in ("chapter_missing_images", "chapter_missing_animations"):
            method = (chapter.textoviva.generate_missing_images
                      if task["action"] == "chapter_missing_images"
                      else chapter.textoviva.generate_missing_animations)
            method(progress=lambda percent, message: publish_progress(client, job_id, percent, message))
            publish_progress(client, job_id, 100, "Missing assets complete", "complete")
            return
        if task.get("action") == "chapter_missing_metadata":
            from artifact_editor.images.images import generate_missing_metadata
            generate_missing_metadata(chapter, progress=lambda percent, message: publish_progress(
                client, job_id, percent, message
            ))
            publish_progress(client, job_id, 100, "Missing metadata complete", "complete")
            return
        if task.get("action") == "chapter_import":
            from artifact_editor.chapter.views import import_chapter_content
            import_chapter_content(
                chapter, sections_json=task["sections"],
                progress=lambda percent, message: publish_progress(
                    client, job_id, percent, message
                ),
            )
            publish_progress(client, job_id, 100, "Chapter import complete", "complete")
            return
        if task.get("action") == "textoviva_time_align":
            from artifact_editor.textoviva.time_align import align_chapter_time
            count = align_chapter_time(chapter, progress=lambda percent, message: publish_progress(
                client, job_id, percent, message
            ))
            publish_progress(client, job_id, 100, f"Time Align complete: {count} images replaced", "complete")
            return
        if task.get("action") in ("textoviva_rerender", "textoviva_regenerate_animation", "textoviva_missing_animation"):
            from artifact_editor.textoviva.jobs import rerender_chapter

            rerender_chapter(
                chapter,
                image_index=task.get("image_index"),
                animation_prompt=task.get("animation_prompt"),
                regenerate_insufficient=task.get("action") == "textoviva_missing_animation",
                progress=lambda percent, message: publish_progress(
                    client, job_id, percent, message
                ),
            )
        elif task.get("action") in (None, "video_render") and "aspect" in task:
            book_to_video(
                chapter,
                aspect=task["aspect"],
                force=bool(task.get("force", False)),
                progress=lambda percent, message: publish_progress(
                    client, job_id, percent, message
                ),
            )
        else:
            raise ValueError(f"Unsupported render job action: {task.get('action')!r}")
        publish_progress(client, job_id, 100, "Video render complete", "complete")
    except Exception as exc:
        log.exception("Video render job %s failed", job_id)
        label = {"timeline_metadata": "Image metadata generation", "timeline_image": "Image generation", "character_image": "Character image generation", "prop_image": "Prop image generation", "chapter_import": "Chapter import",
                 "chapter_missing_metadata": "Metadata generation",
                 "chapter_missing_images": "Image generation",
                 "chapter_missing_animations": "Animation generation"}.get(
                     task.get("action"), "Video render")
        publish_progress(client, job_id, 100, f"{label} failed: {exc}", "failed")
    finally:
        workflow_progress.reset(progress_token)


def video_worker():
    os.environ["VIDEO_WORKER_PROCESS"] = "1"
    client = redis_connection()
    log.info("Video render worker started; waiting on Redis queue %s", VIDEO_QUEUE)
    while True:
        try:
            queued = client.brpop(VIDEO_QUEUE, timeout=5)
            if queued is None:
                continue
            _, payload = queued
            task = json.loads(payload)
            if task.get("action") == "worker_rpc":
                run_worker_rpc(client, task)
            else:
                run_video_job(client, task)
        except Exception:
            log.exception("Video render worker loop failed")
            time.sleep(2)


def run_worker_rpc(client, task):
    from worker_operations import execute

    # Do not start requests whose caller has already timed out.
    if time.time() >= task["deadline"]:
        return
    response = {"job_id": task["job_id"]}
    try:
        response["result"] = execute(task["operation"], task["arguments"])
    except Exception as exc:
        log.exception("Worker RPC failed", operation=task["operation"])
        response["error"] = str(exc)
    key = f"worker_result:{task['job_id']}"
    with client.pipeline() as pipe:
        pipe.rpush(key, json.dumps(response))
        pipe.expire(key, 600)
        pipe.execute()


@sock.route("/worker-rpc")
def worker_rpc(ws):
    handle_worker_rpc(ws)


def handle_worker_rpc(ws):
    from worker_operations import OPERATIONS

    client = redis_connection()
    result_key = None
    try:
        raw = ws.receive(timeout=10)
        if raw is None:
            return
        task = json.loads(raw)
        job_id = uuid.UUID(task["job_id"]).hex
        if task["operation"] not in OPERATIONS or not isinstance(task["arguments"], dict):
            raise ValueError("Unsupported worker request")
        task = {"job_id": job_id, "action": "worker_rpc",
                "operation": task["operation"], "arguments": task["arguments"],
                "deadline": time.time() + 590}
        result_key = f"worker_result:{job_id}"
        client.lpush(VIDEO_QUEUE, json.dumps(task))
        deadline = task["deadline"]
        while time.time() < deadline:
            result = client.blpop(result_key, timeout=5)
            if result:
                ws.send(result[1])
                return
        ws.send(json.dumps({"job_id": job_id, "error": "Worker request timed out"}))
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        ws.send(json.dumps({"error": str(exc)}))
    finally:
        if result_key:
            client.delete(result_key)
        client.close()


@sock.route("/ws")
def websocket(ws):
    handle_websocket(ws)


def handle_websocket(ws):
    client = redis_connection()
    pubsub = client.pubsub()
    pubsub.subscribe(VIDEO_EVENTS)
    try:
        while True:
            incoming = ws.receive(timeout=0.1)
            if incoming:
                try:
                    task = json.loads(incoming)
                    if task.get("action") in ("character_image", "prop_image", "chapter_missing_images", "chapter_missing_animations", "chapter_missing_metadata", "chapter_import", "textoviva_rerender", "textoviva_time_align", "textoviva_missing_animation"):
                        character_fields = {}
                        if task["action"] == "character_image":
                            tag, description = task.get("character_tag"), task.get("description")
                            if not isinstance(tag, str) or not tag or not isinstance(description, str) or not description.strip():
                                raise ValueError("Character and description are required")
                            character_fields = {"character_tag": tag, "description": description}
                        if task["action"] == "prop_image":
                            tag, description = task.get("prop_tag"), task.get("description")
                            if not isinstance(tag, str) or not tag or not isinstance(description, str) or not description.strip():
                                raise ValueError("Prop and description are required")
                            character_fields = {"prop_tag": tag, "description": description}
                        sections = task.get("sections")
                        if task["action"] == "chapter_import":
                            if not isinstance(sections, str) or not isinstance(json.loads(sections), list):
                                raise ValueError("Invalid chapter sections")
                        # Accept only the explicit render command and chapter identity.
                        task = {key: task[key] for key in (
                            "action", "job_id", "author", "title",
                            "chapter_number", "language",
                        )}
                        task.update(character_fields)
                        task["job_id"] = uuid.UUID(task["job_id"]).hex
                        task["chapter_number"] = int(task["chapter_number"])
                        if task["action"] == "chapter_import":
                            task["sections"] = sections
                        label = "Queued for chapter import" if task["action"] == "chapter_import" else "Queued for Time Align" if task["action"] == "textoviva_time_align" else "Queued for re-render"
                        if task["action"] in ("prop_image", "character_image"):
                            label = "Queued for " + ("prop" if task["action"] == "prop_image" else "character") + " image generation"
                        elif task["action"] == "chapter_missing_metadata":
                            label = "Queued for missing metadata"
                        elif task["action"] in ("chapter_missing_images", "chapter_missing_animations"):
                            label = "Queued for missing " + task["action"].removeprefix("chapter_missing_")
                        publish_progress(client, task["job_id"], 0, label, "queued")
                        client.lpush(VIDEO_QUEUE, json.dumps(task))
                except (ValueError, TypeError, KeyError, AttributeError):
                    log.exception("Invalid WebSocket render command")

            for event in pending_video_progress(pubsub, client):
                try:
                    ws.send(progress_html(event))
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

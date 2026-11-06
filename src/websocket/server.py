"""WebSocket progress delivery and the Redis-backed video render worker."""

import json
import os
import re
import time
import traceback
import uuid
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from styler.queue_history import (
    publish_queue_event,
    recent_completions,
    recent_failures,
)
from task_queue import (
    CHAPTER_IMAGE_EVENTS,
    FRAME_EVENTS,
    IDLE_QUEUE,
    PRIORITY_QUEUE,
    TAG_QUEUE,
    VIDEO_EVENTS,
    VIDEO_QUEUE,
    enqueue_task,
    enqueue_payload,
    pop_gpu_service_switch,
    promote_job,
    remove_queued_job,
)
from styler.idle_maintenance import (
    IdleMaintenance,
    background_ready,
    get_mode,
    mark_activity,
    pop_idle_job,
    set_mode,
)

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
LEGACY_PREFIX = "websocket_"
GPU_SWITCH_REQUEST_KEY = "gpu:manual-switch-request"


def redis_connection():
    return redis.Redis(
        host=REDIS_HOST,
        # BRPOP waits five seconds; keep the client timeout longer so Redis
        # can return its empty-queue response before the socket times out.
        socket_timeout=10,
        decode_responses=True,
        # protocol=3,
        maint_notifications_config=MaintNotificationsConfig(enabled=False),
    )


def cleanup_startup_progress():
    from progress_cleanup import expire_stale_progress

    client = redis_connection()
    try:
        count = expire_stale_progress(client)
        log.info("Expired stale Redis progress snapshots", expired=count)
    except redis.RedisError:
        log.exception("Could not expire stale Redis progress at startup")
    finally:
        client.close()


cleanup_startup_progress()


def publish_progress(client, job_id, percent, message, state="running", action=None,
                     debug_details=None):
    event = {
        "job_id": job_id,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "state": state,
    }
    previous = client.hgetall(f"video_job:{job_id}")
    event.update({key: previous[key] for key in ("execution_queue", "parent_job_id", "queue_started_at",
                  "timing_key", "timing_units", "timing_unit", "theater_project_id", "theater_stage")
                  if key in previous})
    if state == 'running' and 'queue_started_at' not in event:
        event['queue_started_at'] = time.time()
    event.update(
        {key: value for key, value in previous.items() if key.startswith("comfy_")}
    )
    if "image_index" in previous:
        event["image_index"] = previous["image_index"]
    action = action or previous.get("action")
    if action:
        event["action"] = action
    if action in {
        "chapter_missing_animations",
        "textoviva_regenerate_animation",
        "textoviva_missing_animation",
    }:
        image_match = re.search(r"(?:image|shot)\s+(\d+)", message, re.IGNORECASE)
        if image_match:
            event["image_index"] = int(image_match.group(1))
    if debug_details:
        event["debug_details"] = debug_details
    client.hset(f"video_job:{job_id}", mapping=event)
    client.expire(f"video_job:{job_id}", 86400)
    from task_timing import enrich
    event = enrich(client, event)
    publish_queue_event(client, VIDEO_EVENTS, event)


def publish_comfy_progress(client, job_id, percent, message):
    key = f"video_job:{job_id}"
    event = client.hgetall(key)
    event.update(
        {
            "job_id": job_id,
            "comfy_percent": max(0, min(100, int(percent))),
            "comfy_message": message,
        }
    )
    client.hset(key, mapping=event)
    client.expire(key, 86400)
    client.publish(VIDEO_EVENTS, json.dumps(event))


def progress_html(event):
    from html import escape
    from task_timing import eta_label

    job_id = escape(str(event['job_id']), quote=True)
    state = escape(str(event['state']), quote=True)
    message = escape(re.sub(r"\(?\b\d+(?:\.\d+)?\s*%\)?\s*[—·-]?\s*", "", str(event.get('message', ''))))
    status = 'success' if state == 'complete' else 'danger' if state == 'failed' else 'neutral' if state == 'cancelled' else 'brand'
    payload = {key: event[key] for key in ('job_id', 'state', 'action', 'image_index', 'timing', 'queue_started_at', 'duration_seconds') if key in event}
    metadata = escape(json.dumps(payload), quote=True)
    bar = 'value="100"' if state == 'complete' else 'value="0"' if state == 'cancelled' else 'indeterminate'
    detail = escape(str(event.get('comfy_message', '')))
    return (
        f'<div id="video-progress-{job_id}" data-task-progress '
        f'hx-swap-oob="outerHTML:#video-progress-{job_id}" class="wa-stack" '
        f'data-video-job="{job_id}" data-video-state="{state}" '
        f'data-video-action="{escape(str(event.get("action", "")), quote=True)}" '
        f'data-image-index="{escape(str(event.get("image_index", "")), quote=True)}">'
        f'<wa-progress-bar {bar} data-task-timing="{metadata}" variant="{status}"></wa-progress-bar>'
        f'<span data-task-eta class="task-timing-label">{escape(eta_label(event))}</span>'
        f'<small data-task-timing-stats class="task-timing-stats"></small>'
        f'<div>{message}</div>' + (f'<small>{detail}</small>' if detail else '') + '</div>'
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
    from task_timing import enrich
    return [enrich(client, client.hgetall(f"video_job:{job_id}") or payload)
            for job_id, payload in latest.items()]


def run_chapter_add_all(chapter, progress):
    """Generate every missing chapter asset and report aggregate progress."""
    from artifact_editor.characters.characters import (
        extract_characters, assign_phrase_speakers, extract_all_description_timelines,
    )
    from artifact_editor.props.props import extract_props
    from artifact_editor.images.images import generate_missing_metadata
    from artifact_editor.phrases.phrases import generate_all_audio
    from artifact_editor.chapter.timeline import place_image_slots
    from gpu_tasks import task_label
    from artifact_editor.chapter.workflow import ACTIONS, complete_step

    def process_appearances(report):
        result = extract_all_description_timelines(chapter, progress=report)
        if result["failures"]:
            raise RuntimeError(
                "Character appearance processing failed: " + "; ".join(result["failures"])
            )

    stages = [
        ("Adding missing characters", lambda report: extract_characters(chapter)),
        ("Assigning characters", lambda report: assign_phrase_speakers(chapter, reassign_all=True, progress=report)),
        ("Processing appearance changes for all characters", process_appearances),
        ("Generating missing audio", lambda report: generate_all_audio(chapter, force=False, batch=True, progress=report)),
        ("Placing image slots", lambda report: place_image_slots(chapter)),
        ("Finding missing props", lambda report: extract_props(chapter)),
        ("Generating missing metadata", lambda report: generate_missing_metadata(chapter, progress=report)),
        ("Generating missing images", lambda report: chapter.textoviva.generate_missing_images(progress=report)),
        ("Generating missing animations", lambda report: chapter.textoviva.generate_missing_animations(progress=report, regenerate_insufficient=True)),
    ]
    stage_width = 100 / len(stages)
    for position, (label, operation) in enumerate(stages):
        progress(position * stage_width, label)
        stage_lock = Lock()
        stage_percent = position * stage_width

        def stage_progress(percent, detail):
            nonlocal stage_percent
            scaled = (position + max(0, min(100, int(percent))) / 100) * stage_width
            with stage_lock:
                if scaled <= stage_percent:
                    return
                stage_percent = scaled
                progress(scaled, detail)

        with task_label(label):
            operation(stage_progress)
        complete_step(chapter, ACTIONS[position + 1])
        progress((position + 1) * stage_width, f"{label} complete")


def run_video_job(client, task):
    # Provider handoffs can move priority caption jobs into the normal GPU queue.
    if task.get("action") == "lora_captions":
        from artifact_editor.book.lora_captioning import run_job
        return run_job(client, task)
    if task.get("action") in ("gpu_workflow", "gpu_llama"):
        from gpu_tasks import run_job
        return run_job(client, task)
    # Drain renders submitted by older producers into the coordinator queue.
    # The GPU worker must remain free for their dependent LLM workflows.
    from task_queue import RENDER_ACTIONS
    if (os.environ.get("VIDEO_WORKER_PROCESS") == "1" and (
        task.get("action") in RENDER_ACTIONS
        or (not task.get("action") and "aspect" in task)
    )):
        enqueue_task(client, VIDEO_QUEUE, task)
        return
    chapter = None

    def report_progress(*args, **kwargs):
        state = kwargs.get("state", args[4] if len(args) > 4 else "running")
        if state == "complete" and chapter is not None:
            from artifact_editor.chapter.workflow import complete_step
            complete_step(chapter, task.get("action"))
        publish_progress(*args, **kwargs)

    job_id = task["job_id"]
    message = {
        "scene_description": "Preparing scene description",
        "scene_image": "Preparing scene image",
        "timeline_metadata": "Preparing image metadata",
        "timeline_animation_description": "Preparing animation description",
        "textoviva_regenerate_animation": "Preparing image animation",
        "timeline_image": "Preparing timeline image",
        "character_image": "Preparing character image",
        "prop_image": "Preparing prop image",
        "chapter_import": "Preparing chapter import",
        "chapter_missing_metadata": "Preparing missing metadata",
        "chapter_missing_images": "Preparing missing images",
        "chapter_missing_animations": "Preparing missing animations",
        "chapter_missing_characters": "Finding missing characters",
        "chapter_character_appearances": "Processing character appearance changes",
        "chapter_assign_characters": "Assigning phrase speakers",
        "chapter_place_image_slots": "Placing image slots",
        "chapter_missing_audio": "Generating missing audio",
        "chapter_missing_props": "Finding missing props",
        "chapter_add_all": "Adding all missing chapter assets",
        "textoviva_one_shot": "Starting One Shot chapter workflow",
    }.get(task.get("action"), task.get("job_type") or "Preparing chapter video render")
    report_progress(client, job_id, 0, message, action=task.get("action"))
    from comfy import workflow_progress

    progress_token = workflow_progress.set(
        lambda percent, message: publish_comfy_progress(
            client, job_id, percent, message
        )
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
        from artifact_editor.chapter.workflow import begin_step
        begin_step(chapter, task.get("action"))
        if task.get("action") in (
            "scene_description",
            "scene_image",
            "scene_tod",
            "scene_lighting",
        ):
            from artifact_editor.chapter.scenes import (
                generate_description,
                generate_image,
                generate_time_of_day,
                generate_lighting,
            )

            scene = chapter.get_xml().find("scene", index=str(task["scene_index"]))
            if scene is None:
                raise ValueError("Scene no longer exists")
            if task["action"] == "scene_description":
                generate_description(chapter, scene)
            elif task["action"] == "scene_lighting":
                generate_lighting(chapter, scene)
            elif task["action"] == "scene_tod":
                generate_time_of_day(chapter, scene)
            else:
                generate_image(chapter, scene, task["description"])
            report_progress(
                client, job_id, 100, "Scene generation complete", "complete"
            )
            return
        if task.get("action") == "timeline_metadata":
            from artifact_editor.images.jobs import generate_timeline_metadata

            generate_timeline_metadata(
                chapter,
                task["image_index"],
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(client, job_id, 100, "Image metadata complete", "complete")
            return
        if task.get("action") == "timeline_animation_description":
            from artifact_editor.images.jobs import generate_timeline_animation_description

            generate_timeline_animation_description(
                chapter,
                task["image_index"],
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(
                client, job_id, 100, "Animation description complete", "complete"
            )
            return
        if task.get("action") == "timeline_image":
            from artifact_editor.images.jobs import generate_timeline_image

            generate_timeline_image(chapter, task["image_index"], force=True)
            report_progress(
                client, job_id, 100, "Image generation complete", "complete"
            )
            return
        if task.get("action") == "prop_image":
            from artifact_editor.props.props import draw_prop

            draw_prop(chapter, task["prop_tag"], description=task["description"])
            report_progress(client, job_id, 100, "Prop image complete", "complete")
            return
        if task.get("action") == "character_image":
            from artifact_editor.characters.characters import draw_headshot

            draw_headshot(
                chapter,
                task["character_tag"],
                force=True,
                description=task["description"],
            )
            report_progress(
                client, job_id, 100, "Character image complete", "complete"
            )
            return
        if task.get("action") in (
            "chapter_missing_images",
            "chapter_missing_animations",
        ):
            method = (
                chapter.textoviva.generate_missing_images
                if task["action"] == "chapter_missing_images"
                else chapter.textoviva.generate_missing_animations
            )
            method(
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
                # An animation that exists but ends before its image's audio
                # is still missing usable coverage. Repair short animations
                # when the user asks to add missing animations.
                **({"regenerate_insufficient": True}
                   if task["action"] == "chapter_missing_animations" else {})
            )
            report_progress(client, job_id, 100, "Missing assets complete", "complete")
            return
        if task.get("action") == "chapter_missing_metadata":
            from artifact_editor.images.images import generate_missing_metadata

            generate_missing_metadata(
                chapter,
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(
                client, job_id, 100, "Missing metadata complete", "complete"
            )
            return
        if task.get("action") in ("chapter_missing_characters", "chapter_missing_props"):
            if task["action"] == "chapter_missing_characters":
                from artifact_editor.characters.characters import extract_characters
                count = extract_characters(chapter)
                noun = "characters"
            else:
                from artifact_editor.props.props import extract_props
                count = extract_props(chapter)
                noun = "props"
            message = f"Found {count} missing {noun}"
            report_progress(client, job_id, 100, message, "complete", task["action"])
            return
        if task.get("action") == "chapter_assign_characters":
            from artifact_editor.characters.characters import assign_phrase_speakers

            assigned = assign_phrase_speakers(
                chapter, reassign_all=True,
                progress=lambda percent, detail: report_progress(
                    client, job_id, percent, detail, action=task["action"]
                ),
            )
            unresolved = sum(phrase.get("speaker_source") == "llm_unresolved"
                             for phrase in chapter.get_xml().find_all("phrase"))
            message = f"Speaker assignment complete; {assigned} speakers updated"
            if unresolved:
                message += f"; {unresolved} phrases need speaker review"
            report_progress(client, job_id, 100, message, "complete", task["action"])
            return
        if task.get("action") == "chapter_character_appearances":
            from artifact_editor.characters.characters import extract_all_description_timelines

            result = extract_all_description_timelines(
                chapter,
                progress=lambda percent, detail: report_progress(client, job_id, percent, detail),
            )
            message = (
                f"Processed appearance changes for {result['characters']} characters; "
                f"{result['changes']} changes in this chapter"
            )
            failures = result["failures"]
            if failures:
                message += ". Failed: " + "; ".join(failures)
            report_progress(client, job_id, 100, message,
                             "failed" if failures else "complete", task["action"])
            return
        if task.get("action") == "chapter_missing_audio":
            from artifact_editor.phrases.phrases import generate_all_audio

            generate_all_audio(chapter, force=False, batch=True,
                               progress=lambda percent, detail: report_progress(client, job_id, percent, detail))
            report_progress(client, job_id, 100, "Missing audio complete", "complete")
            return
        if task.get("action") == "chapter_place_image_slots":
            from artifact_editor.chapter.timeline import place_image_slots

            place_image_slots(chapter)
            report_progress(client, job_id, 100, "Image slot placement complete", "complete")
            return
        if task.get("action") == "chapter_add_all":
            run_chapter_add_all(
                chapter,
                lambda percent, detail: report_progress(client, job_id, percent, detail),
            )
            report_progress(client, job_id, 100, "All chapter assets ready; assemble frames and generate video separately", "complete")
            return
        if task.get("action") == "textoviva_one_shot":
            from artifact_editor.chapter.workflow import begin_step, complete_step
            from artifact_editor.chapter.views import import_chapter_content
            from artifact_editor.textoviva.jobs import rerender_chapter

            def report_phase(start, width):
                return lambda percent, detail: report_progress(
                    client, job_id,
                    start + width * max(0, min(100, int(percent))) / 100,
                    detail, action="textoviva_one_shot",
                )

            begin_step(chapter, "chapter_import")
            report_progress(client, job_id, 0, "Importing chapter text", action="textoviva_one_shot")
            import_chapter_content(
                chapter,
                sections_json=task.get("sections"),
                progress=report_phase(0, 8),
            )
            complete_step(chapter, "chapter_import")

            begin_step(chapter, "chapter_add_all")
            report_progress(client, job_id, 8, "Adding all missing assets", action="textoviva_one_shot")
            run_chapter_add_all(chapter, report_phase(8, 72))

            begin_step(chapter, "textoviva_rerender")
            report_progress(client, job_id, 80, "Generating chapter video", action="textoviva_one_shot")
            rerender_chapter(
                chapter,
                progress=lambda percent, detail: report_progress(
                    client, job_id,
                    80 + 0.2 * max(0, min(100, int(percent))),
                    detail, action="textoviva_one_shot",
                ),
            )
            complete_step(chapter, "textoviva_rerender")
            report_progress(client, job_id, 100, "One Shot complete; chapter video generated", "complete", "textoviva_one_shot")
            return
        if task.get("action") == "chapter_import":
            from artifact_editor.chapter.views import import_chapter_content

            import_chapter_content(
                chapter,
                sections_json=task["sections"],
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(client, job_id, 100, "Chapter import complete", "complete")
            return
        if task.get("action") == "textoviva_time_align":
            from artifact_editor.textoviva.time_align import align_chapter_time

            count = align_chapter_time(
                chapter,
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(
                client,
                job_id,
                100,
                f"Time Align complete: {count} images replaced",
                "complete",
            )
            return
        if task.get("action") == "textoviva_regenerate_animation":
            chapter.load_xml(force=True)
            image_index = int(task["image_index"])
            image = chapter.get_image(index=image_index)
            if image is None:
                raise ValueError(f"Image {image_index} not found")
            image.attrs["animation_prompt"] = task["animation_prompt"]
            chapter.save_image(image)
            chapter.textoviva.generate_missing_animations(
                force=True,
                regenerate_image_index=image_index,
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
            report_progress(
                client, job_id, 100, f"Image {image_index} animation complete", "complete"
            )
            return
        if task.get("action") in (
            "textoviva_rerender",
            "textoviva_missing_animation",
        ):
            from artifact_editor.textoviva.jobs import rerender_chapter

            rerender_chapter(
                chapter,
                image_index=task.get("image_index"),
                animation_prompt=task.get("animation_prompt"),
                regenerate_insufficient=task.get("action")
                == "textoviva_missing_animation",
                theater_stage=task.get("theater_stage"),
                theater_project_id=task.get("theater_project_id"),
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
        elif task.get("action") in (None, "video_render") and "aspect" in task:
            book_to_video(
                chapter,
                aspect=task["aspect"],
                force=bool(task.get("force", False)),
                progress=lambda percent, message: report_progress(
                    client, job_id, percent, message
                ),
            )
        else:
            raise ValueError(f"Unsupported render job action: {task.get('action')!r}")
        report_progress(client, job_id, 100, "Video render complete", "complete")
    except Exception as exc:
        log.exception("Video render job %s failed", job_id)
        try:
            last_progress = client.hget(f"video_job:{job_id}", "message")
        except redis.RedisError:
            log.exception("Could not load the last progress message for failed job %s", job_id)
            last_progress = None
        label = {
            "textoviva_regenerate_animation": "Image animation",
            "scene_description": "Scene description generation",
            "scene_image": "Scene image generation",
            "timeline_metadata": "Image metadata generation",
            "timeline_animation_description": "Animation description generation",
            "timeline_image": "Image generation",
            "character_image": "Character image generation",
            "prop_image": "Prop image generation",
            "chapter_import": "Chapter import",
            "chapter_missing_metadata": "Metadata generation",
            "chapter_missing_images": "Image generation",
            "chapter_missing_animations": "Animation generation",
            "chapter_missing_characters": "Character extraction",
            "chapter_character_appearances": "Character appearance processing",
            "chapter_assign_characters": "Phrase speaker assignment",
            "chapter_place_image_slots": "Image slot placement",
            "chapter_missing_audio": "Audio generation",
            "chapter_missing_props": "Prop extraction",
            "chapter_add_all": "Add all chapter assets",
            "textoviva_one_shot": "One Shot chapter workflow",
        }.get(task.get("action"), "Video render")
        failure_message = f"{label} failed: {exc}"
        if task.get("action") == "textoviva_one_shot" and last_progress:
            failure_message = f"One Shot failed during {last_progress}: {exc}"
        report_progress(
            client, job_id, 100, failure_message, "failed", task.get("action"),
            debug_details=traceback.format_exc(),
        )
    finally:
        workflow_progress.reset(progress_token)


def _active_task_provider(client):
    """Translate the active GPU service into the queue provider name."""
    service = client.get("gpu:active-service")
    if isinstance(service, bytes):
        service = service.decode()
    return {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}.get(service)


def _pop_priority_task_for_provider(client, *, allow_background=True):
    """Pop eligible priority work, including a provider change after drain."""
    from styler.image_tags import pop_tag_job

    switch_task = pop_gpu_service_switch(client, PRIORITY_QUEUE)
    if switch_task:
        return switch_task
    # Explicit user work must not wait for background tasks on the loaded
    # provider to drain. Dispatch it so run_tracked_task can switch providers.
    interactive = pop_tag_job(client, PRIORITY_QUEUE, allow_background=False)
    if interactive:
        return interactive
    provider = _active_task_provider(client)
    if provider is None:
        return pop_tag_job(
            client, PRIORITY_QUEUE, allow_background=allow_background
        )
    control = pop_tag_job(
        client, PRIORITY_QUEUE, provider="none",
        allow_background=allow_background,
    )
    if control:
        return control
    task = pop_tag_job(
        client, PRIORITY_QUEUE, provider=provider,
        allow_background=allow_background,
    )
    if task:
        return task
    if not _has_queued_provider_task(client, provider):
        # Let run_tracked_task requeue this task and perform the provider
        # transition once work for the active provider has drained.
        return pop_tag_job(
            client, PRIORITY_QUEUE, allow_background=allow_background
        )
    return None


def _has_queued_provider_task(client, provider):
    """Check whether the active provider still has interactive queued work."""
    from task_queue import task_provider

    allow_background = get_mode(client) != "off"
    for queue in (PRIORITY_QUEUE, VIDEO_QUEUE, TAG_QUEUE):
        for payload in client.lrange(queue, 0, -1):
            try:
                task = json.loads(payload)
            except (TypeError, ValueError):
                continue
            if not isinstance(task, dict) or task_provider(task) != provider:
                continue
            if not allow_background and not task.get("promoted") and (
                task.get("idle_maintenance")
                or task.get("background_category")
                or task.get("action") in (
                    "styler_scan_image_tags",
                    "stockpile_image_metadata",
                )
            ):
                continue
            description_job_id = task.get("description_waiting_on")
            if description_job_id and client.exists(
                f"styler:description-pending:{description_job_id}"
            ):
                # This tag job cannot run until the description's provider has
                # completed its queued dependency, so it must not block a switch.
                continue
            if task_provider(task) == provider:
                return True
    return False


def wait_for_worker_redis(client):
    """Keep the worker alive while Redis starts or restores its dataset."""
    waiting = False
    while True:
        try:
            client.ping()
            if waiting:
                log.info("Redis is ready; video worker resuming")
            return
        except redis.exceptions.AuthenticationError:
            raise
        except (redis.exceptions.BusyLoadingError, redis.ConnectionError, redis.TimeoutError):
            if not waiting:
                log.warning("Video worker waiting for Redis to finish loading and accept connections")
                waiting = True
            time.sleep(2)


def video_worker():
    os.environ["VIDEO_WORKER_PROCESS"] = "1"
    client = redis_connection()
    wait_for_worker_redis(client)
    from gpu_service import initialize_active_service

    initialize_active_service(client)
    log.info("Video render worker started; waiting on Redis queue %s", VIDEO_QUEUE)
    reset_job_id = client.get("styler:worker-reset-ready")
    if reset_job_id:
        client.delete("styler:worker-reset-ready", "styler:worker-reset-pending")
        from styler.jobs import EVENTS

        client.publish(
            EVENTS,
            json.dumps(
                dict(
                    job_id=reset_job_id,
                    action="worker_reset",
                    state="worker_ready",
                    job_type="Worker reset",
                    message="Worker restarted and ready",
                )
            ),
        )
    client.delete("styler:worker-running")
    client.delete("styler:worker-model")
    styler_engine = None
    tag_style = None
    import const
    maintenance = IdleMaintenance(
        client, const.LIBRARY_DIR, VIDEO_QUEUE, PRIORITY_QUEUE, TAG_QUEUE
    )
    mark_activity(client)
    while True:
        try:
            allow_background = get_mode(client) != "off"
            client.set("styler:worker-model-batching", "1", ex=30)
            from styler.image_tags import pop_tag_job
            from styler.jobs import pop_engine_sample, pop_interactive_job
            from styler.style_scan import pop_scan_job

            active_provider = _active_task_provider(client)
            if active_provider == "training":
                # Training mode is a manual GPU lease. Only an explicit mode
                # switch may leave it; keep every other GPU task queued.
                mode_switch = pop_gpu_service_switch(client, PRIORITY_QUEUE, manual_only=True)
                if mode_switch:
                    run_priority_task(client, mode_switch)
                    continue
                time.sleep(1)
                continue
            if active_provider == "llama":
                priority_tag = _pop_priority_task_for_provider(
                    client, allow_background=allow_background
                )
                if priority_tag:
                    styler_engine = None
                    client.delete("styler:worker-model")
                    if handle_worker_reset_task(client, priority_tag):
                        return
                    if priority_tag.get("action") in (
                        "styler_tag_sample",
                        "styler_tag_group",
                    ):
                        tag_style = priority_tag["style_tag"]
                    styler_engine = run_priority_task(client, priority_tag)
                    continue
                tag_payload = pop_tag_job(
                    client, TAG_QUEUE, tag_style, provider="llama",
                    allow_background=allow_background,
                )
                if tag_payload:
                    styler_engine = None
                    client.delete("styler:worker-model")
                    tag_style = tag_payload["style_tag"]
                    run_queued_tag_task(client, tag_payload, const.LIBRARY_DIR)
                    continue
                scan_payload = (
                    pop_scan_job(client, VIDEO_QUEUE, provider="llama")
                    if allow_background else None
                )
                if scan_payload:
                    from styler.style_scan import run_job as run_style_scan_job

                    scan_task = json.loads(scan_payload)
                    styler_engine = None
                    client.delete("styler:worker-model")
                    run_tracked_task(
                        client, run_style_scan_job, client, scan_task
                    )
                    continue

            payload = None
            if payload is None:
                priority_tag = _pop_priority_task_for_provider(
                    client, allow_background=allow_background
                )
                if priority_tag:
                    styler_engine = None
                    client.delete("styler:worker-model")
                    if handle_worker_reset_task(client, priority_tag):
                        return
                    if priority_tag.get("action") in (
                        "styler_tag_sample",
                        "styler_tag_group",
                    ):
                        tag_style = priority_tag["style_tag"]
                    styler_engine = run_priority_task(client, priority_tag)
                    continue
            # Continue work for the loaded generation model before switching
            # to another non-priority task. Priority tag jobs were checked above.
            if styler_engine:
                payload = pop_engine_sample(
                    client, VIDEO_QUEUE, styler_engine,
                    allow_background=allow_background,
                )
            if payload is None:
                payload = pop_interactive_job(client, VIDEO_QUEUE)
            if payload is None:
                if allow_background:
                    payload = pop_scan_job(
                        client, VIDEO_QUEUE, provider=active_provider
                    )
            if payload is None:
                from styler.backfill import STATE as BACKFILL_STATE

                backfill_ready = allow_background and background_ready(client)
                if client.exists(BACKFILL_STATE) and backfill_ready:
                    pending_payload = client.rpop(VIDEO_QUEUE)
                    queued = (VIDEO_QUEUE, pending_payload) if pending_payload else None
                else:
                    tag_payload = pop_tag_job(
                        client, TAG_QUEUE, tag_style,
                        allow_background=allow_background,
                    )
                    if tag_payload:
                        styler_engine = None
                        client.delete("styler:worker-model")
                        tag_style = tag_payload["style_tag"]
                        run_queued_tag_task(client, tag_payload, const.LIBRARY_DIR)
                        continue
                    queued = (
                        client.brpop(VIDEO_QUEUE, timeout=5)
                        if allow_background else None
                    )
                if queued is None:
                    # A priority request may have arrived while BRPOP waited.
                    priority_tag = _pop_priority_task_for_provider(
                        client, allow_background=allow_background
                    )
                    if priority_tag:
                        styler_engine = None
                        client.delete("styler:worker-model")
                        if handle_worker_reset_task(client, priority_tag):
                            return
                        if priority_tag.get("action") in (
                            "styler_tag_sample",
                            "styler_tag_group",
                        ):
                            tag_style = priority_tag["style_tag"]
                        styler_engine = run_priority_task(client, priority_tag)
                        continue
                    # Backfill can mint new sample work on demand. Drain an
                    # already queued image-tag task before creating that work,
                    # so hidden maintenance jobs cannot jump ahead of the
                    # queue's visible next task.
                    tag_payload = pop_tag_job(
                        client, TAG_QUEUE, tag_style,
                        allow_background=allow_background,
                    )
                    if tag_payload:
                        styler_engine = None
                        client.delete("styler:worker-model")
                        tag_style = tag_payload["style_tag"]
                        run_queued_tag_task(client, tag_payload, const.LIBRARY_DIR)
                        continue
                    import const
                    from styler.backfill import next_sample, next_tag_sample

                    backfill_ready = allow_background and background_ready(client)
                    background = (
                        next_sample(
                            client, VIDEO_QUEUE, const.LIBRARY_DIR, styler_engine
                        )
                        if backfill_ready
                        else None
                    )
                    if background:
                        payload = json.dumps(background)
                    else:
                        from styler.backfill import STATE as BACKFILL_STATE
                        tag_task = (
                            next_tag_sample(client, VIDEO_QUEUE, const.LIBRARY_DIR)
                            if backfill_ready
                            else None
                        )
                        if tag_task:
                            priority_tag = _pop_priority_task_for_provider(
                                client, allow_background=allow_background
                            )
                            if priority_tag:
                                client.lpush(TAG_QUEUE, json.dumps(tag_task))
                                styler_engine = None
                                client.delete("styler:worker-model")
                                if handle_worker_reset_task(client, priority_tag):
                                    return
                                if priority_tag.get("action") in (
                                    "styler_tag_sample",
                                    "styler_tag_group",
                                ):
                                    tag_style = priority_tag["style_tag"]
                                styler_engine = run_priority_task(client, priority_tag)
                                continue
                            styler_engine = None
                            client.delete("styler:worker-model")
                            tag_style = tag_task["style_tag"]
                            run_queued_tag_task(client, tag_task, const.LIBRARY_DIR)
                        elif not client.exists(BACKFILL_STATE) or not backfill_ready:
                            tag_payload = pop_tag_job(
                                client, TAG_QUEUE, tag_style,
                                allow_background=allow_background,
                            )
                            if tag_payload:
                                priority_tag = _pop_priority_task_for_provider(
                                    client, allow_background=allow_background
                                )
                                if priority_tag:
                                    client.lpush(TAG_QUEUE, json.dumps(tag_payload))
                                    styler_engine = None
                                    client.delete("styler:worker-model")
                                    if handle_worker_reset_task(client, priority_tag):
                                        return
                                    if priority_tag.get("action") in (
                                        "styler_tag_sample",
                                        "styler_tag_group",
                                    ):
                                        tag_style = priority_tag["style_tag"]
                                    styler_engine = run_priority_task(
                                        client, priority_tag
                                    )
                                    continue
                                styler_engine = None
                                client.delete("styler:worker-model")
                                tag_style = tag_payload["style_tag"]
                                run_queued_tag_task(
                                    client, tag_payload, const.LIBRARY_DIR
                                )
                            else:
                                idle_task = (
                                    pop_idle_job(
                                        client, VIDEO_QUEUE, PRIORITY_QUEUE, TAG_QUEUE
                                    )
                                    if allow_background else None
                                )
                                if idle_task:
                                    styler_engine = None
                                    client.delete("styler:worker-model")
                                    if idle_task.get("action") in (
                                        "styler_tag_sample",
                                        "styler_tag_group",
                                    ):
                                        tag_style = idle_task["style_tag"]
                                    styler_engine = run_priority_task(client, idle_task)
                                else:
                                    maintenance.tick()
                                    if not allow_background:
                                        time.sleep(0.5)
                        continue
                else:
                    _, payload = queued
            # A priority request may arrive while we wait for another queue.
            priority_tag = _pop_priority_task_for_provider(
                client, allow_background=allow_background
            )
            if priority_tag:
                enqueue_payload(client, VIDEO_QUEUE, payload, front=False)
                styler_engine = None
                client.delete("styler:worker-model")
                if handle_worker_reset_task(client, priority_tag):
                    return
                if priority_tag.get("action") in (
                    "styler_tag_sample",
                    "styler_tag_group",
                ):
                    tag_style = priority_tag["style_tag"]
                styler_engine = run_priority_task(client, priority_tag)
                continue
            task = json.loads(payload)
            if task.get("action") == "styler_sample":
                from styler.jobs import run_sample_job

                styler_engine = task["engine"]
                client.set("styler:worker-model", styler_engine, ex=86400)
                run_tracked_task(client, run_sample_job, client, task)
            elif task.get("action") == "worker_rpc":
                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(client, run_worker_rpc, client, task)
            elif task.get("action") == "stockpile_image_metadata":
                import const
                from stockpile import run_job as run_stockpile_job

                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(
                    client, run_stockpile_job, client, task, const.LIBRARY_DIR
                )
            elif task.get("action") == "styler_scan_image_tags":
                import const
                from styler.style_scan import run_job as run_style_scan_job

                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(
                    client, run_style_scan_job, client, task, const.LIBRARY_DIR
                )
            elif task.get("action") == "styler_style_description":
                from styler.style_description import (
                    run_job as run_style_description_job,
                )

                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(client, run_style_description_job, client, task)
            elif task.get("action") == "styler_style_duplicate_scan":
                import const
                from styler.style_duplicates import (
                    run_job as run_style_duplicate_scan_job,
                )

                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(
                    client,
                    run_style_duplicate_scan_job,
                    client,
                    task,
                    const.LIBRARY_DIR,
                )
            elif task.get("action") == "styler_topic_llm":
                from styler.topic_jobs import run_job as run_topic_llm_job

                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(client, run_topic_llm_job, client, task)
            else:
                styler_engine = None
                client.delete("styler:worker-model")
                run_tracked_task(client, run_video_job, client, task)
        except redis.exceptions.BusyLoadingError:
            wait_for_worker_redis(client)
        except Exception:
            log.exception("Video render worker loop failed")
            time.sleep(2)


def run_queued_tag_task(client, task, library_dir, queue=TAG_QUEUE):
    """Run style-group tag tasks or batch adjacent legacy per-engine records."""
    from styler.image_tags import (
        ANALYSIS_VERSION,
        peek_tag_batch,
        run_job,
        run_tag_group,
    )

    if task.get("action") == "styler_tag_group":
        return run_tracked_task(client, run_tag_group, client, task, library_dir)
    following = peek_tag_batch(client, queue, task)
    if following:
        members = [dict(task)]
        for member, raw in following:
            if client.lrem(queue, 1, raw):
                members.append(dict(member))
        if len(members) > 1:
            import uuid

            base_prompt = task.get("base_style_prompt", "classic")
            for member in members:
                member.setdefault(
                    "pending_key",
                    "styler:tag-pending:"
                    f"{member['job_id']}:{member.get('sha256', '')}:"
                    f"{member.get('analysis_version', ANALYSIS_VERSION)}",
                )
            group = {
                "action": "styler_tag_group",
                "job_id": uuid.uuid4().hex,
                "style_tag": task["style_tag"],
                "base_style_prompt": base_prompt,
                "items": members,
                "engines": [member["engine"] for member in members],
                "priority": bool(task.get("priority")),
            }
            return run_tracked_task(
                client, run_tag_group, client, group, library_dir
            )
    return run_tracked_task(client, run_job, client, task)


def run_priority_task(client, task):
    """Dispatch a promoted job through its original handler, before model batching."""
    action = task.get("action")
    if action == "gpu_service_switch":
        log.info(
            "Dequeued GPU service switch %s targeting %s",
            task.get("job_id"),
            task.get("target_provider"),
        )
        run_tracked_task(client, run_gpu_service_switch_task, client, task)
        return None
    if action == "styler_sample":
        from styler.jobs import run_sample_job

        client.set("styler:worker-model", task["engine"], ex=86400)
        run_tracked_task(client, run_sample_job, client, task)
        return task["engine"]
    if action in ("styler_tag_sample", "styler_tag_group"):
        if action == "styler_tag_group":
            from styler.image_tags import run_tag_group

            import const

            run_tracked_task(client, run_tag_group, client, task, const.LIBRARY_DIR)
        else:
            import const

            run_queued_tag_task(
                client, task, const.LIBRARY_DIR, PRIORITY_QUEUE
            )
    elif action == "stockpile_image_metadata":
        import const
        from stockpile import run_job

        run_tracked_task(client, run_job, client, task, const.LIBRARY_DIR)
    elif action == "styler_scan_image_tags":
        import const
        from styler.style_scan import run_job

        run_tracked_task(client, run_job, client, task, const.LIBRARY_DIR)
    elif action == "styler_style_description":
        from styler.style_description import run_job

        run_tracked_task(client, run_job, client, task)
    elif action == "styler_style_duplicate_scan":
        import const
        from styler.style_duplicates import run_job

        run_tracked_task(client, run_job, client, task, const.LIBRARY_DIR)
    elif action == "styler_topic_llm":
        from styler.topic_jobs import run_job

        run_tracked_task(client, run_job, client, task)
    elif action == "styler_topic_taxonomy_rebuild":
        from styler.topic_jobs import run_taxonomy_rebuild

        run_tracked_task(client, run_taxonomy_rebuild, client, task)
    elif action == "lora_captions":
        from artifact_editor.book.lora_captioning import run_job
        run_tracked_task(client, run_job, client, task)
    elif action == "worker_rpc":
        run_tracked_task(client, run_worker_rpc, client, task)
    else:
        run_tracked_task(client, run_video_job, client, task)
    return None


def run_gpu_service_switch_task(client, task):
    """Switch to a user-selected provider through the exclusive GPU handoff."""
    from gpu_service import service_switching_enabled, use_gpu_service

    if not service_switching_enabled():
        log.error(
            "GPU service switch %s cannot run: GPU_SERVICE_SWITCHING is disabled",
            task.get("job_id"),
        )
        raise RuntimeError("GPU provider switching is disabled in the worker")
    target = task.get("target_provider")
    service = {"comfyui": "comfy", "llama": "llama", "training": "training", "python": "python"}.get(target)
    if service is None:
        raise ValueError(f"Unsupported GPU provider target: {target!r}")
    log.info(
        "Starting GPU service switch task %s: target=%s",
        task.get("job_id"),
        target,
    )
    try:
        with use_gpu_service(
            client,
            service,
            transition_task=task,
        ):
            pass
    finally:
        if client.get(GPU_SWITCH_REQUEST_KEY) in (task["job_id"], task["job_id"].encode()):
            client.delete(GPU_SWITCH_REQUEST_KEY)


def _pending_gpu_switch_id(client, *, clear_orphaned=True):
    """Return the manual switch ID only while its task is queued or running."""
    raw_pending_id = client.get(GPU_SWITCH_REQUEST_KEY)
    if not raw_pending_id:
        return None
    pending_id = (
        raw_pending_id.decode()
        if isinstance(raw_pending_id, bytes)
        else str(raw_pending_id)
    )

    task_payloads = client.lrange(PRIORITY_QUEUE, 0, -1)
    active_payloads = (
        client.get("gpu:service-transition"),
        client.get("styler:worker-running"),
    )
    for payload in [*task_payloads, *active_payloads]:
        if not payload:
            continue
        try:
            task = json.loads(payload)
        except (TypeError, ValueError):
            continue
        if (
            isinstance(task, dict)
            and task.get("job_id") == pending_id
            and task.get("action") == "gpu_service_switch"
        ):
            return pending_id

    # A worker crash or task deletion can leave the deduplication key behind.
    # Compare before deleting so a newer request created during this check survives.
    if clear_orphaned:
        client.eval(
            "if redis.call('get', KEYS[1]) == ARGV[1] then "
            "return redis.call('del', KEYS[1]) else return 0 end",
            1,
            GPU_SWITCH_REQUEST_KEY,
            pending_id,
        )
    return None


def run_tracked_task(client, callback, *args):
    task = next(
        (
            value
            for value in args
            if isinstance(value, dict) and isinstance(value.get("job_id"), str)
        ),
        None,
    )
    if task:
        from task_queue import with_task_provider
        from gpu_service import (
            service_switching_enabled,
            use_gpu_service,
        )

        task.update(with_task_provider(task))
        provider = task["provider"]
        if (
            service_switching_enabled()
            and task.get("action") != "gpu_service_switch"
            and provider in ("llama", "comfyui", "python")
        ):
            service = "comfy" if provider == "comfyui" else provider
            active_provider = _active_task_provider(client)
            if active_provider != provider:
                priority = bool(task.get("priority"))
                waiting_for_active_provider = bool(
                    active_provider
                    and _has_queued_provider_task(client, active_provider)
                )
                # A priority request may switch providers ahead of queued
                # background work on the currently active provider. The worker
                # is serial, so any call already in progress has finished before
                # we reach this dispatch point.
                if waiting_for_active_provider and not priority:
                    if provider == "llama" and task.get("action") in (
                        "styler_tag_sample",
                        "styler_tag_group",
                    ):
                        queue, front = TAG_QUEUE, False
                    elif provider == "llama":
                        queue, front = VIDEO_QUEUE, True
                    elif task.get("idle_maintenance"):
                        queue, front = IDLE_QUEUE, False
                    else:
                        queue, front = VIDEO_QUEUE, True
                elif provider == "llama" and task.get("action") in (
                    "styler_tag_sample",
                    "styler_tag_group",
                ):
                    queue = PRIORITY_QUEUE if priority else TAG_QUEUE
                    front = True
                elif provider == "llama":
                    queue, front = VIDEO_QUEUE, False
                elif priority:
                    queue, front = PRIORITY_QUEUE, True
                elif task.get("idle_maintenance"):
                    queue, front = IDLE_QUEUE, True
                else:
                    queue, front = VIDEO_QUEUE, False
                enqueue_task(
                    client,
                    queue,
                    task,
                    front=front,
                    ensure_ascii=False,
                )
                if waiting_for_active_provider and not priority:
                    return None
                # Keep the original task queued, but report its provider
                # transition separately and let the same Home-backed handoff
                # path handle automatic and user-requested switches.
                service_names = {"comfy": "ComfyUI", "llama": "Llama", "training": "Training", "python": "Python"}
                transition_task = {
                    "job_id": f"gpu-switch-auto-{task['job_id']}",
                    "action": "gpu_service_switch",
                    "job_type": "GPU service switch",
                    "service_from": {
                        "comfyui": "ComfyUI",
                        "llama": "Llama",
                        "training": "Training", "python": "Python",
                    }.get(active_provider, "Stopped"),
                    "service_to": service_names[service],
                    "target_provider": provider,
                    "provider": provider,
                    "priority": True,
                    "trigger_job_id": task["job_id"],
                }
                log.info(
                    "Starting automatic GPU provider transition for task %s: %s -> %s",
                    task["job_id"],
                    active_provider or "Stopped",
                    provider,
                )
                with use_gpu_service(
                    client,
                    service,
                    transition_task=transition_task,
                ):
                    pass
                return None
    if task:
        from styler.queue_history import clear_failed_job

        clear_failed_job(client, task["job_id"])
        client.delete(f"styler:retry-pending:{task['job_id']}")
    mark_activity(client)
    if task:
        from task_timing import prepare
        task.update(prepare(task))
        task['queue_started_at'] = time.time()
    current_task = dict(task) if task else None
    if current_task and current_task.get("action") in (
        "styler_tag_sample",
        "styler_tag_group",
    ):
        current_task.update(state="preparing", message="Preparing image tags")
    current = json.dumps(current_task) if current_task else "1"
    client.set("styler:worker-running", current, ex=86400)
    try:
        if task and task.get("action") in (
            "styler_sample",
            "styler_tag_sample",
            "styler_tag_group",
            "styler_scan_image_tags",
            "styler_style_description",
        ):
            from styler.tag_consensus import style_key

            style_tag = task.get("style_tag")
            style_key_value = style_key(style_tag) if isinstance(style_tag, str) else ""
            if not style_key_value or client.type(style_key_value) not in (
                "hash",
                b"hash",
            ):
                discard_missing_style_task(client, task)
                return None
        if task and task.get("idle_maintenance"):
            from worker_startup import ensure_worker_services

            ensure_worker_services(comfyui=True)
        return callback(*args)
    except Exception as exc:
        if task:
            publish_queue_event(
                client,
                "styler_sample_events",
                {**task, "state": "failed", "message": str(exc)},
            )
        raise
    finally:
        client.delete("styler:worker-running")
        if task and task.get("idle_maintenance"):
            from styler.idle_maintenance import RETRY_SECONDS

            client.set(IdleMaintenance._retry_key(task), "1", ex=RETRY_SECONDS)
        mark_activity(client)
        if task:
            client.publish(
                "styler_sample_events",
                json.dumps(dict(state="queue_changed", job_id=task["job_id"])),
            )


def handle_worker_reset_task(client, task):
    """Exit cleanly after the current task when a priority reset is dequeued."""
    if task.get("action") != "worker_reset":
        return False
    from styler.jobs import EVENTS

    job_id = task["job_id"]
    client.set("styler:worker-reset-ready", job_id, ex=300)
    client.publish(
        EVENTS,
        json.dumps(
            dict(
                job_id=job_id,
                action="worker_reset",
                state="complete",
                job_type="Worker reset",
                message="Current task finished; restarting worker",
            )
        ),
    )
    log.warning("Priority worker reset %s reached a safe restart point", job_id)
    return True


def retry_failed_task(client, job_id):
    """Requeue one recently completed or failed task at the priority queue head."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        return dict(state="retry_rejected", job_id=job_id, message="Invalid task ID")
    outcome = next(
        (entry for entry in recent_failures(client) if entry.get("job_id") == job_id),
        None,
    )
    if not outcome:
        outcome = next(
            (
                entry
                for entry in recent_completions(client)
                if entry.get("job_id") == job_id
            ),
            None,
        )
    if not outcome:
        return dict(
            state="retry_rejected",
            job_id=job_id,
            message="Completed or failed task is no longer available",
        )
    task = dict(outcome)
    for key in (
        "state",
        "completed_at",
        "duration_seconds",
        "message",
        "traceback",
        "percent",
        "queue_failed",
        "queue_completed",
        "job_type",
        "label",
        "queue_started_at",
        "promoted",
        "priority_removed",
    ):
        task.pop(key, None)
    action = task.get("action")
    if not isinstance(action, str) or not action:
        return dict(
            state="retry_rejected", job_id=job_id, message="Task cannot be retried"
        )
    task["priority"] = True
    from task_queue import RENDER_ACTIONS, RENDER_QUEUE, task_provider

    task["provider"] = task_provider(task)

    for running_key in ("styler:worker-running", "chapter_render:running"):
        raw_running = client.get(running_key)
        if raw_running:
            try:
                running = json.loads(raw_running)
            except (TypeError, ValueError):
                running = {}
            if isinstance(running, dict) and running.get("job_id") == job_id:
                return dict(
                    state="retry_rejected", job_id=job_id, message="Task is already running"
                )

    queues = (PRIORITY_QUEUE, VIDEO_QUEUE, TAG_QUEUE, IDLE_QUEUE, RENDER_QUEUE)
    for queue in queues:
        for raw in client.lrange(queue, 0, -1):
            try:
                queued = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if isinstance(queued, dict) and queued.get("job_id") == job_id:
                from styler.queue_history import clear_job_outcome

                clear_job_outcome(client, job_id)
                return dict(
                    state="retry_queued",
                    job_id=job_id,
                    job=styler_queue_entry(queued, priority=queue == PRIORITY_QUEUE),
                )

    retry_key = f"styler:retry-pending:{job_id}"
    if not client.set(retry_key, "1", nx=True, ex=86400):
        return dict(
            state="retry_rejected", job_id=job_id, message="Retry is already queued"
        )
    try:
        if action in RENDER_ACTIONS:
            enqueue_task(client, VIDEO_QUEUE, task)
            from worker_startup import ensure_worker_services
            try:
                ensure_worker_services()
            except RuntimeError:
                log.exception("Retried coordinator queued but worker startup failed")
        else:
            client.lpush(PRIORITY_QUEUE, json.dumps(task, ensure_ascii=False))
        from styler.queue_history import clear_job_outcome

        clear_job_outcome(client, job_id)
        event = dict(
            state="job_queued",
            job_id=job_id,
            job_type=styler_queue_entry(task, True)["job_type"],
            action=action,
            priority=True,
        )
        for key in ("style_tag", "engine", "filename"):
            if key in task:
                event[key] = task[key]
        client.publish("styler_sample_events", json.dumps(event, ensure_ascii=False))
    except Exception:
        client.delete(retry_key)
        raise
    return dict(
        state="retry_queued", job_id=job_id, job=styler_queue_entry(task, priority=True)
    )


def discard_missing_style_task(client, task):
    """Drop a queued Styler job whose style was deleted before dispatch."""
    from styler.image_tags import EVENTS
    from styler.queue_history import publish_queue_event

    action = task.get("action")
    job_id = task["job_id"]
    event = {
        key: task[key]
        for key in ("job_id", "style_tag", "engine", "style_key")
        if key in task
    }
    event["message"] = "Style no longer exists; queued task was discarded"
    if action == "styler_sample":
        client.delete(f"styler_pending:{job_id}", f"styler_progress:{job_id}")
        event.update(state="failed", job_type="Sample image", action=action)
    elif action == "styler_tag_group":
        for member in task.get("items", []):
            if isinstance(member, dict) and member.get("pending_key"):
                client.delete(member["pending_key"])
        if task.get("group_key"):
            client.delete(f"styler:tag-group-open:{task['group_key']}")
        event.update(
            state="tag_failed",
            job_type="Image tags",
            action=action,
            engines=task.get("engines", []),
            priority=bool(task.get("priority")),
        )
    elif action == "styler_tag_sample":
        from styler.image_tags import ANALYSIS_VERSION

        pending = task.get("pending_key") or (
            f"styler:tag-pending:{job_id}:{task.get('sha256', '')}:"
            f"{task.get('analysis_version', ANALYSIS_VERSION)}"
        )
        client.delete(pending)
        event.update(
            state="tag_failed",
            job_type="Image tags",
            action=action,
            priority=bool(task.get("priority")),
        )
    elif action == "styler_scan_image_tags":
        client.delete(f"styler:styles-scan:pending:{job_id}")
        event.update(
            state="styles_scan_image_failed",
            job_type="Tag normalization",
            action=action,
        )
        try:
            from styler.style_scan import _finish_scan_job

            _finish_scan_job(client, task)
        except Exception:
            log.exception("Unable to finalize stale style scan task %s", job_id)
    elif action == "styler_style_description":
        client.delete(f"styler:description-pending:{job_id}")
        event.update(
            state="style_description_failed",
            job_type="Historical description",
            action=action,
            priority=True,
        )
    else:
        return
    log.warning(
        "Discarding queued %s task %s because style %s no longer exists",
        action,
        job_id,
        task.get("style_tag"),
    )
    publish_queue_event(client, EVENTS, event)


def clear_removed_task_markers(client, task):
    """Clear enqueue guards and durable bookkeeping for a removed queued task."""
    action = task.get("action")
    job_id = task["job_id"]
    client.delete(f"styler:retry-pending:{job_id}")
    if action in ("gpu_workflow", "gpu_llama"):
        with client.pipeline() as pipe:
            pipe.rpush(f"gpu_result:{job_id}", json.dumps({"error": "GPU task was removed from the queue"}))
            pipe.expire(f"gpu_result:{job_id}", 600)
            pipe.execute()
    if action == "styler_sample":
        client.delete(f"styler_pending:{job_id}", f"styler_progress:{job_id}")
    elif action == "styler_tag_group":
        for member in task.get("items", []):
            if isinstance(member, dict):
                if member.get("pending_key"):
                    client.delete(member["pending_key"])
                client.delete(f"styler:retry-pending:{member.get('job_id', '')}")
        if task.get("group_key"):
            client.delete(f"styler:tag-group-open:{task['group_key']}")
    elif action == "styler_tag_sample":
        from styler.image_tags import ANALYSIS_VERSION

        client.delete(
            task.get("pending_key")
            or f"styler:tag-pending:{job_id}:{task.get('sha256', '')}:{task.get('analysis_version', ANALYSIS_VERSION)}"
        )
    elif action == "styler_scan_image_tags":
        client.delete(f"styler:styles-scan:pending:{job_id}")
        from styler.style_scan import _finish_scan_job

        _finish_scan_job(client, task)
    elif action == "styler_style_description":
        client.delete(f"styler:description-pending:{job_id}")
    elif action == "styler_style_duplicate_scan":
        key = "styler:style-duplicate-scan:pending"
        if client.get(key) == job_id or client.get(key) == job_id.encode():
            client.delete(key)
    elif action == "styler_topic_llm":
        client.delete(f"styler:topic-llm-pending:{job_id}")
        client.set(
            f"styler:topic-llm-result:{job_id}",
            json.dumps(
                {
                    "state": "failed",
                    "error": "Category LLM task was removed from the queue",
                }
            ),
            ex=3600,
        )
    elif action == "styler_topic_taxonomy_rebuild":
        if client.get("styler:topic-tree:pending") in (job_id, job_id.encode()):
            client.delete("styler:topic-tree:pending")
    elif action == "stockpile_image_metadata":
        client.delete(f"stockpile:pending:{job_id}")
    elif action == "worker_reset":
        key = "styler:worker-reset-pending"
        if client.get(key) in (job_id, job_id.encode()):
            client.delete(key)
    elif action == "gpu_service_switch":
        if client.get(GPU_SWITCH_REQUEST_KEY) in (job_id, job_id.encode()):
            client.delete(GPU_SWITCH_REQUEST_KEY)


def run_worker_rpc(client, task):
    from worker_operations import execute_isolated as execute

    # Do not start requests whose caller has already timed out.
    if time.time() >= task["deadline"]:
        return
    response = {"job_id": task["job_id"]}
    try:
        from gpu_service import use_gpu_service
        if task.get("operation") == "t5_tokens":
            response["result"] = execute(task["operation"], task["arguments"])
        else:
            with use_gpu_service(client, "python"):
                response["result"] = execute(task["operation"], task["arguments"])
    except Exception as exc:
        log.exception("Worker RPC failed", operation=task["operation"])
        response["error"] = str(exc)
    publish_queue_event(client, 'styler_sample_events', {
        **{key: value for key, value in task.items() if key not in ('arguments', 'deadline')},
        'state': 'failed' if 'error' in response else 'complete',
        'message': response.get('error', 'Worker operation complete'),
    })
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
        if task["operation"] not in OPERATIONS or not isinstance(
            task["arguments"], dict
        ):
            raise ValueError("Unsupported worker request")
        task = {
            "job_id": job_id,
            "action": "worker_rpc",
            "operation": task["operation"],
            "arguments": task["arguments"],
            "deadline": time.time() + 590,
        }
        result_key = f"worker_result:{job_id}"
        enqueue_task(client, VIDEO_QUEUE, task)
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


def _theater_render_snapshot(project_id, client):
    """Return the current Theater render state and its review media versions."""
    from artifact_editor.theater.views import _project_dir, _read_project
    import const

    project = _read_project(project_id)
    folder = _project_dir(project_id)
    job_id = project.get("render_job_id", "")
    job = client.hgetall(f"video_job:{job_id}") if job_id else {}
    status = project.get("render_status", "planning")
    error = project.get("render_error", "")
    if job.get("state") == "failed" and status in (
        "rough_queued", "rough_rendering", "rough_film_queued", "rough_film_rendering",
        "final_queued", "final_rendering"
    ):
        status = "render_failed"
        error = job.get("message") or "The render worker failed."

    rough_path = folder / "rough-draft.mp4"
    final_path = folder / "final.mp4"

    def version(path):
        try:
            stat = path.stat()
        except OSError:
            return ""
        return f"{stat.st_mtime_ns}-{stat.st_size}"

    generated_images = []
    from artifact_editor.chapter_source import read_chapter_source
    chapter_xml = folder / "chapter" / "0001" / "english" / "chapter.xml"
    soup = read_chapter_source(chapter_xml) if project.get("render_stage") == "rough" else None
    if soup is not None:
        try:
            for image in soup.find_all("image"):
                generation_id = image.get("image_generation_id")
                paragraph = image.find_parent("paragraph")
                src = image.get("src")
                if not generation_id or paragraph is None or not src:
                    continue
                image_path = Path(const.LIBRARY_DIR) / paragraph.get("dir", "") / Path(src).name
                if not image_path.is_file():
                    continue
                index = int(image.get("index", -1))
                generated_images.append({
                    "id": generation_id,
                    "index": index,
                    "url": (
                        f"/library/Theater/{quote(folder.name, safe='')}/1/english/images/0/{index}.png"
                        f"?v={quote(generation_id, safe='')}"
                    ),
                })
        except (OSError, ValueError):
            log.warning("Could not read generated Theater images", project_id=project_id)

    progress = {
        key: job[key]
        for key in ("state", "percent", "message", "timing", "duration_seconds")
        if key in job
    }
    return {
        "job_id": job_id,
        "status": status,
        "render_stage": project.get("render_stage"),
        "error": error,
        "runtime_seconds": project.get("runtime_seconds"),
        "aspect": project.get("aspect"),
        "rough_exists": rough_path.is_file(),
        "final_exists": final_path.is_file(),
        "rough_version": version(rough_path),
        "final_version": version(final_path),
        "generated_images": generated_images,
        "progress": progress,
    }


@sock.route("/theater-ws/<project_id>")
def theater_render_websocket(ws, project_id):
    """Push one Theater project's initial render snapshot and live job updates."""
    if not re.fullmatch(r"[a-f0-9]{32}", project_id):
        return
    client = redis_connection()
    pubsub = client.pubsub()
    try:
        pubsub.subscribe(VIDEO_EVENTS)
        initial = _theater_render_snapshot(project_id, client)
        watched_job_id = initial.get("job_id")
        ws.send(json.dumps({"type": "snapshot", **initial}))
        while True:
            incoming = ws.receive(timeout=0.1)
            if incoming:
                try:
                    command = json.loads(incoming)
                except (TypeError, ValueError):
                    command = {}
                if command.get("action") == "watch" and isinstance(command.get("job_id"), str):
                    watched_job_id = command["job_id"]
                continue
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if not message or message.get("type") != "message":
                continue
            try:
                event = json.loads(message["data"])
            except (TypeError, ValueError):
                continue
            if (
                event.get("theater_project_id") != project_id
                and event.get("job_id") != watched_job_id
            ):
                continue
            snapshot = _theater_render_snapshot(project_id, client)
            ws.send(json.dumps({"type": "update", **snapshot}))
    except Exception:
        log.debug("Theater render WebSocket closed", project_id=project_id)
    finally:
        pubsub.close()
        client.close()


@sock.route("/chapter-frames-ws")
def chapter_frames_websocket(ws):
    """Send completed-frame notices to a single open Frames page."""
    client = redis_connection()
    pubsub = client.pubsub()
    pubsub.subscribe(FRAME_EVENTS)
    watched = None
    last_cpu_update = 0.0
    try:
        while True:
            incoming = ws.receive(timeout=0.1)
            if incoming:
                try:
                    message = json.loads(incoming)
                    from artifact_editor.images.completions import normalize_chapter_identity
                    identity = normalize_chapter_identity(message)
                    aspect = message.get("aspect")
                    if message.get("action") != "watch_chapter_frames" or aspect not in ("widescreen", "portrait"):
                        continue
                    watched = {**identity, "aspect": aspect}
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
            event = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if event and event.get("type") == "message" and watched is not None:
                try:
                    payload = json.loads(event["data"])
                except (ValueError, TypeError):
                    payload = None
                if payload and all(payload.get(key) == value for key, value in watched.items()):
                    ws.send(json.dumps(payload))
            if watched and time.monotonic() - last_cpu_update >= 1.0:
                active_frames = []
                for raw in client.hvals("cpu_jobs:running"):
                    try:
                        task = json.loads(raw)
                    except (ValueError, TypeError):
                        continue
                    chapter = task.get("chapter") or {}
                    if (task.get("action") == "draw_frame"
                            and all(str(chapter.get(key)) == str(watched.get(key))
                                    for key in ("author", "title", "chapter_number", "language"))
                            and chapter.get("aspect") == watched["aspect"]):
                        active_frames.append({
                            "action": "draw_frame", "label": task.get("label"), "chapter": chapter,
                        })
                ws.send(json.dumps({"state": "cpu_queue_plan", "running": active_frames}))
                last_cpu_update = time.monotonic()
    except Exception:
        log.debug("Chapter frame WebSocket closed")
    finally:
        pubsub.close()
        client.close()


@sock.route("/styler-ws")
def styler_websocket(ws):
    from styler.jobs import EVENTS, sample_task, request_sample
    from gpu_metrics import get_gpu_metrics
    from task_queue import PRIORITY_QUEUE as _priority_queue, TAG_QUEUE as _tag_queue
    import const

    PRIORITY_QUEUE = _priority_queue
    TAG_QUEUE = _tag_queue

    client = redis_connection()
    pubsub = client.pubsub()
    pubsub.subscribe(EVENTS, VIDEO_EVENTS, CHAPTER_IMAGE_EVENTS, FRAME_EVENTS)
    watched_chapter = None
    watched_frame_chapter = None
    requested = set()
    queue_capacity = 10
    queue_dirty = True
    last_queue_state = None
    last_queue_sent = 0.0
    last_running_id = None
    last_cpu_sent = 0.0
    last_cpu_signature = None
    last_gpu_sent = float('-inf')

    def send_queue_plan(completed_job_id=None):
        nonlocal queue_dirty, last_queue_state, last_queue_sent, last_running_id
        plan = styler_queue_snapshot(client, PRIORITY_QUEUE, TAG_QUEUE, queue_capacity)
        plan["state"] = "queue_plan"
        if completed_job_id:
            plan["completed_job_id"] = completed_job_id
        signature = json.dumps(plan, sort_keys=True)
        if signature != last_queue_state or completed_job_id:
            ws.send(json.dumps(plan))
            last_queue_state = signature
            last_queue_sent = time.monotonic()
            last_running_id = (plan.get("running") or {}).get("job_id")
        queue_dirty = False

    try:
        send_queue_plan()
        while True:
            if time.monotonic() - last_gpu_sent >= 2:
                ws.send(json.dumps({"state": "gpu_metrics", **get_gpu_metrics()}))
                last_gpu_sent = time.monotonic()
            if time.monotonic() - last_cpu_sent >= 1:
                from cpu_queue import snapshot as cpu_snapshot
                cpu_plan = cpu_snapshot(client)
                signature = json.dumps(cpu_plan, sort_keys=True)
                if signature != last_cpu_signature:
                    ws.send(json.dumps(cpu_plan))
                    last_cpu_signature = signature
                last_cpu_sent = time.monotonic()
            incoming = ws.receive(timeout=0.1)
            if incoming:
                message = None
                try:
                    message = json.loads(incoming)
                    if not isinstance(message, dict):
                        raise ValueError("Invalid request")
                    if message.get("action") == "watch_chapter_images":
                        from artifact_editor.images.completions import (
                            chapter_image_snapshot, normalize_chapter_identity,
                        )
                        identity = normalize_chapter_identity(message)
                        snapshot = chapter_image_snapshot(identity)
                        watched_chapter = identity
                        ws.send(json.dumps(snapshot))
                    elif message.get("action") == "watch_chapter_frames":
                        from artifact_editor.images.completions import normalize_chapter_identity
                        identity = normalize_chapter_identity(message)
                        aspect = message.get("aspect")
                        if aspect not in ("widescreen", "portrait"):
                            raise ValueError("Invalid frame aspect")
                        watched_frame_chapter = {**identity, "aspect": aspect}
                    elif message.get("action") == "queue_capacity":
                        queue_capacity = max(0, min(200, int(message.get("rows", 10))))
                        queue_dirty = True
                    elif message.get("action") == "maintenance_mode":
                        mode = set_mode(client, message.get("mode"))
                        ws.send(json.dumps(dict(state="maintenance_mode", mode=mode)))
                        queue_dirty = True
                    elif message.get("action") == "queue_promote":
                        result = promote_job(
                            client,
                            message.get("job_id"),
                            PRIORITY_QUEUE,
                            VIDEO_QUEUE,
                            TAG_QUEUE,
                            enabled=message.get("priority", True),
                        )
                        if result:
                            response = dict(
                                state="job_promoted"
                                if result["job"].get("promoted")
                                else "job_demoted",
                                job_id=message["job_id"],
                                promoted=bool(result["job"].get("promoted")),
                                priority=bool(result["job"].get("priority")),
                            )
                        else:
                            response = dict(
                                state="job_promote_failed",
                                job_id=message.get("job_id"),
                                message="Task is no longer pending",
                            )
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "queue_delete":
                        result = remove_queued_job(
                            client,
                            message.get("job_id"),
                            PRIORITY_QUEUE,
                            VIDEO_QUEUE,
                            TAG_QUEUE,
                        )
                        if result and result.get("state") == "queue_removed":
                            task = result["task"]
                            clear_removed_task_markers(client, task)
                            response = dict(
                                state="queue_task_removed",
                                job_id=task["job_id"],
                                label=styler_queue_entry(task)["label"],
                            )
                        elif result and result.get("state") == "running":
                            response = dict(
                                state="queue_delete_rejected",
                                job_id=message.get("job_id"),
                                message="Task has already started",
                            )
                        else:
                            response = dict(
                                state="queue_delete_rejected",
                                job_id=message.get("job_id"),
                                message="Task is no longer queued",
                            )
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "retry_job":
                        response = retry_failed_task(client, message.get("job_id"))
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "retry_cpu_job":
                        from cpu_queue import retry_task

                        response = retry_task(client, message.get("job_id"))
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "cancel_cpu_job":
                        from cpu_queue import cancel_task

                        response = cancel_task(client, message.get("job_id"),
                                               allow_running=message.get("allow_running") is True)
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "gpu_service_switch":
                        target_provider = message.get("provider")
                        log.info(
                            "Received GPU service switch request: target=%s",
                            target_provider,
                        )
                        service = {
                            "comfyui": "comfy",
                            "comfy": "comfy",
                            "llama": "llama",
                            "training": "training", "python": "python",
                        }.get(target_provider)
                        if service is None:
                            response = dict(
                                state="gpu_service_switch_failed",
                                message="Choose ComfyUI, Llama, Training, or Python",
                            )
                        else:
                            target_provider = {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}[service]
                            active_service = client.get("gpu:active-service")
                            if isinstance(active_service, bytes):
                                active_service = active_service.decode()
                            training_running = False
                            if active_service == "training" and target_provider != "training":
                                from artifact_editor.book.lora_training import queue_entries
                                training_running = any(row["state"] == "running" for row in queue_entries())
                            if training_running:
                                response = dict(
                                    state="gpu_service_switch_failed",
                                    message="Wait for LoRA training to finish before leaving Training mode.",
                                )
                            else:
                                existing = _pending_gpu_switch_id(client)
                                if existing:
                                    response = dict(
                                        state="gpu_service_switch_queued",
                                        job_id=existing,
                                        already_queued=True,
                                    )
                                else:
                                    task = {
                                        "job_id": uuid.uuid4().hex,
                                        "action": "gpu_service_switch",
                                        "job_type": "GPU service switch",
                                        "service_to": {"comfy": "ComfyUI", "llama": "Llama", "training": "Training · Clear VRAM", "python": "Python"}[service],
                                        "target_provider": target_provider,
                                        "provider": target_provider,
                                        "priority": True,
                                        "manual_override": True,
                                    }
                                    if client.set(
                                        GPU_SWITCH_REQUEST_KEY,
                                        task["job_id"],
                                        nx=True,
                                        ex=7200,
                                    ):
                                        try:
                                            enqueue_task(
                                                client,
                                                PRIORITY_QUEUE,
                                                task,
                                                front=True,
                                                ensure_ascii=False,
                                            )
                                            client.publish(
                                                EVENTS,
                                                json.dumps(
                                                    {
                                                        "state": "job_queued",
                                                        **task,
                                                    },
                                                    ensure_ascii=False,
                                                ),
                                            )
                                            log.info(
                                                "Queued GPU service switch %s targeting %s",
                                                task["job_id"],
                                                target_provider,
                                            )
                                        except Exception:
                                            client.delete(GPU_SWITCH_REQUEST_KEY)
                                            raise
                                        response = dict(
                                            state="gpu_service_switch_queued",
                                            job_id=task["job_id"],
                                            provider=target_provider,
                                        )
                                    else:
                                        response = dict(
                                            state="gpu_service_switch_queued",
                                            job_id=client.get(GPU_SWITCH_REQUEST_KEY),
                                            already_queued=True,
                                        )
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "worker_reset":
                        reset_key = "styler:worker-reset-pending"
                        existing = client.get(reset_key)
                        if existing:
                            response = dict(
                                state="worker_reset_queued",
                                job_id=existing,
                                already_queued=True,
                            )
                        else:
                            task = {
                                "job_id": uuid.uuid4().hex,
                                "action": "worker_reset",
                                "priority": True,
                                "provider": "none",
                            }
                            if client.set(reset_key, task["job_id"], nx=True, ex=86400):
                                try:
                                    client.lpush(PRIORITY_QUEUE, json.dumps(task))
                                    client.publish(
                                        EVENTS,
                                        json.dumps(
                                            dict(
                                                state="job_queued",
                                                job_id=task["job_id"],
                                                job_type="Worker reset",
                                                action="worker_reset",
                                                priority=True,
                                            )
                                        ),
                                    )
                                except Exception:
                                    client.delete(reset_key)
                                    raise
                                response = dict(
                                    state="worker_reset_queued", job_id=task["job_id"]
                                )
                            else:
                                response = dict(
                                    state="worker_reset_queued",
                                    job_id=client.get(reset_key),
                                    already_queued=True,
                                )
                        requested.add(response["job_id"])
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "stockpile_scan":
                        from stockpile import scan_stockpile

                        response = scan_stockpile(
                            client, const.LIBRARY_DIR, VIDEO_QUEUE
                        )
                        requested.update(response["jobs"])
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "styles_scan":
                        from styler.style_scan import scan_styles

                        response = scan_styles(client, const.LIBRARY_DIR, VIDEO_QUEUE)
                        requested.update(response["jobs"])
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") == "style_duplicate_scan":
                        from styler.style_duplicates import (
                            request as request_style_duplicate_scan,
                        )

                        response = request_style_duplicate_scan(client)
                        requested.add(response["job_id"])
                        ws.send(json.dumps(response))
                        queue_dirty = True
                    elif message.get("action") in ("retag", "missing_tags"):
                        from styler.image_tags import (
                            request_retag,
                            request_missing_tags,
                        )

                        request_tags = (
                            request_missing_tags
                            if message["action"] == "missing_tags"
                            else request_retag
                        )
                        if message["action"] == "retag":
                            log.info(
                                "Retag requested: style=%s engine=%s variant=%s",
                                message.get("style_tag"), message.get("engine"),
                                message.get("base_style_prompt", "classic"),
                            )
                            tasks = request_tags(
                                client,
                                message.get("style_tag"),
                                const.LIBRARY_DIR,
                                message.get("engine"),
                                message.get("base_style_prompt", "classic"),
                            )
                        else:
                            tasks = request_tags(
                                client, message.get("style_tag"), const.LIBRARY_DIR
                            )
                        requested.update(task["job_id"] for task in tasks)
                        if message["action"] == "missing_tags" and not tasks:
                            from styler.image_tags import reconcile_style_on_detail

                            status = reconcile_style_on_detail(
                                client, message["style_tag"], const.LIBRARY_DIR
                            )
                            if status:
                                ws.send(json.dumps(status))
                                continue
                        ws.send(
                            json.dumps(
                                dict(
                                    state="retag_queued",
                                    style_tag=message["style_tag"],
                                    engines=[task["engine"] for task in tasks],
                                    engine=message.get("engine"),
                                    base_style_prompt=message.get("base_style_prompt", "classic"),
                                    samples=[
                                        {"engine": task["engine"],
                                         "base_style_prompt": task.get("base_style_prompt", "classic")}
                                        for task in tasks
                                    ],
                                )
                            )
                        )
                        queue_dirty = True
                    else:
                        task = sample_task(message)
                        requested.add(task["job_id"])
                        ws.send(
                            json.dumps(
                                request_sample(
                                    client, task, VIDEO_QUEUE, const.LIBRARY_DIR
                                )
                            )
                        )
                        queue_dirty = True
                except (ValueError, TypeError, KeyError):
                    failure = {
                        "retag": "retag_failed",
                        "missing_tags": "retag_failed",
                        "stockpile_scan": "stockpile_scan_failed",
                        "styles_scan": "styles_scan_failed",
                        "style_duplicate_scan": "style_duplicate_scan_failed",
                        "queue_delete": "queue_delete_rejected",
                        "watch_chapter_images": "chapter_image_snapshot_failed",
                        "watch_chapter_frames": "chapter_frame_watch_failed",
                        "gpu_service_switch": "gpu_service_switch_failed",
                    }.get(
                        message.get("action") if isinstance(message, dict) else None,
                        "failed",
                    )
                    ws.send(
                        json.dumps(
                            {
                                "state": failure,
                                "job_id": message.get("job_id")
                                if isinstance(message, dict)
                                else None,
                                "style_tag": message.get("style_tag")
                                if isinstance(message, dict)
                                else None,
                                "engine": message.get("engine")
                                if isinstance(message, dict)
                                else None,
                                "base_style_prompt": message.get("base_style_prompt", "classic")
                                if isinstance(message, dict) else "classic",
                                "message": "Invalid request or no sample images found",
                            }
                        )
                    )
                except Exception:
                    log.exception("Styler WebSocket request failed")
                    failure = {
                        "retag": "retag_failed",
                        "missing_tags": "retag_failed",
                        "stockpile_scan": "stockpile_scan_failed",
                        "styles_scan": "styles_scan_failed",
                        "style_duplicate_scan": "style_duplicate_scan_failed",
                        "queue_delete": "queue_delete_rejected",
                    }.get(
                        message.get("action") if isinstance(message, dict) else None,
                        "failed",
                    )
                    ws.send(
                        json.dumps(
                            {
                                "state": failure,
                                "job_id": message.get("job_id")
                                if isinstance(message, dict)
                                else None,
                                "style_tag": message.get("style_tag")
                                if isinstance(message, dict)
                                else None,
                                "engine": message.get("engine")
                                if isinstance(message, dict)
                                else None,
                                "base_style_prompt": message.get("base_style_prompt", "classic")
                                if isinstance(message, dict) else "classic",
                                "message": "Unable to queue request",
                            }
                        )
                    )
            # Drain each burst, preserve all semantic events, and collapse noisy
            # progress updates to the latest value for each job.
            progress_latest = {}
            semantic_events = []
            for _ in range(500):
                event = pubsub.get_message(ignore_subscribe_messages=True, timeout=0)
                if not event:
                    break
                if event["type"] != "message":
                    continue
                try:
                    payload = json.loads(event["data"])
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict) and payload.get("state") == "chapter_frame_complete":
                    if watched_frame_chapter and all(
                            payload.get(key) == value for key, value in watched_frame_chapter.items()):
                        ws.send(json.dumps(payload))
                    continue
                if isinstance(payload, dict) and payload.get("state") == "chapter_image_complete":
                    if watched_chapter and all(payload.get(key) == value for key, value in watched_chapter.items()):
                        semantic_events.append(payload)
                    continue
                if isinstance(payload, dict) and payload.get("execution_queue") == "render":
                    continue
                if isinstance(payload, dict) and payload.get("state") in (
                    "lost_resolved",
                    "style_tags_reconciled",
                    "maintenance_mode",
                ):
                    semantic_events.append(payload)
                elif isinstance(payload, dict) and isinstance(
                    payload.get("job_id"), str
                ):
                    state = payload.get("state")
                    terminal_states = (
                        "complete",
                        "failed",
                        "prompt_tagged",
                        "tag_failed",
                        "tag_abyss",
                        "stockpile_complete",
                        "stockpile_failed",
                        "styles_scan_image_complete",
                        "styles_scan_image_failed",
                        "style_description_complete",
                        "style_description_failed",
                        "style_duplicate_scan_complete",
                        "style_duplicate_scan_failed",
                    )
                    if state in (
                        "job_queued",
                        "job_promoted",
                        "job_demoted",
                        "queue_changed",
                        "queued",
                        "preparing",
                        "clearing_vram",
                        "running",
                        *terminal_states,
                    ):
                        queue_dirty = True
                    if state == "running":
                        progress_latest[payload["job_id"]] = payload
                        continue
                    toast_event = state in (
                        "complete",
                        "failed",
                        "prompt_tagged",
                        "tag_failed",
                        "tag_abyss",
                        "stockpile_complete",
                        "stockpile_failed",
                        "styles_scan_image_complete",
                        "styles_scan_image_failed",
                        "style_description_complete",
                        "style_description_failed",
                        "style_duplicate_scan_complete",
                        "style_duplicate_scan_failed",
                        "worker_ready",
                    )
                    if toast_event or payload["job_id"] in requested:
                        semantic_events.append(payload)
            for payload in progress_latest.values():
                if payload.get("job_id") in requested:
                    semantic_events.append(payload)
            completed_ids = []
            queue_terminal_seen = False
            for payload in semantic_events:
                ws.send(json.dumps(payload))
                state = payload.get("state")
                if state in (
                    "complete",
                    "failed",
                    "prompt_tagged",
                    "tag_failed",
                    "tag_abyss",
                    "stockpile_complete",
                    "stockpile_failed",
                    "styles_scan_image_complete",
                    "styles_scan_image_failed",
                    "style_description_complete",
                    "style_description_failed",
                    "style_duplicate_scan_complete",
                    "style_duplicate_scan_failed",
                ):
                    queue_terminal_seen = True
                    if state in (
                        "complete",
                        "prompt_tagged",
                        "stockpile_complete",
                        "styles_scan_image_complete",
                        "style_description_complete",
                        "style_duplicate_scan_complete",
                    ):
                        completed_ids.append(payload["job_id"])
            if queue_terminal_seen:
                completed_job_id = next(
                    (job_id for job_id in completed_ids if job_id == last_running_id),
                    None,
                )
                send_queue_plan(completed_job_id=completed_job_id)
            if queue_dirty and (time.monotonic() - last_queue_sent >= 0.075):
                send_queue_plan()
    finally:
        pubsub.close()
        client.close()


def styler_queue_entry(task, priority=False):
    if not isinstance(task, dict) or not isinstance(task.get("job_id"), str):
        return None
    from task_queue import task_provider
    from task_timing import prepare
    task = prepare(task)

    action = task.get("action") or ""
    labels = {
        "lora_captions": "Generating Caption",
        "lora_training": "Training LoRA",
        "styler_tag_sample": "Image tags",
        "styler_tag_group": "Image tags",
        "styler_sample": "Sample image",
        "styler_scan_image_tags": "Tag normalization",
        "styler_style_description": "Historical description",
        "styler_style_duplicate_scan": "Style duplicate scan",
        "styler_topic_llm": "Category grouping",
        "styler_topic_taxonomy_rebuild": "Category taxonomy rebuild",
        "gpu_service_switch": "GPU service switch",
        "worker_reset": "Worker reset",
        "stockpile_image_metadata": "Stockpile tags",
        "chapter_import": "Chapter import",
        "chapter_missing_metadata": "Metadata generation",
        "chapter_missing_images": "Image generation",
        "chapter_missing_animations": "Animation generation",
        "chapter_missing_characters": "Character extraction",
        "chapter_character_appearances": "Character appearance processing",
        "chapter_assign_characters": "Phrase speaker assignment",
        "chapter_place_image_slots": "Image slot placement",
        "chapter_missing_audio": "Audio generation",
        "chapter_missing_props": "Prop extraction",
        "chapter_add_all": "Add all chapter assets",
        "textoviva_one_shot": "One Shot chapter workflow",
        "timeline_metadata": "Image metadata",
        "timeline_animation_description": "Animation description",
        "timeline_image": "Image generation",
        "character_image": "Character image",
        "prop_image": "Prop image",
        "scene_description": "Scene description",
        "scene_image": "Scene image",
    }
    job_type = task.get("job_type") or labels.get(
        action, action.replace("_", " ").strip().title() or "GPU task"
    )
    if action == "worker_rpc":
        job_type = task.get("operation", "Worker task").replace("_", " ").title()
    item = {
        key: task[key]
        for key in (
            "job_id",
            "externally_managed",
            "executor",
            "parent_job_id",
            "style_tag",
            "engine",
            "engines",
            "filename",
            "background_category",
            "operation",
            "promoted",
            "priority_removed",
            "idle_maintenance",
            "queue_started_at",
            "timing_key",
            "timing_units",
            "timing_unit",
            "topic_level",
            "topic_parent",
            "service_from",
            "service_to",
            "target_provider",
            "provider",
        )
        if key in task
    } | {
        "job_type": job_type,
        "priority": bool(priority),
        "action": action,
        "provider": (
            task.get("target_provider")
            if action == "gpu_service_switch" and task.get("target_provider")
            else task_provider(task)
        ),
        "state": "queued",
    }
    subject = (
        str(item.get("style_tag") or item.get("filename") or "")
        .replace("_", " ")
        .split("/")[-1]
    )
    if action == "gpu_service_switch":
        subject = f"{item.get('service_from', 'GPU')} → {item.get('service_to', 'GPU')}"
    if action == "styler_topic_llm":
        subject = item.get("topic_parent") or str(
            item.get("topic_level") or "topics"
        ).replace("-", " ")
    engine = {"zit": "Z-Image", "krea2": "Krea 2", "qwen": "Qwen 2.1"}.get(
        item.get("engine")
    )
    if action == "styler_tag_group":
        engine = None
    elif item.get("engines"):
        engine_names = {
            "zit": "Z-Image",
            "krea2": "Krea 2",
            "qwen": "Qwen 2.1",
        }
        engine = ", ".join(
            engine_names.get(name, name) for name in item["engines"]
        )
    item["label"] = " · ".join(part for part in (job_type, subject, engine) if part)
    return item


def styler_queue_snapshot(client, priority_queue, tag_queue, capacity=10):
    """Return a bounded, worker-ordered queue view for the styler UI."""
    from task_timing import Estimator, learn_outcomes
    completed_history = recent_completions(client)
    failed_history = recent_failures(client)
    learn_outcomes(client, [*completed_history, *failed_history])
    from artifact_editor.book.lora_training import queue_entries
    training_entries = queue_entries()
    completed_history += [entry for entry in training_entries if entry['state'] == 'complete']
    failed_history += [entry for entry in training_entries if entry['state'] == 'failed']
    estimator = Estimator(client)
    running = None
    raw_running = client.get("gpu:service-transition") or client.get(
        "styler:worker-running"
    )
    if raw_running:
        try:
            task = json.loads(raw_running)
            if isinstance(task, dict) and task.get("job_id"):
                running = styler_queue_entry(task, priority=bool(task.get("priority")))
                running["state"] = task.get("state", "running")
                progress = client.get(f"styler_progress:{task['job_id']}")
                if progress:
                    try:
                        detail = json.loads(progress)
                        if isinstance(detail, dict):
                            running.update(
                                {
                                    key: detail[key]
                                    for key in ("percent", "message")
                                    if key in detail
                                }
                            )
                    except (TypeError, ValueError):
                        pass
                video_progress = client.hgetall(f"video_job:{task['job_id']}")
                running.update(
                    {
                        key: value
                        for key, value in video_progress.items()
                        if key in ("percent", "message")
                    }
                )
                if task.get("message"):
                    running["message"] = task["message"]
        except (TypeError, ValueError):
            pass
    loaded_engine = client.get("styler:worker-model")
    model_batching = bool(loaded_engine)
    active_service = client.get("gpu:active-service")
    if isinstance(active_service, bytes):
        active_service = active_service.decode()
    active_provider = {
        "comfy": "comfyui",
        "llama": "llama",
        "training": "training", "python": "python",
    }.get(active_service)
    transition_task = None
    raw_transition = client.get("gpu:service-transition")
    if raw_transition:
        try:
            transition_task = json.loads(raw_transition)
        except (TypeError, ValueError):
            transition_task = None
        active_provider = None
    switching_to = (
        transition_task.get("provider")
        if isinstance(transition_task, dict)
        else None
    )
    if active_provider is None and switching_to is None and running:
        running_provider = running.get("provider")
        if running_provider in ("comfyui", "llama"):
            active_provider = running_provider
    reported_provider = active_provider
    from gpu_service import service_switching_enabled

    if not service_switching_enabled():
        active_provider = "*"
    sources = (
        (priority_queue, False, True),
        (VIDEO_QUEUE, True, False),
        (tag_queue, False, False),
        (IDLE_QUEUE, False, False),
    )
    queued = []
    seen = {running["job_id"]} if running else set()
    for queue, reverse, priority in sources:
        payloads = client.lrange(queue, 0, -1)
        if reverse:
            payloads.reverse()
        for payload in payloads:
            try:
                task = json.loads(payload)
                item = styler_queue_entry(
                    task, priority=priority or bool(task.get("priority"))
                )
            except (TypeError, ValueError, AttributeError):
                continue
            if not item or item["job_id"] in seen:
                continue
            seen.add(item["job_id"])
            queued.append(item)

    gpu_switch_pending_to = switching_to
    if not gpu_switch_pending_to and running and running.get("action") == "gpu_service_switch":
        gpu_switch_pending_to = running.get("target_provider") or running.get("provider")
    if not gpu_switch_pending_to:
        pending_switch_id = client.get(GPU_SWITCH_REQUEST_KEY)
        pending_switch = next(
            (
                item
                for item in queued
                if item["job_id"] == pending_switch_id
                and item.get("action") == "gpu_service_switch"
            ),
            None,
        )
        if pending_switch:
            gpu_switch_pending_to = pending_switch.get("target_provider")

    def rank(job):
        if job["action"] == "gpu_service_switch":
            return -1
        if active_provider is None and job["provider"] == "none":
            return -1
        if active_provider != "*" and active_provider and job["provider"] not in (
            active_provider,
            "none",
        ):
            return 6
        if job["priority"]:
            return 0
        if job.get("idle_maintenance"):
            return 5
        action = job["action"]
        if (
            model_batching
            and action == "styler_sample"
            and job.get("engine") == loaded_engine
            and not job.get("priority_removed")
        ):
            return 1
        if action in ("styler_tag_sample", "styler_tag_group") or (
            action == "styler_sample" and job.get("background_category")
        ):
            return 4
        if action == "styler_scan_image_tags":
            return 3
        if action == "stockpile_image_metadata":
            return 4
        if action == "styler_sample" or action:
            return 2
        return 4

    for training_entry in training_entries:
        if training_entry['state'] == 'running' and running is None:
            running = training_entry
        elif training_entry['state'] in ('queued', 'running'):
            queued.append(training_entry)
    queued.sort(key=rank)
    running, queued = estimator.serial(running, queued)
    capacity = max(0, min(200, int(capacity)))
    visible = queued[:capacity]
    for item in visible:
        item["isNext"] = False
    eligible = next(
        (
            item
            for item in visible
            if item["action"] == "gpu_service_switch"
            or active_provider == "*"
            or item["provider"] == "none"
            or item["provider"] == active_provider
        ),
        None,
    )
    if eligible:
        eligible["isNext"] = True
    queued_ids = {job["job_id"] for job in queued}
    failures = [
        entry
        for entry in failed_history
        if entry["job_id"] not in queued_ids
        and not (
            running
            and running["job_id"] == entry["job_id"]
            and float(running.get("queue_started_at") or 0) > entry["completed_at"]
        )
    ]
    return {
        "running": running,
        "jobs": visible,
        "queue_count": len(queued) + int(running is not None),
        "overflow_count": max(0, len(queued) - len(visible)),
        "completed": [
            {
                **{key: value for key, value in estimator.attach(entry).items() if key not in ("arguments", "deadline")},
                "label": styler_queue_entry(entry)["label"],
                "provider": styler_queue_entry(entry)["provider"],
            }
            for entry in completed_history
        ],
        "failed": [
            {
                **{key: value for key, value in estimator.attach(entry).items() if key not in ("arguments", "deadline")},
                "label": styler_queue_entry(entry)["label"],
                "provider": styler_queue_entry(entry)["provider"],
            }
            for entry in failures
        ],
        "maintenance_mode": get_mode(client),
        "worker_reset_pending": bool(client.exists("styler:worker-reset-pending")),
        "gpu_switch_pending": bool(
            _pending_gpu_switch_id(client, clear_orphaned=False)
        ),
        "gpu_switch_pending_to": gpu_switch_pending_to,
        "active_provider": reported_provider,
        "switching_to": switching_to,
    }


def handle_websocket(ws):
    client = redis_connection()
    pubsub = client.pubsub()
    pubsub.subscribe(VIDEO_EVENTS)
    legacy_cursor = 0
    try:
        while True:
            incoming = ws.receive(timeout=0.1)
            if incoming:
                try:
                    task = json.loads(incoming)
                    if task.get("action") in (
                        "character_image",
                        "prop_image",
                        "chapter_missing_images",
                        "chapter_missing_animations",
                        "chapter_missing_characters",
                        "chapter_character_appearances",
                        "chapter_assign_characters",
                        "chapter_place_image_slots",
                        "chapter_missing_audio",
                        "chapter_missing_props",
                        "chapter_add_all",
                        "chapter_missing_metadata",
                        "chapter_import",
                        "textoviva_one_shot",
                        "textoviva_rerender",
                        "textoviva_time_align",
                        "textoviva_missing_animation",
                    ):
                        character_fields = {}
                        if task["action"] == "character_image":
                            tag, description = (
                                task.get("character_tag"),
                                task.get("description"),
                            )
                            if (
                                not isinstance(tag, str)
                                or not tag
                                or not isinstance(description, str)
                                or not description.strip()
                            ):
                                raise ValueError(
                                    "Character and description are required"
                                )
                            character_fields = {
                                "character_tag": tag,
                                "description": description,
                            }
                        if task["action"] == "prop_image":
                            tag, description = (
                                task.get("prop_tag"),
                                task.get("description"),
                            )
                            if (
                                not isinstance(tag, str)
                                or not tag
                                or not isinstance(description, str)
                                or not description.strip()
                            ):
                                raise ValueError("Prop and description are required")
                            character_fields = {
                                "prop_tag": tag,
                                "description": description,
                            }
                        sections = task.get("sections")
                        if task["action"] in ("chapter_import", "textoviva_one_shot"):
                            if task["action"] == "chapter_import" or sections is not None:
                                if not isinstance(sections, str) or not isinstance(
                                    json.loads(sections), list
                                ):
                                    raise ValueError("Invalid chapter sections")
                        thumbnail_fields = {}
                        thumbnail_index = task.get("thumbnail_image_index")
                        if (
                            task["action"] == "chapter_missing_animations"
                            and isinstance(thumbnail_index, int)
                            and thumbnail_index >= 0
                        ):
                            thumbnail_fields["thumbnail_image_index"] = thumbnail_index
                        # Accept only the explicit render command and chapter identity.
                        task = {
                            key: task[key]
                            for key in (
                                "action",
                                "job_id",
                                "author",
                                "title",
                                "chapter_number",
                                "language",
                            )
                        }
                        task.update(character_fields)
                        task.update(thumbnail_fields)
                        task["job_id"] = uuid.UUID(task["job_id"]).hex
                        task["chapter_number"] = int(task["chapter_number"])
                        if task["action"] == "chapter_import":
                            task["sections"] = sections
                        elif task["action"] == "textoviva_one_shot" and sections is not None:
                            task["sections"] = sections
                        label = (
                            "Queued for chapter import"
                            if task["action"] == "chapter_import"
                            else "Queued for Time Align"
                            if task["action"] == "textoviva_time_align"
                            else "Queued for video generation"
                        )
                        if task["action"] in ("prop_image", "character_image"):
                            label = (
                                "Queued for "
                                + (
                                    "prop"
                                    if task["action"] == "prop_image"
                                    else "character"
                                )
                                + " image generation"
                            )
                        elif task["action"] == "chapter_missing_metadata":
                            label = "Queued for missing metadata"
                        elif task["action"] == "chapter_missing_audio":
                            label = "Queued for missing audio"
                        elif task["action"] == "chapter_place_image_slots":
                            label = "Queued for image slot placement"
                        elif task["action"] == "chapter_assign_characters":
                            label = "Queued for phrase speaker assignment"
                        elif task["action"] == "chapter_character_appearances":
                            label = "Queued for character appearance changes"
                        elif task["action"] in (
                            "chapter_missing_images",
                            "chapter_missing_animations",
                        ):
                            label = "Queued for missing " + task["action"].removeprefix(
                                "chapter_missing_"
                            )
                        # Persist both identity and queued status before starting
                        # consumers. Startup may disconnect this WebSocket; the
                        # coordinator must still be able to finish the request.
                        queued_status = {
                            "job_id": task["job_id"], "action": task["action"],
                            "percent": 0, "message": label, "state": "queued", "job_type": label.removeprefix("Queued for ").capitalize(),
                        }
                        if "image_index" in task:
                            queued_status["image_index"] = task["image_index"]
                        enqueue_task(
                            client, VIDEO_QUEUE, task,
                            status_key=f"video_job:{task['job_id']}", status=queued_status,
                            event_channel=VIDEO_EVENTS, event=queued_status,
                        )
                        # Worker startup can take a long time. Acknowledge the
                        # queued job on the initiating socket before waiting
                        # for Home to start the CPU coordinator.
                        ws.send(progress_html(queued_status))
                        from task_queue import RENDER_ACTIONS
                        if task["action"] in RENDER_ACTIONS:
                            from worker_startup import ensure_worker_services
                            try:
                                ensure_worker_services()
                            except RuntimeError as exc:
                                status = client.hgetall(f"video_job:{task['job_id']}")
                                if status.get("state") == "queued":
                                    publish_progress(client, task["job_id"], 0,
                                                     f"Queued; {exc}", "queued")
                except (ValueError, TypeError, KeyError, AttributeError):
                    log.exception("Invalid WebSocket render command")

            for event in pending_video_progress(pubsub, client):
                try:
                    ws.send(progress_html(event))
                except (redis.RedisError, ValueError, TypeError):
                    log.exception("Could not deliver video progress event")

            # Bound legacy polling: scanning the entire cache on every socket
            # loop delays receiving new commands when Redis holds many keys.
            legacy_cursor, legacy_keys = client.scan(
                cursor=legacy_cursor, match=f"{LEGACY_PREFIX}*", count=200
            )
            for key in legacy_keys:
                message = client.lpop(key)
                if message is None:
                    continue
                queue_name = key.removeprefix(LEGACY_PREFIX)
                try:
                    chapter_key, image_index = queue_name.rsplit("_", 1)
                    value = client.incr(queue_name + "_counter")
                    if value == 1:
                        client.set(queue_name + "_steps", int(float(message)))
                        client.set(queue_name + '_started_at', time.time(), ex=86400)
                    max_value = int(client.get(queue_name + "_steps") or 100)
                    percent = min(100, int(value / max_value * 100))
                    from task_timing import Estimator, safe_record
                    from html import escape
                    import hashlib
                    started = client.get(queue_name + '_started_at')
                    legacy = dict(job_id='legacy:' + hashlib.sha256(queue_name.encode()).hexdigest(),
                                  action='legacy_image', state='complete' if message == 'Complete' else 'running',
                                  queue_started_at=started)
                    if message == 'Complete' and started:
                        legacy['duration_seconds'] = time.time() - float(started)
                        safe_record(client, legacy, legacy['duration_seconds'], started_at=started)
                    timing_data = escape(json.dumps(Estimator(client).attach(legacy)), quote=True)
                    ws.send(
                        f'<wa-progress-ring hx-swap-oob="outerHTML:.image_{image_index}" '
                        f'class="image_{image_index}" data-task-timing="{timing_data}" '
                        f'indeterminate></wa-progress-ring>'
                    )
                    if message == "Complete":
                        client.delete(
                            key, queue_name + "_steps", queue_name + "_counter", queue_name + '_started_at'
                        )
                        author, title, chapter_number, language = json.loads(
                            chapter_key
                        )
                        img_url = os.path.join(
                            "library",
                            author,
                            title,
                            str(chapter_number),
                            language,
                            "images",
                            "512",
                            f"{image_index}.png",
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

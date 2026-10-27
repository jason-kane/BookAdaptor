"""Coordinate image-level style evidence from ComfyUI to Styler consumers.

## Why this module exists

The Styler compares sample images from three image engines before promoting a
tag to a style. This module owns each image's analysis lifecycle: identifying
its sample file, parsing model output, deduplicating Redis work, calling the
ComfyUI workflow, persisting image-scoped evidence, and publishing worker
events. It deliberately does not decide the style's final tags; consensus and
canonical tag matching belong to ``tag_consensus`` and ``tag_matching``.

## Consumers and contracts

``styler.jobs``, the Styler views and backfill tools, and
``websocket.server`` call the scheduling, retagging, reconciliation, and worker
functions below. ``stockpile`` reuses ``analyze`` with a different parser, and
``tag_matching`` plus ``topic_taxonomy`` reuse ``tag_payload``. The Redis worker
uses ``QUEUE``, ``PRIORITY_QUEUE``, and ``EVENTS``; queue payloads carry a sample
identity, image digest, prompt version, and analysis version. The persisted
record and ``.tags.json`` sidecar are per-image evidence; downstream readers
must check their image digest and analysis version before use.

Image identities include the base style prompt. Legacy queued jobs may omit
that field, so the worker infers ``kid_friendly`` only when that identity
matches and the default ``classic`` identity does not. ``analyze`` accepts a
source path, workflow name, optional parser, and presentation metadata; it runs
one inference workflow, returns the parser's result (including an empty tag
list), and removes its temporary image/output files. ``run_job`` reports
failures as queue events and releases its pending marker unless it deliberately
deferred behind a description job.

## Boundaries and structure

Redis operations are the queue and record boundary; ``sample_metadata`` is the
sidecar/index boundary; ComfyUI is the inference boundary. Model output is
treated as untrusted text: the parser accepts a limited set of common wrappers,
never executes the response, and does not invent tags. Worker helpers keep
identity validation, dependency deferral, persistence, and outcome events
separate so a change to one boundary can be tested without live services.

## Operational settings and monitoring

Tag length validation keeps each observation useful to the vocabulary
matcher, but visual analysis does not impose a per-image tag-count target.
Pending-marker lifetimes bound queued work. Empty results are persisted as
completed evidence so opening the same style page does not repeatedly rerun
inference; explicit retag requests remain available when a fresh pass is
desired. The marked monitoring TODOs identify production distributions to
measure before changing defaults. Monitoring is not active until a shared API
is implemented.

Rewritten with contract-first-rewrite v1.3.0; last used 2026-10-27T00:51:06Z.
"""

import ast
import hashlib
import json
import logging
import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, TYPE_CHECKING, cast

from task_queue import (
    PRIORITY_QUEUE,
    STYLER_EVENTS as EVENTS,
    TAG_QUEUE as QUEUE,
)

if TYPE_CHECKING:
    from redis import Redis

from styler.queue_history import publish_queue_event

ANALYSIS_VERSION = "visual-style-tags-v9-primary-topic"

MAX_TAG_LENGTH = 80

# Pending work expires after one day; automatic retag requests cool down for six
# hours so opening a busy style page cannot continuously resubmit the same image.
PENDING_JOB_TTL_SECONDS = 86_400
AUTO_RETAG_COOLDOWN_SECONDS = 21_600

log = logging.getLogger(__name__)

# Tasks are JSON objects crossing the Redis boundary. Their external field set
# is intentionally flexible because this worker also consumes legacy jobs.
_Task = Mapping[str, Any]
_Record = dict[str, Any]


class _StyleLike(Protocol):
    """Describe only the Style behavior this module consumes."""

    @property
    def style(self) -> Any:
        """Expose the persisted style fields used for description checks."""

    @property
    def name(self) -> str:
        """Return the display name passed to the analysis workflow."""

    def set_engine_tier(self, engine: str, tier: int, *, source: str) -> None:
        """Persist image-analysis evidence as the engine's tier."""

    def engine_tier_badge(self, engine: str) -> str:
        """Build the UI badge reflecting the engine's tier."""


# -- Sample identity and model-output parsing ---------------------------------


def sample_image_path(
    library_dir: str | Path,
    style_tag: str,
    engine: str,
    base_style_prompt: str = "classic",
) -> Path:
    """Return the image path for one style, engine, and base-prompt identity."""
    directory = Path(library_dir) / "style_exploration"
    if base_style_prompt == "kid_friendly":
        directory /= base_style_prompt
    return directory / f"{style_tag}_{engine}.png"


def fingerprint(path: str | Path) -> str:
    """Hash the exact image bytes used to bind analysis to a sample revision."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pop_tag_job(
    client: "Redis",
    queue: str,
    style_tag: str | None = None,
) -> dict[str, Any] | None:
    """Atomically take this style's queued job first, then fall back to FIFO."""
    payload = client.eval(
        """
        if ARGV[1] ~= '' then
            for _, payload in ipairs(redis.call('LRANGE', KEYS[1], 0, -1)) do
                local job = cjson.decode(payload)
                if job.style_tag == ARGV[1] then
                    redis.call('LREM', KEYS[1], 1, payload)
                    return payload
                end
            end
        end
        return redis.call('LPOP', KEYS[1])
        """,
        1,
        queue,
        style_tag or "",
    )
    return json.loads(payload) if payload else None


def tag_payload(text: str) -> Any:
    """Extract one JSON/Python-literal value from common model wrappers safely."""
    text = (
        re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
        .strip()
        .lstrip("\ufeff")
    )
    # Model responses may include Markdown or a short explanation before data.
    start = re.search(r"[\[{]", text)
    if not start:
        raise ValueError("Image analysis must return a tags array")

    text = text[start.start() :]
    cleaned = []
    quote = None
    escaped = False
    depth = 0
    for index, char in enumerate(text):
        if quote:
            cleaned.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue

        if char in ('"', "'"):
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and text[index + 1 :].lstrip().startswith(("]", "}")):
            continue
        cleaned.append(char)
        if depth == 0:
            break

    payload = "".join(cleaned)
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Python-style quoting is tolerated without evaluating arbitrary text.
        try:
            return ast.literal_eval(payload)
        except (ValueError, SyntaxError):
            raise ValueError("Image analysis must return a valid tags array") from None


def parse_tags(text: str) -> list[str]:
    """Normalize a model response into a unique, bounded list of style tags."""
    result = tag_payload(text)
    values = result.get("tags") if isinstance(result, dict) else result
    if not isinstance(values, list) or not all(isinstance(tag, str) for tag in values):
        raise ValueError("Image analysis must return a tags array")

    # TODO(MONITOR): measure raw item count and raw string lengths before
    # normalization and deduplication, without retaining tag contents.
    tags = list(dict.fromkeys(" ".join(tag.lower().split()) for tag in values))
    if any(not tag or len(tag) > MAX_TAG_LENGTH for tag in tags):
        raise ValueError("Image analysis returned invalid style tags")
    return tags


def parse_analysis(
    text: str,
    primary_categories: list[str] | None = None,
) -> dict[str, Any]:
    """Parse tags, character placement, and an optional allowed primary topic."""
    result = tag_payload(text)
    if not isinstance(result, dict):
        raise ValueError("Image analysis must include character_placement")  # noqa: TRY004

    tags = parse_tags(text)
    placement = result.get("character_placement")
    if placement == "in_front":  # Compatibility with the previous output schema.
        placement = "outside_image"
    if placement not in ("in_image", "outside_image", "not_visible", "uncertain"):
        raise ValueError("Image analysis must classify character_placement")
    analysis = {"tags": tags, "character_placement": placement}
    if primary_categories is not None:
        primary_category = result.get("primary_category")
        if primary_category not in primary_categories and primary_category != "uncertain":
            raise ValueError("Image analysis must choose an allowed primary_category")
        analysis["primary_category"] = primary_category
    return analysis


def _current_style_description(style: _StyleLike) -> str:
    """Return the stored historical description only while its signature matches."""
    description = str(style.style.get("historical_description", "")).strip()
    if not description:
        return ""
    from styler.style_description import _source

    _, signature = _source(style)
    if style.style.get("historical_description_signature") != signature:
        return ""
    return description


# -- Queue scheduling and style-level requests --------------------------------


def schedule(
    client: "Redis",
    task: _Task,
    path: str | Path,
    *,
    force: bool = False,
    priority: bool = False,
) -> list[str]:
    """Reuse a current analysis or atomically queue one for the image digest."""
    from styler.style_duplicates import style_merge_locked

    if style_merge_locked(client, task["style_tag"]):
        return []

    from styler.tag_consensus import prompt_version, style_key

    if client.sismember("ABYSS/tags", style_key(task["style_tag"])):
        return []

    prompt_digest = prompt_version(client, task["style_tag"])
    image_digest = fingerprint(path)
    record = client.get(f"styler:image-tags:{task['job_id']}")
    if isinstance(record, (str, bytes)):
        record = json.loads(record)
    if (
        not force
        and isinstance(record, dict)
        and record.get("sha256") == image_digest
        and isinstance(record.get("tags"), list)
        and all(isinstance(tag, str) for tag in record["tags"])
        and record.get("analysis_version") == ANALYSIS_VERSION
    ):
        return record["tags"]

    pending_key = (
        f"styler:tag-pending:{task['job_id']}:{image_digest}:{ANALYSIS_VERSION}"
    )
    job = {key: task[key] for key in ("job_id", "style_tag", "engine")}
    if task.get("base_style_prompt"):
        job["base_style_prompt"] = task["base_style_prompt"]
    job.update(
        action="styler_tag_sample",
        sha256=image_digest,
        prompt_sha256=prompt_digest,
        analysis_version=ANALYSIS_VERSION,
    )

    if priority:
        job["priority"] = True
        queued = client.eval(
            """
            for i = 2, 3 do
                for _, payload in ipairs(redis.call('LRANGE', KEYS[i], 0, -1)) do
                    local job = cjson.decode(payload)
                    if job.job_id == ARGV[2] and job.sha256 == ARGV[3]
                        and job.analysis_version == ARGV[4] then
                        redis.call('LREM', KEYS[i], 1, payload)
                        job.priority = true
                        redis.call('LPUSH', KEYS[3], cjson.encode(job))
                        return 1
                    end
                end
            end
            if redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[5]) then
                redis.call('LPUSH', KEYS[3], ARGV[1])
                return 1
            end
            return 0
            """,
            3,
            pending_key,
            QUEUE,
            PRIORITY_QUEUE,
            json.dumps(job),
            task["job_id"],
            image_digest,
            ANALYSIS_VERSION,
            PENDING_JOB_TTL_SECONDS,
        )
        if queued:
            client.publish(EVENTS, json.dumps(_queue_event(task, priority=True)))
        if client.exists("styler:worker-running"):
            log.warning("Tag task blocked by running task")
        return []

    if client.set(pending_key, "1", nx=True, ex=PENDING_JOB_TTL_SECONDS):
        try:
            client.rpush(QUEUE, json.dumps(job))
            client.publish(EVENTS, json.dumps(_queue_event(task)))
        except Exception:
            client.delete(pending_key)
            raise
    return []


def _queue_event(task: _Task, *, priority: bool = False) -> dict[str, Any]:
    """Build the public queued event with its sample identity fields."""
    event = {
        "state": "job_queued",
        "job_id": task["job_id"],
        "job_type": "Image tags",
        "action": "styler_tag_sample",
        "style_tag": task["style_tag"],
        "engine": task["engine"],
        "base_style_prompt": task.get("base_style_prompt", "classic"),
    }
    if priority:
        event["priority"] = True
    return event


def request_retag(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
    engine: str | None = None,
    base_style_prompt: str = "classic",
) -> list[dict[str, Any]]:
    """Force priority analysis for each existing sample, optionally one engine."""
    from styler.style_duplicates import style_merge_locked

    if style_merge_locked(client, style_tag):
        raise ValueError("This style is being merged; try again when the scan finishes")

    from styler.jobs import sample_task
    from styler.tag_consensus import style_key

    key = style_key(style_tag)
    if client.sismember("ABYSS/tags", key):
        raise ValueError("Style is in ABYSS; add a tag manually to resume processing")
    client.delete("styler:auto-retag:" + key)

    engines: tuple[str, ...] = ("zit", "krea2", "qwen")
    if engine is not None:
        if engine not in engines:
            raise ValueError("Unknown sample engine")
        engines = (engine,)

    tasks = [
        sample_task(
            {
                "style_tag": style_tag,
                "engine": name,
                "base_style_prompt": base_style_prompt,
            }
        )
        for name in engines
    ]
    existing: list[dict[str, Any]] = []
    for task in reversed(tasks):
        path = sample_image_path(
            library_dir, style_tag, task["engine"], base_style_prompt
        )
        if path.is_file():
            schedule(client, task, path, force=True, priority=True)
            existing.insert(0, task)
    if not existing:
        raise ValueError("No sample images found")
    return existing


def reconcile_style_on_detail(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
) -> dict[str, Any] | None:
    """Reconcile only after all six current sample records are ready to display."""
    from styler.jobs import sample_task
    from styler.tag_consensus import ENGINES, style_key

    key = style_key(style_tag)
    was_lost = bool(client.sismember("LOST/tags", key))
    if client.sismember("ABYSS/tags", key):
        return None

    for base_style_prompt in ("classic", "kid_friendly"):
        for engine in ENGINES:
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            task = sample_task(
                {
                    "style_tag": style_tag,
                    "engine": engine,
                    "base_style_prompt": base_style_prompt,
                }
            )
            if not path.is_file() or not _analysis_record_current(client, task, path):
                return None

    from styler.tag_consensus import reconcile

    agreement = reconcile(client, style_tag, library_dir)
    has_tags = client.scard(key + "/tags") > 0
    lost_count = client.scard("LOST/tags")
    if was_lost and has_tags:
        return {
            "state": "lost_resolved",
            "style_tag": style_tag,
            "lost_count": lost_count,
            "tags": agreement["tags"],
            "added": agreement["added"],
            "removed": agreement["removed"],
            "counts": agreement["counts"],
        }
    if not agreement["added"] and not agreement["removed"]:
        return None
    return {
        "state": "style_tags_reconciled",
        "style_tag": style_tag,
        "tags": agreement["tags"],
        "added": agreement["added"],
        "removed": agreement["removed"],
        "counts": agreement["counts"],
        "lost": bool(client.sismember("LOST/tags", key)),
        "lost_count": lost_count,
    }


def request_missing_tags(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
) -> list[dict[str, Any]]:
    """Queue priority analyses for missing or stale metadata on existing samples."""
    from styler.style_duplicates import style_merge_locked

    if style_merge_locked(client, style_tag):
        raise ValueError("This style is being merged; try again when the scan finishes")

    from styler.jobs import sample_task
    from styler.tag_consensus import style_key

    if client.sismember("ABYSS/tags", style_key(style_tag)):
        return []

    tasks: list[dict[str, Any]] = []
    for base_style_prompt in ("classic", "kid_friendly"):
        for engine in ("qwen", "krea2", "zit"):
            task = sample_task(
                {
                    "style_tag": style_tag,
                    "engine": engine,
                    "base_style_prompt": base_style_prompt,
                }
            )
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file() and not _analysis_record_current(client, task, path):
                schedule(client, task, path, force=True, priority=True)
                tasks.insert(0, task)
    return tasks


def _analysis_record_current(
    client: "Redis",
    task: _Task,
    path: str | Path,
) -> bool:
    """Check that a saved tag result, including empty abstention, is current."""
    raw = client.get("styler:image-tags:" + task["job_id"])
    if isinstance(raw, bytes):
        raw = raw.decode()
    if not isinstance(raw, str):
        return False
    try:
        record = json.loads(raw)
    except ValueError:
        return False
    if not isinstance(record, dict):
        return False
    tags = record.get("tags")
    return (
        record.get("sha256") == fingerprint(path)
        and record.get("analysis_version") == ANALYSIS_VERSION
        and isinstance(tags, list)
        and all(isinstance(tag, str) for tag in tags)
    )


def auto_retag_if_empty(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
) -> bool:
    """Priority-queue visible samples with no display tags, once per cooldown."""
    from styler.jobs import sample_task
    from styler.sample_metadata import image_tags
    from styler.tag_consensus import style_key

    key = style_key(style_tag)
    if client.sismember("ABYSS/tags", key):
        return False

    samples = []
    for base_style_prompt in ("classic", "kid_friendly"):
        for engine in ("qwen", "krea2", "zit"):
            task = sample_task(
                {
                    "style_tag": style_tag,
                    "engine": engine,
                    "base_style_prompt": base_style_prompt,
                }
            )
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file() and not image_tags(path):
                marker = f"styler:auto-retag:{key}:{base_style_prompt}:{engine}"
                if not client.set(marker, "1", nx=True, ex=AUTO_RETAG_COOLDOWN_SECONDS):
                    continue
                try:
                    schedule(client, task, path, force=True, priority=True)
                except Exception:
                    client.delete(marker)
                    raise
                samples.append((task, path))

    if not samples:
        return False
    client.publish(
        EVENTS,
        json.dumps(
            {
                "style_tag": style_tag,
                "state": "retag_queued",
                "engines": [task["engine"] for task, _ in samples],
                "automatic": True,
                "base_style_prompts": sorted(
                    {task["base_style_prompt"] for task, _ in samples}
                ),
            }
        ),
    )
    return True


def prioritize_style_tags(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
) -> list[dict[str, Any]]:
    """Promote every existing sample for an explicitly opened style to priority."""
    from styler.jobs import sample_task

    queued: list[dict[str, Any]] = []
    for base_style_prompt in ("classic", "kid_friendly"):
        for engine in ("qwen", "krea2", "zit"):
            task = sample_task(
                {
                    "style_tag": style_tag,
                    "engine": engine,
                    "base_style_prompt": base_style_prompt,
                }
            )
            path = sample_image_path(library_dir, style_tag, engine, base_style_prompt)
            if path.is_file():
                schedule(client, task, path, priority=True)
                queued.insert(0, task)
    return queued


# -- ComfyUI analysis ----------------------------------------------------------


def analyze(
    path: str | Path,
    *,
    workflow_name: str = "styler-image-tags",
    parser: Callable[[str], Any] = parse_analysis,
    label: str | None = None,
    style_name: str = "",
    style_description: str = "",
    primary_categories: list[str] | None = None,
    on_status: Callable[[str, str], None] | None = None,
) -> Any:
    """Run one image-tag inference pass and parse its result.

    The workflow writes a uniquely named text file. The temporary input copy
    and every text output from this invocation are removed whether inference,
    parsing, or a status callback succeeds or fails. Empty tag lists are valid
    results, and workflow or parser failures propagate so the queue reports the
    actual failure instead of silently spending another full inference pass.
    """
    import comfy
    import const

    invocation_id = "styler_tags_" + uuid.uuid4().hex
    image_path = (
        Path(const.COMFY_DIRS["artifactserver"]["INPUT_DIR"]) / f"{invocation_id}.png"
    )
    output_dir = Path(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"])

    try:
        shutil.copyfile(path, image_path)
        output_prefix = invocation_id
        workflow = comfy.load_workflow_template("api", "i2json", workflow_name)

        if on_status:
            on_status("clearing_vram", "Waiting for ComfyUI to free GPU memory")
        comfy.free_vram()
        if on_status:
            on_status("running", "Running image analysis")
        log.info(
            "Submitting one image-tag workflow for %s (invocation %s)",
            label or Path(path).name,
            invocation_id,
        )
        allowed_categories = primary_categories or []
        try:
            comfy.run_workflow(
                workflow,
                template_environment={
                    "IMAGE": image_path.name,
                    "OUTPUT_PREFIX": output_prefix,
                    "STYLE_NAME": style_name,
                    "STYLE_DESCRIPTION": style_description,
                    "PRIMARY_CATEGORIES": json.dumps(
                        allowed_categories, ensure_ascii=False, separators=(",", ":")
                    ),
                },
            )
        finally:
            if on_status:
                on_status("clearing_vram", "Waiting for ComfyUI to free GPU memory")
            comfy.free_vram()
        log.info(
            "Completed image-tag workflow for %s (invocation %s)",
            label or Path(path).name,
            invocation_id,
        )

        if on_status:
            on_status("running", "Matching image tags")
        outputs = list(output_dir.glob(output_prefix + "*.txt"))
        if len(outputs) != 1:
            raise ValueError("Image tag workflow did not produce one text result")
        text = outputs[0].read_text()
        if parser is parse_analysis:
            return parse_analysis(text, allowed_categories)
        return parser(text)
    finally:
        image_path.unlink(missing_ok=True)
        for output in output_dir.glob(invocation_id + "*.txt"):
            output.unlink(missing_ok=True)


# -- Worker validation, dependencies, and persistence --------------------------


def _validated_base_prompt(task: _Task) -> str:
    """Validate a queued sample identity, resolving the known legacy identity."""
    from styler.jobs import sample_task

    validated = sample_task(task)
    base_style_prompt = task.get("base_style_prompt", "classic")
    if validated["job_id"] != task["job_id"] and "base_style_prompt" not in task:
        # Older kid-friendly jobs omitted the prompt field but retained its ID.
        validated = sample_task(dict(task, base_style_prompt="kid_friendly"))
        if validated["job_id"] == task["job_id"]:
            base_style_prompt = "kid_friendly"
    if validated["job_id"] != task["job_id"]:
        raise ValueError("Invalid sample identity")
    return base_style_prompt


def _worker_event(
    task: _Task,
    base_style_prompt: str,
    state: str,
    message: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    """Create one queue-history event with the fields shared by sample jobs."""
    event = {key: task[key] for key in ("job_id", "style_tag", "engine")}
    event.update(
        base_style_prompt=base_style_prompt,
        priority=bool(task.get("priority")),
        state=state,
    )
    if message is not None:
        event["message"] = message
    event.update(fields)
    return event


def _publish_status(
    client: "Redis",
    task: _Task,
    base_style_prompt: str,
    state: str,
    message: str,
) -> None:
    """Publish progress and update Redis's running record only for this job."""
    running_key = "styler:worker-running"
    raw_running = client.get(running_key)
    if raw_running:
        try:
            running_task = json.loads(raw_running)
        except (TypeError, ValueError):
            running_task = None
        if (
            isinstance(running_task, dict)
            and running_task.get("job_id") == task["job_id"]
        ):
            running_task.update(state=state, message=message)
            client.set(
                running_key, json.dumps(running_task), ex=PENDING_JOB_TTL_SECONDS
            )

    client.publish(
        EVENTS,
        json.dumps(
            {
                "job_id": task["job_id"],
                "style_tag": task["style_tag"],
                "engine": task["engine"],
                "base_style_prompt": base_style_prompt,
                "action": "styler_tag_sample",
                "state": state,
                "message": message,
                "job_type": "Image tags",
                "priority": bool(task.get("priority")),
            }
        ),
    )


def _ensure_style_description(
    client: "Redis",
    task: _Task,
    style: _StyleLike,
    base_style_prompt: str,
    style_class: Callable[..., _StyleLike],
    mark_deferred: Callable[[], None],
) -> tuple[_StyleLike, str, bool]:
    """Use a current description or queue one dependency before image analysis."""
    description = _current_style_description(style)
    if description:
        return style, description, False
    if task.get("description_waited"):
        raise ValueError(
            "Style description generation failed; image tagging is waiting for a current description"
        )

    from styler.style_description import request as request_description

    dependency = request_description(client, task["style_tag"])
    if dependency.get("state") != "complete":
        resumed_task = dict(task, description_waited=True)
        target_queue = PRIORITY_QUEUE if task.get("priority") else QUEUE
        client.rpush(target_queue, json.dumps(resumed_task, ensure_ascii=False))
        mark_deferred()
        client.publish(
            EVENTS,
            json.dumps(
                {
                    "state": "job_queued",
                    "job_id": task["job_id"],
                    "job_type": "Image tags",
                    "action": "styler_tag_sample",
                    "style_tag": task["style_tag"],
                    "engine": task["engine"],
                    "priority": bool(task.get("priority")),
                    "base_style_prompt": base_style_prompt,
                    "depends_on": dependency.get("job_id"),
                    "dependency_type": "Historical description",
                }
            ),
        )
        return style, "", True

    style = style_class(task["style_tag"])
    description = _current_style_description(style)
    if not description:
        raise ValueError(
            "Style description task completed without a current description"
        )
    return style, description, False


def _save_analysis_record(
    client: "Redis",
    task: _Task,
    path: Path,
    record: _Record,
    tags: list[str],
) -> None:
    """Replace Redis indexes and atomically publish the per-image sidecar."""
    record_key = "styler:image-tags:" + task["job_id"]
    old = client.get(record_key)
    old_tags = json.loads(old).get("tags", []) if isinstance(old, (str, bytes)) else []

    with client.pipeline() as pipe:
        for tag in old_tags:
            pipe.srem("styler:image-tag:" + tag, task["job_id"])
        pipe.set(record_key, json.dumps(record))
        if tags:
            pipe.sadd("styler:image-tag-names", *tags)
        for tag in tags:
            pipe.sadd("styler:image-tag:" + tag, task["job_id"])
        pipe.execute()

    sidecar = path.with_suffix(".tags.json")
    temporary = sidecar.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(record, indent=2) + "\n")
    temporary.chmod(0o644)
    temporary.replace(sidecar)

    from styler.sample_metadata import write_metadata

    write_metadata(path, task, record=record, digest=task["sha256"])


def _primary_categories_for_style(
    client: "Redis",
    style_tag: str,
    library_dir: str | Path,
    current_category: str,
    prompt_digest: str,
) -> list[str]:
    """Union current image topics with valid sibling-image topic choices."""
    from styler.jobs import BASE_STYLE_PROMPTS, sample_task
    from styler.tag_consensus import ENGINES
    from styler.topic_taxonomy import cached_tree

    tree = cached_tree(client)
    topics = [
        topic["name"]
        for topic in (tree or {}).get("topics", [])
        if isinstance(topic, dict) and isinstance(topic.get("name"), str)
    ]
    allowed = {topic.casefold(): topic for topic in topics}
    selected: set[str] = set()
    if isinstance(current_category, str) and current_category.casefold() in allowed:
        selected.add(allowed[current_category.casefold()])

    for base_style_prompt in BASE_STYLE_PROMPTS:
        for engine in ENGINES:
            sibling = sample_task(
                {
                    "style_tag": style_tag,
                    "engine": engine,
                    "base_style_prompt": base_style_prompt,
                }
            )
            raw = client.get("styler:image-tags:" + sibling["job_id"])
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(record, dict):
                continue
            category = record.get("primary_category")
            if (
                record.get("analysis_version") != ANALYSIS_VERSION
                or record.get("prompt_sha256") != prompt_digest
                or not isinstance(category, str)
                or category.casefold() not in allowed
            ):
                continue
            path = sample_image_path(
                library_dir, style_tag, engine, base_style_prompt
            )
            if not path.is_file() or record.get("sha256") != fingerprint(path):
                continue
            selected.add(allowed[category.casefold()])

    return [topic for topic in topics if topic in selected]


def _move_empty_lost_style_to_abyss(client: "Redis", style: str) -> None:
    """Move a lost style to manual repair only after all image analyses abstain."""
    with client.pipeline() as pipe:
        pipe.srem("LOST/tags", style)
        pipe.zrem("LOST/index", style)
        pipe.sadd("ABYSS/tags", style)
        pipe.sadd("tags", "ABYSS")
        pipe.execute()
    if client.scard("LOST/tags") == 0:
        client.srem("tags", "LOST")
        client.lrem("categories", 0, "LOST")


def run_job(client: "Redis", task: _Task) -> None:
    """Run a queued image analysis and publish its durable result or failure."""
    import const

    base_style_prompt = _validated_base_prompt(task)
    path = sample_image_path(
        const.LIBRARY_DIR,
        task["style_tag"],
        task["engine"],
        base_style_prompt,
    )
    pending_key = f"styler:tag-pending:{task['job_id']}:{task['sha256']}:{task.get('analysis_version', ANALYSIS_VERSION)}"
    deferred = False

    try:
        _publish_status(
            client, task, base_style_prompt, "preparing", "Preparing image tags"
        )
        from styler.tag_consensus import style_key

        if client.sismember("ABYSS/tags", style_key(task["style_tag"])):
            event = _worker_event(
                task,
                base_style_prompt,
                "tag_abyss",
                "Style requires manual tag repair",
            )
            publish_queue_event(client, EVENTS, event)
            return

        if not path.is_file():
            raise FileNotFoundError(f"Sample image no longer exists: {path.name}")
        if fingerprint(path) != task["sha256"]:
            schedule(client, task, path)
            event = _worker_event(
                task,
                base_style_prompt,
                "tag_failed",
                "Sample image changed while tags were being generated; newer analysis queued",
            )
            publish_queue_event(client, EVENTS, event)
            return

        from styler.styles import Style

        style: _StyleLike = Style(task["style_tag"])

        def mark_deferred() -> None:
            """Keep the pending marker once the resumed task is already queued."""
            nonlocal deferred
            deferred = True

        style, style_description, deferred = _ensure_style_description(
            client, task, style, base_style_prompt, Style, mark_deferred
        )
        if deferred:
            return

        from styler.tag_consensus import prompt_version, reconcile
        from tag_matching import match_tags
        from styler.topic_taxonomy import cached_tree

        version = prompt_version(client, task["style_tag"])
        topic_tree = cached_tree(client)
        primary_categories = [
            topic["name"]
            for topic in (topic_tree or {}).get("topics", [])
            if isinstance(topic, dict) and isinstance(topic.get("name"), str)
        ]
        analysis = analyze(
            path,
            label=f"{task['style_tag']}/{task['engine']} job {task['job_id']}",
            style_name=style.name,
            style_description=style_description,
            primary_categories=primary_categories,
            on_status=lambda state, message: _publish_status(
                client, task, base_style_prompt, state, message
            ),
        )
        if isinstance(analysis, dict):
            raw_tags = analysis.get("tags", [])
            character_placement = analysis.get("character_placement")
            primary_category = analysis.get("primary_category", "uncertain")
        else:
            # Custom parsers and older integrations may return tags directly.
            raw_tags = analysis
            character_placement = None
            primary_category = "uncertain"

        selected_categories = _primary_categories_for_style(
            client,
            task["style_tag"],
            const.LIBRARY_DIR,
            primary_category,
            version,
        )
        matching = match_tags(
            client,
            const.LIBRARY_DIR,
            raw_tags,
            style_name=task["style_tag"],
            engine=task["engine"],
            primary_categories=selected_categories,
        )
        tags = matching["tags"]

        # A concurrent image or prompt edit invalidates the result being saved.
        if (
            fingerprint(path) != task["sha256"]
            or prompt_version(client, task["style_tag"]) != version
        ):
            schedule(client, task, path)
            event = _worker_event(
                task,
                base_style_prompt,
                "tag_failed",
                "Sample image or tag prompt changed during analysis; newer analysis queued",
            )
            publish_queue_event(client, EVENTS, event)
            return

        image_tier = cast(
            int | None,
            {"outside_image": 1, "in_image": 3}.get(cast(Any, character_placement)),
        )
        tier_applied = image_tier is not None
        if image_tier is not None:
            style.set_engine_tier(task["engine"], image_tier, source="image-analysis")

        record = {key: task[key] for key in ("job_id", "style_tag", "engine", "sha256")}
        record["base_style_prompt"] = base_style_prompt
        record.update(
            tags=tags,
            prompt_sha256=version,
            analysis_version=ANALYSIS_VERSION,
            primary_category=primary_category,
        )
        record.update(matching)
        if character_placement:
            record["character_placement"] = character_placement
        if image_tier is not None:
            record["image_tier"] = image_tier
        _save_analysis_record(client, task, path, record, tags)

        event = _worker_event(task, base_style_prompt, "prompt_tagged")
        event["primary_category"] = primary_category
        if character_placement:
            event["character_placement"] = character_placement
        if tier_applied:
            event["image_tier"] = image_tier
            event["tier_badge_html"] = style.engine_tier_badge(task["engine"])

        if not tags and client.sismember("LOST/tags", style_key(task["style_tag"])):
            _move_empty_lost_style_to_abyss(client, style_key(task["style_tag"]))
            event.update(
                state="tag_abyss",
                image_tags=[],
                message="Style moved to ABYSS for manual tag repair",
            )
            publish_queue_event(client, EVENTS, event)
            return

        from styler.sample_metadata import image_tags

        event["image_tags"] = image_tags(path)
        event.update(reconcile(client, task["style_tag"], const.LIBRARY_DIR))
        event.update(
            lost=bool(client.sismember("LOST/tags", style_key(task["style_tag"]))),
            lost_count=client.scard("LOST/tags"),
        )
        publish_queue_event(client, EVENTS, event)
    except Exception as exc:
        log.exception(
            "Sample image tagging failed for %s/%s",
            task["style_tag"],
            task["engine"],
        )
        event = _worker_event(
            task,
            base_style_prompt,
            "tag_failed",
            str(exc) or "Image tagging failed",
        )
        publish_queue_event(client, EVENTS, event)
    finally:
        if not deferred:
            client.delete(pending_key)

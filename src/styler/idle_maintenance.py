"""Plan bounded Styler repairs during quiet periods and enqueue them safely.

## Responsibility and callers

``websocket.server`` owns one :class:`IdleMaintenance` instance beside the
serial worker and calls ``tick`` only when user queues are empty. The same
module's ``background_ready`` and ``pop_idle_job`` functions let worker loops
recheck idleness atomically before taking queued work. UI requests use
``get_mode`` and ``set_mode`` to choose whether maintenance is off, paced, or
run in small batches.

## Inputs, state, and outputs

The planner reads Redis style hashes, image-tag records, stockpile sidecars,
and files under the configured library. Redis stores the mode, activity time,
retry leases, and a JSON checkpoint so restarts resume from the next style or
stockpile path. ``tick`` returns the first planned task for the worker loop;
Lua admission then enqueues up to the mode's batch size only if the worker,
user queues, and quiet-period check still allow it. It returns ``None`` when
work is not due, another queue owns the worker, or no repair is needed.

## Boundaries and extension

This module decides what repair is due and when it may enter the queue. It does
not perform image generation, tag analysis, taxonomy rebuilding, or metadata
normalization; those remain worker jobs. Style and stockpile cursors are kept
separate so a repair can resume after a worker restart, while retry keys bind
backoff to the current image and processing versions. Keep queue admission and
checkpoint persistence in the same Redis transaction when changing scheduling.
"""
# Rewritten with contract-first-rewrite v1.3.0; last used 2026-10-27T00:57:23Z.

import hashlib
import json
import logging
from pathlib import Path
import time
from typing import Any, Iterator, Mapping, TYPE_CHECKING, cast

from task_queue import IDLE_QUEUE as QUEUE, STYLER_EVENTS as EVENTS

if TYPE_CHECKING:
    from redis import Redis

STATE = "styler:idle:state"
ACTIVITY = "styler:idle:last-activity"
# Interactive work must be quiet this long before the planner may start.
IDLE_SECONDS = 30
MODE = "styler:idle:mode"
# A style plus both prompt variants and three engines gets bounded inspection
# per tick so a large library cannot monopolize the worker's scheduler.
DEFAULT_SCAN_BUDGET = 24
# ``walk`` waits for 30 quiet seconds and admits one task; ``run`` waits five
# seconds and admits two. Off mode is checked again by atomic Redis admission.
MODE_INTERVALS: dict[str, int | None] = {"off": None, "walk": IDLE_SECONDS, "run": 5}
MODE_BATCH_SIZES = {"off": 0, "walk": 1, "run": 2}
# A complete catalog pass is retried after five minutes; failed tasks cool down
# for fifteen minutes. TODO(MONITOR): measure pass duration, queue wait, and
# retry frequency by repair domain before adjusting these intervals.
RESCAN_SECONDS = 300
RETRY_SECONDS = 900
TOPIC_REBUILD_SECONDS = 7 * 24 * 60 * 60
ENGINES = ("zit", "krea2", "qwen")
BASE_STYLE_PROMPTS = ("classic", "kid_friendly")
log = logging.getLogger(__name__)

_Task = dict[str, Any]
_Candidate = tuple[_Task, str]


def get_mode(client: "Redis") -> str:
    """Return a valid persisted mode, defaulting unknown values to Walk."""
    value = client.get(MODE)
    value = value.decode() if isinstance(value, bytes) else value
    return value if value in MODE_INTERVALS else "walk"


def set_mode(client: "Redis", mode: str) -> str:
    """Validate, persist, and announce a background-maintenance mode."""
    if not isinstance(mode, str) or mode not in MODE_INTERVALS:
        raise ValueError("Unknown background task rate")
    client.set(MODE, mode)
    client.publish(EVENTS, json.dumps(dict(state="maintenance_mode", mode=mode)))
    return mode


def background_ready(client: "Redis", now: float | None = None) -> bool:
    """Check the selected quiet interval, initializing missing activity time."""
    interval = MODE_INTERVALS[get_mode(client)]
    if interval is None:
        return False
    now = time.time() if now is None else now
    try:
        last_activity = float(cast(Any, client.get(ACTIVITY)))
    except (TypeError, ValueError):
        mark_activity(client, now)
        return False
    return now - last_activity >= interval


def mark_activity(client: "Redis", now: float | None = None) -> None:
    """Record the latest user or worker activity timestamp."""
    client.set(ACTIVITY, str(time.time() if now is None else now))


def pop_idle_job(
    client: "Redis",
    video_queue: str,
    priority_queue: str,
    tag_queue: str,
) -> _Task | None:
    """Atomically pop maintenance work only while every interactive queue is idle."""
    raw = client.eval(
        """
        if redis.call('EXISTS', KEYS[5]) == 1 then return nil end
        for i = 1, 3 do
            if redis.call('LLEN', KEYS[i]) > 0 then return nil end
        end
        return redis.call('LPOP', KEYS[4])
    """,
        5,
        video_queue,
        priority_queue,
        tag_queue,
        QUEUE,
        "styler:worker-running",
    )
    return json.loads(raw) if raw else None


def _json(value: Any) -> dict[str, Any]:
    """Decode a Redis or sidecar JSON object, treating malformed values as absent."""
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (TypeError, ValueError):
        return {}


def _metadata(path: Path) -> dict[str, Any]:
    """Read optional image metadata without making a missing sidecar fatal."""
    try:
        return _json(path.with_suffix(".json").read_text())
    except (OSError, UnicodeError):
        return {}


def _text(value: str | bytes) -> str:
    """Normalize Redis scan keys to strings for checkpoint state."""
    return value.decode() if isinstance(value, bytes) else value


def _image_paths(root: Path) -> Iterator[Path]:
    """Walk in stable order without reading images or materializing the whole tree."""
    from stockpile import EXTENSIONS

    if not root.is_dir():
        return
    try:
        entries = sorted(
            root.iterdir(), key=lambda path: (path.name.casefold(), path.name)
        )
    except OSError:
        log.warning("Unable to list stockpile directory %s", root)
        return
    for path in entries:
        if path.is_symlink():
            continue
        if path.is_dir():
            yield from _image_paths(path)
        elif path.is_file() and path.suffix.lower() in EXTENSIONS:
            yield path


class IdleMaintenance:
    def __init__(
        self,
        client: "Redis",
        library_dir: str | Path,
        video_queue: str,
        priority_queue: str,
        tag_queue: str,
        *,
        budget: int = DEFAULT_SCAN_BUDGET,
    ) -> None:
        """Load the persisted cursor and configure a bounded candidate scan."""
        self.client = client
        self.library = Path(library_dir)
        self.queues = (video_queue, priority_queue, tag_queue)
        self.budget = budget
        self.state = _json(client.get(STATE))
        self.stockpile_paths: Iterator[Path] | None = None
        self.batch_reserved: set[str] = set()

    def _style_candidate(
        self,
        tag: str,
        engine: str,
        base_style_prompt: str,
    ) -> _Candidate | None:
        """Return the next generation, analysis, or normalization job for one sample."""
        from styler.image_tags import ANALYSIS_VERSION, fingerprint
        from styler.jobs import sample_task
        from styler.tag_consensus import prompt_version
        from tag_matching import VERSION, matched

        task = sample_task(
            dict(style_tag=tag, engine=engine, base_style_prompt=base_style_prompt)
        )
        sample_dir = self.library / "style_exploration"
        if base_style_prompt == "kid_friendly":
            sample_dir /= base_style_prompt
        path = sample_dir / f"{tag}_{engine}.png"
        if path.is_symlink():
            return None
        if not path.is_file():
            task["background_category"] = "idle maintenance"
            return task, "styler_pending:" + task["job_id"]
        digest = fingerprint(path)
        metadata = _metadata(path)
        record = _json(self.client.get("styler:image-tags:" + task["job_id"]))

        def analyzed(value: dict[str, Any]) -> bool:
            """Require the sidecar or Redis record to match this image revision."""
            return (
                value.get("sha256") == digest
                and value.get("analysis_version") == ANALYSIS_VERSION
                and isinstance(value.get("tags"), list)
                and all(isinstance(tag, str) for tag in value["tags"])
            )

        if not analyzed(metadata) or not analyzed(record):
            task.update(
                action="styler_tag_sample",
                sha256=digest,
                analysis_version=ANALYSIS_VERSION,
                prompt_sha256=prompt_version(self.client, tag),
            )
            task["pending_key"] = (
                f"styler:tag-pending:{task['job_id']}:{digest}:{ANALYSIS_VERSION}:"
                f"{task['prompt_sha256']}"
            )
            return (
                task,
                task["pending_key"],
            )
        if (
            not matched(metadata)
            or not matched(record)
            or metadata["tags"] != record["tags"]
        ):
            version = "idle:" + VERSION
            task.update(
                action="styler_scan_image_tags",
                sample_job_id=task["job_id"],
                job_id=f"style-scan:{version}:{task['job_id']}:{digest[:16]}",
                sha256=digest,
                scan_version=version,
            )
            return task, "styler:styles-scan:pending:" + task["job_id"]
        return None

    def _next_style(self, now: float) -> _Candidate | None:
        """Advance the persisted style cursor and return its next stale sample."""
        if now < self.state.get("styles_after", 0):
            return None
        for _ in range(self.budget):
            tag = self.state.get("style")
            if tag and any(
                value is None for value in self.client.hmget(tag, "name", "prompt")
            ):
                self.state["style"] = None
                continue
            if not tag:
                keys = self.state.get("style_keys", [])
                if not keys:
                    if (
                        self.state.get("style_started")
                        and self.state.get("style_cursor", 0) == 0
                    ):
                        self.state.update(
                            style_started=False, styles_after=now + RESCAN_SECONDS
                        )
                        return None
                    cursor, keys = self.client.scan(
                        cursor=self.state.get("style_cursor", 0),
                        count=512,
                        _type="hash",
                    )
                    keys = [_text(key) for key in keys]
                    self.state.update(
                        style_cursor=int(cursor), style_started=True, style_keys=keys
                    )
                    if not keys:
                        continue
                tag = self.state["style_keys"].pop(0)
                fields = self.client.hmget(tag, "name", "prompt")
                if any(value is None for value in fields) or self.client.sismember(
                    "ABYSS/tags", tag
                ):
                    continue
                self.state.update(style=tag, engine=0, style_base_index=0)
            if self.client.sismember("ABYSS/tags", tag):
                self.state["style"] = None
                continue
            engine_index = self.state.get("engine", 0)
            engine = ENGINES[engine_index]
            base_index = self.state.get("style_base_index", 0)
            base_style_prompt = BASE_STYLE_PROMPTS[base_index]
            try:
                candidate = self._style_candidate(tag, engine, base_style_prompt)
                if (
                    candidate
                    and self._retry_key(candidate[0]) not in self.batch_reserved
                    and not self.client.exists(self._retry_key(candidate[0]))
                ):
                    return candidate
            except (OSError, ValueError):
                log.warning(
                    "Unable to inspect idle style sample %s/%s",
                    tag,
                    engine,
                    exc_info=True,
                )
            # Stay on this image through generation, analysis, and normalization.
            # Advance once it is healthy, or back off a failed stage without starving other work.
            self.state["engine"] = engine_index + 1
            if self.state["engine"] >= len(ENGINES):
                self.state["engine"] = 0
                self.state["style_base_index"] = base_index + 1
                if self.state["style_base_index"] >= len(BASE_STYLE_PROMPTS):
                    self.state.update(style=None, style_base_index=0)
        return None

    def _next_stockpile(self, now: float) -> _Candidate | None:
        """Advance the stockpile cursor and return its next incomplete image."""
        from PIL import Image
        from stockpile import ANALYSIS_VERSION, complete, read_metadata
        from styler.image_tags import fingerprint
        from tag_matching import matched

        if now < self.state.get("stockpile_after", 0):
            return None
        root = self.library / "stockpile"
        if self.stockpile_paths is None:
            self.stockpile_paths = iter(_image_paths(root))
        for _ in range(self.budget):
            try:
                path = next(self.stockpile_paths)
            except StopIteration:
                self.stockpile_paths = None
                self.state.update(
                    stockpile_cursor="", stockpile_after=now + RESCAN_SECONDS
                )
                return None
            name = path.relative_to(root).as_posix()

            def path_order(value: str) -> tuple[tuple[str, str], ...]:
                """Compare relative paths case-insensitively with stable tie breaks."""
                return tuple((part.casefold(), part) for part in Path(value).parts)

            if self.state.get("stockpile_cursor") and path_order(name) <= path_order(
                self.state["stockpile_cursor"]
            ):
                continue
            self.state["stockpile_cursor"] = name
            try:
                with Image.open(path) as image:
                    dimensions = image.size
                digest = fingerprint(path)
                metadata = read_metadata(path) or {}
                if (
                    complete(metadata, dimensions, digest)
                    and metadata.get("sha256") == digest
                    and matched(metadata)
                    and metadata.get("analysis_version") == ANALYSIS_VERSION
                ):
                    continue
                job_id = (
                    "stockpile:"
                    + hashlib.sha256((name + ":" + digest).encode()).hexdigest()
                )
                task = dict(
                    action="stockpile_image_metadata",
                    job_id=job_id,
                    filename=name,
                    sha256=digest,
                    analysis_version=ANALYSIS_VERSION,
                )
                if self._retry_key(
                    task
                ) not in self.batch_reserved and not self.client.exists(
                    self._retry_key(task)
                ):
                    return task, "stockpile:pending:" + job_id
            except (OSError, ValueError):
                log.warning(
                    "Unable to inspect idle stockpile image %s", name, exc_info=True
                )
        return None

    @staticmethod
    def _retry_key(task: Mapping[str, Any]) -> str:
        """Bind backoff to task identity, image revision, and matcher version."""
        # A new image or analysis/normalization version gets a fresh attempt immediately.
        from tag_matching import VERSION

        signature = json.dumps(
            [task["job_id"], task.get("sha256"), task.get("analysis_version"), VERSION]
        )
        return "styler:idle:retry:" + hashlib.sha256(signature.encode()).hexdigest()

    def _plan_batch(
        self,
        now: float,
        mode: str,
    ) -> tuple[dict[str, Any], list[_Candidate], list[dict[str, Any]]]:
        """Collect fair, bounded candidates and a restart checkpoint per task."""
        checkpoint = json.loads(json.dumps(self.state))
        candidates: list[_Candidate] = []
        checkpoints: list[dict[str, Any]] = []
        self.batch_reserved = set()
        try:
            for _ in range(MODE_BATCH_SIZES[mode]):
                domains = (
                    ("stockpile", "styles")
                    if self.state.get("turn") == "stockpile"
                    else ("styles", "stockpile")
                )
                candidate = None
                for domain in domains:
                    # Looking ahead for a second style must not skip the first
                    # image's remaining analysis/normalization stages next time.
                    preserve_style_cursor = domain == "styles" and any(
                        "style_tag" in task for task, _ in candidates
                    )
                    style_checkpoint = (
                        {
                            key: value
                            for key, value in json.loads(json.dumps(self.state)).items()
                            if key.startswith("style") or key == "engine"
                        }
                        if preserve_style_cursor
                        else None
                    )
                    candidate = (
                        self._next_style(now)
                        if domain == "styles"
                        else self._next_stockpile(now)
                    )
                    if style_checkpoint is not None:
                        for key in list(self.state):
                            if key.startswith("style") or key == "engine":
                                self.state.pop(key)
                        self.state.update(style_checkpoint)
                    if candidate:
                        self.state["turn"] = (
                            "stockpile" if domain == "styles" else "styles"
                        )
                        break
                if not candidate:
                    break

                task, pending_key = candidate
                task["idle_maintenance"] = True
                candidates.append((task, pending_key))
                self.batch_reserved.add(self._retry_key(task))
                checkpoints.append(json.loads(json.dumps(self.state)))
        finally:
            self.batch_reserved.clear()

        return checkpoint, candidates, checkpoints

    def _admit_batch(
        self,
        now: float,
        checkpoint: dict[str, Any],
        candidates: list[_Candidate],
        checkpoints: list[dict[str, Any]],
    ) -> _Task | None:
        """Atomically recheck quiet-time/queues, enqueue tasks, and persist progress."""
        if not candidates:
            self.client.set(STATE, json.dumps(self.state))
            return None

        checkpoints[-1] = json.loads(json.dumps(self.state))
        tasks = [task for task, _ in candidates]
        leases = [
            key
            for task, pending in candidates
            for key in (pending, self._retry_key(task))
        ]
        # some raw redis, because why not.
        accepted = int(
            self.client.eval(
                """
                if redis.call('EXISTS', KEYS[5]) == 1 then return 0 end
                for i = 1, 4 do
                    if redis.call('LLEN', KEYS[i]) > 0 then return 0 end
                end
                local mode = redis.call('GET', KEYS[8]) or 'walk'
                if mode == 'off' then return 0 end
                local interval = mode == 'run' and tonumber(ARGV[4]) or tonumber(ARGV[3])
                local active = tonumber(redis.call('GET', KEYS[6]) or ARGV[2])
                if tonumber(ARGV[2]) - active < interval then return 0 end
                local tasks = cjson.decode(ARGV[1])
                local limit = math.min(#tasks, mode == 'run' and 2 or 1)
                for i = 1, limit do
                    if redis.call('EXISTS', KEYS[8 + 2 * i]) == 1 then return 0 end
                end
                local events = cjson.decode(ARGV[7])
                for i = 1, limit do
                    -- Empty queues and no running owner make stale pending leases recoverable.
                    redis.call('SET', KEYS[7 + 2 * i], '1', 'EX', 86400)
                    redis.call('SET', KEYS[8 + 2 * i], '1', 'EX', ARGV[5])
                    redis.call('RPUSH', KEYS[4], cjson.encode(tasks[i]))
                    redis.call('PUBLISH', ARGV[6], cjson.encode(events[i]))
                end
                local checkpoints = cjson.decode(ARGV[8])
                redis.call('SET', KEYS[7], cjson.encode(checkpoints[limit]))
                return limit
                """,
                8 + len(leases),
                *self.queues,
                QUEUE,
                "styler:worker-running",
                ACTIVITY,
                STATE,
                MODE,
                *leases,
                json.dumps(tasks),
                now,
                IDLE_SECONDS,
                cast(int, MODE_INTERVALS["run"]),
                RETRY_SECONDS,
                EVENTS,
                json.dumps([{**task, "state": "job_queued"} for task in tasks]),
                json.dumps(checkpoints),
            )
        )
        if not accepted:
            # User work won the race; revisit the same candidates after more quiet time.
            self.state = checkpoint
            self.stockpile_paths = None
            return None
        if accepted < len(candidates):
            # A Run -> Walk change during planning admits only the first task.
            self.state = checkpoints[accepted - 1]
            self.stockpile_paths = None
        return tasks[0]

    def tick(self, now: float | None = None) -> _Task | None:
        """Plan and atomically enqueue the bounded batch due after a quiet period."""
        now = time.time() if now is None else now
        mode = get_mode(self.client)
        interval = MODE_INTERVALS[mode]
        if interval is None:
            return None
        if self.client.exists("styler:worker-running") or any(
            self.client.llen(queue) for queue in (*self.queues, QUEUE)
        ):
            mark_activity(self.client, now)
            return None

        try:
            last_activity = float(cast(Any, self.client.get(ACTIVITY)))
        except (TypeError, ValueError):
            mark_activity(self.client, now)
            return None
        if now - last_activity < interval:
            return None

        try:
            last_topic_build = float(
                self.client.get("styler:topic-tree:last-success") or 0
            )
        except (TypeError, ValueError):
            last_topic_build = 0
        if now - last_topic_build >= TOPIC_REBUILD_SECONDS:
            from styler.topic_jobs import enqueue_taxonomy_rebuild

            queued = enqueue_taxonomy_rebuild(self.client, reason="weekly", now=now)
            if queued.get("state") == "queued":
                return {
                    "action": "styler_topic_taxonomy_rebuild",
                    "job_id": queued["job_id"],
                }

        with self.client.lock("styler:idle:lock", timeout=30, blocking_timeout=1):
            checkpoint, candidates, checkpoints = self._plan_batch(now, mode)
            return self._admit_batch(now, checkpoint, candidates, checkpoints)

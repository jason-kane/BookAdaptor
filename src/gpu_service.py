"""Switch the single shared GPU between ComfyUI, Llama, Training, and Python workloads."""

from contextlib import contextmanager
from contextvars import ContextVar
import json
import logging
import os
import time
from typing import Callable, Iterator, Literal
import uuid

import httpx

GPUService = Literal["comfy", "llama", "training", "python"]
_active_service: ContextVar[GPUService | None] = ContextVar(
    "active_gpu_service", default=None
)

LOCK_NAME = "gpu:service-switch-lock"
LOCK_INFO_KEY = "gpu:service-switch-lock-info"
LOCK_TIMEOUT_SECONDS = 7_200
LOCK_STATUS_POLL_SECONDS = 5
STOPPED_OWNER_GRACE_SECONDS = 30
MANUAL_SWITCH_LOCK_WAIT_SECONDS = 5
SERVICE_START_TIMEOUT_SECONDS = 300
SERVICE_POLL_SECONDS = 1
LAST_SWITCH_KEY = "gpu:last-switch-at"
TRANSITION_KEY = "gpu:service-transition"
STYLER_EVENTS = "styler_sample_events"
GPU_SWITCH_REQUEST_KEY = "gpu:manual-switch-request"
log = logging.getLogger(__name__)


def queue_manual_switch(client, target_provider: str) -> dict:
    """Queue an explicit user mode selection through the serial GPU worker."""
    from task_queue import PRIORITY_QUEUE, enqueue_task

    services = {"comfyui": "ComfyUI", "llama": "Llama", "training": "Training · Clear VRAM", "python": "Python"}
    if target_provider not in services:
        raise ValueError("Choose ComfyUI, Llama, Training, or Python")
    existing = client.get(GPU_SWITCH_REQUEST_KEY)
    if existing:
        return {"job_id": existing.decode() if isinstance(existing, bytes) else str(existing),
                "already_queued": True, "provider": target_provider}
    task = {
        "job_id": uuid.uuid4().hex,
        "action": "gpu_service_switch",
        "job_type": "GPU service switch",
        "service_to": services[target_provider],
        "target_provider": target_provider,
        "provider": target_provider,
        "priority": True,
        "manual_override": True,
    }
    if not client.set(GPU_SWITCH_REQUEST_KEY, task["job_id"], nx=True, ex=7200):
        existing = client.get(GPU_SWITCH_REQUEST_KEY)
        return {"job_id": existing.decode() if isinstance(existing, bytes) else str(existing),
                "already_queued": True, "provider": target_provider}
    try:
        enqueue_task(client, PRIORITY_QUEUE, task, front=True, ensure_ascii=False)
        client.publish(STYLER_EVENTS, json.dumps({"state": "job_queued", **task}, ensure_ascii=False))
    except Exception:
        client.delete(GPU_SWITCH_REQUEST_KEY)
        raise
    return {"job_id": task["job_id"], "provider": target_provider, "already_queued": False}


def initialize_active_service(client) -> GPUService | None:
    """Record the sole already-running GPU service when the worker starts."""
    if not _enabled():
        return None

    try:
        log.info("Checking the active GPU provider through Home")
        providers = _home_provider_status()["running_providers"]
    except Exception:
        logging.getLogger(__name__).exception(
            "Could not detect the active GPU provider through Home at worker startup"
        )
        return None

    if len(providers) == 1:
        service = {"comfyui": "comfy", "llama": "llama"}.get(providers[0])
        if service is None:
            client.delete("gpu:active-service")
            return None
        previous = client.get("gpu:active-service")
        if isinstance(previous, bytes):
            previous = previous.decode()
        client.set("gpu:active-service", service)
        if previous != service:
            client.publish(STYLER_EVENTS, json.dumps({"state": "queue_changed"}))
        return service

    previous = client.get("gpu:active-service")
    if isinstance(previous, bytes):
        previous = previous.decode()
    if not providers and previous in ("training", "python"):
        return previous
    client.delete("gpu:active-service")
    if len(providers) > 1:
        logging.getLogger(__name__).error(
            "Both ComfyUI and Llama are running; GPU provider state is ambiguous"
        )
    client.publish(STYLER_EVENTS, json.dumps({"state": "queue_changed"}))
    return None


def _enabled() -> bool:
    """Enable container switching only in the configured Compose runtime."""
    return os.environ.get("GPU_SERVICE_SWITCHING", "0").casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def service_switching_enabled() -> bool:
    """Report whether tasks must wait for an active provider in this runtime."""
    return _enabled()


def _home_api_url() -> str:
    """Return Home's internal URL for Docker-backed provider control."""
    return os.environ.get("HOME_API_URL", "http://home:8080").rstrip("/")


def _home_provider_status() -> dict:
    response = httpx.get(
        f"{_home_api_url()}/api/gpu/provider",
        timeout=httpx.Timeout(10, connect=3),
    )
    response.raise_for_status()
    return response.json()


def _home_program_ready(service: GPUService) -> bool:
    """Ask Home whether the requested Compose service's API is responding."""
    if service != "comfy":
        raise ValueError("Home program readiness is only used for ComfyUI")
    url = f"{_home_api_url()}/programs/comfyui/status"
    log.info("GPU status probe: GET %s", url)
    response = httpx.get(url, timeout=httpx.Timeout(10, connect=3))
    response.raise_for_status()
    return bool(response.json().get("ready"))


def _lock_owner_info(client) -> dict:
    """Read the diagnostic record for the current Redis GPU lock holder."""
    raw = client.get(LOCK_INFO_KEY)
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _provider_is_active(client, service: GPUService) -> bool:
    """Allow shared workloads on the active provider while no handoff is running."""
    active = client.get("gpu:active-service")
    if isinstance(active, bytes):
        active = active.decode()
    return active == service and not client.exists(TRANSITION_KEY)


def _release_stopped_owner_lock(
    client,
    owner: dict,
    transition_job_id: str | None = None,
    *,
    expected_token: str | bytes | None = None,
    expected_service: str | None = None,
) -> bool:
    """Reclaim a lock only if its recorded owner is stopped and unchanged."""
    owner_token = owner.get("owner") or expected_token
    service = owner.get("service") or expected_service
    if isinstance(owner_token, bytes):
        owner_token = owner_token.decode()
    if isinstance(service, str) and service == "comfyui":
        service = "comfy"
    if not owner_token:
        return False
    return bool(
        client.eval(
            """
            if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
            if redis.call('EXISTS', KEYS[3]) == 1 then
                local transition = redis.call('GET', KEYS[3])
                local ok, task = pcall(cjson.decode, transition)
                if not ok or task.job_id ~= ARGV[3] then return 0 end
            end
            local info = redis.call('GET', KEYS[2])
            if info then
                local ok, owner = pcall(cjson.decode, info)
                if not ok or owner.owner ~= ARGV[1] then return 0 end
                if ARGV[2] ~= '' and owner.service ~= ARGV[2] then return 0 end
            elseif ARGV[2] == '' and ARGV[3] == '' then
                return 0
            end
            redis.call('DEL', KEYS[1], KEYS[2])
            return 1
            """,
            3,
            LOCK_NAME,
            LOCK_INFO_KEY,
            TRANSITION_KEY,
            owner_token,
            service or "",
            transition_job_id or "",
        )
    )


def _lock_owner_description(client) -> str:
    raw = client.get(LOCK_INFO_KEY)
    if not raw:
        ttl = client.ttl(LOCK_NAME)
        return f"owner details unavailable; lock lease has {ttl}s remaining"
    try:
        info = json.loads(raw)
    except (TypeError, ValueError):
        return "owner details unavailable"
    owner = info.get("service", "unknown service")
    if info.get("job_id"):
        owner += f", task {info['job_id']}"
    return f"held by {owner}"


def _api_url(service: GPUService) -> str:
    if service == "comfy":
        import const

        return const.COMFYUI_API_URL.rstrip("/")
    return os.environ.get("LLAMA_API_URL", "http://llama:8080").rstrip("/")


def _ready(service: GPUService, client: httpx.Client) -> bool:
    endpoint = "/health" if service == "llama" else "/system_stats"
    response = client.get(f"{_api_url(service)}{endpoint}")
    return response.status_code == 200


def _wait_until_ready(
    service: GPUService,
    progress: Callable[[str], None] | None = None,
) -> None:
    name = "ComfyUI" if service == "comfy" else "Llama"
    log.info("GPU switch: waiting for %s API readiness", name)
    if progress:
        progress(f"Waiting for {name} API readiness")
    deadline = time.monotonic() + SERVICE_START_TIMEOUT_SECONDS
    next_log = time.monotonic() + 15
    with httpx.Client(timeout=httpx.Timeout(5, connect=2)) as probe:
        while time.monotonic() < deadline:
            try:
                ready = (
                    _home_program_ready(service)
                    if service == "comfy"
                    else _ready(service, probe)
                )
                if ready:
                    log.info("GPU switch: %s API is ready", name)
                    if progress:
                        progress(f"{name} is ready")
                    return
            except httpx.HTTPError:
                pass
            if time.monotonic() >= next_log:
                log.info("GPU switch: still waiting for %s API readiness", name)
                next_log = time.monotonic() + 15
            time.sleep(SERVICE_POLL_SECONDS)
    raise TimeoutError(
        f"{name} did not become ready within {SERVICE_START_TIMEOUT_SECONDS} seconds"
    )


def _publish_switch_progress(client, task: dict, message: str) -> None:
    """Keep the queue row and logs current during a provider handoff."""
    if task.get("action") == "gpu_service_switch" and task.get("target_provider"):
        task["provider"] = task["target_provider"]
    task["state"] = "running"
    task["message"] = message
    task.setdefault("queue_started_at", time.time())
    client.set(TRANSITION_KEY, json.dumps(task), ex=LOCK_TIMEOUT_SECONDS)
    client.publish(STYLER_EVENTS, json.dumps(task))
    log.info("GPU switch %s: %s", task.get("job_id", "unknown"), message)


def _switch(
    client,
    service: GPUService,
    progress: Callable[[str], None] | None = None,
) -> None:
    provider = {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}[service]
    name = {"comfy": "ComfyUI", "llama": "Llama", "training": "Training", "python": "Python"}[service]
    if progress:
        progress(f"Asking Home to stop the other service and start {name} with Compose")
    log.info(
        "GPU switch: POST %s/api/gpu/provider/switch target=%s",
        _home_api_url(),
        provider,
    )
    try:
        response = httpx.post(
            f"{_home_api_url()}/api/gpu/provider/switch",
            json={"provider": provider},
            timeout=httpx.Timeout(570, connect=5),
        )
    except httpx.HTTPError as exc:
        raise RuntimeError(f"Home could not run the GPU provider switch: {exc}") from exc
    if response.is_error:
        try:
            detail = response.json().get("error")
        except (ValueError, AttributeError):
            detail = None
        raise RuntimeError(
            detail or f"Home could not switch the GPU provider (HTTP {response.status_code})"
        )

    log.info("Home started the Compose service for %s", name)
    if service not in ("training", "python"):
        _wait_until_ready(service, progress)
    else:
        import subprocess
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            result = subprocess.run(
                ["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, check=True, timeout=15,
            )
            if not result.stdout.strip():
                break
            if progress:
                progress("Waiting for GPU memory to be released")
            time.sleep(2)
        else:
            raise RuntimeError("GPU memory is still held by another compute process after stopping ComfyUI and Llama")
    client.set("gpu:active-service", service)


def _switch_with_task(
    client,
    previous: str | None,
    service: GPUService,
    *,
    transition_task: dict | None = None,
) -> None:
    """Run a service handoff as a visible, timed Styler queue task."""
    from styler.queue_history import publish_queue_event

    names = {"comfy": "ComfyUI", "llama": "Llama", "training": "Training", "python": "Python"}
    providers = {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}
    task = {
        **(transition_task or {}),
        "job_id": (transition_task or {}).get("job_id")
        or f"gpu-switch-{uuid.uuid4().hex}",
        "action": "gpu_service_switch",
        "job_type": "GPU service switch",
        "service_from": names.get(previous, "Stopped"),
        "service_to": names[service],
        "provider": providers[service],
        "target_provider": providers[service],
        "queue_started_at": (transition_task or {}).get("queue_started_at")
        or time.time(),
        "state": "running",
        "message": "Clearing GPU memory for Training" if service == "training" else f"Switching GPU to {names[service]}",
    }
    _publish_switch_progress(client, task, task["message"])
    try:
        _publish_switch_progress(client, task, f"Switching GPU to {names[service]}")
        _switch(
            client,
            service,
            progress=lambda message: _publish_switch_progress(client, task, message),
        )
        client.set(LAST_SWITCH_KEY, str(time.time()))
    except Exception as exc:
        log.exception("GPU switch %s failed", task["job_id"])
        publish_queue_event(
            client,
            STYLER_EVENTS,
            {
                **task,
                "state": "failed",
                "message": str(exc) or "GPU service switch failed",
            },
        )
        raise
    else:
        log.info("GPU switch %s completed: %s is ready", task["job_id"], names[service])
        publish_queue_event(
            client,
            STYLER_EVENTS,
            {
                **task,
                "state": "complete",
                "message": f"GPU ready: {names[service]}",
            },
        )
    finally:
        client.delete(TRANSITION_KEY)
        client.publish(
            STYLER_EVENTS,
            json.dumps({"job_id": task["job_id"], "state": "queue_changed"}),
        )


@contextmanager
def use_gpu_service(
    client,
    service: GPUService,
    *,
    transition_task: dict | None = None,
) -> Iterator[None]:
    """Reuse the active provider, taking exclusive ownership only for handoffs."""
    if not _enabled():
        yield
        return

    active = _active_service.get()
    if active is not None:
        if active != service:
            raise RuntimeError(
                f"Cannot switch GPU service from {active} to {service} inside one workload"
            )
        yield
        return

    token = _active_service.set(service)
    redis_client = client
    try:
        import redis

        redis_client = client or redis.Redis(
            host=os.environ.get("REDIS_HOST", "redis"), decode_responses=True
        )
        active_mode = redis_client.get("gpu:active-service")
        if isinstance(active_mode, bytes):
            active_mode = active_mode.decode()
        if active_mode == "training" and service != "training" and transition_task is None:
            raise RuntimeError("Training mode is active; leave Training manually before starting this GPU task")
        lock = redis_client.lock(
            LOCK_NAME,
            timeout=LOCK_TIMEOUT_SECONDS,
            blocking_timeout=LOCK_TIMEOUT_SECONDS,
        )
        if transition_task and redis_client is not None:
            _publish_switch_progress(
                redis_client,
                transition_task,
                "Waiting for GPU service lock before contacting Home",
            )
        else:
            log.info("Waiting for exclusive GPU lock for %s", service)
        wait_deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        wait_started = time.monotonic()
        last_status_probe = 0.0
        stopped_owner_since = None
        forced_switch_attempted = False
        while True:
            # Same-provider work reuses the active provider. GPU tasks execute
            # through the serial video worker, so they cannot overlap while
            # another provider transition is in progress. Recheck after waits
            # in case a handoff completed while we waited.
            if transition_task is None and _provider_is_active(redis_client, service):
                log.debug("Reusing active GPU provider %s without taking its lock", service)
                yield
                return
            remaining = wait_deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Could not acquire exclusive GPU-service ownership")
            acquired = lock.acquire(
                blocking=True,
                blocking_timeout=min(5, remaining),
            )
            if acquired:
                break
            waited = int(time.monotonic() - wait_started)
            owner_info = _lock_owner_info(redis_client)
            owner = _lock_owner_description(redis_client)
            message = f"Waiting for GPU service lock ({waited}s; {owner})"
            if transition_task:
                _publish_switch_progress(redis_client, transition_task, message)
            else:
                log.info(
                    "Still waiting for exclusive GPU lock for %s (%ss; %s)",
                    service,
                    waited,
                    owner,
                )
            now = time.monotonic()
            if now - last_status_probe >= LOCK_STATUS_POLL_SECONDS:
                last_status_probe = now
                owner_service = owner_info.get("service")
                if owner_service in ("comfy", "llama"):
                    try:
                        service_name = "comfyui" if owner_service == "comfy" else "llama"
                        if owner_service == "comfy":
                            ready = _home_program_ready("comfy")
                        else:
                            with httpx.Client(
                                timeout=httpx.Timeout(5, connect=2)
                            ) as probe:
                                ready = _ready("llama", probe)
                        running = service_name in _home_provider_status().get(
                            "running_providers", []
                        )
                        log.info(
                            "GPU lock owner status: provider=%s running=%s ready=%s",
                            service_name,
                            running,
                            ready,
                        )
                        if running:
                            stopped_owner_since = None
                        else:
                            stopped_owner_since = stopped_owner_since or now
                            stopped_for = now - stopped_owner_since
                            if (
                                stopped_for >= STOPPED_OWNER_GRACE_SECONDS
                                and _release_stopped_owner_lock(
                                    redis_client,
                                    owner_info,
                                    transition_task.get("job_id")
                                    if transition_task
                                    else None,
                                )
                            ):
                                log.warning(
                                    "Reclaimed GPU lock from stopped provider %s "
                                    "after %.0fs; retrying acquisition",
                                    service_name,
                                    stopped_for,
                                )
                                stopped_owner_since = None
                    except (httpx.HTTPError, ValueError, TypeError):
                        log.warning(
                            "Could not confirm GPU lock owner's service status; "
                            "will retry in %ss",
                            LOCK_STATUS_POLL_SECONDS,
                            exc_info=True,
                        )
            # A manual switch task is an explicit request to stop the current
            # provider. If its lock remains held after a short wait, ask
            # Home to perform the Compose handoff instead of waiting on the
            # Redis lease for its full two-hour TTL.
            owner_service = owner_info.get("service")
            if not owner_service:
                owner_service = redis_client.get("gpu:active-service")
                if isinstance(owner_service, bytes):
                    owner_service = owner_service.decode()
                if owner_service == "comfyui":
                    owner_service = "comfy"
            if (
                transition_task
                and not forced_switch_attempted
                and waited >= MANUAL_SWITCH_LOCK_WAIT_SECONDS
                and owner_service != service
            ):
                raw_transition = redis_client.get(TRANSITION_KEY)
                try:
                    active_transition = (
                        json.loads(raw_transition) if raw_transition else {}
                    )
                except (TypeError, ValueError):
                    active_transition = {}
                if active_transition.get("job_id") == transition_task.get("job_id"):
                    forced_switch_attempted = True
                    log.warning(
                        "GPU lock remains held by %s; forcing requested provider "
                        "handoff through Home after %ss",
                        owner_service,
                        waited,
                    )
                    lock_token = redis_client.get(LOCK_NAME)
                    _switch_with_task(
                        redis_client,
                        owner_service,
                        service,
                        transition_task=transition_task,
                    )
                    if not _release_stopped_owner_lock(
                        redis_client,
                        owner_info,
                        transition_task.get("job_id"),
                        expected_token=lock_token,
                        expected_service=owner_service,
                    ):
                        log.warning(
                            "Home switched providers, but the previous GPU lock "
                            "owner changed; continuing to wait for its release"
                        )
        log.info("Acquired exclusive GPU lock for %s", service)
        lock_owner = None
        try:
            # redis-py stores this acquisition's token in thread-local state;
            # Lock has no get_owner() method.
            lock_owner = lock.local.token
            if isinstance(lock_owner, bytes):
                lock_owner = lock_owner.decode()
            redis_client.set(
                LOCK_INFO_KEY,
                json.dumps(
                    {
                        "owner": lock_owner,
                        "service": service,
                        "job_id": transition_task.get("job_id") if transition_task else None,
                        "acquired_at": time.time(),
                    }
                ),
                ex=LOCK_TIMEOUT_SECONDS,
            )
            previous = redis_client.get("gpu:active-service")
            if isinstance(previous, bytes):
                previous = previous.decode()
            log.info("GPU switch request: active=%s target=%s", previous, service)
            if previous != service:
                _switch_with_task(
                    redis_client,
                    previous,
                    service,
                    transition_task=transition_task,
                )
            else:
                target_provider = {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}[service]
                running_providers = _home_provider_status().get(
                    "running_providers", []
                )
                if service == "python" and not running_providers:
                    log.debug("Python already owns the exclusive GPU; skipping provider switch")
                elif service in ("training", "python") or running_providers != [target_provider]:
                    _switch_with_task(
                        redis_client,
                        previous,
                        service,
                        transition_task=transition_task,
                    )
                elif service not in ("training", "python"):
                    _wait_until_ready(service)
                if transition_task and running_providers == [target_provider]:
                    from styler.queue_history import publish_queue_event

                    publish_queue_event(
                        redis_client,
                        STYLER_EVENTS,
                        {
                            **transition_task,
                            "provider": {"comfy": "comfyui", "llama": "llama", "training": "training", "python": "python"}[service],
                            "state": "complete",
                            "queue_started_at": transition_task.get(
                                "queue_started_at", time.time()
                            ),
                            "message": f"GPU already ready: {service}",
                        },
                    )
            yield
        finally:
            try:
                raw_owner = redis_client.get(LOCK_INFO_KEY)
                try:
                    owner_info = json.loads(raw_owner) if raw_owner else {}
                except (TypeError, ValueError):
                    owner_info = {}
                if lock_owner and owner_info.get("owner") == lock_owner:
                    redis_client.delete(LOCK_INFO_KEY)
            except Exception:
                log.exception("Could not clear GPU switch lock diagnostics")
            finally:
                try:
                    lock.release()
                except redis.exceptions.LockError:
                    log.warning(
                        "GPU lock ownership changed while this workload was "
                        "running; another provider switch reclaimed the lease"
                    )
    finally:
        if transition_task and redis_client is not None:
            raw_transition = redis_client.get(TRANSITION_KEY)
            try:
                active_transition = json.loads(raw_transition) if raw_transition else {}
            except (TypeError, ValueError):
                active_transition = {}
            if active_transition.get("job_id") == transition_task.get("job_id"):
                redis_client.delete(TRANSITION_KEY)
                redis_client.publish(
                    STYLER_EVENTS,
                    json.dumps(
                        {
                            "job_id": transition_task["job_id"],
                            "state": "queue_changed",
                        }
                    ),
                )
        _active_service.reset(token)

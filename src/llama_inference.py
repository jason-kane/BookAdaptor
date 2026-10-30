"""OpenAI-compatible client for the single shared llama.cpp GPU service."""

from concurrent.futures import ThreadPoolExecutor
import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Mapping

import httpx

DEFAULT_MAX_PARALLEL_REQUESTS = 3
REQUEST_TIMEOUT_SECONDS = 1_200
MODEL_NAME = "Qwen3.5-9B"
log = logging.getLogger(__name__)


def _api_url() -> str:
    return os.environ.get("LLAMA_API_URL", "http://llama:8080").rstrip("/")


def _image_data_url(path: str | Path) -> str:
    image_path = Path(path)
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/png"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _request(request: Mapping[str, Any]) -> str:
    prompt = request["prompt"]
    # Older illustration producers accidentally queued a one-element tuple,
    # which Redis JSON serialization turns into a one-element list.
    if isinstance(prompt, (list, tuple)) and len(prompt) == 1 and isinstance(prompt[0], str):
        log.warning("Unwrapping legacy one-element Llama prompt")
        prompt = prompt[0]
    if not isinstance(prompt, str):
        raise TypeError(
            f"Llama prompt must be a string, got {type(prompt).__name__}"
        )
    if not isinstance(request.get("system_prompt", ""), str):
        raise TypeError("Llama system_prompt must be a string")
    content: str | list[dict[str, Any]] = prompt
    image_path = request.get("image_path")
    if image_path is not None:
        content = [
            {"type": "text", "text": prompt},
            {
                "type": "image_url",
                "image_url": {"url": _image_data_url(image_path)},
            },
        ]
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": request.get(
                    "system_prompt", "Return only the requested result."
                ),
            },
            {"role": "user", "content": content},
        ],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": int(request.get("max_tokens", 1024)),
    }
    if request.get("thinking_budget_tokens") is not None:
        budget = int(request["thinking_budget_tokens"])
        if budget == 0:
            payload["reasoning_effort"] = "none"
            payload["reasoning_format"] = "deepseek"
        else:
            payload["thinking_budget_tokens"] = budget
    if request.get("response_format") is not None:
        payload["response_format"] = request["response_format"]
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=15)
    with httpx.Client(timeout=timeout) as client:
        response = client.post(f"{_api_url()}/v1/chat/completions", json=payload)
        if response.is_error:
            # Keep the server's diagnostic in the GPU task traceback. The
            # status-only HTTPStatusError hides llama.cpp's useful failure
            # detail (for example, context or model execution errors).
            detail = " ".join(response.text.split())[:2000]
            if not detail:
                detail = "no response body"
            request_shape = (
                "image+text content array"
                if image_path is not None
                else "text-only string content"
            )
            raise RuntimeError(
                f"Llama API returned HTTP {response.status_code} for {request_shape}: "
                f"{detail}"
            )
    result = response.json()
    try:
        choice = result["choices"][0]
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Llama returned no chat-completion content") from exc
    usage = result.get('usage') or {}
    log.info(
        "Llama token usage: input=%s output=%s total=%s max_output=%s finish_reason=%s",
        usage.get('prompt_tokens', 'unknown'), usage.get('completion_tokens', 'unknown'),
        usage.get('total_tokens', 'unknown'), payload['max_tokens'], choice.get('finish_reason'),
    )
    if choice.get("finish_reason") == "length":
        log.warning(
            "Llama completion reached max_tokens=%s; response may be truncated",
            payload["max_tokens"],
        )
    if isinstance(content, list):
        content = "".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if isinstance(content, str):
        if not content.strip():
            raise ValueError(
                "Llama returned an empty final response "
                f"(finish_reason={choice.get('finish_reason')}, max_tokens={payload['max_tokens']}); "
                "the output budget may have been consumed by reasoning"
            )
        return content
    raise ValueError("Llama returned an unsupported chat-completion content shape")


def _request_safely(request: Mapping[str, Any]) -> str | Exception:
    """Preserve successful sibling results when one request in a batch fails."""
    try:
        return _request(request)
    except Exception as error:
        return error


def chat_completion(
    client,
    prompt: str,
    *,
    system_prompt: str = "Return only the requested result.",
    image_path: str | Path | None = None,
    max_tokens: int = 1024,
    response_format: Mapping[str, Any] | None = None,
    thinking_budget_tokens: int | None = None,
    label: str | None = None,
) -> str:
    """Run one text or image-and-text request while holding Llama GPU ownership."""
    if (os.environ.get("RENDER_WORKER_PROCESS") == "1"
            and os.environ.get("VIDEO_WORKER_PROCESS") != "1"):
        from gpu_tasks import submit
        from gpu_tasks import gpu_label
        return submit("gpu_llama", {
            "prompt": prompt, "system_prompt": system_prompt,
            "image_path": str(image_path) if image_path else None,
            "max_tokens": max_tokens, "response_format": response_format,
            "thinking_budget_tokens": thinking_budget_tokens,
        }, provider="llama", label=label or gpu_label.get())

    from gpu_service import use_gpu_service

    with use_gpu_service(client, "llama"):
        return _request(
            {
                "prompt": prompt,
                "system_prompt": system_prompt,
                "image_path": image_path,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "thinking_budget_tokens": thinking_budget_tokens,
            }
        )


def chat_completions(
    client,
    requests: list[Mapping[str, Any]],
    *,
    max_parallel: int = DEFAULT_MAX_PARALLEL_REQUESTS,
) -> list[str | Exception]:
    """Submit one bounded parallel request batch without switching GPU services."""
    if max_parallel < 1:
        raise ValueError("max_parallel must be positive")
    if not requests:
        return []

    if (os.environ.get("RENDER_WORKER_PROCESS") == "1"
            and os.environ.get("VIDEO_WORKER_PROCESS") != "1"):
        results = []
        for request in requests:
            try:
                results.append(chat_completion(client, **request))
            except Exception as error:
                results.append(error)
        return results

    from gpu_service import use_gpu_service

    with use_gpu_service(client, "llama"):
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(requests))) as pool:
            return list(pool.map(_request_safely, requests))

#!/usr/bin/env python3
"""Run one MiniMax H3 Turbo resolution/duration snap and append it to the CSV.

Run inside artifactserver by piping this file to ``python -``. Edit WIDTH,
HEIGHT, and DURATION_SECONDS below for each additional snap value.
"""

import csv
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

WIDTH, HEIGHT, DURATION_SECONDS = 576, 1024, 10
OUT = Path("/output/video/experiments")
CSV_PATH = OUT / "minimax_h3_turbo_duration_resolution.csv"
SOURCE = Path("/workflows/experiment_minimax_h3_turbo_1s_warm.json")
RUNS = Path("/tmp/h3_turbo_duration_runs")


def vram_monitor(stop, stats):
    while not stop.is_set():
        try:
            with urllib.request.urlopen("http://comfyui:8188/system_stats", timeout=3) as response:
                device = (json.load(response).get("devices") or [{}])[0]
            total, free = int(device.get("vram_total", 0)), int(device.get("vram_free", 0))
            stats["peak"] = max(stats.get("peak", 0), (total - free) // 1048576)
            stats["minimum_free"] = min(stats.get("minimum_free", 10**9), free // 1048576)
        except Exception:
            pass
        stop.wait(2)


def main():
    RUNS.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen("http://comfyui:8188/queue", timeout=5) as response:
        queue = json.load(response)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("ComfyUI must be idle before the snap test starts.")

    tag = f"{WIDTH}x{HEIGHT}_{DURATION_SECONDS:03d}s"
    workflow_path = RUNS / f"{tag}.json"
    graph = json.loads(SOURCE.read_text())
    nodes = {node["id"]: node for node in graph["nodes"]}
    for node_id, value in ((141, WIDTH), (140, HEIGHT)):
        nodes[node_id]["widgets_values"][0] = value
        nodes[node_id].setdefault("widgets_values_named", {})["value"] = value
    duration = float(DURATION_SECONDS)
    nodes[142]["widgets_values"][0] = duration
    nodes[142].setdefault("widgets_values_named", {})["value"] = duration
    nodes[123]["widgets_values"][1:5] = [WIDTH, HEIGHT, duration, 20261028]
    nodes[123].setdefault("widgets_values_named", {}).update(
        {"width": WIDTH, "height": HEIGHT, "value_1": duration, "noise_seed": 20261028}
    )
    prefix = f"video/experiments/minimax_h3_turbo_{tag}"
    for node_id in (139, 143):
        nodes[node_id]["widgets_values"][0] = prefix
        nodes[node_id].setdefault("widgets_values_named", {})["value"] = prefix
    workflow_path.write_text(json.dumps(graph))

    stats, stop = {}, threading.Event()
    monitor = threading.Thread(target=vram_monitor, args=(stop, stats), daemon=True)
    monitor.start()
    started = time.monotonic()
    print(f"START {DURATION_SECONDS}s {WIDTH}x{HEIGHT}", flush=True)
    try:
        result = subprocess.run(
            ["comfy", "run", "--workflow", str(workflow_path), "--wait", "--json", "--no-notify",
             "--host", "comfyui", "--port", "8188", "--timeout", "900"],
            capture_output=True, text=True, timeout=7200,
        )
    finally:
        stop.set()
        monitor.join(timeout=3)
    elapsed = time.monotonic() - started
    stdout, stderr = result.stdout or "", result.stderr or ""
    (RUNS / f"{tag}.stdout.ndjson").write_text(stdout)
    (RUNS / f"{tag}.stderr.log").write_text(stderr)
    events = []
    for line in stdout.splitlines():
        try:
            events.append(json.loads(line))
        except ValueError:
            pass
    envelope = next((event for event in reversed(events) if event.get("type") == "envelope"), {})
    files = sorted(OUT.glob(f"minimax_h3_turbo_{tag}_*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True)
    status, frames, actual, filename, error = "success", "", "", "", ""
    if result.returncode or not envelope.get("ok", False):
        details = str(envelope.get("error") or "") + "\n" + stderr + "\n" + stdout[-12000:]
        error = re.sub(r"\x1b\[[0-9;]*m", "", details)[-3000:].replace("\n", " | ")
        status = "oom" if re.search(
            r"out of memory|outofmemoryerror|cuda error:.*memory|cuda out of memory|allocation on device.*failed",
            details, re.I,
        ) else "error"
    elif not files:
        status, error = "error", "ComfyUI reported success but no output MP4 was found."
    else:
        filename = files[0].relative_to(Path("/output")).as_posix()
        metadata = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
             "-show_entries", "stream=nb_read_frames,avg_frame_rate,duration", "-of", "json", str(files[0])],
            capture_output=True, text=True, check=True, timeout=60,
        )
        stream = json.loads(metadata.stdout)["streams"][0]
        rate = stream.get("avg_frame_rate", "24/1").split("/")
        fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 else float(rate[0])
        frames = int(stream.get("nb_read_frames") or 0)
        actual = float(stream.get("duration") or frames / fps)

    requested_frames = math.ceil(DURATION_SECONDS * 24)
    with CSV_PATH.open("a", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow([DURATION_SECONDS, WIDTH, HEIGHT, "9:16", requested_frames, frames, actual,
                         round(elapsed, 3), stats.get("peak", ""), stats.get("minimum_free", ""),
                         status, filename, error])
        stream.flush()
        os.fsync(stream.fileno())
    print(f"{status.upper()} {DURATION_SECONDS}s {WIDTH}x{HEIGHT}: {elapsed:.1f}s wall, "
          f"{frames} frames ({actual}s); CSV: {CSV_PATH}", flush=True)
    if status == "error":
        raise RuntimeError(error)


if __name__ == "__main__":
    main()

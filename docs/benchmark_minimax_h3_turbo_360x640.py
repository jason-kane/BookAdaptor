#!/usr/bin/env python3
"""Resume the H3 Turbo 360x640 duration sweep until VRAM OOM.

Run from the repository root with:
  cat docs/benchmark_minimax_h3_turbo_360x640.py | docker compose exec -T artifactserver python -

Results append to the existing minimax_h3_turbo_duration_resolution.csv.
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

OUT = Path("/output/video/experiments")
CSV_PATH = OUT / "minimax_h3_turbo_duration_resolution.csv"
SOURCE = Path("/workflows/experiment_minimax_h3_turbo_1s_warm.json")
RUNS = Path("/tmp/h3_turbo_duration_runs")
WIDTH, HEIGHT = 360, 640


def frame_grid(seconds):
    requested = max(5, math.ceil(seconds * 24))
    return requested + (5 - requested % 17) % 17


def append(row):
    with CSV_PATH.open("a", newline="") as stream:
        csv.writer(stream).writerow(row)
        stream.flush()
        os.fsync(stream.fileno())


def sample_vram(stop, result):
    while not stop.is_set():
        try:
            with urllib.request.urlopen("http://comfyui:8188/system_stats", timeout=3) as response:
                device = (json.load(response).get("devices") or [{}])[0]
            total, free = int(device.get("vram_total", 0)), int(device.get("vram_free", 0))
            result["peak_vram_mib"] = max(result.get("peak_vram_mib", 0), (total - free) // 1048576)
            result["minimum_free_mib"] = min(result.get("minimum_free_mib", 10**9), free // 1048576)
        except Exception:
            pass
        stop.wait(2)


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames,avg_frame_rate,duration", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    )
    stream = json.loads(result.stdout)["streams"][0]
    rate = stream.get("avg_frame_rate", "24/1").split("/")
    fps = float(rate[0]) / float(rate[1]) if len(rate) == 2 else float(rate[0])
    frames = int(stream.get("nb_read_frames") or 0)
    seconds = float(stream.get("duration") or frames / fps)
    return frames, seconds


def wait_for_idle():
    with urllib.request.urlopen("http://comfyui:8188/queue", timeout=5) as response:
        queue = json.load(response)
    if queue.get("queue_running") or queue.get("queue_pending"):
        raise RuntimeError("ComfyUI has queued work; wait for it to finish before resuming.")


def main():
    RUNS.mkdir(parents=True, exist_ok=True)
    rows = list(csv.DictReader(CSV_PATH.open(newline="")))
    existing = [r for r in rows if r["width"] == str(WIDTH) and r["height"] == str(HEIGHT)]
    if any(r["status"] == "oom" for r in existing):
        print("360x640 already reached VRAM OOM.", flush=True)
        return
    done = [int(r["requested_duration_seconds"]) for r in existing if r["status"] == "success"]
    duration = max(6, max(done, default=5) + 1)
    base = json.loads(SOURCE.read_text())

    while True:
        wait_for_idle()
        tag = f"{WIDTH}x{HEIGHT}_{duration:03d}s"
        runfile = RUNS / f"{tag}.json"
        graph = json.loads(json.dumps(base))
        nodes = {n["id"]: n for n in graph["nodes"]}
        for node_id, value in ((141, WIDTH), (140, HEIGHT)):
            nodes[node_id]["widgets_values"][0] = value
            nodes[node_id].setdefault("widgets_values_named", {})["value"] = value
        nodes[142]["widgets_values"][0] = float(duration)
        nodes[142].setdefault("widgets_values_named", {})["value"] = float(duration)
        compound = nodes[123]
        compound["widgets_values"][1:5] = [WIDTH, HEIGHT, float(duration), 20261028]
        compound.setdefault("widgets_values_named", {}).update(
            {"width": WIDTH, "height": HEIGHT, "value_1": float(duration), "noise_seed": 20261028}
        )
        prefix = f"video/experiments/minimax_h3_turbo_{tag}"
        for node_id in (139, 143):
            nodes[node_id]["widgets_values"][0] = prefix
            nodes[node_id].setdefault("widgets_values_named", {})["value"] = prefix
        runfile.write_text(json.dumps(graph))

        mem, stop = {}, threading.Event()
        monitor = threading.Thread(target=sample_vram, args=(stop, mem), daemon=True)
        monitor.start()
        start = time.monotonic()
        print(f"START {duration}s {WIDTH}x{HEIGHT}", flush=True)
        try:
            result = subprocess.run(
                ["comfy", "run", "--workflow", str(runfile), "--wait", "--json", "--no-notify",
                 "--host", "comfyui", "--port", "8188", "--timeout", "900"],
                capture_output=True, text=True, timeout=7200,
            )
        except KeyboardInterrupt:
            request = urllib.request.Request("http://comfyui:8188/interrupt", data=b"{}",
                headers={"Content-Type": "application/json"}, method="POST")
            urllib.request.urlopen(request, timeout=5).close()
            stop.set()
            monitor.join(timeout=3)
            raise
        elapsed = time.monotonic() - start
        stop.set()
        monitor.join(timeout=3)
        stdout, stderr = result.stdout or "", result.stderr or ""
        (RUNS / f"{tag}.stdout.ndjson").write_text(stdout)
        (RUNS / f"{tag}.stderr.log").write_text(stderr)
        events = []
        for line in stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        envelope = next((e for e in reversed(events) if e.get("type") == "envelope"), {})
        output = sorted(OUT.glob(f"minimax_h3_turbo_{tag}_*.mp4"),
                        key=lambda p: p.stat().st_mtime, reverse=True)
        status, frames, actual, output_name, error = "success", "", "", "", ""
        if result.returncode or not envelope.get("ok", False):
            details = str(envelope.get("error") or "") + "\n" + stderr + "\n" + stdout[-12000:]
            error = re.sub(r"\x1b\[[0-9;]*m", "", details)[-3000:].replace("\n", " | ")
            status = "oom" if re.search(
                r"out of memory|outofmemoryerror|cuda error:.*memory|cuda out of memory|allocation on device.*failed",
                details, re.I,
            ) else "error"
        elif not output:
            status, error = "error", "ComfyUI reported success but no output MP4 was found."
        else:
            output_name = output[0].relative_to(Path("/output")).as_posix()
            frames, actual = probe(output[0])
        append([duration, WIDTH, HEIGHT, "9:16", math.ceil(duration * 24), frames, actual,
                round(elapsed, 3), mem.get("peak_vram_mib", ""), mem.get("minimum_free_mib", ""),
                status, output_name, error])
        if status == "oom":
            print(f"OOM at {duration}s; results appended to {CSV_PATH}", flush=True)
            return
        if status != "success":
            raise RuntimeError(f"{tag} failed: {error}")
        print(f"DONE {duration}s: {elapsed:.1f}s wall, {frames} frames ({actual}s); "
              f"peak VRAM {mem.get('peak_vram_mib')} MiB", flush=True)
        duration += 1


if __name__ == "__main__":
    main()

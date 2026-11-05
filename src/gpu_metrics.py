"""Read-only, bounded GPU telemetry shared by websocket connections."""
import math
import os
import subprocess
import time
from threading import Lock

SAMPLE_INTERVAL_SECONDS = 2
_lock = Lock()
_sampled_at = float('-inf')
_cached = {'gpu_percent': None, 'vram_used_mib': None, 'vram_total_mib': None, 'gpu_name': None}


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def read_gpu_metrics():
    """Query the shared GPU; missing hardware/tools produce unavailable values."""
    unavailable = dict(gpu_percent=None, vram_used_mib=None, vram_total_mib=None, gpu_name=None)
    try:
        result = subprocess.run([
            'nvidia-smi', '--id=' + os.environ.get('GPU_METRICS_DEVICE', '0'),
            '--query-gpu=utilization.gpu,memory.used,memory.total,name',
            '--format=csv,noheader,nounits',
        ], capture_output=True, text=True, check=True, timeout=1)
        fields = result.stdout.strip().split(',', 3)
        if len(fields) != 4:
            return unavailable
        utilization, used, total = map(_number, fields[:3])
        name = fields[3].strip()
        if not name or name == "[N/A]":
            name = None
        if utilization is not None and utilization > 100:
            utilization = None
        if total is None or total <= 0 or used is None or used > total:
            used = total = None
        return dict(gpu_percent=utilization, vram_used_mib=used, vram_total_mib=total, gpu_name=name)
    except (OSError, subprocess.SubprocessError):
        return unavailable


def get_gpu_metrics():
    """Sample at most once every two seconds, irrespective of viewer count."""
    global _cached, _sampled_at
    with _lock:
        if time.monotonic() - _sampled_at >= SAMPLE_INTERVAL_SECONDS:
            _cached = read_gpu_metrics()
            _sampled_at = time.monotonic()
        return dict(_cached)

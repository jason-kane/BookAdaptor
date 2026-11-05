"""Shared, nonblocking Linux host CPU and memory telemetry."""
from pathlib import Path
from threading import Lock
import time

_lock = Lock()
_sampled_at = float('-inf')
_previous = {}
_cached = {}


def _cpu_ticks():
    result = {}
    for line in Path('/proc/stat').read_text().splitlines():
        fields = line.split()
        if not fields or not fields[0].startswith('cpu'):
            continue
        values = [int(value) for value in fields[1:9]]  # Exclude guest time already counted in user/nice.
        result[fields[0]] = (sum(values), values[3], values[4] if len(values) > 4 else 0)
    return result


def read_cpu_metrics():
    global _previous
    metrics = dict(cpu_percent=None, core_percent=[], io_wait_percent=None,
                   cpu_pressure_percent=None, memory_used_mib=None, memory_total_mib=None,
                   swap_used_mib=None, swap_total_mib=None)
    try:
        current = _cpu_ticks()
        usage = {}
        for name, (total, idle, wait) in current.items():
            prior = _previous.get(name)
            if prior is None:
                continue
            delta = total - prior[0]
            if delta > 0 and idle >= prior[1] and wait >= prior[2]:
                busy = delta - (idle - prior[1]) - (wait - prior[2])
                usage[name] = round(max(0, min(100, busy * 100 / delta)), 1)
                if name == 'cpu':
                    metrics['io_wait_percent'] = round((wait - prior[2]) * 100 / delta, 1)
        metrics['cpu_percent'] = usage.get('cpu')
        metrics['core_percent'] = [usage[name] for name in sorted(usage, key=lambda name: int(name[3:] or -1)) if name != 'cpu']
        _previous = current
    except (OSError, ValueError, IndexError):
        _previous = {}
    try:
        fields = {}
        for line in Path('/proc/meminfo').read_text().splitlines():
            key, _, value = line.partition(':')
            fields[key] = int(value.split()[0]) / 1024
        total, available = fields['MemTotal'], fields['MemAvailable']
        metrics.update(memory_total_mib=total, memory_used_mib=max(0, total - available),
                       swap_total_mib=fields['SwapTotal'],
                       swap_used_mib=max(0, fields['SwapTotal'] - fields['SwapFree']))
    except (OSError, ValueError, KeyError, IndexError):
        pass
    try:
        line = next(line for line in Path('/proc/pressure/cpu').read_text().splitlines() if line.startswith('some '))
        metrics['cpu_pressure_percent'] = float(dict(field.split('=') for field in line.split()[1:])['avg10'])
    except (OSError, ValueError, KeyError, StopIteration):
        pass
    return metrics


def get_cpu_metrics():
    """Share a two-second sample across all viewers without blocking workers."""
    global _sampled_at, _cached
    with _lock:
        now = time.monotonic()
        if now - _sampled_at >= 2:
            _cached = read_cpu_metrics()
            _sampled_at = now
        return {**_cached, 'core_percent': list(_cached.get('core_percent', []))}

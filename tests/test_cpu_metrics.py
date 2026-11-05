"""Host capacity metrics distinguish compute, I/O wait, and reclaimable RAM."""
from unittest.mock import Mock
import cpu_metrics


def test_cpu_deltas_exclude_io_wait_and_guest_double_counting(monkeypatch):
    monkeypatch.setattr(cpu_metrics, '_previous', {'cpu': (1000, 400, 100), 'cpu0': (1000, 400, 100)})
    files = {
        '/proc/stat': 'cpu 500 0 100 450 150 0 0 0 500 0\ncpu0 500 0 100 450 150 0 0 0 500 0\n',
        '/proc/meminfo': 'MemTotal: 8192 kB\nMemAvailable: 3072 kB\nSwapTotal: 2048 kB\nSwapFree: 1024 kB\n',
        '/proc/pressure/cpu': 'some avg10=12.50 avg60=8.0 avg300=3.0 total=100\n',
    }
    monkeypatch.setattr(cpu_metrics.Path, 'read_text', lambda path: files[str(path)])
    result = cpu_metrics.read_cpu_metrics()
    assert result['cpu_percent'] == 50
    assert result['core_percent'] == [50]
    assert result['io_wait_percent'] == 25
    assert result['cpu_pressure_percent'] == 12.5
    assert result['memory_used_mib'] == 5
    assert result['swap_used_mib'] == 1


def test_first_sample_and_missing_proc_are_unavailable_not_idle(monkeypatch):
    monkeypatch.setattr(cpu_metrics, '_previous', {})
    monkeypatch.setattr(cpu_metrics, '_cpu_ticks', lambda: {'cpu': (100, 20, 5)})
    monkeypatch.setattr(cpu_metrics.Path, 'read_text', Mock(side_effect=OSError))
    result = cpu_metrics.read_cpu_metrics()
    assert result['cpu_percent'] is None
    assert result['memory_used_mib'] is None
    monkeypatch.setattr(cpu_metrics, '_cpu_ticks', Mock(side_effect=OSError))
    assert cpu_metrics.read_cpu_metrics()['cpu_percent'] is None
    assert cpu_metrics._previous == {}


def test_sampling_shared_and_returned_lists_are_independent(monkeypatch):
    now = [1]
    monkeypatch.setattr(cpu_metrics.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(cpu_metrics, '_sampled_at', float('-inf'))
    sample = Mock(return_value={'cpu_percent': 75, 'core_percent': [90, 60]})
    monkeypatch.setattr(cpu_metrics, 'read_cpu_metrics', sample)
    result = cpu_metrics.get_cpu_metrics()
    result['core_percent'].clear()
    assert cpu_metrics.get_cpu_metrics()['core_percent'] == [90, 60]
    assert sample.call_count == 1
    now[0] += 2
    cpu_metrics.get_cpu_metrics()
    assert sample.call_count == 2

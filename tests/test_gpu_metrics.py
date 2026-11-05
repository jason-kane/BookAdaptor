"""GPU telemetry must never turn missing or invalid hardware data into idle usage."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import gpu_metrics


@pytest.mark.parametrize('output,expected', [
    ('75, 8192, 24576\n', {'gpu_percent': 75, 'vram_used_mib': 8192, 'vram_total_mib': 24576}),
    ('0, 0, 24576\n', {'gpu_percent': 0, 'vram_used_mib': 0, 'vram_total_mib': 24576}),
    ('[N/A], 8192, 24576', {'gpu_percent': None, 'vram_used_mib': 8192, 'vram_total_mib': 24576}),
    ('25, [N/A], [N/A]', {'gpu_percent': 25, 'vram_used_mib': None, 'vram_total_mib': None}),
    ('nan, -1, 0', {'gpu_percent': None, 'vram_used_mib': None, 'vram_total_mib': None}),
    ('101, 10, 5', {'gpu_percent': None, 'vram_used_mib': None, 'vram_total_mib': None}),
    ('unexpected output', {'gpu_percent': None, 'vram_used_mib': None, 'vram_total_mib': None}),
])
def test_gpu_samples_validate_each_metric(monkeypatch, output, expected):
    if output != 'unexpected output':
        output = output.rstrip() + ', NVIDIA GeForce RTX 3060\n'
    expected = {**expected, 'gpu_name': None if output == 'unexpected output' else 'NVIDIA GeForce RTX 3060'}
    run = Mock(return_value=SimpleNamespace(stdout=output))
    monkeypatch.setattr(gpu_metrics.subprocess, 'run', run)
    assert gpu_metrics.read_gpu_metrics() == expected
    assert run.call_args.kwargs['timeout'] == 1
    assert run.call_args.args[0][0] == 'nvidia-smi'


@pytest.mark.parametrize('error', [FileNotFoundError(), subprocess.TimeoutExpired('nvidia-smi', 1), subprocess.CalledProcessError(1, 'nvidia-smi')])
def test_unavailable_gpu_does_not_break_queue(monkeypatch, error):
    monkeypatch.setattr(gpu_metrics.subprocess, 'run', Mock(side_effect=error))
    assert gpu_metrics.read_gpu_metrics() == {'gpu_percent': None, 'vram_used_mib': None, 'vram_total_mib': None, 'gpu_name': None}


def test_viewers_share_bounded_sampling_and_failures_replace_old_values(monkeypatch):
    now = [10]
    monkeypatch.setattr(gpu_metrics.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(gpu_metrics, '_sampled_at', float('-inf'))
    sample = Mock(side_effect=[{'gpu_percent': 85}, {'gpu_percent': None}])
    monkeypatch.setattr(gpu_metrics, 'read_gpu_metrics', sample)
    assert gpu_metrics.get_gpu_metrics() == {'gpu_percent': 85}
    returned = gpu_metrics.get_gpu_metrics()
    returned['gpu_percent'] = 0
    assert gpu_metrics.get_gpu_metrics() == {'gpu_percent': 85}
    assert sample.call_count == 1
    now[0] += 2
    assert gpu_metrics.get_gpu_metrics() == {'gpu_percent': None}
    assert sample.call_count == 2


def test_idle_websocket_sends_metrics_without_queue_changes(monkeypatch):
    import ast
    import json
    from pathlib import Path
    import sys
    from unittest.mock import MagicMock
    path = Path(__file__).resolve().parents[1] / 'src/websocket/server.py'
    fn = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'styler_websocket')
    fn.decorator_list = []
    metrics = {'gpu_percent': 42, 'vram_used_mib': 8192, 'vram_total_mib': 24576}
    monkeypatch.setattr(gpu_metrics, 'get_gpu_metrics', lambda: metrics)
    for name, module in {
        'styler.jobs': SimpleNamespace(EVENTS='events', sample_task=None, request_sample=None),
        'task_queue': SimpleNamespace(PRIORITY_QUEUE='priority', TAG_QUEUE='tags'),
        'const': SimpleNamespace(),
        'cpu_queue': SimpleNamespace(snapshot=lambda client: {'state': 'cpu_queue_plan'}),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    client = MagicMock()
    scope = dict(redis_connection=lambda: client, json=json,
        time=SimpleNamespace(monotonic=lambda: 100),
        VIDEO_EVENTS='video', CHAPTER_IMAGE_EVENTS='images', FRAME_EVENTS='frames',
        styler_queue_snapshot=lambda *args: {'running': None, 'jobs': []})
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(path), 'exec'), scope)
    ws = MagicMock()
    ws.receive.side_effect = EOFError('connection closed')
    with pytest.raises(EOFError):
        scope[fn.name](ws)
    sent = [json.loads(call.args[0]) for call in ws.send.call_args_list]
    assert {'state': 'gpu_metrics', **metrics} in sent
    client.close.assert_called_once()

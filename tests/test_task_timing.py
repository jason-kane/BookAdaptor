"""Timing profiles, elapsed-time estimates, and worker-aware queue scheduling."""
from unittest.mock import Mock

import pytest
import task_timing as timing


def task(identity='one', *, units=1, start=None, state='running', key='known'):
    result = dict(job_id=identity, action='draw_frame', timing_key=key,
                  timing_units=units, timing_unit='frame', state=state)
    if start is not None:
        result['queue_started_at'] = start
    return result


def estimator(now=100):
    client = Mock()
    client.hgetall.side_effect = lambda key: {'count': '3', 'sum': '60', 'min': '10', 'max': '30'} if key.endswith(':known') else {}
    client.lrange.side_effect = lambda key, *_: ['10', '20', '30'] if key.endswith(':known') else []
    return timing.Estimator(client, now=now)


def workflow(prompt='one', seed=1, width=512, model='a.safetensors', seconds=2):
    return dict(job_id=prompt, action='gpu_workflow', provider='comfyui', arguments={
        'duration_seconds': seconds, 'workflow': {
            '1': {'class_type': 'CheckpointLoaderSimple', 'inputs': {'ckpt_name': model}},
            '2': {'class_type': 'VideoSampler', 'inputs': {'prompt': prompt, 'seed': seed,
                                                         'width': width, 'height': 512, 'steps': 20}},
        }})


def test_profiles_group_comparable_work_and_exclude_content_identity():
    first = timing.prepare(workflow())
    second = timing.prepare(workflow('unrelated private chapter text', seed=999, seconds=8))
    assert first['timing_key'] == second['timing_key']
    assert first['timing_units'] == 2
    assert second['timing_units'] == 8
    assert first['timing_key'] != timing.prepare(workflow(width=1024))['timing_key']
    assert first['timing_key'] != timing.prepare(workflow(model='different.safetensors'))['timing_key']
    implicit = workflow()
    del implicit['provider']
    assert timing.prepare(implicit)['timing_key'] == first['timing_key']


def test_estimate_uses_elapsed_time_and_workload_not_provider_percentage():
    estimate = estimator().attach({**task(units=2, start=90), 'percent': 99})['timing']
    assert estimate['min_seconds'] == 20
    assert estimate['avg_seconds'] == 40
    assert estimate['max_seconds'] == 60
    assert estimate['remaining_seconds'] == 30
    other = estimator().attach({**task(units=2, start=90), 'percent': 1})['timing']
    assert estimate == other


def test_overruns_condition_on_longer_observations_then_report_unknown():
    observed = estimator().attach(task(units=2, start=50))['timing']
    assert observed['remaining_seconds'] == 10
    overrun = estimator().attach(task(units=2, start=39))['timing']
    assert overrun['remaining_seconds'] is None
    assert overrun['status'] == 'overrun'


def test_missing_history_never_fabricates_a_countdown():
    unknown = estimator().attach(task(start=90, key='new-work'))['timing']
    assert unknown['count'] == 0
    assert unknown['eta_seconds'] is None
    assert unknown['status'] == 'learning'


def test_gpu_serial_eta_includes_every_task_ahead():
    current, queued = estimator().serial(task(start=90), [task('two', state='queued'), task('three', state='queued')])
    assert current['timing']['remaining_seconds'] == 10
    assert [job['timing']['eta_seconds'] for job in queued] == [30, 50]
    _, unknown = estimator().serial(task(start=90, key='new-work'), [task(state='queued')])
    assert unknown[0]['timing']['eta_seconds'] is None
    assert unknown[0]['timing']['avg_seconds'] == 20


def test_cpu_eta_uses_parallel_worker_slots():
    active, queued = estimator().parallel(
        [task('active-1', start=90), task('active-2', start=100)],
        [task(str(i), state='queued') for i in range(3)], 2)
    assert [job['timing']['wait_seconds'] for job in queued] == [10, 20, 30]
    assert [job['timing']['eta_seconds'] for job in queued] == [30, 40, 50]
    _, free_slot = estimator().parallel([task(start=90, key='new-work')], [task(state='queued')], 2)
    assert free_slot[0]['timing']['wait_seconds'] == 0
    assert free_slot[0]['timing']['eta_seconds'] == 20


def test_hash_string_metadata_is_supported_and_terminal_duration_stops_clock():
    measured = estimator().attach({**task(units='2', start='90'), 'state': 'complete', 'duration_seconds': '8.5'})
    assert measured['timing']['elapsed_seconds'] == 8.5
    assert measured['timing']['eta_seconds'] == 0
    assert timing.eta_label(measured) == 'Done · 9s'
    assert timing.format_seconds(0.032) == '32ms'


def test_rendered_progress_displays_eta_and_escapes_user_content():
    import ast
    import json
    import re
    from pathlib import Path
    path = Path(__file__).parents[1] / 'src/websocket/server.py'
    node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'progress_html')
    namespace = dict(json=json, re=re)
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    event = estimator().attach({**task(start=90), 'percent': 99, 'message': '<script>Unsafe</script> 99% done'})
    html = namespace['progress_html'](event)
    assert '~10s left' in html
    assert '99%' not in html
    assert '<script>' not in html
    assert '&lt;script&gt;' in html
    assert 'data-task-timing' in html

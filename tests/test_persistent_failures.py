"""Failed chapter and CPU work stays visible independently of transient progress."""
import ast
import json
from pathlib import Path
from unittest.mock import Mock

import cpu_queue


def test_old_failures_are_not_capped_or_filtered_by_age():
    client = Mock()
    tasks = [dict(job_id=f'failed-{i}', action='textoviva_one_shot', state='failed',
                  completed_at=1, message='Failed', debug_details='Original traceback')
             for i in range(65)]
    client.get.return_value = None
    client.hvals.side_effect = lambda key: list(map(json.dumps, tasks)) if key == cpu_queue.FAILED_TASKS else []
    client.lrange.return_value = []
    client.llen.return_value = 0
    plan = cpu_queue.snapshot(client)
    assert len(plan['failed']) == 65
    assert all(task['debug_details'] == 'Original traceback' for task in plan['failed'])
    assert all(task['cancellable'] is False for task in plan['failed'])


def test_publish_failure_retains_identity_and_traceback():
    path = Path('src/websocket/server.py')
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'publish_progress')
    import re, time
    namespace = dict(json=json, time=time, re=re, publish_queue_event=Mock(), VIDEO_EVENTS='video_render_events')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    client = Mock()
    client.hgetall.return_value = dict(action='textoviva_one_shot', author='Baum', title='Oz', chapter_number='14')
    client.lrange.return_value = []
    namespace['publish_progress'](client, 'failed', 10, 'Import failed', 'failed', debug_details='Traceback')
    saved = next(call.args for call in client.hset.call_args_list
                 if call.args[0] == cpu_queue.FAILED_TASKS)
    retained = json.loads(saved[2])
    assert retained['chapter_number'] == '14'
    assert retained['debug_details'] == 'Traceback'
    assert retained['state'] == 'failed'

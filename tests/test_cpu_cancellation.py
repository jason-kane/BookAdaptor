"""Cancellation removes queued jobs and stops only the selected process group."""
import fnmatch
import json
import multiprocessing as mp
import os
from pathlib import Path
import signal
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import cpu_queue as cpu
import render_worker
from task_queue import PRIORITY_QUEUE, RENDER_QUEUE, VIDEO_QUEUE


class RedisMemory:
    def __init__(self):
        self.values, self.hashes, self.lists, self.events = {}, {}, {}, []

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, **kwargs):
        self.values[key] = value
        return True

    def delete(self, *keys):
        for key in keys:
            for store in (self.values, self.hashes, self.lists):
                store.pop(key, None)

    def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    def hgetall(self, key):
        return dict(self.hashes.get(key, {}))

    def hvals(self, key):
        return list(self.hashes.get(key, {}).values())

    def hset(self, key, field=None, value=None, mapping=None):
        self.hashes.setdefault(key, {}).update(mapping if mapping is not None else {field: value})

    def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)

    def hincrby(self, key, field, increment):
        self.hset(key, field, int(self.hget(key, field) or 0) + increment)

    def lrange(self, key, start, end):
        return self.lists.get(key, [])[start:None if end == -1 else end + 1]

    def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)

    def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        if value not in items:
            return 0
        items.remove(value)
        return 1

    def ltrim(self, key, start, end):
        self.lists[key] = self.lrange(key, start, end)

    def scan_iter(self, pattern):
        return iter(key for key in self.lists if fnmatch.fnmatch(key, pattern))

    def expire(self, *args):
        pass

    def publish(self, channel, payload):
        self.events.append((channel, json.loads(payload)))

    def pipeline(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def execute(self):
        return []

    def watch(self, *args):
        pass

    def multi(self):
        pass


def frame(job_id='frame', parent=None):
    return dict(job_id=job_id, batch_id='batch', parent_job_id=parent,
                action='draw_frame', arguments={}, track_status=True)


def test_cancel_pending_cpu_updates_status_and_batch_once():
    client = RedisMemory()
    task = frame()
    client.lpush(cpu.CPU_QUEUE, json.dumps(task))
    assert cpu.cancel_task(client, 'frame')['state'] == 'cpu_cancelled'
    assert client.lrange(cpu.CPU_QUEUE, 0, -1) == []
    assert json.loads(client.get('cpu_job:frame'))['state'] == 'cancelled'
    assert client.hget('cpu_batch:batch', 'failed') == 1
    assert cpu.cancel_task(client, 'frame')['state'] == 'cpu_cancel_rejected'
    assert client.hget('cpu_batch:batch', 'failed') == 1


def test_pending_render_is_removed_and_keeps_private_inputs_private():
    client = RedisMemory()
    client.lpush(RENDER_QUEUE, json.dumps(dict(job_id='render', action='chapter_import',
                                             sections='private source text')))
    assert cpu.cancel_task(client, 'render')['state'] == 'cpu_cancelled'
    assert not client.lrange(RENDER_QUEUE, 0, -1)
    assert client.hget('video_job:render', 'state') == 'cancelled'
    assert all('sections' not in event for _, event in client.events)


def test_running_cpu_requires_explicit_confirmation():
    client = RedisMemory()
    task = {**frame(), 'worker_id': '1'}
    client.hset(cpu.RUNNING, '1', json.dumps(task))
    assert cpu.cancel_task(client, 'frame')['state'] == 'cpu_cancel_rejected'
    assert not cpu.cancellation_requested(client, task)
    assert cpu.cancel_task(client, 'frame', allow_running=True)['state'] == 'cpu_cancelling'
    assert cpu.cancellation_requested(client, task)


def test_queue_claim_race_does_not_stop_a_running_task_without_confirmation(monkeypatch):
    client = RedisMemory()
    task = frame()
    raw = json.dumps(task)
    client.lpush(cpu.CPU_QUEUE, raw)
    def claimed(*_):
        client.lists[cpu.CPU_QUEUE] = []
        client.lists[cpu.PROCESSING + ':1'] = [raw]
        return 0
    monkeypatch.setattr(client, 'lrem', claimed)
    assert cpu.cancel_task(client, 'frame')['state'] == 'cpu_cancel_rejected'
    assert not cpu.cancellation_requested(client, task)


def test_cancel_running_coordinator_cancels_only_its_children():
    client = RedisMemory()
    client.set(cpu.RENDER_RUNNING, json.dumps(dict(job_id='parent', action='chapter_add_all')))
    client.lpush(cpu.CPU_QUEUE, json.dumps(frame('child', 'parent')))
    client.lpush(cpu.CPU_QUEUE, json.dumps(frame('other', 'unrelated')))
    client.hset(cpu.RUNNING, '1', json.dumps(frame('active-child', 'parent')))
    client.lpush(PRIORITY_QUEUE, json.dumps(dict(job_id='gpu-child', parent_job_id='parent')))
    client.lpush(VIDEO_QUEUE, json.dumps(dict(job_id='other-gpu', parent_job_id='unrelated')))
    assert cpu.cancel_task(client, 'parent', allow_running=True)['state'] == 'cpu_cancelling'
    assert [json.loads(raw)['job_id'] for raw in client.lrange(cpu.CPU_QUEUE, 0, -1)] == ['other']
    assert cpu.cancellation_requested(client, frame('active-child', 'parent'))
    assert not cpu.cancellation_requested(client, frame('other', 'unrelated'))
    assert not client.lrange(PRIORITY_QUEUE, 0, -1)
    assert client.lrange(VIDEO_QUEUE, 0, -1)


def test_cancelled_claim_is_acknowledged_without_execution(monkeypatch):
    import task_timing
    client = RedisMemory()
    task = frame()
    payload = json.dumps(task)
    claim = cpu.PROCESSING + ':1'
    client.lpush(claim, payload)
    client.set(cpu.CANCEL_PREFIX + task['job_id'], '1')
    execute = Mock()
    monkeypatch.setattr(cpu, 'execute', execute)
    monkeypatch.setattr(task_timing, 'prepare', lambda task: task)
    cpu.run_task(client, payload, '1', {}, claim)
    execute.assert_not_called()
    assert not client.lrange(claim, 0, -1)
    assert not client.hvals(cpu.RUNNING)
    assert client.hget('cpu_batch:batch', 'failed') == 1


def test_completion_and_cancellation_cannot_double_count_a_job():
    client = RedisMemory()
    task = frame()
    assert cpu.settle_cpu_task(client, 'frame', lambda pipe:
                               pipe.hincrby('cpu_batch:batch', 'completed', 1))
    assert not cpu.finish_cancelled_cpu(client, task, json.dumps(task))
    assert client.hget('cpu_batch:batch', 'completed') == 1
    assert client.hget('cpu_batch:batch', 'failed') is None


def test_retry_of_cancelled_child_is_independent_of_cancelled_parent():
    client = RedisMemory()
    task = frame('child', 'parent')
    client.set(cpu.CANCEL_PREFIX + 'parent', '1')
    client.set(cpu.PAYLOAD_PREFIX + 'child', json.dumps(task))
    cpu.finish_cancelled_cpu(client, task, json.dumps(task))
    result = cpu.retry_task(client, 'child')
    assert result['state'] == 'cpu_retry_queued'
    retry = json.loads(client.lrange(cpu.CPU_QUEUE, 0, -1)[0])
    assert retry['job_id'] != 'child' and retry['batch_id'] != task['batch_id']
    assert 'parent_job_id' not in retry
    assert not cpu.cancellation_requested(client, retry)


def _work_with_subprocess(pipe):
    os.setsid()
    child = subprocess.Popen(['sleep', '60'])
    pipe.send(child.pid)
    while True:
        time.sleep(1)


def test_running_cpu_cancellation_kills_its_process_group_and_acks_claim():
    client = RedisMemory()
    task = {**frame(), 'worker_id': '1', 'started_at': time.time()}
    claim = cpu.PROCESSING + ':1'
    client.lpush(claim, json.dumps(frame()))
    client.hset(cpu.RUNNING, '1', json.dumps(task))
    client.set(cpu.CANCEL_PREFIX + 'frame', '1')
    receive, send = mp.Pipe(duplex=False)
    process = mp.get_context('fork').Process(target=_work_with_subprocess, args=(send,))
    process.start()
    try:
        assert receive.poll(5)
        child_pid = receive.recv()
        assert cpu.stop_cancelled_worker(client, '1', process)
        assert process.exitcode == -signal.SIGKILL
        # A killed subprocess can briefly remain as a zombie until init reaps it.
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            status = Path(f'/proc/{child_pid}/stat')
            if not status.exists() or status.read_text().split()[2] == 'Z':
                break
            time.sleep(0.02)
        else:
            pytest.fail('Task subprocess was left running')
        assert not client.lrange(claim, 0, -1)
        assert not client.hvals(cpu.RUNNING)
        assert client.hget('cpu_batch:batch', 'failed') == 1
    finally:
        if process.is_alive():
            os.killpg(process.pid, signal.SIGKILL)
        process.join(timeout=3)
        receive.close()
        send.close()


def test_worker_identity_is_rechecked_before_killing(monkeypatch):
    client = RedisMemory()
    client.set(cpu.CANCEL_PREFIX + 'old', '1')
    monkeypatch.setattr(client, 'hget', Mock(side_effect=[
        json.dumps(frame('old')), json.dumps(frame('new'))]))
    kill, killpg = Mock(), Mock()
    monkeypatch.setattr(cpu.os, 'kill', kill)
    monkeypatch.setattr(cpu.os, 'killpg', killpg)
    assert not cpu.stop_cancelled_worker(client, '1', SimpleNamespace(pid=123))
    assert [call.args[1] for call in kill.call_args_list] == [signal.SIGSTOP, signal.SIGCONT]
    killpg.assert_not_called()


def _coordinator(task):
    os.setsid()
    Path(task['ready_file']).write_text('running')
    while True:
        time.sleep(1)


def test_running_coordinator_is_terminated_and_reported_cancelled(monkeypatch, tmp_path):
    client = RedisMemory()
    task = dict(job_id='render', action='chapter_add_all', ready_file=str(tmp_path / 'ready'))
    client.set(cpu.CANCEL_PREFIX + 'render', '1')
    client.set(cpu.RENDER_RUNNING, json.dumps(task))
    monkeypatch.setattr(render_worker, 'run_job_process', _coordinator)
    monkeypatch.setattr(render_worker, 'mp', SimpleNamespace(Process=mp.get_context('fork').Process))
    assert render_worker.run_supervised_job(client, task) == -signal.SIGKILL
    assert client.hget('video_job:render', 'state') == 'cancelled'
    assert client.get(cpu.RENDER_RUNNING) is None

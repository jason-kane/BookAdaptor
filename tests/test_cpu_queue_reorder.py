"""Exercise the atomic queue move against Redis using isolated, disposable keys."""
import json
import uuid

import pytest
import redis

import cpu_queue
import task_queue


@pytest.fixture
def queues(monkeypatch):
    client=redis.Redis(host='127.0.0.1',decode_responses=True,socket_connect_timeout=1,socket_timeout=2)
    try:client.ping()
    except redis.RedisError:pytest.skip('Local Redis unavailable')
    root='queue_reorder_test:'+uuid.uuid4().hex
    keys=[root+':cpu',root+':render',root+':render_running',root+':cpu_running']
    monkeypatch.setattr(cpu_queue,'CPU_QUEUE',keys[0])
    monkeypatch.setattr(task_queue,'RENDER_QUEUE',keys[1])
    monkeypatch.setattr(cpu_queue,'RENDER_RUNNING',keys[2])
    monkeypatch.setattr(cpu_queue,'RUNNING',keys[3])
    yield client,keys
    client.delete(*keys)
    client.close()


def populate(client,key,ids):
    client.lpush(key,*(json.dumps({'job_id':value,'arguments':{'keep':value}}) for value in ids))


def order(client,key):
    return [json.loads(raw)['job_id'] for raw in client.lrange(key,0,-1)][::-1]


@pytest.mark.parametrize('queue_index',[0,1])
def test_reordering_changes_fifo_order_and_preserves_exact_payloads(queues,queue_index):
    client,keys=queues;key=keys[queue_index]
    populate(client,key,['a','b','c'])
    payloads=sorted(client.lrange(key,0,-1))
    assert cpu_queue.reorder_task(client,'c','a','before')['state']=='cpu_job_reordered'
    assert order(client,key)==['c','a','b']
    assert cpu_queue.reorder_task(client,'c','b','after')['state']=='cpu_job_reordered'
    assert order(client,key)==['a','b','c']
    assert sorted(client.lrange(key,0,-1))==payloads


@pytest.mark.parametrize('running_key',[2,3])
def test_running_tasks_and_running_drop_targets_are_fixed(queues,running_key):
    client,keys=queues;populate(client,keys[0],['a','b'])
    if running_key==2:client.set(keys[running_key],json.dumps({'job_id':'a'}))
    else:client.hset(keys[running_key],'worker',json.dumps({'job_id':'a'}))
    assert cpu_queue.reorder_task(client,'a','b')['state']=='cpu_reorder_rejected'
    assert cpu_queue.reorder_task(client,'b','a')['state']=='cpu_reorder_rejected'
    assert order(client,keys[0])==['a','b']


def test_a_worker_claiming_source_or_anchor_before_drop_rejects_move(queues):
    client,keys=queues;populate(client,keys[0],['a','b','c'])
    claimed=client.rpop(keys[0])
    assert json.loads(claimed)['job_id']=='a'
    assert cpu_queue.reorder_task(client,'a','b')['state']=='cpu_reorder_rejected'
    assert cpu_queue.reorder_task(client,'b','a')['state']=='cpu_reorder_rejected'
    assert order(client,keys[0])==['b','c']


def test_cross_queue_moves_cannot_change_execution_owner(queues):
    client,keys=queues;populate(client,keys[0],['cpu']);populate(client,keys[1],['render'])
    assert cpu_queue.reorder_task(client,'cpu','render')['state']=='cpu_reorder_rejected'
    assert order(client,keys[0])==['cpu'] and order(client,keys[1])==['render']


def test_large_queue_move_searches_past_first_chunk(queues):
    client,keys=queues;ids=[str(i) for i in range(300)];populate(client,keys[0],ids)
    assert cpu_queue.reorder_task(client,'290','2')['state']=='cpu_job_reordered'
    ids.remove('290');ids.insert(2,'290')
    assert order(client,keys[0])==ids

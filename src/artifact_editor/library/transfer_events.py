"""Stream persisted transfer changes, including jobs already running at deploy."""
import json

from .storage import pending_key, release_transfer_slot, status_key, task_identity


STATUS_PATTERN = '__keyspace@0__:library_transfer:*'
CPU_PATTERN = '__keyspace@0__:cpu_job:*'


def enable_notifications(client):
    # Re-enable on reconnect after Redis restarts; preserve other subscribers'
    # configuration. Hash events cover existing workers' HSET progress reports,
    # and string events cover CPU completion/cancellation status written by SET.
    flags = client.config_get('notify-keyspace-events').get('notify-keyspace-events', '')
    required = 'K' + ('' if 'A' in flags else 'h$')
    updated = flags + ''.join(flag for flag in required if flag not in flags)
    if updated != flags:
        client.config_set('notify-keyspace-events', updated)


def snapshot(client, key):
    status = client.hgetall(key)
    if status.get('state') in ('complete', 'failed', 'cancelled') and status.get('transfer_id'):
        release_transfer_slot(client, status['transfer_id'])
    if status.get('state') in ('queued', 'running') and status.get('job_id'):
        raw = client.get('cpu_job:' + status['job_id'])
        task = json.loads(raw) if raw else {}
        if task.get('state') in ('failed', 'cancelled'):
            status.update(state=task['state'], message=task.get('message', 'Move stopped; source files were kept.'))
            client.hset(key, mapping=status)
            if status.get('author') and status.get('title'):
                client.delete(pending_key(status['author'], status['title']))
            if status.get('transfer_id'):
                release_transfer_slot(client, status['transfer_id'])
    return status


def event_snapshot(client, event):
    channel = event.get('channel', '')
    prefix = '__keyspace@0__:'
    if not channel.startswith(prefix):
        return None
    key = channel[len(prefix):]
    if key.startswith('library_transfer:'):
        return snapshot(client, key)
    if key.startswith('cpu_job:'):
        raw = client.get(key)
        task = json.loads(raw) if raw else {}
        identity = task_identity(task)
        if task.get('action') == 'move_book' and identity:
            status = snapshot(client, status_key(*identity))
            # An old CPU outcome must never settle a later move of this book.
            if status.get('job_id') == task.get('job_id'):
                return status
    return None


def serve(ws, client):
    pubsub = client.pubsub()
    try:
        enable_notifications(client)
        pubsub.psubscribe(STATUS_PATTERN, CPU_PATTERN)
        # Subscribe before taking the initial snapshot, so a change occurring
        # during that snapshot is delivered afterwards rather than lost.
        acknowledgements = 0
        while acknowledgements < 2:
            event = pubsub.get_message(timeout=5)
            if event is None:
                raise RuntimeError('Unable to subscribe to library transfer updates.')
            if event.get('type') == 'psubscribe':
                acknowledgements += 1
        for key in client.scan_iter('library_transfer:*'):
            status = snapshot(client, key)
            if status:
                ws.send(json.dumps({'type': 'library_transfer', **status}))
        while True:
            # Receive detects a browser disconnect even when no book is moving.
            ws.receive(timeout=0.1)
            event = pubsub.get_message(ignore_subscribe_messages=True, timeout=0.2)
            if event and event.get('type') == 'pmessage':
                status = event_snapshot(client, event)
                if status:
                    ws.send(json.dumps({'type': 'library_transfer', **status}))
    finally:
        pubsub.close()
        client.close()

"""Recent worker outcomes, shared by all queue viewers."""
import json
import logging
import time

KEY = 'styler:queue-completed'
FAILED_KEY = 'styler:queue-failed'
SUCCESS_STATES = {'complete', 'prompt_tagged', 'stockpile_complete', 'styles_scan_image_complete',
                  'style_description_complete', 'style_duplicate_scan_complete'}
FAILURE_STATES = {'failed', 'tag_failed', 'tag_abyss', 'stockpile_failed', 'styles_scan_image_failed',
                  'style_description_failed', 'style_duplicate_scan_failed'}
FAILURE_SECONDS = 300


def publish_queue_event(client, channel, event):
    """Retain real worker outcomes; cached image replies never pass through here."""
    if event.get('state') in SUCCESS_STATES | FAILURE_STATES and event.get('job_id'):
        try:
            raw = client.get('styler:worker-running')
            task = json.loads(raw) if isinstance(raw, (str, bytes)) else {}
            if isinstance(task, dict) and task.get('job_id') == event['job_id']:
                completed_at = time.time()
                failed = event['state'] in FAILURE_STATES
                entry = {**task, **event, 'state': 'failed' if failed else 'complete',
                         'percent': event.get('percent') if failed else 100, 'completed_at': completed_at}
                # Stable IDs can be retried. Keep only the latest outcome for each job.
                client.eval('''
                    for i = 1, 2 do
                        for _, value in ipairs(redis.call('ZRANGE', KEYS[i], 0, -1)) do
                            local ok, old = pcall(cjson.decode, value)
                            if ok and old.job_id == ARGV[3] then redis.call('ZREM', KEYS[i], value) end
                        end
                    end
                    redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', ARGV[4])
                    redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', ARGV[5])
                    redis.call('ZADD', KEYS[ARGV[6] == '1' and 2 or 1], ARGV[1], ARGV[2])
                    redis.call('ZREMRANGEBYRANK', KEYS[1], 0, -6)
                    redis.call('EXPIRE', KEYS[1], 31)
                    redis.call('EXPIRE', KEYS[2], 301)
                ''', 2, KEY, FAILED_KEY, completed_at, json.dumps(entry), event['job_id'],
                            completed_at - 30, completed_at - FAILURE_SECONDS, '1' if failed else '0')
                event = {**event, 'queue_completed': not failed, 'queue_failed': failed,
                         'completed_at': completed_at, 'promoted': bool(task.get('promoted'))}
        except Exception:
            # A history failure must never suppress the actual worker event.
            logging.getLogger(__name__).exception('Unable to retain queue outcome')
    return client.publish(channel, json.dumps(event))


def clear_failed_job(client, job_id):
    if not client.exists(FAILED_KEY):
        return
    client.eval('''
        for _, value in ipairs(redis.call('ZRANGE', KEYS[1], 0, -1)) do
            local ok, old = pcall(cjson.decode, value)
            if ok and old.job_id == ARGV[1] then redis.call('ZREM', KEYS[1], value) end
        end
    ''', 1, FAILED_KEY, job_id)


def clear_job_outcome(client, job_id):
    """Remove both retained success and failure outcomes for a retried task."""
    client.eval('''
        for i = 1, 2 do
            for _, value in ipairs(redis.call('ZRANGE', KEYS[i], 0, -1)) do
                local ok, old = pcall(cjson.decode, value)
                if ok and old.job_id == ARGV[1] then redis.call('ZREM', KEYS[i], value) end
            end
        end
    ''', 2, KEY, FAILED_KEY, job_id)


def _recent(client, key, seconds, now):
    entries = []
    for raw in client.zrangebyscore(key, now - seconds, '+inf'):
        try:
            entry = json.loads(raw)
            if isinstance(entry, dict) and isinstance(entry.get('job_id'), str):
                entries.append(entry)
        except (TypeError, ValueError):
            continue
    return entries


def recent_completions(client, now=None):
    now = time.time() if now is None else now
    return _recent(client, KEY, 30, now)[-5:]


def recent_failures(client, now=None):
    now = time.time() if now is None else now
    return [entry for entry in _recent(client, FAILED_KEY, FAILURE_SECONDS, now) if entry.get('state') == 'failed']

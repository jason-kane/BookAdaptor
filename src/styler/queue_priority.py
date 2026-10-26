"""Atomically change queue priority without creating a second copy of the work."""
import json

from styler.idle_maintenance import QUEUE as IDLE_QUEUE


def promote_job(client, job_id, priority_queue, video_queue, tag_queue,
                running_key='styler:worker-running', channel='styler_sample_events', enabled=True, idle_queue=IDLE_QUEUE):
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        raise ValueError('Invalid job ID')
    if not isinstance(enabled, bool):
        raise ValueError('Invalid priority state')
    raw = client.eval('''
        local current = redis.call('GET', KEYS[5])
        if current then
            local ok, task = pcall(cjson.decode, current)
            if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                -- A queued task can start between the click and this operation.
                -- Never requeue or remove its lifecycle highlight once it is running.
                if ARGV[3] == '1' then
                    task.promoted = true
                    redis.call('SET', KEYS[5], cjson.encode(task), 'KEEPTTL')
                    redis.call('PUBLISH', ARGV[2], cjson.encode({state='job_promoted', job_id=ARGV[1]}))
                end
                return cjson.encode({state='running', job=task})
            end
        end
        local selected = nil
        for index = 1, 4 do
            for _, payload in ipairs(redis.call('LRANGE', KEYS[index], 0, -1)) do
                local ok, task = pcall(cjson.decode, payload)
                if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                    if not selected then
                        selected = task
                        if not selected.queue_origin then
                            selected.queue_origin = index == 4 and 'idle' or index == 3 and 'tags' or index == 2 and 'video'
                                or (task.action == 'styler_tag_sample' and 'tags' or 'video')
                        end
                    end
                    redis.call('LREM', KEYS[index], 0, payload)
                end
            end
        end
        if not selected then return nil end
        selected.promoted = ARGV[3] == '1'
        selected.priority = selected.promoted
        selected.priority_removed = not selected.promoted or nil
        local state = selected.promoted and 'job_promoted' or 'job_demoted'
        if selected.promoted then
            redis.call('LPUSH', KEYS[1], cjson.encode(selected))
        else
            local origin = selected.queue_origin
            selected.queue_origin = nil
            if origin == 'idle' or selected.idle_maintenance then
                redis.call('RPUSH', KEYS[4], cjson.encode(selected))
            elseif origin == 'tags' then
                redis.call('RPUSH', KEYS[3], cjson.encode(selected))
            else
                redis.call('LPUSH', KEYS[2], cjson.encode(selected))
            end
        end
        redis.call('PUBLISH', ARGV[2], cjson.encode({state=state, job_id=ARGV[1]}))
        return cjson.encode({state='queued', job=selected})
    ''', 5, priority_queue, video_queue, tag_queue, idle_queue, running_key, job_id, channel, '1' if enabled else '0')
    return json.loads(raw) if raw else None


def remove_queued_job(client, job_id, priority_queue, video_queue, tag_queue,
                      running_key='styler:worker-running', idle_queue=IDLE_QUEUE):
    """Atomically remove a pending task from any worker queue."""
    if not isinstance(job_id, str) or not job_id or len(job_id) > 256:
        raise ValueError('Invalid job ID')
    raw = client.eval('''
        local current = redis.call('GET', KEYS[5])
        if current then
            local ok, task = pcall(cjson.decode, current)
            if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                return cjson.encode({state='running'})
            end
        end
        local selected = nil
        for index = 1, 4 do
            for _, payload in ipairs(redis.call('LRANGE', KEYS[index], 0, -1)) do
                local ok, task = pcall(cjson.decode, payload)
                if ok and type(task) == 'table' and task.job_id == ARGV[1] then
                    if not selected then selected = task end
                    redis.call('LREM', KEYS[index], 0, payload)
                end
            end
        end
        if not selected then return nil end
        return cjson.encode({state='queue_removed', task=selected})
    ''', 5, priority_queue, video_queue, tag_queue, idle_queue, running_key, job_id)
    return json.loads(raw) if raw else None

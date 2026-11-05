"""Discard stale transient progress snapshots when the WebSocket service starts."""

PROGRESS_PATTERNS = ('video_job:*', 'styler_progress:*', 'websocket_*')
MAX_PROGRESS_AGE = 3600
# Existing progress writers renew their expiry to 24 hours on every update.
PROGRESS_TTL = 86400

# Check and expire atomically so a concurrent progress update cannot be removed
# using an earlier TTL. Redis has no creation timestamp for persistent legacy
# keys; their idle time is the available fallback.
_EXPIRE_STALE = '''
local ttl = redis.call('TTL', KEYS[1])
if ttl == -2 then return 0 end
local age
if ttl >= 0 then
    age = tonumber(ARGV[1]) - ttl
else
    local idle = redis.pcall('OBJECT', 'IDLETIME', KEYS[1])
    if type(idle) ~= 'number' then return 0 end
    age = idle
end
if age > tonumber(ARGV[2]) then
    return redis.call('EXPIRE', KEYS[1], 0)
end
return 0
'''


def expire_stale_progress(client):
    """Expire progress last updated over an hour ago; leave task/cache data alone."""
    expired = 0
    for pattern in PROGRESS_PATTERNS:
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor=cursor, match=pattern, count=1000)
            if keys:
                with client.pipeline(transaction=False) as pipe:
                    for key in keys:
                        pipe.eval(_EXPIRE_STALE, 1, key, PROGRESS_TTL, MAX_PROGRESS_AGE)
                    expired += sum(pipe.execute())
            if not cursor:
                break
    return expired

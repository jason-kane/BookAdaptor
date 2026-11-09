"""Redis-cached, media-triggered rough on-disk sizes for library books."""
import json
import subprocess
import time
import uuid

from .storage import identity_key


REFRESH_INTERVAL = 600
REFRESH_LOCK_TTL = 60 * 60
CACHE_PREFIX = 'library_book_size:'
DIRTY_PREFIX = 'library_book_size_dirty:'
REFRESH_PREFIX = 'library_book_size_refresh:'


def _keys(author, title, location):
    identity = identity_key(author, title)
    placed = identity + ':' + location
    return (CACHE_PREFIX + placed, DIRTY_PREFIX + identity, REFRESH_PREFIX + placed)


def mark_dirty(client, author, title):
    """Record that newly created media may have changed a book's disk size."""
    try:
        dirty_key = DIRTY_PREFIX + identity_key(author, title)
        client.incr(dirty_key)
        client.expire(dirty_key, 60 * 60 * 24 * 365)
    except Exception:
        # A cache notification must never fail a successfully created asset.
        return False
    return True


def mark_dirty_for_path(client, path):
    """Find the physical library book containing a successfully created file."""
    from pathlib import Path
    from .storage import location_roots

    try:
        target = Path(path).resolve()
        for root in location_roots().values():
            try:
                relative = target.relative_to(root.resolve())
            except (OSError, ValueError):
                continue
            if len(relative.parts) >= 2:
                return mark_dirty(client, relative.parts[0], relative.parts[1])
    except (OSError, RuntimeError):
        pass
    return False


def cached_sizes(client, books, roots, *, now=None):
    """Return cached du-style sizes; batch due measurements by page request."""
    from pathlib import Path

    now = time.time() if now is None else now
    results = {}
    pending = []
    locks = []
    for author, title, location in books:
        result_key = (author, title, location)
        cache_key, dirty_key, refresh_key = _keys(author, title, location)
        try:
            raw = client.get(cache_key)
            dirty = int(client.get(dirty_key) or 0)
            cached = json.loads(raw) if raw else None
            last_update = float(client.get(refresh_key) or 0)
        except (TypeError, ValueError):
            cached, dirty, last_update = None, 0, 0
        except Exception:
            results[result_key] = '—'
            continue

        missing = not isinstance(cached, dict) or not isinstance(cached.get('size'), str)
        due = now - last_update >= REFRESH_INTERVAL and (missing or (
            dirty > int(cached.get('generation', 0))
        ))
        if not due:
            results[result_key] = cached['size'] if not missing else '—'
            continue
        path = Path(roots[location]) / author / title
        lock_key, token = refresh_key + ':lock', uuid.uuid4().hex
        try:
            acquired = client.set(lock_key, token, nx=True, ex=REFRESH_LOCK_TTL)
        except Exception:
            acquired = False
        if not acquired:
            results[result_key] = cached['size'] if not missing else '—'
            continue
        locks.append((lock_key, token))
        pending.append({
            'key': result_key, 'path': path, 'cache_key': cache_key,
            'refresh_key': refresh_key, 'generation': dirty,
        })

    if pending:
        try:
            result = subprocess.run(
                ['du', '-sh', '--null', '--', *(str(item['path']) for item in pending)],
                capture_output=True, check=False,
            )
            measured = {}
            for row in result.stdout.split(b'\0'):
                if b'\t' not in row:
                    continue
                size, path = row.split(b'\t', 1)
                measured[path.decode(errors='replace')] = size.decode(errors='replace').strip()
            for item in pending:
                size = measured.get(str(item['path']))
                if not size:
                    results[item['key']] = '—'
                    continue
                client.set(item['cache_key'], json.dumps({
                    'size': size, 'generation': item['generation'],
                }))
                client.set(item['refresh_key'], str(now), ex=60 * 60 * 24 * 365)
                results[item['key']] = size
        except Exception:
            for item in pending:
                results.setdefault(item['key'], '—')
        finally:
            for lock_key, token in locks:
                try:
                    client.eval(
                        "if redis.call('GET', KEYS[1]) == ARGV[1] then return redis.call('DEL', KEYS[1]) else return 0 end",
                        1, lock_key, token,
                    )
                except Exception:
                    pass
    return results

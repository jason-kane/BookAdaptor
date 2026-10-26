"""Require engine agreement, allowing one explicit empty-result abstention."""
import hashlib
import json
from pathlib import Path
import re
import random
from collections import Counter

ENGINES = ('zit', 'krea2', 'qwen')


def style_key(tag):
    return re.sub(r'[^\-a-z0-9_]', '_', tag.strip().lower())


def prompt_version(client, tag):
    value = client.hget(style_key(tag), 'prompt')
    if not isinstance(value, (str, bytes)):
        value = '{prompt}'
    if isinstance(value, str):
        value = value.encode()
    return hashlib.sha256(value).hexdigest()


def agreed_tags(records, hashes, version):
    from styler.image_tags import ANALYSIS_VERSION
    sets = []
    for engine in ENGINES:
        record = records.get(engine)
        if (not record or not hashes.get(engine) or record.get('sha256') != hashes[engine]
                or record.get('analysis_version') != ANALYSIS_VERSION):
            return []
        if record['tags']:
            sets.append(set(record['tags']))
    if len(sets) < 2:
        return []
    unanimous = sorted(set.intersection(*sets))
    if len(unanimous) >= 4:
        return unanimous[:5]
    votes = Counter(tag for tags in sets for tag in tags)
    candidates = sorted(tag for tag, count in votes.items() if count >= 2 and tag not in unanimous)
    # Random per image set, but stable when unchanged evidence is reconciled
    # again, so refreshing does not replace the chosen memberships.
    rng = random.Random(version + ':' + ':'.join(hashes[engine] for engine in ENGINES))
    count = min(len(candidates), rng.randint(4, 5) - len(unanimous))
    return sorted(unanimous + rng.sample(candidates, count))


def saved_tags(client, tag):
    record = client.get('styler:consensus:' + style_key(tag))
    tags = json.loads(record).get('tags', []) if isinstance(record, (str, bytes)) else []
    suppressed = client.smembers('styler:tag-suppressed:' + style_key(tag))
    return [value for value in tags if value not in suppressed]


def reconcile(client, tag, library_dir):
    from styler.image_tags import fingerprint
    from styler.jobs import BASE_STYLE_PROMPTS, sample_task
    key = style_key(tag)
    if client.type(key) not in ('hash', b'hash'):
        return dict(tags=[], added=[], removed=[], counts=[])
    samples = [(base_style_prompt, engine,
                sample_task(dict(style_tag=tag, engine=engine,
                                 base_style_prompt=base_style_prompt)))
               for base_style_prompt in BASE_STYLE_PROMPTS for engine in ENGINES]
    record_keys = ['styler:image-tags:' + task['job_id'] for _, _, task in samples]
    raw = client.mget(record_keys)
    if not isinstance(raw, (list, tuple)):
        return dict(tags=[], added=[], removed=[], counts=[])
    raw = [value.decode() if isinstance(value, bytes) else value for value in raw]
    records = {(base_style_prompt, engine): json.loads(value)
               for (base_style_prompt, engine, _), value in zip(samples, raw) if value}
    hashes = {}
    for base_style_prompt, engine, _ in samples:
        folder = Path(library_dir) / 'style_exploration'
        if base_style_prompt == 'kid_friendly':
            folder /= base_style_prompt
        path = folder / f'{tag}_{engine}.png'
        if path.is_file():
            hashes[(base_style_prompt, engine)] = fingerprint(path)
    source_prompt = client.hget(key, 'prompt')
    if isinstance(source_prompt, bytes):
        source_prompt = source_prompt.decode()
    if not isinstance(source_prompt, str):
        source_prompt = '{prompt}'
    version = hashlib.sha256(source_prompt.encode()).hexdigest()
    proposed = []
    for base_style_prompt in BASE_STYLE_PROMPTS:
        base_records = {engine: records.get((base_style_prompt, engine)) for engine in ENGINES}
        base_hashes = {engine: hashes.get((base_style_prompt, engine)) for engine in ENGINES}
        proposed.extend(agreed_tags(base_records, base_hashes, version))
    proposed = sorted(set(proposed))
    # Commit membership changes atomically, only if none of the evidence was
    # replaced while consensus was being calculated. Never own manual tags.
    result = client.eval('''
        local style_type = redis.call('TYPE', KEYS[12])
        if type(style_type) == 'table' then style_type = style_type.ok end
        if style_type ~= 'hash' then return false end
        if (redis.call('HGET', KEYS[12], 'prompt') or '{prompt}') ~= ARGV[4] then return false end
        local expected = cjson.decode(ARGV[2])
        for i = 1, 6 do
            if (redis.call('GET', KEYS[5+i]) or '') ~= expected[i] then return false end
        end
        local was_lost = redis.call('SISMEMBER', 'LOST/tags', ARGV[3]) == 1
        local tags = cjson.decode(ARGV[1])
        local wanted = {}
        local added, removed, approved, counts = {}, {}, {}, {}
        for _, tag in ipairs(tags) do
            if redis.call('SISMEMBER', KEYS[4], tag) == 0 then
                wanted[tag] = true
                table.insert(approved, tag)
            end
        end
        for _, tag in ipairs(redis.call('SMEMBERS', KEYS[2])) do
            if not wanted[tag] then
                if redis.call('SISMEMBER', KEYS[3], tag) == 0 then
                    redis.call('SREM', KEYS[1], tag)
                    redis.call('SREM', tag .. '/tags', ARGV[3])
                    table.insert(removed, tag)
                end
                redis.call('SREM', KEYS[2], tag)
            end
        end
        for _, tag in ipairs(approved) do
            if redis.call('SISMEMBER', KEYS[1], tag) == 0 then
                redis.call('SADD', KEYS[1], tag)
                redis.call('SADD', tag .. '/tags', ARGV[3])
                redis.call('SADD', 'tags', tag)
                redis.call('SADD', KEYS[2], tag)
                table.insert(added, tag)
            end
        end
        for _, tag in ipairs(added) do
            table.insert(counts, {tag=tag, count=redis.call('SCARD', tag .. '/tags')})
        end
        for _, tag in ipairs(removed) do
            local count = redis.call('SCARD', tag .. '/tags')
            if count == 0 then
                redis.call('SREM', 'tags', tag)
                redis.call('LREM', 'categories', 0, tag)
            end
            table.insert(counts, {tag=tag, count=count})
        end
        if redis.call('SCARD', KEYS[1]) == 0 then
            redis.call('SADD', 'LOST/tags', ARGV[3])
            redis.call('ZADD', 'LOST/index', 0, ARGV[3])
            redis.call('SADD', 'tags', 'LOST')
        else
            redis.call('SREM', 'LOST/tags', ARGV[3])
            redis.call('ZREM', 'LOST/index', ARGV[3])
            if redis.call('SCARD', 'LOST/tags') == 0 then
                redis.call('SREM', 'tags', 'LOST')
                redis.call('LREM', 'categories', 0, 'LOST')
            end
        end
        local result = cjson.encode({tags=approved, added=added, removed=removed, counts=counts,
            was_lost=was_lost, lost=redis.call('SCARD', KEYS[1]) == 0,
            lost_count=redis.call('SCARD', 'LOST/tags')})
        redis.call('SET', KEYS[5], result)
        return result
    ''', 12, key + '/tags', 'styler:auto-tags:' + key, 'styler:manual-tags:' + key,
        'styler:tag-suppressed:' + key, 'styler:consensus:' + key, *record_keys, key,
        json.dumps(proposed), json.dumps([value or '' for value in raw]), key, source_prompt)
    if not isinstance(result, (str, bytes)):
        return dict(tags=[], added=[], removed=[], counts=[])
    result = json.loads(result)
    # Redis Lua encodes empty tables as objects.
    agreement = {field: list(result.get(field) or []) for field in ('tags', 'added', 'removed', 'counts')}
    if 'lost' in result:
        from styler.image_tags import EVENTS
        # Every viewer needs membership updates, including for unrequested idle jobs.
        client.publish(EVENTS, json.dumps(dict(
            state='lost_resolved' if result.get('was_lost') and not result['lost'] else 'style_tags_reconciled',
            style_tag=tag, lost=result['lost'], lost_count=result['lost_count'], **agreement)))
    return agreement

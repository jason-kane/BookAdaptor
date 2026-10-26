"""Build and cache a small topic taxonomy for Styler's valid image tags."""
import hashlib
import json
import logging

log = logging.getLogger(__name__)

TOPIC_KEY = 'styler:topic-taxonomy:v1'
TREE_KEY = 'styler:topic-tree:v1'
BUILD_LAST_SUCCESS = 'styler:topic-tree:last-success'
BUILD_LAST_ATTEMPT = 'styler:topic-tree:last-attempt'
BUILD_PENDING = 'styler:topic-tree:pending'
BUILD_INTERVAL = 7 * 24 * 60 * 60
ROUNDS = 3
MEMBERSHIP_BATCH_SIZE = 40


def _decode(value):
    return value.decode() if isinstance(value, bytes) else value


def valid_tags(client):
    """Return the existing tag vocabulary that has actual styles."""
    tags = {_decode(tag) for tag in client.smembers('tags')}
    tags = sorted((tag for tag in tags if tag), key=str.casefold)
    with client.pipeline(transaction=False) as pipe:
        for tag in tags:
            pipe.scard(f'{tag}/tags')
        counts = pipe.execute()
    return [tag for tag, count in zip(tags, counts) if count]


def _digest(tags):
    return hashlib.sha256(json.dumps(tags, ensure_ascii=False).encode()).hexdigest()


def _json(text):
    from styler.image_tags import tag_payload
    payload = tag_payload(text)
    if not isinstance(payload, dict):
        raise ValueError('Expected a JSON object')
    return payload


def _prompt_request(client, prompt, system_prompt, *, force=False, level='taxonomy',
                    parent=None, prompt_fn=None):
    if prompt_fn:
        return prompt_fn(prompt, system_prompt=system_prompt, force=force)
    from .topic_jobs import queued_prompt
    return queued_prompt(client, prompt, system_prompt, force=force,
                         topic_level=level, topic_parent=parent)


def build_topics(client, *, force=False, prompt_fn=None):
    tags = valid_tags(client)
    digest = _digest(tags)
    cached = client.get(TOPIC_KEY)
    if cached and not force:
        try:
            taxonomy = json.loads(_decode(cached))
            if taxonomy.get('tag_digest') == digest:
                return taxonomy
        except (ValueError, TypeError, AttributeError):
            pass

    data = json.dumps({'valid_tags': tags}, ensure_ascii=False, separators=(',', ':'))
    prompt = (
        'Propose a concise taxonomy for these Styler tags. Group related tags into topics '
        'at a consistent, broad level of abstraction, like the existing category level. '
        'Use roughly 8 to 16 topics. Do not list, repeat, assign, or include any tags in the '
        'response; tag membership will be evaluated separately. Return only 8 to 16 short topic '
        'names in this exact compact JSON shape, with no other keys: {"topics":["Topic name"]}.\n' + data)
    result = None
    for round_number in range(ROUNDS):
        raw = _prompt_request(client, prompt,
            'You organize tag vocabularies into clear, consistent topics. Return only JSON.',
            force=force, level='parent-topics', prompt_fn=prompt_fn)
        parsed = _json(raw)
        topics = parsed.get('topics')
        if not isinstance(topics, list) or not topics:
            raise ValueError('Topic proposal did not contain topics')
        clean = []
        for topic in topics:
            if isinstance(topic, str) and topic.strip():
                clean.append({'name': topic.strip(), 'description': ''})
            elif isinstance(topic, dict) and isinstance(topic.get('name'), str) and topic['name'].strip():
                clean.append({'name': topic['name'].strip(),
                              'description': str(topic.get('description', '')).strip()})
        if not clean:
            raise ValueError('Topic proposal had no valid topic names')
        result = clean
        if round_number < ROUNDS - 1:
            prompt = (
                'Refine this draft taxonomy for the same tag vocabulary. Merge overlapping topics, '
                'split topics that combine unrelated concepts, and keep topic names at one broad '
                'level of abstraction. This is a taxonomy proposal only: do not list, repeat, assign, '
                'or include tags in the response. Return only 8 to 16 short topic names in the '
                'compact JSON shape {"topics":["Topic name"]}, with no other keys.\n'
                + json.dumps({'valid_tags': tags, 'draft_topics': [item['name'] for item in result]}, ensure_ascii=False,
                             separators=(',', ':')))
    taxonomy = {'version': 1, 'tag_digest': digest, 'topics': result}
    client.set(TOPIC_KEY, json.dumps(taxonomy, ensure_ascii=False))
    return taxonomy


def topic_members(client, name, *, force=False, prompt_fn=None, taxonomy=None):
    taxonomy = taxonomy or build_topics(client)
    topic = next((item for item in taxonomy['topics'] if item['name'] == name), None)
    if topic is None:
        raise KeyError(name)
    return _members_for_scope(client, topic, valid_tags(client), f'topic:{name}', force=force,
                              prompt_fn=prompt_fn)


def _members_for_scope(client, topic, tags, scope, *, force=False, prompt_fn=None):
    digest = _digest(tags)
    topic_id = hashlib.sha256(json.dumps([scope, topic], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    key = f'styler:topic-members:v2:{topic_id}'
    cached = client.get(key)
    if cached and not force:
        try:
            data = json.loads(_decode(cached))
            if data.get('tag_digest') == digest:
                return data['tags']
        except (ValueError, TypeError, KeyError, AttributeError):
            pass
    members = []
    for offset in range(0, len(tags), MEMBERSHIP_BATCH_SIZE):
        batch = tags[offset:offset + MEMBERSHIP_BATCH_SIZE]
        prompt = (
            'From the supplied valid_tags, identify exactly which entries are members of the '
            'specified topic. This is one-sided membership: do not create, rename, or broaden tags. '
            f'There are exactly {len(batch)} entries. Return only zero-based integer indices in the '
            f'range 0 through {len(batch) - 1}; never return an index outside that range. '
            'Do not return tag strings. Use compact JSON: {"members":[0,4]}.\n'
            + json.dumps({'topic': topic, 'valid_tags': batch}, ensure_ascii=False,
                         separators=(',', ':')))
        is_subtopic = scope.startswith('subtopic:')
        selected = None
        for attempt in range(2):
            request_prompt = prompt if not attempt else (
                prompt + f'\nYour previous indices were invalid. The only valid indices are '
                f'integers from 0 to {len(batch) - 1}. Return a valid JSON object now.')
            try:
                parsed = _json(_prompt_request(client, request_prompt,
                    'You classify existing tags. Return only JSON.', force=force or bool(attempt),
                    level='subtopic-membership' if is_subtopic else 'parent-membership',
                    parent=topic.get('parent') or topic['name'], prompt_fn=prompt_fn))
                choices = parsed.get('members')
                if (isinstance(choices, list)
                        and all(isinstance(index, int) and not isinstance(index, bool)
                                and 0 <= index < len(batch) for index in choices)):
                    selected = choices
                    break
            except ValueError:
                pass
        if selected is None:
            log.warning('Invalid membership response for %s in %s; assigning batch to fallback',
                        topic['name'], scope)
            selected = []
        members.extend(batch[index] for index in selected)
    members = list(dict.fromkeys(members))
    client.set(key, json.dumps({'tag_digest': digest, 'tags': members}, ensure_ascii=False))
    return members


def _child_parent_key(child_name):
    child_id = hashlib.sha256(child_name.encode()).hexdigest()
    return f'styler:topic-parents:v1:{child_id}'


def _cache_subtaxonomy(client, redis_key, parent_name, data):
    """Persist parent→children and child→parents links from the same taxonomy."""
    previous = client.get(redis_key)
    if previous:
        try:
            previous_topics = json.loads(_decode(previous)).get('topics', [])
            current_names = {item['name'] for item in data['topics']}
            for old in previous_topics:
                if isinstance(old, dict) and old.get('name') not in current_names:
                    client.srem(_child_parent_key(old['name']), parent_name)
        except (ValueError, TypeError, AttributeError):
            pass
    for child in data['topics']:
        child['parent'] = parent_name
        client.sadd(_child_parent_key(child['name']), parent_name)
    client.set(redis_key, json.dumps(data, ensure_ascii=False))
    return data


def parents_for_subtopic(client, subtopic_name):
    """Read the reverse index for a subtopic label."""
    return sorted((_decode(value) for value in client.smembers(_child_parent_key(subtopic_name))),
                  key=str.casefold)


def build_subtopics(client, parent_name, *, force=False, prompt_fn=None,
                    parent_tags=None, taxonomy=None):
    """Group only the raw tags already classified under the selected parent."""
    taxonomy = taxonomy or build_topics(client)
    parent = next((item for item in taxonomy['topics'] if item['name'] == parent_name), None)
    if parent is None:
        raise KeyError(parent_name)
    if parent_tags is None:
        parent_tags = topic_members(client, parent_name, force=force, prompt_fn=prompt_fn,
                                    taxonomy=taxonomy)
    digest = _digest(parent_tags)
    parent_key = hashlib.sha256(parent_name.encode()).hexdigest()
    redis_key = f'styler:topic-subtaxonomy:v1:{parent_key}'
    cached = client.get(redis_key)
    if cached and not force:
        try:
            data = json.loads(_decode(cached))
            if data.get('tag_digest') == digest:
                return _cache_subtaxonomy(client, redis_key, parent_name, data)
        except (ValueError, TypeError, AttributeError):
            pass
    if not parent_tags:
        result = {'version': 1, 'tag_digest': digest, 'topics': []}
        return _cache_subtaxonomy(client, redis_key, parent_name, result)

    prompt = (
        f'Group these tags from the parent topic {parent_name!r} into useful subtopics. '
        'Create a second abstraction level, with a similar number and scale of groups as the '
        'parent taxonomy where the subset allows. Use only this subset. Do not repeat or assign '
        'tags in your response; exact membership is a separate step. Return only compact JSON '
        '{"topics":["Subtopic name"]}, with 4 to 12 short names.\n'
        + json.dumps({'valid_tags': parent_tags}, ensure_ascii=False, separators=(',', ':')))
    result = None
    for round_number in range(ROUNDS):
        parsed = _json(_prompt_request(client, prompt,
            'You group a subset of existing tags into a consistent second taxonomy level. Return only JSON.',
            force=force, level='subtopics', parent=parent_name, prompt_fn=prompt_fn))
        proposed = parsed.get('topics')
        if not isinstance(proposed, list) or not proposed:
            raise ValueError('Subtopic proposal did not contain topics')
        clean = []
        for item in proposed:
            if isinstance(item, str) and item.strip():
                clean.append({'name': item.strip(), 'description': ''})
            elif isinstance(item, dict) and isinstance(item.get('name'), str) and item['name'].strip():
                clean.append({'name': item['name'].strip(), 'description': str(item.get('description', '')).strip()})
        if not clean:
            raise ValueError('Subtopic proposal had no valid names')
        result = clean
        if round_number < ROUNDS - 1:
            prompt = (
                f'Refine these subtopics for the tags under parent topic {parent_name!r}. '
                'Merge overlaps, split unrelated groups, and keep this second level coherent. '
                'Do not repeat or assign tags. Return only compact JSON '
                '{"topics":["Subtopic name"]}, with 4 to 12 short names.\n'
                + json.dumps({'valid_tags': parent_tags,
                              'draft_topics': [item['name'] for item in result]},
                             ensure_ascii=False, separators=(',', ':')))
    data = {'version': 1, 'tag_digest': digest, 'topics': result}
    return _cache_subtaxonomy(client, redis_key, parent_name, data)


def subtopic_members(client, parent_name, subtopic_name, *, force=False):
    taxonomy = build_subtopics(client, parent_name)
    subtopic = next((item for item in taxonomy['topics'] if item['name'] == subtopic_name), None)
    if subtopic is None:
        raise KeyError(subtopic_name)
    parent_tags = topic_members(client, parent_name)
    return _members_for_scope(client, {'name': subtopic_name, 'description': subtopic.get('description', ''),
                                       'parent': parent_name}, parent_tags,
                              f'subtopic:{parent_name}:{subtopic_name}', force=force)


def _other_name(base, existing):
    names = {item['name'].casefold() for item in existing}
    candidate = base
    suffix = 2
    while candidate.casefold() in names:
        candidate = f'{base} {suffix}'
        suffix += 1
    return candidate


def build_full_tree(client, *, force=False, prompt_fn=None):
    """Build a complete, non-overlapping parent/category/raw-tag tree."""
    tags = valid_tags(client)
    digest = _digest(tags)
    cached = client.get(TREE_KEY)
    if cached and not force:
        try:
            tree = json.loads(_decode(cached))
            if tree.get('tag_digest') == digest and tree.get('tag_count') == len(tags):
                return tree
        except (ValueError, TypeError, AttributeError):
            pass

    root = build_topics(client, force=force, prompt_fn=prompt_fn)
    available = set(tags)
    complete_topics = []
    seen_names = set()
    for parent in root['topics']:
        name = parent['name'].strip()
        if not name or name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())
        members = topic_members(client, name, force=force, prompt_fn=prompt_fn, taxonomy=root)
        parent_tags = [tag for tag in tags if tag in available and tag in set(members)]
        if not parent_tags:
            continue
        available.difference_update(parent_tags)
        complete_topics.append(_build_parent_tree(
            client, {'name': name, 'description': parent.get('description', '')},
            parent_tags, root, force=force, prompt_fn=prompt_fn))

    if available:
        fallback_name = _other_name('Other tags', complete_topics)
        complete_topics.append(_build_parent_tree(
            client, {'name': fallback_name, 'description': 'Tags not classified by the topic groups.'},
            [tag for tag in tags if tag in available], root, force=force, prompt_fn=prompt_fn,
            fallback_only=True))

    accounted = [tag for parent in complete_topics for category in parent['categories']
                 for tag in category['tags']]
    if len(accounted) != len(tags) or set(accounted) != set(tags) or len(set(accounted)) != len(accounted):
        raise ValueError('The taxonomy tree must assign every valid tag exactly once')
    tree = {'version': 1, 'tag_digest': digest, 'built_at': __import__('time').time(),
            'tag_count': len(tags), 'topics': complete_topics}
    client.set(TREE_KEY, json.dumps(tree, ensure_ascii=False))
    return tree


def _build_parent_tree(client, parent, parent_tags, root, *, force, prompt_fn,
                       fallback_only=False):
    if fallback_only:
        categories = [{'name': 'Uncategorized', 'description': '', 'parent': parent['name'],
                       'tags': list(parent_tags)}]
        taxonomy = {'version': 1, 'tag_digest': _digest(parent_tags),
                    'topics': [{'name': 'Uncategorized', 'description': ''}]}
        parent_key = hashlib.sha256(parent['name'].encode()).hexdigest()
        _cache_subtaxonomy(client, f'styler:topic-subtaxonomy:v1:{parent_key}',
                           parent['name'], taxonomy)
        return {**parent, 'categories': categories}
    else:
        taxonomy = build_subtopics(client, parent['name'], force=force, prompt_fn=prompt_fn,
                                   parent_tags=parent_tags, taxonomy=root)
        category_names = taxonomy['topics']

    remaining = set(parent_tags)
    categories = []
    seen_names = set()
    for category in category_names:
        name = category['name'].strip()
        if not name or name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())
        membership_topic = {'name': name, 'description': category.get('description', ''),
                            'parent': parent['name']}
        members = _members_for_scope(
            client, membership_topic, parent_tags,
            f'subtopic:{parent["name"]}:{name}', force=force, prompt_fn=prompt_fn)
        selected = [tag for tag in parent_tags if tag in remaining and tag in set(members)]
        if selected:
            remaining.difference_update(selected)
            categories.append({'name': name, 'description': category.get('description', ''),
                               'parent': parent['name'], 'tags': selected})

    if remaining:
        fallback_name = _other_name('Other', categories)
        categories.append({'name': fallback_name, 'description': 'Tags not assigned to another category.',
                           'parent': parent['name'],
                           'tags': [tag for tag in parent_tags if tag in remaining]})

    taxonomy['topics'] = [{'name': category['name'], 'description': category['description']}
                         for category in categories]
    parent_key = hashlib.sha256(parent['name'].encode()).hexdigest()
    _cache_subtaxonomy(client, f'styler:topic-subtaxonomy:v1:{parent_key}', parent['name'], taxonomy)
    return {**parent, 'categories': categories}


def cached_tree(client):
    raw = client.get(TREE_KEY)
    if not raw:
        return None
    try:
        tree = json.loads(_decode(raw))
        return tree if isinstance(tree, dict) and isinstance(tree.get('topics'), list) else None
    except (ValueError, TypeError):
        return None

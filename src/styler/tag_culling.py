"""Remove a tag and its links from sample metadata and Styler indexes."""
import hashlib
import json
from pathlib import Path
import tempfile


def _normalized(value):
    return ' '.join(value.casefold().split())


def _remove_from_file(path, tag):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False

    changed = _remove_from_record(data, tag)
    if not changed:
        return False
    _update_tag_digest(data)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, prefix=path.name,
                                         suffix='.tmp', delete=False) as output:
            temporary = Path(output.name)
            output.write(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def _remove_from_record(data, tag):
    matches = data.get('tag_matches', [])
    removed_sources = {
        item.get('source') for item in matches
        if isinstance(item, dict) and isinstance(item.get('source'), str)
        and isinstance(item.get('tag'), str) and _normalized(item['tag']) == tag
    }
    changed = False
    for field in ('tags', 'raw_tags'):
        values = data.get(field)
        if isinstance(values, list):
            kept = [value for value in values if not (
                isinstance(value, str) and (
                    _normalized(value) == tag or (field == 'raw_tags' and value in removed_sources)
                ))]
            if kept != values:
                data[field] = kept
                changed = True
    if isinstance(matches, list):
        kept = [item for item in matches if not (
            isinstance(item, dict) and isinstance(item.get('tag'), str)
            and _normalized(item['tag']) == tag)]
        if kept != matches:
            data['tag_matches'] = kept
            changed = True
    return changed


def _update_tag_digest(data):
    if isinstance(data.get('tag_matching_tags_sha256'), str):
        data['tag_matching_tags_sha256'] = hashlib.sha256(
            json.dumps(data.get('tags', []), ensure_ascii=False).encode()).hexdigest()


def delete_tag(client, library_dir, tag):
    """Cull a canonical tag from all sample records and style memberships."""
    wanted = _normalized(tag)
    root = Path(library_dir)
    changed_files = 0
    for folder in (root / 'style_exploration', root / 'stockpile'):
        if not folder.is_dir():
            continue
        for sidecar in folder.rglob('*.json'):
            if _remove_from_file(sidecar, wanted):
                changed_files += 1

    image_names = client.smembers('styler:image-tag-names')
    image_names = {value.decode() if isinstance(value, bytes) else value for value in image_names}
    image_names = {value for value in image_names if _normalized(value) == wanted}
    image_names.add(tag)
    member_ids = set()
    for image_name in image_names:
        member_ids.update(client.smembers('styler:image-tag:' + image_name))
    for member_id in member_ids:
        member_id = member_id.decode() if isinstance(member_id, bytes) else member_id
        key = 'styler:image-tags:' + member_id
        raw = client.get(key)
        if not isinstance(raw, (str, bytes)):
            continue
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(record, dict) and isinstance(record.get('tags'), list):
            if _remove_from_record(record, wanted):
                _update_tag_digest(record)
            client.set(key, json.dumps(record))
    for image_name in image_names:
        client.delete('styler:image-tag:' + image_name)
        client.srem('styler:image-tag-names', image_name)

    style_names = client.smembers('tags')
    style_names = {value.decode() if isinstance(value, bytes) else value for value in style_names}
    style_names = {value for value in style_names if _normalized(value) == wanted}
    style_names.add(tag)
    affected_styles = set()
    for style_name in style_names:
        style_set = style_name + '/tags'
        affected_styles.update(client.smembers(style_set))
    for style in affected_styles:
        style = style.decode() if isinstance(style, bytes) else style
        for style_name in style_names:
            client.srem(style + '/tags', style_name)
            client.srem('styler:auto-tags:' + style, style_name)
            client.srem('styler:manual-tags:' + style, style_name)
            client.sadd('styler:tag-suppressed:' + style, style_name)
        if client.type(style) not in ('hash', b'hash'):
            client.srem('LOST/tags', style)
            client.zrem('LOST/index', style)
            continue
        if client.scard(style + '/tags') == 0:
            client.sadd('LOST/tags', style)
            client.zadd('LOST/index', {style: 0})
            client.sadd('tags', 'LOST')
    for style_name in style_names:
        client.delete(style_name + '/tags')
        client.srem('tags', style_name)
        client.lrem('categories', 0, style_name)
    if client.scard('LOST/tags') == 0:
        client.srem('tags', 'LOST')
        client.lrem('categories', 0, 'LOST')
    return dict(tag=tag, samples_updated=changed_files, styles_updated=len(affected_styles))

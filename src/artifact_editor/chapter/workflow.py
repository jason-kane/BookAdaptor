"""Persist chapter preparation milestones independently of optional assets."""
import json
import os
from pathlib import Path
import uuid

from filelock import FileLock

STEPS = (
    ('chapter_import', 'import chapter'),
    ('chapter_missing_characters', 'add missing characters'),
    ('chapter_assign_characters', 'assign characters'),
    ('chapter_character_appearances', 'process appearance changes for all characters'),
    ('chapter_missing_audio', 'add missing audio'),
    ('chapter_place_image_slots', 'place image slots'),
    ('chapter_missing_props', 'add missing props'),
    ('chapter_missing_metadata', 'add missing metadata'),
    ('chapter_missing_images', 'add missing images'),
    ('chapter_missing_animations', 'add missing animation'),
    ('textoviva_rerender', 'generate video'),
)
ACTIONS = [action for action, _ in STEPS]


def _path(chapter):
    import const
    return Path(const.LIBRARY_DIR) / chapter.chapterdir / chapter.language / 'workflow.json'


def completed_steps(chapter):
    path = _path(chapter)
    if path.exists():
        return set(json.loads(path.read_text()).get('completed', []))
    # Legacy chapters have no milestones. Parsed phrases establish import,
    # but an empty character/prop catalog cannot establish extraction success.
    return {'chapter_import'} if chapter.get_xml().find('phrase') else set()


def prerequisite_message(chapter, action, *, completed=None):
    if action == 'chapter_import':
        return None
    if action == 'chapter_add_all':
        required = 'chapter_import'
    elif action == 'textoviva_time_align':
        required = 'chapter_missing_audio'
    elif action in ACTIONS:
        required = ACTIONS[ACTIONS.index(action) - 1]
    else:
        return None
    done = completed_steps(chapter) if completed is None else completed
    # Explain the earliest missing step, including when an earlier rerun
    # invalidated several downstream milestones.
    for candidate, label in STEPS[:ACTIONS.index(required) + 1]:
        if candidate not in done:
            return f'Must {label} first'
    return None


def _save(path, completed):
    temporary = path.with_name(f'{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'completed': [a for a in ACTIONS if a in completed]}))
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def begin_step(chapter, action):
    if action not in ACTIONS and action != 'chapter_add_all':
        return
    path = _path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock'):
        reason = prerequisite_message(chapter, action)
        if reason:
            raise ValueError(reason)
        done = completed_steps(chapter)
        index = ACTIONS.index(action) if action in ACTIONS else 1
        done.difference_update(ACTIONS[index:])
        _save(path, done)


def complete_step(chapter, action):
    if action not in ACTIONS:
        return
    path = _path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + '.lock'):
        done = completed_steps(chapter)
        done.add(action)
        _save(path, done)

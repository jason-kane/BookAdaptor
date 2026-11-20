"""Apply positive ratings with Git three-way merging; never resolve overlapping edits."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile

from flask import current_app


class Conflict(Exception):
    pass


def validate_target(target):
    if not isinstance(target, dict) or target.get('kind') not in ('goal', 'file'):
        raise ValueError('Unsupported application target')
    if 'before' not in target or 'after' not in target:
        raise ValueError('Target requires baseline and proposed value')
    if target['kind'] == 'goal':
        if not isinstance(target.get('id'), str) or not target['id'].strip():
            raise ValueError('Goal target requires an ID')
        for name in ('before', 'after'):
            value = target[name]
            if value is None and name == 'before':
                continue
            if not isinstance(value, dict) or set(value) != {'title', 'evidence'}:
                raise ValueError('Goal snapshots require title and evidence')
            if not all(isinstance(item, str) for item in value.values()) or not value['title'].strip():
                raise ValueError('Invalid goal snapshot')
    else:
        path = target.get('path')
        if not isinstance(path, str) or not path or Path(path).is_absolute() or any(
            part in ('..', '.git') for part in Path(path).parts
        ) or path in ('.', ''):
            raise ValueError('File target must be a relative workspace path outside .git')
        if target['before'] is not None and not isinstance(target['before'], str):
            raise ValueError('File baseline must be text or null for a new file')
        if not isinstance(target['after'], str):
            raise ValueError('Proposed file content must be text')
        if Path(path).suffix in ('.py', '.pyi'):
            from .validation import syntax_check
            syntax_check(path, target['after'])


def file_path(target):
    root = current_app.config.get('POLISH_WORKSPACE')
    if not root:
        raise ValueError('No writable workspace configured; change was not applied.')
    root = Path(root).resolve(strict=True)
    path = root / target['path']
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError('Symlink targets are not supported; change was not applied.')
    if not path.resolve().is_relative_to(root):
        raise ValueError('Target is outside the workspace; change was not applied.')
    return path


def merge_text(base, current, proposed):
    """Use Git's merge engine on snapshots, without touching a repository or target."""
    if current == proposed or proposed == base:
        return current
    if current == base:
        return proposed
    if base is None or current is None:
        raise Conflict('Merge conflict: target was independently created or deleted. Nothing was overwritten.')
    with tempfile.TemporaryDirectory(prefix='polish-merge-') as directory:
        paths = [Path(directory) / name for name in ('current', 'base', 'proposed')]
        for path, content in zip(paths, (current, base, proposed)):
            path.write_bytes(content.encode('utf-8'))
        try:
            result = subprocess.run(
                ['git', 'merge-file', '--stdout', '--diff3',
                 '-L', 'Current', '-L', 'Reviewed baseline', '-L', 'Proposal',
                 *map(str, paths)], cwd=directory, capture_output=True, timeout=10,
            )
        except subprocess.TimeoutExpired as error:
            raise ValueError('Git merge timed out; change was not applied.') from error
    if 1 <= result.returncode <= 127:
        # Conflict-marker output stays in memory and is never written to the target.
        raise Conflict('Merge conflict: overlapping edits require review. Nothing was overwritten.')
    if result.returncode != 0:
        raise ValueError('Git could not merge these snapshots; change was not applied.')
    return result.stdout.decode('utf-8')


def read_current(connection, target):
    if target['kind'] == 'goal':
        row = connection.execute('SELECT title, evidence FROM goals WHERE id = ?', (target['id'],)).fetchone()
        return dict(row) if row else None
    path = file_path(target)
    return path.read_bytes().decode('utf-8') if path.exists() else None


def inspect_target(connection, entry):
    if not entry['target_json']:
        raise ValueError('No application target recorded; change was not applied.')
    target = json.loads(entry['target_json'])
    validate_target(target)
    current = read_current(connection, target)
    if current == target['before'] or current == target['after']:
        merged = target['after']
    elif target['kind'] == 'goal':
        if current is None or target['before'] is None:
            raise Conflict('Merge conflict: goal was independently created or deleted. Nothing was overwritten.')
        merged = {name: merge_text(target['before'][name], current[name], target['after'][name])
                  for name in ('title', 'evidence')}
    else:
        merged = merge_text(target['before'], current, target['after'])
    return target, current, merged


def application_state(connection, entry):
    # Rejected suggestions do not spend even baseline-checking work.
    if entry['application_status'] == 'applied':
        return {'application_status': 'applied', 'application_message': entry['application_message'] or 'Applied.'}
    if entry['reaction'] in ('Bad', 'Trash'):
        return {'application_status': 'skipped', 'application_message': 'Not applied.'}
    if entry['application_status'] == 'validating':
        return {'application_status': 'validating', 'application_message': 'Validating syntax and before/after tests…'}
    if entry['application_status'] == 'blocked' and entry['application_message'].startswith('Validation failed.'):
        return {'application_status': 'blocked', 'application_message': entry['application_message']}
    try:
        inspect_target(connection, entry)
    except Conflict as error:
        return {'application_status': 'conflict', 'application_message': str(error)}
    except (ValueError, OSError) as error:
        return {'application_status': 'blocked', 'application_message': str(error)}
    if entry['application_status'] == 'blocked':
        return {'application_status': 'blocked', 'application_message': entry['application_message']}
    return {'application_status': 'pending', 'application_message': 'Good or Awesome validates and applies this change.'}


def apply_entry(connection, entry, checked=None):
    if entry['reaction'] not in ('Good', 'Awesome') or entry['application_status'] == 'applied':
        return
    try:
        target, current, merged = inspect_target(connection, entry)
        if target['kind'] == 'file':
            from .validation import syntax_check, CODE_SUFFIXES
            syntax_check(target['path'], merged)
            if Path(target['path']).suffix in CODE_SUFFIXES:
                if not checked or not checked.get('passed'):
                    raise ValueError('Code validation is required before application.')
                if (checked['_path'],checked['_before'],checked['_after']) != (target['path'],current,merged):
                    raise Conflict('Target changed during validation. Nothing was overwritten; rate again to revalidate.')
        if current != merged:
            if target['kind'] == 'goal':
                connection.execute('''INSERT INTO goals (id, title, evidence) VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET title=excluded.title, evidence=excluded.evidence''',
                    (target['id'], merged['title'], merged['evidence']))
            else:
                path = file_path(target)
                # Single-file changes are replaced atomically, retaining executable permissions.
                path.parent.mkdir(parents=True, exist_ok=True)
                owner = path.stat() if path.exists() else path.parent.stat()
                mode = stat.S_IMODE(owner.st_mode) if path.exists() else 0o644
                descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.polish-')
                try:
                    with os.fdopen(descriptor, 'wb') as output:
                        output.write(merged.encode('utf-8'))
                        output.flush()
                        os.fsync(output.fileno())
                    os.chown(temporary, owner.st_uid, owner.st_gid)
                    os.chmod(temporary, mode)
                    # Stop if an editor changed the file during the merge/write.
                    if read_current(connection, target) != current:
                        raise Conflict('Merge conflict: target changed during application. Nothing was overwritten.')
                    os.replace(temporary, path)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
        status = 'applied'
        message = 'Applied with Git three-way merge.' if merged != target['after'] else 'Applied.'
    except Conflict as error:
        status, message = 'conflict', str(error)
    except (ValueError, OSError) as error:
        status, message = 'blocked', str(error)
    connection.execute('''UPDATE entries SET application_status=?, application_message=?, applied_at=?, applied_result_json=?
        WHERE id=?''', (status, message,
        datetime.now(timezone.utc).isoformat() if status == 'applied' else None,
        json.dumps(merged) if status == 'applied' else None, entry['id']))


def apply_positive(entry_id):
    """Run expensive checks outside SQLite's write transaction, then recheck before writing."""
    from . import database
    from .validation import validate_change, ValidationFailure
    from .repairs import queue_repair, notify_worker
    try:
        with database() as connection:
            entry=dict(connection.execute('SELECT * FROM entries WHERE id=?',(entry_id,)).fetchone())
            if entry['reaction'] not in ('Good','Awesome') or entry['application_status'] in ('applied','validating'):
                return
            target,current,merged=inspect_target(connection,entry)
            if target['kind']=='goal':
                apply_entry(connection,entry)
                return
            connection.execute("UPDATE entries SET application_status='validating',application_message='Validating syntax and before/after tests…' WHERE id=?",(entry_id,))
        report=validate_change(target['path'],current,merged)
        checked={**report,'_path':target['path'],'_before':current,'_after':merged}
        with database() as connection:
            entry=dict(connection.execute('SELECT * FROM entries WHERE id=?',(entry_id,)).fetchone())
            connection.execute('UPDATE entries SET validation_json=? WHERE id=?',(json.dumps(report),entry_id))
            if entry['reaction'] not in ('Good','Awesome'):
                connection.execute("UPDATE entries SET application_status='pending' WHERE id=?",(entry_id,))
            else:
                apply_entry(connection,entry,checked)
    except Conflict as error:
        with database() as connection:
            connection.execute("UPDATE entries SET application_status='conflict',application_message=? WHERE id=?",(str(error),entry_id))
    except (ValueError,OSError) as error:
        report=error.report if isinstance(error,ValidationFailure) else {
            'passed':False,'retryable':str(error).startswith('Python syntax validation failed'),
            'stage':'syntax' if str(error).startswith('Python syntax validation failed') else 'application','message':str(error)}
        sources={target['path']:current} if 'target' in locals() and target['kind']=='file' else None
        if queue_repair(entry_id,report,sources):
            notify_worker()


def model_scores(connection):
    from . import POINTS
    return [dict(row) for row in connection.execute('''SELECT model_label,
        SUM(CASE reaction WHEN 'Good' THEN ? WHEN 'Awesome' THEN ? WHEN 'Bad' THEN ? WHEN 'Trash' THEN ? ELSE 0 END) AS points,
        COUNT(reaction) AS rated_count FROM entries
        WHERE model_label IS NOT NULL GROUP BY model_label ORDER BY points DESC, model_label''',
        (POINTS['Good'], POINTS['Awesome'], POINTS['Bad'], POINTS['Trash']))]


def reconcile_positive_ratings():
    """Apply existing positive ratings when introducing the new application policy."""
    from . import database
    with database() as connection:
        entries = connection.execute("SELECT * FROM entries WHERE reaction IN ('Good', 'Awesome') AND application_status != 'applied'").fetchall()
        ids=[entry['id'] for entry in entries]
    for entry_id in ids:
        apply_positive(entry_id)

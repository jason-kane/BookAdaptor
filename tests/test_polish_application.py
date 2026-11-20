"""Positive ratings apply once; changed baselines never get overwritten."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from home.app import create_app
from home.polish import database
from home.polish.application import reconcile_positive_ratings
from home.polish.proposals import submit_proposal


@pytest.fixture
def app(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    app = create_app({'TESTING': True, 'TODO_DIR': str(tmp_path / 'data'),
                       'POLISH_WORKSPACE': str(workspace)})
    with patch('home.polish.validation.validate_change', return_value={'passed': True, 'message': 'Test adapter passed.'}):
        yield app


def propose(app, key='one', model='Model A', target=None):
    target = target or {'kind': 'goal', 'id': 'G100', 'before': None,
                        'after': {'title': 'Improve something', 'evidence': 'Observable progress'}}
    before = (target['before'] or '') if target['kind'] == 'file' else ''
    after = target['after'] if target['kind'] == 'file' else json.dumps(target['after'])
    with app.app_context():
        return submit_proposal(goal_id='G001', model_label=model, description='A proposal',
                               before_text=before, after_text=after, proposal_key=key, target=target)


def rate(app, entry, reaction):
    response = app.test_client().post(f'/polish/entries/{entry}/reaction', json={'reaction': reaction})
    assert response.status_code == 200
    return response.json


def test_positive_ratings_apply_goal_and_score_current_rating_once(app):
    entry = propose(app)
    good = rate(app, entry, 'Good')
    assert good['points'] == 1
    assert good['application_status'] == 'applied'
    goals = app.test_client().get('/polish/goals').json['goals']
    assert next(goal for goal in goals if goal['id'] == 'G100')['evidence'] == 'Observable progress'
    for reaction in ('Awesome', 'Awesome'):
        result = rate(app, entry, reaction)
        assert result['scores'] == [{'model_label': 'Model A', 'points': 5, 'rated_count': 1}]
    assert rate(app, entry, 'Good')['scores'][0]['points'] == 1
    assert rate(app, entry, 'Bad')['scores'][0]['points'] == -0.5
    # Re-rating never deletes the applied goal or replays an old suggestion.
    with app.app_context(), database() as connection:
        connection.execute("UPDATE goals SET title='Subsequent edit' WHERE id='G100'")
    assert rate(app, entry, 'Awesome')['application_status'] == 'applied'
    assert app.test_client().get('/polish/goals').json['goals'][-1]['title'] == 'Subsequent edit'
    other = propose(app, key='two', model='Model B', target={
        'kind': 'goal', 'id': 'G101', 'before': None,
        'after': {'title': 'Another goal', 'evidence': 'Different result'}})
    scores = rate(app, other, 'Good')['scores']
    assert scores == [{'model_label': 'Model A', 'points': 5, 'rated_count': 1},
                      {'model_label': 'Model B', 'points': 1, 'rated_count': 1}]


def test_goal_conflicts_warn_without_overwriting_or_losing_rating(app):
    entry = propose(app)
    with app.app_context(), database() as connection:
        connection.execute("INSERT INTO goals (id,title,evidence) VALUES ('G100','Someone else changed it','Keep this')")
    page = app.test_client().get('/polish').get_data(as_text=True)
    assert 'Merge conflict:' in page
    result = rate(app, entry, 'Awesome')
    assert result['application_status'] == 'conflict'
    assert result['scores'][0]['points'] == 5
    goals = app.test_client().get('/polish/goals').json['goals']
    assert goals[-1]['title'] == 'Someone else changed it'
    assert 'Merge conflict:' in app.test_client().get('/polish').get_data(as_text=True)


@pytest.mark.parametrize('reaction,points', [('Bad', -0.5), ('Trash', -2.5)])
def test_rejected_entries_never_inspect_or_apply_targets(app, reaction, points):
    entry = propose(app)
    with (patch('home.polish.application.inspect_target', side_effect=AssertionError('Unnecessary conflict work')),
          patch('home.polish.application.apply_entry', side_effect=AssertionError('Unnecessary application'))):
        result = rate(app, entry, reaction)
        assert result['points'] == points
        assert result['scores'][0]['points'] == points
        assert result['application_status'] == 'skipped'
        assert app.test_client().get('/polish').status_code == 200


def test_file_application_and_stale_baseline_conflict(app):
    root = Path(app.config['POLISH_WORKSPACE'])
    source = root / 'example.py'
    source.write_bytes(b'old\r\n')
    source.chmod(0o755)
    entry = propose(app, target={'kind': 'file', 'path': 'example.py', 'before': 'old\r\n', 'after': 'new\r\n'})
    assert rate(app, entry, 'Good')['application_status'] == 'applied'
    assert source.read_bytes() == b'new\r\n'
    assert source.stat().st_mode & 0o777 == 0o755
    stale = propose(app, key='stale', target={'kind': 'file', 'path': 'example.py', 'before': 'old\r\n', 'after': 'overwrite\n'})
    assert rate(app, stale, 'Good')['application_status'] == 'conflict'
    assert source.read_bytes() == b'new\r\n'
    created = propose(app, key='create', target={'kind': 'file', 'path': 'new/file.txt', 'before': None, 'after': 'created'})
    assert rate(app, created, 'Awesome')['application_status'] == 'applied'
    assert (root / 'new/file.txt').read_text() == 'created'


def test_paths_and_visible_evidence_are_checked(app, tmp_path):
    for path in ('../escape.txt', '/absolute.txt', '.git/config', '.'):
        with pytest.raises(ValueError):
            propose(app, target={'kind': 'file', 'path': path, 'before': '', 'after': 'changed'})
    root = Path(app.config['POLISH_WORKSPACE'])
    outside = tmp_path / 'outside.txt'
    outside.write_text('private')
    (root / 'link').symlink_to(outside)
    entry = propose(app, target={'kind': 'file', 'path': 'link', 'before': 'private', 'after': 'overwritten'})
    assert rate(app, entry, 'Good')['application_status'] == 'blocked'
    assert outside.read_text() == 'private'
    with app.app_context(), pytest.raises(ValueError, match='reviewed'):
        submit_proposal(goal_id='G001', model_label='Model A', description='Mismatch',
                        before_text='visible', after_text='visible', proposal_key='mismatch',
                        target={'kind': 'file', 'path': 'file', 'before': 'hidden', 'after': 'hidden'})


def test_write_failure_is_visible_and_retry_keeps_points_idempotent(app):
    entry = propose(app, target={'kind': 'file', 'path': 'new.txt', 'before': None, 'after': 'new'})
    with patch('home.polish.application.os.replace', side_effect=OSError('Read-only filesystem')):
        result = rate(app, entry, 'Good')
    assert result['application_status'] == 'blocked'
    assert result['scores'][0]['points'] == 1
    assert not (Path(app.config['POLISH_WORKSPACE']) / 'new.txt').exists()
    assert rate(app, entry, 'Good')['application_status'] == 'applied'
    assert rate(app, entry, 'Good')['scores'][0]['points'] == 1


def test_existing_positive_ratings_are_reconciled(app):
    entry = propose(app)
    with app.app_context():
        with database() as connection:
            connection.execute("UPDATE entries SET reaction='Good' WHERE id=?", (entry,))
        reconcile_positive_ratings()
        reconcile_positive_ratings()
        with database() as connection:
            assert connection.execute("SELECT COUNT(*) FROM goals WHERE id='G100'").fetchone()[0] == 1
            assert connection.execute('SELECT application_status FROM entries WHERE id=?', (entry,)).fetchone()[0] == 'applied'


def test_git_three_way_merge_preserves_non_overlapping_edits_and_records_result(app):
    root = Path(app.config['POLISH_WORKSPACE'])
    source = root / 'merge.py'
    base = 'first = 1\n\na = 1\nb = 2\nc = 3\nd = 4\n\nlast = 1\n'
    proposed = base.replace('first = 1', 'first = 2')
    current = base.replace('last = 1', 'last = 3')
    merged = proposed.replace('last = 1', 'last = 3')
    source.write_text(current)
    entry = propose(app, target={'kind': 'file', 'path': 'merge.py', 'before': base, 'after': proposed})
    page = app.test_client().get('/polish').get_data(as_text=True)
    assert 'Merge conflict:' not in page
    assert source.read_text() == current  # Checking never writes.
    result = rate(app, entry, 'Good')
    assert result['application_status'] == 'applied'
    assert source.read_text() == merged
    with app.app_context(), database() as connection:
        row = connection.execute('SELECT * FROM entries WHERE id=?', (entry,)).fetchone()
        assert row['after_text'] == proposed
        assert json.loads(row['applied_result_json']) == merged
    assert rate(app, entry, 'Awesome')['application_status'] == 'applied'
    assert source.read_text() == merged


def test_git_overlap_never_writes_conflict_markers(app):
    source = Path(app.config['POLISH_WORKSPACE']) / 'conflict.txt'
    source.write_text('current\n')
    entry = propose(app, target={'kind': 'file', 'path': 'conflict.txt', 'before': 'base\n', 'after': 'proposal\n'})
    result = rate(app, entry, 'Good')
    assert result['application_status'] == 'conflict'
    assert 'overlapping edits' in result['application_message']
    assert source.read_text() == 'current\n'
    assert not list(source.parent.glob('.polish-*'))


def test_goal_fields_merge_independently(app):
    with app.app_context(), database() as connection:
        connection.execute("INSERT INTO goals (id,title,evidence) VALUES ('G100','Current title','Baseline evidence')")
    entry = propose(app, target={'kind': 'goal', 'id': 'G100',
                                'before': {'title': 'Baseline title', 'evidence': 'Baseline evidence'},
                                'after': {'title': 'Baseline title', 'evidence': 'New evidence'}})
    assert rate(app, entry, 'Awesome')['application_status'] == 'applied'
    goal = app.test_client().get('/polish/goals').json['goals'][-1]
    assert (goal['title'], goal['evidence']) == ('Current title', 'New evidence')


def test_git_failure_and_timeout_block_without_writing(app):
    import subprocess
    source = Path(app.config['POLISH_WORKSPACE']) / 'error.txt'
    source.write_text('current\n')
    entry = propose(app, target={'kind': 'file', 'path': 'error.txt', 'before': 'base\n', 'after': 'proposal\n'})
    cases = [FileNotFoundError('git unavailable'), subprocess.TimeoutExpired('git', 10)]
    for error in cases:
        with patch('home.polish.application.subprocess.run', side_effect=error):
            assert rate(app, entry, 'Good')['application_status'] == 'blocked'
        assert source.read_text() == 'current\n'
    with patch('home.polish.application.subprocess.run', return_value=subprocess.CompletedProcess([], 255, b'', b'error')):
        assert rate(app, entry, 'Good')['application_status'] == 'blocked'
    assert source.read_text() == 'current\n'


def test_existing_file_deletion_is_not_resurrected_by_merge(app):
    entry = propose(app, target={'kind': 'file', 'path': 'deleted.txt', 'before': 'old\n', 'after': 'new\n'})
    assert rate(app, entry, 'Good')['application_status'] == 'conflict'
    assert not (Path(app.config['POLISH_WORKSPACE']) / 'deleted.txt').exists()


def test_penalty_changes_replace_old_score_and_never_invoke_git(app):
    entry = propose(app)
    with patch('home.polish.application.subprocess.run', side_effect=AssertionError('No merge for rejected work')):
        for reaction, points in [('Trash', -2.5), ('Trash', -2.5), ('Bad', -0.5)]:
            result = rate(app, entry, reaction)
            assert result['scores'][0]['points'] == points
            assert result['scores'][0]['rated_count'] == 1
    result = rate(app, entry, 'Good')
    assert result['scores'][0]['points'] == 1
    assert result['application_status'] == 'applied'

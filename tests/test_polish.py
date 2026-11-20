"""Polish renders real diffs and persists exactly one reaction per entry."""
import pytest

from home.app import create_app
from home.polish import database, diff_rows


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "TODO_DIR": str(tmp_path)})


def test_empty_list_and_preview_do_not_seed_entries(app):
    client = app.test_client()
    page = client.get('/polish')
    assert page.status_code == 200
    assert page.headers['Cache-Control'] == 'no-store'
    assert b'No entries to review yet' in page.data
    preview = client.get('/polish?preview=1')
    assert preview.status_code == 200
    assert preview.data.count(b'data-entry-id=') == 2
    for reaction in (b'Trash', b'Bad', b'Good', b'Awesome'):
        assert b'data-reaction="' + reaction + b'"' in preview.data
    with app.app_context(), database() as connection:
        assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 0
    assert client.get('/polish/').status_code == 200
    assert client.get('/polish/entries').status_code == 404


def test_diff_alignment_and_empty_sides():
    rows = diff_rows('same\nold\nremoved\nend', 'same\nnew\nend')
    assert rows[0] == {'before': (1, 'same'), 'after': (1, 'same'), 'changed': False}
    assert rows[1]['before'] == (2, 'old')
    assert rows[1]['after'] == (2, 'new')
    assert rows[2]['before'] == (3, 'removed')
    assert rows[2]['after'] is None
    assert rows[3]['changed'] is False
    assert diff_rows('', 'added')[0]['before'] is None
    assert diff_rows('removed', '')[0]['after'] is None
    assert diff_rows('', '') == []


def test_reaction_survives_app_reload_and_content_is_escaped(app):
    with app.app_context(), database() as connection:
        connection.execute('INSERT INTO entries (description, before_text, after_text) VALUES (?, ?, ?)',
                           ('<script>description</script>', '<img src=x onerror=alert(1)>', 'Improved'))
    client = app.test_client()
    for reaction in ('Trash', 'Bad', 'Good', 'Awesome'):
        response = client.post('/polish/entries/1/reaction', json={'reaction': reaction})
        assert response.json['reaction'] == reaction
    page = create_app(dict(app.config)).test_client().get('/polish?view=graded').get_data(as_text=True)
    assert 'Saved: Awesome' in page
    assert 'data-reaction="Awesome" aria-pressed="true"' in page
    assert '<script>description</script>' not in page
    assert '&lt;script&gt;description&lt;/script&gt;' in page
    assert '<img src=x' not in page
    for payload in ({'reaction': 'Delete'}, {}, [], {'reaction': None}):
        assert client.post('/polish/entries/1/reaction', json=payload).status_code == 400
    assert client.post('/polish/entries/999/reaction', json={'reaction': 'Good'}).status_code == 404
    with app.app_context(), database() as connection:
        assert connection.execute('SELECT reaction FROM entries').fetchone()[0] == 'Awesome'


def test_home_links_to_polish_in_each_view(app):
    page = app.test_client().get('/').get_data(as_text=True)
    assert '<th scope="row">Polish</th>' in page
    assert 'data-panel-id="polish"' in page
    assert 'data-member-start="home"' not in page
    assert page.count('href="/polish"') >= 3
    assert app.test_client().get('/home-static/images/polish.svg').status_code == 200


def test_catalogue_proposals_are_attributed_and_retries_preserve_ratings(app):
    from pathlib import Path
    from home.polish.proposals import submit_goal_catalogue
    catalogue = (Path(__file__).resolve().parents[1] / 'docs/polish.md').read_text()
    with app.app_context():
        ids = submit_goal_catalogue(catalogue, model_label='GPT 6.1 Sol Medium')
        assert len(ids) == 55
        with database() as connection:
            assert connection.execute('SELECT COUNT(*) FROM goals').fetchone()[0] == 1
            rows = connection.execute('SELECT * FROM entries').fetchall()
            assert all(row['goal_id'] == 'G001' for row in rows)
            assert all(row['model_label'] == 'GPT 6.1 Sol Medium' for row in rows)
            assert all(row['before_text'] == '' and row['reaction'] is None for row in rows)
            assert rows[0]['after_text'].startswith('G002 — ')
        client = app.test_client()
        assert client.post(f'/polish/entries/{ids[0]}/reaction', json={'reaction': 'Good'}).status_code == 200
        assert submit_goal_catalogue(catalogue, model_label='GPT 6.1 Sol Medium') == ids
        with database() as connection:
            assert connection.execute('SELECT COUNT(*) FROM entries').fetchone()[0] == 55
            assert connection.execute('SELECT reaction FROM entries WHERE id = ?', (ids[0],)).fetchone()[0] == 'Good'
    page = app.test_client().get('/polish').get_data(as_text=True)
    assert 'GPT 6.1 Sol Medium' in page
    assert 'G001' in page
    assert 'Build and maintain an exhaustive list of goals.' in page


def test_migration_preserves_legacy_evidence_and_rating(app):
    import sqlite3
    from pathlib import Path
    path = Path(app.config['TODO_DIR']) / 'polish.sqlite3'
    with sqlite3.connect(path) as connection:
        connection.execute('CREATE TABLE entries (id INTEGER PRIMARY KEY, description TEXT NOT NULL, before_text TEXT NOT NULL, after_text TEXT NOT NULL, reaction TEXT)')
        connection.execute("INSERT INTO entries VALUES (1, 'Legacy', 'Before', 'After', 'Awesome')")
    page = app.test_client().get('/polish?view=graded').get_data(as_text=True)
    assert 'Saved: Awesome' in page
    assert 'Unattributed (legacy entry)' in page
    with app.app_context(), database() as connection:
        row = connection.execute('SELECT * FROM entries WHERE id=1').fetchone()
        assert (row['before_text'], row['after_text'], row['reaction']) == ('Before', 'After', 'Awesome')
        assert row['model_label'] is None


def test_graded_entries_leave_default_queue_and_goals_remain_reviewable(app):
    from home.polish.proposals import submit_proposal
    with app.app_context():
        entry = submit_proposal(goal_id='G001', model_label='Model', description='Add a useful goal',
                                before_text='', after_text='G100: Useful goal', proposal_key='review-goal',
                                target={'kind': 'goal', 'id': 'G100', 'before': None,
                                        'after': {'title': 'Useful goal', 'evidence': 'A clear result'}})
    client = app.test_client()
    assert f'data-entry-id="{entry}"' in client.get('/polish').get_data(as_text=True)
    result = client.post(f'/polish/entries/{entry}/reaction', json={'reaction': 'Good'}).json
    assert any(goal['id'] == 'G100' for goal in result['goals'])
    page = client.get('/polish').get_data(as_text=True)
    assert f'data-entry-id="{entry}"' not in page
    assert 'Show Goals' in page
    assert 'G100 · Useful goal' in page
    assert 'A clear result' in page
    assert 'Show graded' in page
    graded = client.get('/polish?view=graded').get_data(as_text=True)
    assert f'data-entry-id="{entry}"' in graded
    assert 'Saved: Good' in graded
    assert 'Show ungraded' in graded


def test_hidden_graded_conflicts_keep_a_visible_warning(app):
    from home.polish.proposals import submit_proposal
    with app.app_context():
        entry = submit_proposal(goal_id='G001', model_label='Model', description='Conflicting addition',
                                before_text='', after_text='Proposed', proposal_key='conflict-warning',
                                target={'kind': 'goal', 'id': 'G100', 'before': None,
                                        'after': {'title': 'Proposed', 'evidence': 'Result'}})
        with database() as connection:
            connection.execute("INSERT INTO goals (id,title) VALUES ('G100','Existing')")
    client = app.test_client()
    client.post(f'/polish/entries/{entry}/reaction', json={'reaction': 'Awesome'})
    page = client.get('/polish').get_data(as_text=True)
    assert f'data-entry-id="{entry}"' not in page
    assert 'Merge conflict:' in page
    assert f'data-warning-entry="{entry}"' in page
    assert f'/polish?view=graded#entry-{entry}' in page


def test_diff_folds_preserve_four_lines_of_context_and_original_alignment():
    from home.polish import diff_sections
    before = '\n'.join(f'line {n}' for n in range(40))
    rows = diff_rows(before, before.replace('line 20\n', 'changed 20\n'))
    sections = diff_sections(rows)
    assert [section['collapsed'] for section in sections] == [True, False, True]
    assert [len(section['rows']) for section in sections] == [16, 9, 15]
    assert sections[1]['rows'][0]['before'] == (17, 'line 16')
    assert sections[1]['rows'][-1]['before'] == (25, 'line 24')
    assert [row for section in sections for row in section['rows']] == rows
    assert not any(row['changed'] for section in sections if section['collapsed'] for row in section['rows'])


def test_diff_folds_between_distant_changes_but_not_nearby_changes():
    from home.polish import diff_sections
    before = '\n'.join(f'line {n}' for n in range(70))
    distant = before.replace('line 10\n', 'changed 10\n').replace('line 50\n', 'changed 50\n')
    sections = diff_sections(diff_rows(before, distant))
    assert [len(section['rows']) for section in sections if section['collapsed']] == [6, 31, 15]
    nearby = before.replace('line 10\n', 'changed 10\n').replace('line 15\n', 'changed 15\n')
    assert len([section for section in diff_sections(diff_rows(before, nearby)) if not section['collapsed']]) == 1


def test_hidden_diff_context_is_expandable_and_remains_escaped(app):
    with app.app_context(), database() as connection:
        before = '<script>hidden</script>\n' + '\n'.join(f'line {n}' for n in range(30))
        connection.execute('INSERT INTO entries (description,before_text,after_text) VALUES (?,?,?)',
                           ('A small edit',before,before.replace('line 15\n','changed\n')))
    page = app.test_client().get('/polish').get_data(as_text=True)
    assert page.count('<details class="diff-fold">') == 2
    assert '<details class="diff-fold" open' not in page
    assert 'unchanged lines</span>' in page
    assert '&lt;script&gt;hidden&lt;/script&gt;' in page
    assert '<script>hidden</script>' not in page
    assert 'changed</code>' in page

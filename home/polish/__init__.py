"""Polish review list and attributed proposals."""
from contextlib import contextmanager
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path
import sqlite3
import json

from flask import Blueprint, abort, current_app, jsonify, render_template, request

bp = Blueprint("polish", __name__)
REACTIONS = ("Trash", "Bad", "Good", "Awesome")
POINTS = {"Trash": -2.5, "Bad": -0.5, "Good": 1, "Awesome": 5}


@contextmanager
def database():
    path = Path(current_app.config.get("POLISH_DB") or
                Path(current_app.config["TODO_DIR"]) / "polish.sqlite3")
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS entries (
                id INTEGER PRIMARY KEY,
                description TEXT NOT NULL,
                before_text TEXT NOT NULL,
                after_text TEXT NOT NULL,
                reaction TEXT CHECK (reaction IN ('Trash', 'Bad', 'Good', 'Awesome'))
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS goals (
                id TEXT PRIMARY KEY, title TEXT NOT NULL
            )""")
            connection.execute("INSERT OR IGNORE INTO goals (id, title) VALUES (?, ?)",
                               ("G001", "Build and maintain an exhaustive list of goals."))
            goal_columns = {row["name"] for row in connection.execute("PRAGMA table_info(goals)")}
            if "evidence" not in goal_columns:
                connection.execute("ALTER TABLE goals ADD COLUMN evidence TEXT NOT NULL DEFAULT ''")
            # Preserve existing text entries and ratings when upgrading the framework.
            columns = {row["name"] for row in connection.execute("PRAGMA table_info(entries)")}
            for name, definition in (("goal_id", "TEXT REFERENCES goals(id)"),
                                     ("model_label", "TEXT"), ("proposal_key", "TEXT"),
                                     ("target_json", "TEXT"),
                                     ("application_status", "TEXT NOT NULL DEFAULT 'pending'"),
                                     ("application_message", "TEXT NOT NULL DEFAULT ''"),
                                     ("applied_result_json", "TEXT"),
                                     ("validation_json", "TEXT"),
                                     ("correction_entry_id", "INTEGER"),
                                     ("prompt_profile_json", "TEXT"),
                                     ("applied_at", "TEXT")):
                if name not in columns:
                    connection.execute(f"ALTER TABLE entries ADD COLUMN {name} {definition}")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS entries_proposal_key ON entries(proposal_key)")
            connection.execute("""CREATE TABLE IF NOT EXISTS polish_state (
                key TEXT PRIMARY KEY, value TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS polish_jobs (
                id TEXT PRIMARY KEY, goal_id TEXT NOT NULL REFERENCES goals(id),
                model_json TEXT NOT NULL, goal_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued', message TEXT NOT NULL DEFAULT 'Queued.',
                prompt_version TEXT NOT NULL, context_json TEXT, response_json TEXT,
                entry_id INTEGER REFERENCES entries(id), created_at REAL NOT NULL, updated_at REAL NOT NULL
            )""")
            connection.execute("""CREATE UNIQUE INDEX IF NOT EXISTS polish_active_goal
                ON polish_jobs(goal_id) WHERE status IN ('queued', 'running')""")
            job_columns = {row['name'] for row in connection.execute('PRAGMA table_info(polish_jobs)')}
            if 'selection_json' not in job_columns:
                connection.execute('ALTER TABLE polish_jobs ADD COLUMN selection_json TEXT')
            if 'attempts_json' not in job_columns:
                connection.execute("ALTER TABLE polish_jobs ADD COLUMN attempts_json TEXT NOT NULL DEFAULT '[]'")
            connection.execute("""CREATE TABLE IF NOT EXISTS polish_repairs (
                entry_id INTEGER PRIMARY KEY REFERENCES entries(id), job_id TEXT NOT NULL REFERENCES polish_jobs(id),
                status TEXT NOT NULL DEFAULT 'queued', message TEXT NOT NULL,
                failure_json TEXT NOT NULL, source_json TEXT NOT NULL DEFAULT '{}',
                result_entry_id INTEGER REFERENCES entries(id), created_at REAL NOT NULL
            )""")
            # Upgrade this chat's original goal proposals into explicit application targets.
            for row in connection.execute("""SELECT id, proposal_key, after_text FROM entries
                WHERE target_json IS NULL AND proposal_key LIKE 'initial-goal-catalogue-v1:%'"""):
                target_id = row["proposal_key"].split(":", 1)[1]
                prefix = target_id + " — "
                title, separator, evidence = row["after_text"].partition("\n\nEvidence of success:\n")
                if title.startswith(prefix) and separator:
                    target = {"kind": "goal", "id": target_id, "before": None,
                              "after": {"title": title[len(prefix):], "evidence": evidence}}
                    connection.execute("UPDATE entries SET target_json = ? WHERE id = ?",
                                       (json.dumps(target), row["id"]))
            yield connection
    finally:
        connection.close()


def diff_rows(before, after):
    """Align changed lines while keeping the original text safe for autoescaping."""
    left, right = before.splitlines(), after.splitlines()
    rows = []
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    for tag, i, j, a, b in matcher.get_opcodes():
        left_lines = list(enumerate(left[i:j], i + 1))
        right_lines = list(enumerate(right[a:b], a + 1))
        for old, new in zip_longest(left_lines, right_lines):
            rows.append({"before": old, "after": new, "changed": tag != "equal"})
    return rows


def diff_sections(rows, context=4):
    """Fold only aligned, unchanged rows outside every change's context window."""
    visible = [False] * len(rows)
    for index, row in enumerate(rows):
        if row['changed']:
            for neighbor in range(max(0, index-context), min(len(rows), index+context+1)):
                visible[neighbor] = True
    sections = []
    for row, shown in zip(rows, visible):
        if not sections or sections[-1]['collapsed'] != (not shown):
            sections.append({'collapsed': not shown, 'rows': []})
        sections[-1]['rows'].append(row)
    return sections


@bp.get("/polish", strict_slashes=False)
def index():
    from .application import application_state, model_scores
    preview = request.args.get("preview") == "1"
    graded = request.args.get("view") == "graded" and not preview
    goal_rows, warnings = [], []
    if preview:
        entries = [
            {"id": "preview-1", "description": "Give the opening a clearer rhythm.",
             "before_text": "The room was very quiet.\nShe went to the window and looked outside.\nThe rain continued.",
             "after_text": "The room fell silent.\nShe crossed to the window. Beyond the glass, rain blurred the street.\nThe rain continued.", "reaction": None},
            {"id": "preview-2", "description": "Tighten the dialogue and remove a redundant line.",
             "before_text": '"I think that we should leave now," he said.\nHe spoke those words aloud.\nShe reached for her coat.',
             "after_text": '"We should leave," he said.\nShe reached for her coat.', "reaction": None},
        ]
    else:
        with database() as connection:
            all_entries = [dict(row) for row in connection.execute("""
                SELECT entries.*, goals.title AS goal_title FROM entries
                LEFT JOIN goals ON entries.goal_id = goals.id ORDER BY entries.id DESC
            """)]
            entries = [entry for entry in all_entries if (entry['reaction'] is not None) == graded]
            warnings = [entry for entry in all_entries if entry['reaction'] in ('Good', 'Awesome')
                        and entry['application_status'] in ('conflict', 'blocked')]
            goal_rows = [dict(row) for row in connection.execute("SELECT * FROM goals ORDER BY id")]
            for entry in entries:
                entry.update(application_state(connection, entry))
            scores = model_scores(connection)
    for entry in entries:
        entry['prompt_profile']=json.loads(entry.get('prompt_profile_json') or 'null')
        entry["rows"] = diff_rows(entry["before_text"], entry["after_text"])
        entry["sections"] = diff_sections(entry["rows"])
    response = current_app.make_response(render_template(
        "polish.html", entries=entries, reactions=REACTIONS, preview=preview,
        scores=[] if preview else scores, points=POINTS, graded=graded,
        goals=goal_rows, warnings=warnings))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get('/polish/models')
def models():
    from .seeking import model_catalogue
    response = jsonify(model_catalogue())
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.post('/polish/models/refresh')
def refresh_models():
    from .seeking import request_model_refresh
    try:
        request_model_refresh()
    except (OSError, RuntimeError) as error:
        return jsonify(error=str(error)), 503
    return jsonify(refreshing=True), 202


@bp.post('/polish/goals/<goal_id>/improvements')
def seek_improvement(goal_id):
    from .seeking import enqueue_search
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('model'), str):
        return jsonify(error='Choose a model.'), 400
    try:
        job = enqueue_search(goal_id, payload['model'], payload.get('request_key'))
    except LookupError as error:
        return jsonify(error=str(error)), 404
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except (OSError, RuntimeError) as error:
        return jsonify(error=str(error)), 503
    return jsonify(job=job), 202


@bp.get('/polish/jobs')
def search_jobs():
    from .seeking import jobs_snapshot, latest_automatic_job, six_pack_snapshot
    response = jsonify(jobs=jobs_snapshot(), automatic_job=latest_automatic_job(), six_pack=six_pack_snapshot())
    response.headers['Cache-Control'] = 'no-store'
    return response


@bp.post('/polish/rub-one-out')
def rub_one_out():
    from .seeking import enqueue_search
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('model'), str):
        return jsonify(error='Choose a model.'), 400
    try:
        job = enqueue_search(None, payload['model'], payload.get('request_key'), automatic=True)
    except LookupError as error:
        return jsonify(error=str(error)), 409
    except ValueError as error:
        return jsonify(error=str(error)), 400
    except (OSError, RuntimeError) as error:
        return jsonify(error=str(error)), 503
    return jsonify(job=job), 202


@bp.post('/polish/six-pack')
def six_pack():
    from .seeking import enqueue_six_pack
    payload=request.get_json(silent=True)
    if not isinstance(payload,dict) or not isinstance(payload.get('model'),str):
        return jsonify(error='Choose a model.'),400
    try:
        batch=enqueue_six_pack(payload['model'],payload.get('request_key'))
    except LookupError as error:
        return jsonify(error=str(error)),409
    except ValueError as error:
        return jsonify(error=str(error)),400
    except (OSError,RuntimeError) as error:
        return jsonify(error=str(error)),503
    return jsonify(six_pack=batch),202


@bp.post("/polish/entries/<int:entry_id>/reaction")
def react(entry_id):
    from .application import apply_positive, application_state, model_scores
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("reaction") not in REACTIONS:
        return jsonify(error="Choose Trash, Bad, Good, or Awesome."), 400
    with database() as connection:
        result = connection.execute("UPDATE entries SET reaction = ? WHERE id = ?",
                                    (payload["reaction"], entry_id))
        if result.rowcount == 0:
            abort(404)
        if payload['reaction'] in ('Bad','Trash'):
            connection.execute("UPDATE entries SET application_status='pending' WHERE id=? AND application_status='validating'",(entry_id,))
    if payload['reaction'] in ('Good','Awesome'):
        apply_positive(entry_id)
    with database() as connection:
        entry = dict(connection.execute("SELECT * FROM entries WHERE id = ?", (entry_id,)).fetchone())
        state = application_state(connection, entry)
        scores = model_scores(connection)
        goal_rows = [dict(row) for row in connection.execute("SELECT * FROM goals ORDER BY id")]
    return jsonify(reaction=payload["reaction"], points=POINTS[payload["reaction"]],
                   scores=scores, goals=goal_rows, correction_entry_id=entry['correction_entry_id'], **state)


@bp.get("/polish/goals")
def goals():
    with database() as connection:
        rows = [dict(row) for row in connection.execute("SELECT * FROM goals ORDER BY id")]
    response = jsonify(goals=rows)
    response.headers["Cache-Control"] = "no-store"
    return response

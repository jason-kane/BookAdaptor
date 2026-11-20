"""Submit attributed suggestions without applying their proposed changes."""
from . import database
import json


def submit_proposal(*, goal_id, model_label, description, before_text, after_text, proposal_key, target=None):
    """Persist once per source revision; retries preserve the original evidence/rating."""
    for name, value in (("goal_id", goal_id), ("model_label", model_label),
                        ("description", description), ("proposal_key", proposal_key)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    if not isinstance(before_text, str) or not isinstance(after_text, str):
        raise ValueError("Before and after evidence must be text")
    if target is not None:
        from .application import validate_target
        validate_target(target)
        if target["kind"] == "file" and (before_text != (target["before"] or "") or after_text != target["after"]):
            raise ValueError("File target must match the reviewed before/after text")
    with database() as connection:
        if connection.execute("SELECT 1 FROM goals WHERE id = ?", (goal_id,)).fetchone() is None:
            raise ValueError("Unknown goal")
        connection.execute("""INSERT INTO entries
            (goal_id, model_label, description, before_text, after_text, proposal_key, target_json)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(proposal_key) DO NOTHING""",
            (goal_id, model_label, description, before_text, after_text, proposal_key,
             json.dumps(target) if target is not None else None))
        return connection.execute("SELECT id FROM entries WHERE proposal_key = ?", (proposal_key,)).fetchone()[0]


def submit_goal_catalogue(markdown, *, model_label):
    """Stage this chat's candidate goals as additions, all advancing G001."""
    proposals = []
    for line in markdown.splitlines():
        if not line.startswith("| G"):
            continue
        parts = [part.strip() for part in line.split("|")[1:-1]]
        if len(parts) != 3:
            raise ValueError("Expected goal ID, title, and completion evidence")
        goal_id, title, evidence = parts
        if goal_id == "G001":
            continue  # The user supplied G001; it is not a model suggestion.
        proposals.append((goal_id, title, evidence))
    return [submit_proposal(
        goal_id="G001", model_label=model_label,
        description=f"Propose {goal_id}: {title}", before_text="",
        after_text=f"{goal_id} — {title}\n\nEvidence of success:\n{evidence}",
        proposal_key=f"initial-goal-catalogue-v1:{goal_id}",
        target={"kind": "goal", "id": goal_id, "before": None,
                "after": {"title": title, "evidence": evidence}},
    ) for goal_id, title, evidence in proposals]

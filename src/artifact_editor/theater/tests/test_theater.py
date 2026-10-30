import json
from pathlib import Path

from bs4 import BeautifulSoup
from flask import Flask

from artifact_editor.theater import views


def client(tmp_path, monkeypatch):
    monkeypatch.setattr(views.const, "THEATER_DIR", str(tmp_path / "Theater"))
    app = Flask(__name__, template_folder=str(Path(views.__file__).parents[1] / "templates"))
    app.jinja_env.globals["websocket_url"] = lambda: "ws://localhost:5001/ws"
    app.jinja_loader.searchpath.extend([
        str(Path(views.__file__).parents[1] / "templates"),
        str(Path(views.__file__).parents[2] / "styler" / "templates"),
    ])
    app.register_blueprint(views.bp, url_prefix="/theater")
    app.testing = True
    return app.test_client()


def new_project(test_client):
    response = test_client.post("/theater/projects", data={
        "raw_story": "A traveler finds a door in the sea.",
        "director_profile": next(iter(views.profiles())),
    }, follow_redirects=False)
    assert response.status_code == 302
    return response.headers["Location"].rsplit("/", 1)[-1]


def test_projects_are_created_and_persisted_in_theater(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_id = new_project(test_client)
    response = test_client.get(f"/theater/projects/{project_id}")
    assert response.status_code == 200
    profile_name = views.profiles()[next(iter(views.profiles()))]["name"]
    soup = BeautifulSoup(response.data, "html.parser")
    assert soup.select_one("h1").get_text(strip=True) == profile_name
    assert soup.select_one(".director-profile") is None
    assert soup.select_one(".creative-brief") is None
    assert soup.select_one(".decision-criteria") is None
    assert soup.select_one("#styler-job-queue[aria-label='GPU queue']") is not None
    assert b"styler-samples.js" in response.data
    assert b"gpu-queue.css" in response.data
    assert b"Save direction" not in response.data
    assert b"Save script" not in response.data
    saved = json.loads((tmp_path / "Theater" / project_id / "project.json").read_text())
    assert saved["raw_story"] == "A traveler finds a door in the sea."
    assert "planned_minutes" not in saved
    assert "aspect" not in saved
    assert "dimensions" not in saved


def test_director_reads_story_once_and_saves_understanding(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_id = new_project(test_client)
    calls = []

    def fake_submit(action, arguments, **kwargs):
        calls.append((action, arguments, kwargs))
        return json.dumps({
            "reply": "I see a traveler facing an impossible threshold.",
            "story_read": {
                "summary": "A traveler finds a door at sea.", "characters": ["traveler"],
                "central_conflict": "Whether to enter", "emotional_movement": "Curiosity to resolve",
                "ending": "Unspecified", "open_questions": [],
            },
        })

    import gpu_tasks
    monkeypatch.setattr(gpu_tasks, "submit", fake_submit)
    first = test_client.post(f"/theater/projects/{project_id}/read")
    second = test_client.post(f"/theater/projects/{project_id}/read")
    assert first.status_code == 200
    assert first.json["cached"] is False
    assert second.json["cached"] is True
    assert len(calls) == 1
    assert calls[0][0] == "gpu_llama"
    assert calls[0][2]["priority"] is True
    assert "A traveler finds a door" in calls[0][1]["prompt"]
    saved = json.loads((tmp_path / "Theater" / project_id / "project.json").read_text())
    assert saved["story_read"]["central_conflict"] == "Whether to enter"


def test_configuration_does_not_assign_runtime_or_aspect(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_id = new_project(test_client)
    response = test_client.post(
        f"/theater/projects/{project_id}/configure", data={"director_profile": next(iter(views.profiles()))}
    )
    assert response.json == {"ok": True}
    saved = json.loads((tmp_path / "Theater" / project_id / "project.json").read_text())
    assert not {"planned_minutes", "aspect", "dimensions"} & saved.keys()


def test_project_requires_base_story_and_director(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    response = test_client.post("/theater/projects", data={"raw_story": "A story."})
    assert response.status_code == 400
    assert b"choose a director" in response.data.lower()


def test_chat_returns_and_persists_structured_script(tmp_path, monkeypatch):
    test_client = client(tmp_path, monkeypatch)
    project_id = new_project(test_client)
    profile_id = next(iter(views.profiles()))
    test_client.post(f"/theater/projects/{project_id}/configure", data={"director_profile": profile_id})

    class FakeRedis:
        def __init__(self, *args, **kwargs):
            pass

    captured = {}

    def fake_completion(*args, **kwargs):
        captured.update(kwargs)
        return json.dumps({
            "reply": "The opening image should establish the impossible setting.",
            "follow_up_question": "",
            "options": [{"label": "Quiet opening", "description": "Begin with one visual clue."}],
            "sample_still_prompts": [{"label": "The door", "prompt": "A door standing in the ocean."}],
            "script": {
                "story_rewrite": "A traveler finds the door and chooses to enter.",
                "characters": [{"id": "traveler", "name": "Traveler", "appearance": "A red scarf", "continuity_notes": "Scarf stays wet."}],
                "props": [{"id": "key", "name": "Brass key", "appearance": "A long brass key", "story_function": "Opens the door"}],
                "story_requirements": [{"id": "find-key", "scene_id": "shore", "must_happen": "The traveler finds the key", "why_it_matters": "It opens the door."}],
                "scenes": [{"id": "shore", "title": "The Shore", "dramatic_purpose": "Make the choice legible.", "visual_note": "The key catches the dawn light.", "shots": [{"visual_note": "Hold on the key before the hand enters.", "image_prompt": "A brass key in wet sand."}]}],
                "read_along_text": [],
            },
        })

    import llama_inference
    monkeypatch.setattr(views.redis, "Redis", FakeRedis)
    monkeypatch.setattr(llama_inference, "chat_completion", fake_completion)
    response = test_client.post(f"/theater/projects/{project_id}/chat", json={"message": "Make it uncanny."})
    assert response.status_code == 200
    assert "dimensions" not in response.json
    assert "story_requirements" in captured["system_prompt"]
    assert "stable appearance description" in captured["system_prompt"]
    saved = json.loads((tmp_path / "Theater" / project_id / "project.json").read_text())
    assert saved["script"]["scenes"][0]["shots"][0]["visual_note"] == "Hold on the key before the hand enters."
    assert saved["script"]["story_requirements"][0]["must_happen"] == "The traveler finds the key"
    assert saved["script"]["characters"][0]["appearance"] == "A red scarf"
    assert saved["conversation"][-1]["role"] == "assistant"

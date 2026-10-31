"""Exercise Home without importing the editor or its dependencies."""
import sys
from unittest.mock import patch

from home.app import create_app


def test_dashboard_and_assets(tmp_path):
    app = create_app({"TESTING": True, "TODO_DIR": str(tmp_path)})
    client = app.test_client()
    response = client.get("/")
    assert response.status_code == 200
    assert b'href="/programs/artifactserver"' in response.data
    assert b'href="/theater/" data-service-port="8082"' in response.data
    assert b'Enter Theater' in response.data
    assert client.get("/home-static/images/theater.svg").status_code == 200
    assert b'/home-static/js/service-members.js' in response.data
    assert b'data-program-stop' not in response.data
    for path in ("/home-static/js/service-members.js", "/home-static/js/quill.js",
                 "/home-static/images/library.png", "/programs/glances", "/programs/redisinsight", "/programs/artifactserver"):
        assert client.get(path).status_code == 200
    assert "artifact_editor.app" not in sys.modules


def test_home_todo_persists(tmp_path):
    client = create_app({"TESTING": True, "TODO_DIR": str(tmp_path)}).test_client()
    assert client.get("/home-todo/").json == {}
    delta = '{"ops": [{"insert": "Remember\\n"}]}'
    assert client.post("/home-todo/", data={"delta": delta}).status_code == 204
    assert client.get("/home-todo/").json == {"ops": [{"insert": "Remember\n"}]}
    assert client.post("/home-todo/", data={"delta": "invalid"}).status_code == 400


def test_program_routes_and_errors():
    client = create_app({"TESTING": True}).test_client()
    assert client.post("/programs/redis/stop").status_code == 404
    with patch("home.programs.start_program") as start:
        assert client.post("/programs/redisinsight/start").json == {"started": True}
        start.assert_called_once_with("redisinsight")
    with patch("home.programs.stop_program", side_effect=RuntimeError("unavailable")):
        assert client.post("/programs/glances/stop").status_code == 503


def test_library_loader_destination_and_controls():
    client = create_app({"TESTING": True}).test_client()
    page = client.get("/programs/artifactserver")
    assert page.status_code == 200
    assert b'/programs/artifactserver/start' in page.data
    assert b'/programs/artifactserver/status' in page.data
    assert b'destination.port = "8082"' in page.data
    assert b'destination.pathname = "/library/"' in page.data
    assert b'Date.now() + 300000' in page.data
    with patch("home.programs.start_program") as start:
        assert client.post("/programs/artifactserver/start").json == {"started": True}
        start.assert_called_once_with("artifactserver")
    with patch("home.programs.program_ready", return_value=False):
        assert client.get("/programs/artifactserver/status").json == {"ready": False}
    for path in ("/programs/redis", "/programs/redis/start", "/programs/redis/status"):
        response = client.post(path) if path.endswith("start") else client.get(path)
        assert response.status_code == 404


def test_default_list_is_a_text_table_with_explicit_actions():
    page = create_app({"TESTING": True}).test_client().get('/').get_data(as_text=True)
    table = page.split('<table class="service-table">', 1)[1].split('</table>', 1)[0]
    assert '<th scope="col">Compose service</th>' in table
    assert '<th scope="col">Version</th>' in table
    assert 'data-service-state="redis"' in table
    assert 'data-service-version="redisinsight"' in table
    assert 'data-service-image="artifactserver"' in table
    assert 'data-service-ports="redis"' in table
    from home.service_info import TABLE_ROWS
    assert table.count('data-table-info=') == len(TABLE_ROWS)
    assert 'data-program-stop' not in table
    assert 'onclick' not in table


def test_datatable_controls_and_local_dependencies_are_available():
    client = create_app({"TESTING": True}).test_client()
    page = client.get('/').get_data(as_text=True)
    for control in ('data-table-copy', 'data-table-csv', 'data-table-reset'):
        assert control in page
    assert '<tfoot>' in page
    for asset in ('jquery-3.7.1.min.js', 'dataTables-2.3.4.min.js',
                  'dataTables-2.3.4.min.css', 'colReorder-2.1.0.min.js', 'colReorder-2.1.0.min.css'):
        assert client.get('/home-static/vendor/datatables/' + asset).status_code == 200

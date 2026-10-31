"""PostgreSQL and pgAdmin follow Home's service-owned companion pattern."""
import json
from pathlib import Path
import struct
from unittest.mock import patch

from home.app import create_app
from home import programs


def test_home_groups_postgres_and_pgadmin_without_starting_services():
    app = create_app({"TESTING": True})
    with patch("home.programs.start_program") as start:
        response = app.test_client().get("/")
    start.assert_not_called()
    html = response.get_data(as_text=True)
    assert '<h2>PostgreSQL</h2>' in html
    assert '<h2>pgAdmin 4</h2>' not in html
    assert 'href="/programs/pgadmin"' in html
    assert 'data-member-start="postgres"' in html
    assert 'data-member-start="pgadmin"' in html
    assert 'hx-get="/programs/postgres/panel-status"' in html
    assert app.extensions["home_panel_services"]["postgres"] == ("postgres", "pgadmin")


def test_postgres_probe_checks_protocol_and_handles_stopped_service():
    panel = create_app({"TESTING": True}).extensions["home_panels"]["postgres"]
    with patch("src.postgres.home_panel.socket.create_connection") as connect:
        connection = connect.return_value.__enter__.return_value
        for response in (b"S", b"N"):
            connection.recv.return_value = response
            assert panel.running()
        connection.sendall.assert_called_with(struct.pack("!II", 8, 80877103))
        connect.assert_called_with(("postgres", 5432), timeout=1)
        connection.recv.return_value = b"E"
        assert not panel.running()
    with patch("src.postgres.home_panel.socket.create_connection", side_effect=ConnectionRefusedError):
        assert not panel.running()


def test_status_and_companion_launcher():
    app = create_app({"TESTING": True})
    client = app.test_client()
    with patch.object(type(app.extensions["home_panels"]["postgres"]), "running", return_value=True):
        response = client.get("/programs/postgres/panel-status")
        assert b'Running' in response.data
        assert response.headers["Cache-Control"] == "no-store"
    page = client.get("/programs/pgadmin")
    assert page.status_code == 200
    assert b'destination.port = "5050"' in page.data
    with patch("home.programs.start_service") as start:
        assert client.post("/programs/panels/postgres/members/pgadmin/start").json == {"started": True}
        start.assert_called_once_with("pgadmin", ("postgres", "pgadmin"))
    assert client.post("/programs/panels/postgres/members/redis/start").status_code == 404


def test_pgadmin_readiness_uses_ping_instead_of_login_redirect():
    with patch.object(programs.http.client, "HTTPConnection") as connection:
        connection.return_value.getresponse.return_value.status = 200
        assert programs.program_ready("pgadmin")
    connection.assert_called_once_with("pgadmin", 80, timeout=1)
    connection.return_value.request.assert_called_once_with("GET", "/misc/ping")


def test_pgadmin_server_definition_uses_compose_database():
    root = Path(__file__).resolve().parents[1]
    server = json.loads((root / "src/postgres/servers.json").read_text())["Servers"]["1"]
    assert server["Host"] == "postgres"
    assert server["Port"] == 5432
    assert server["MaintenanceDB"] == "bookadaptor"
    assert server["Username"] == "bookadaptor"

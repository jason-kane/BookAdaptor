"""Service-owned Redis panel and its non-starting status probe."""
from unittest.mock import patch

from home.app import create_app
from src.redis.home_panel import home_panel


def test_home_instantiates_service_panel_and_nests_insight():
    app = create_app({"TESTING": True})
    assert isinstance(app.extensions["home_panels"]["redis"], home_panel)
    with patch.object(home_panel, "running") as running:
        page = app.test_client().get("/")
    running.assert_not_called()  # Browser loads status separately.
    html = page.get_data(as_text=True)
    assert html.count('<h2>Redis</h2>') == 1
    assert '<h2>Redis Insight</h2>' not in html
    panel = html.split('<h2>Redis</h2>', 1)[1].split('<section class="service-panel"', 1)[0]
    assert 'href="/programs/redisinsight"' in panel
    assert 'hx-get="/programs/redis/panel-status"' in panel
    assert 'hx-trigger="load, every 60s"' in panel
    assert 'data-stop-url="/programs/panels/redis/members/redisinsight/stop"' in panel
    assert 'data-program-stop' not in panel
    assert 'data-member-start="redisinsight"' in panel


def test_redis_running_probe_uses_ping_and_closes_connection():
    panel = create_app({"TESTING": True}).extensions["home_panels"]["redis"]
    with patch("src.redis.home_panel.socket.create_connection") as connect:
        connection = connect.return_value.__enter__.return_value
        response = connection.makefile.return_value.__enter__.return_value
        response.readline.return_value = b"+PONG\r\n"
        assert panel.running()
        connect.assert_called_once_with(("redis", 6379), timeout=1)
        connection.sendall.assert_called_once_with(b"*1\r\n$4\r\nPING\r\n")
        connect.return_value.__exit__.assert_called_once()
        response.readline.return_value = b"-ERR unavailable\r\n"
        assert not panel.running()


def test_missing_or_stopped_redis_does_not_fail_panel():
    app = create_app({"TESTING": True})
    for error in (ConnectionRefusedError(), TimeoutError(), OSError("DNS unavailable")):
        with patch("src.redis.home_panel.socket.create_connection", side_effect=error), patch(
            "home.programs.start_program"
        ) as start:
            response = app.test_client().get("/programs/redis/panel-status")
        start.assert_not_called()
        assert response.status_code == 200
        assert b'Not running' in response.data
        assert response.headers['Cache-Control'] == 'no-store'


def test_status_reports_running_and_insight_still_launches_on_demand():
    app = create_app({"TESTING": True})
    with patch.object(home_panel, "running", return_value=True):
        response = app.test_client().get("/programs/redis/panel-status")
        assert b'variant="success"' in response.data
        assert b'Running' in response.data
    with patch("home.programs.start_program") as start:
        response = app.test_client().post("/programs/redisinsight/start")
        assert response.json == {"started": True}
        start.assert_called_once_with("redisinsight")

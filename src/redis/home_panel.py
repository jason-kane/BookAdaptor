"""Redis's Home panel, instantiated inside the Home Flask process.

Keep this directory a namespace package: an __init__.py here would shadow the
third-party redis client when the editor runs with src on its Python path.
"""
import socket

from flask import Blueprint, make_response, render_template
from markupsafe import Markup


class home_panel:
    """Provide Redis status and the nested Redis Insight launcher."""

    services = ("redis", "redisinsight")
    members = {
        "redis": {"name": "Redis", "image": "redis_insight.png", "owner": "redis",
                  "description": "Shared data and job queues.", "template": "redis/member_redis.html"},
        "redisinsight": {"name": "Redis Insight", "image": "redis_insight.png", "owner": "redis",
                         "description": "Inspect and manage Redis data.", "template": "redis/member_insight.html"},
    }

    def __init__(self, app):
        blueprint = Blueprint("redis_panel", __name__, template_folder="templates")
        blueprint.add_url_rule("/programs/redis/panel-status", "status", self.status)
        app.register_blueprint(blueprint)

    def running(self):
        """A bounded PING works without importing the third-party Redis client."""
        try:
            with socket.create_connection(("redis", 6379), timeout=1) as connection:
                connection.sendall(b"*1\r\n$4\r\nPING\r\n")
                with connection.makefile("rb") as response:
                    return response.readline(64) == b"+PONG\r\n"
        except OSError:
            return False

    def render(self):
        return Markup(render_template("redis/home_panel.html"))

    def status(self):
        response = make_response(render_template("redis/status.html", running=self.running()))
        response.headers["Cache-Control"] = "no-store"
        return response

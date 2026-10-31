"""PostgreSQL and pgAdmin controls for the independent Home dashboard."""
import socket
import struct

from flask import Blueprint, make_response, render_template
from markupsafe import Markup


class home_panel:
    services = ("postgres", "pgadmin")
    members = {
        "postgres": {"name": "PostgreSQL", "image": "postgres.svg", "owner": "postgres",
                     "description": "Chapter and asset database.", "template": "postgres/member_postgres.html"},
        "pgadmin": {"name": "pgAdmin 4", "image": "postgres.svg", "owner": "postgres",
                    "description": "Inspect and manage PostgreSQL data.", "template": "postgres/member_pgadmin.html"},
    }

    def __init__(self, app):
        blueprint = Blueprint("postgres_panel", __name__, template_folder="templates")
        blueprint.add_url_rule("/programs/postgres/panel-status", "status", self.status)
        app.register_blueprint(blueprint)

    def running(self):
        """Probe the PostgreSQL protocol without adding a database client to Home."""
        try:
            with socket.create_connection(("postgres", 5432), timeout=1) as connection:
                connection.sendall(struct.pack("!II", 8, 80877103))  # SSLRequest
                return connection.recv(1) in (b"S", b"N")
        except OSError:
            return False

    def render(self):
        return Markup(render_template("postgres/home_panel.html"))

    def status(self):
        response = make_response(render_template("postgres/status.html", running=self.running()))
        response.headers["Cache-Control"] = "no-store"
        return response

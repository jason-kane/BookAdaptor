"""Home dashboard; runs without the artifact editor installed."""
import json
import os
from pathlib import Path

from flask import Flask, request

from home.views import bp
from home.service_info import PANEL_SERVICES, TABLE_ROWS
from home.service_members import MEMBER_METADATA
from src.redis.home_panel import home_panel
from src.postgres.home_panel import home_panel as postgres_home_panel


def create_app(config=None):
    app = Flask(__name__, static_url_path="/home-static")
    app.config["TODO_DIR"] = os.environ.get("TODO_DIR", "/data/todo")
    if config:
        app.config.update(config)
    app.register_blueprint(bp)
    app.extensions["home_panels"] = {"redis": home_panel(app), "postgres": postgres_home_panel(app)}
    app.extensions["home_panel_services"] = {
        **PANEL_SERVICES,
        **{name: panel.services for name, panel in app.extensions["home_panels"].items()},
    }
    # Open entries describe the panel's UI, rather than its dependency graph.
    app.extensions["home_panel_members"] = {
        panel_id: tuple(dict.fromkeys(row["service"] for row in TABLE_ROWS if row["panel"] == panel_id))
        for panel_id in app.extensions["home_panel_services"]
    }

    member_metadata = dict(MEMBER_METADATA)
    for panel_id, owned_panel in app.extensions["home_panels"].items():
        member_metadata.update(getattr(owned_panel, "members", {}))
        app.extensions["home_panel_members"][panel_id] = tuple(owned_panel.members)

    def panel_members(panel_id, title, thumbnail):
        result = []
        for index, service in enumerate(app.extensions["home_panel_members"][panel_id]):
            member = {"service": service, **member_metadata[service]}
            if index == 0:
                member.update(name=title, image=thumbnail)
            if member.get("owner") != panel_id:
                member.pop("template", None)
            result.append(member)
        return result

    app.jinja_env.globals["panel_members"] = panel_members

    def panel_destination(panel_id):
        return next((row for row in TABLE_ROWS if row["panel"] == panel_id
                     and (row.get("program") or row.get("path"))), None)

    app.jinja_env.globals["panel_destination"] = panel_destination

    @app.context_processor
    def panels():
        return {"home_panels": app.extensions["home_panels"], "service_table_rows": TABLE_ROWS}

    @app.route("/home-todo/", methods=["GET", "POST"])
    def todo():
        directory = Path(app.config["TODO_DIR"])
        directory.mkdir(parents=True, exist_ok=True)
        filename = directory / "home.json"
        if request.method == "POST":
            try:
                delta = json.loads(request.form.get("delta", ""))
            except ValueError:
                return "Invalid JSON", 400
            filename.write_text(json.dumps(delta, indent=2))
            return "", 204
        return app.response_class(filename.read_text() if filename.exists() else "{}",
                                  mimetype="application/json")

    return app

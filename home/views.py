import logging
import os
import subprocess

from flask import Blueprint, render_template, jsonify, url_for, abort, request

log = logging.getLogger(__name__)

bp = Blueprint(
    "home",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

bp.route("/")(lambda: render_template("home.html"))


@bp.get("/programs/<service_name>/running")
def program_running(service_name):
    from .programs import PROGRAMS, program_running
    if service_name not in PROGRAMS:
        abort(404)
    try:
        return jsonify(running=program_running(service_name))
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to check program: %s (%s)", service_name, exc)
        return jsonify(error="Unable to check program status."), 503


@bp.post("/programs/<service_name>/stop")
def stop_program(service_name):
    from .programs import PROGRAMS, stop_program
    if service_name not in PROGRAMS:
        abort(404)
    try:
        stop_program(service_name)
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to stop program: %s (%s)", service_name, exc)
        return jsonify(error="Unable to stop program. Please try again."), 503
    return jsonify(stopped=True)


@bp.get("/programs/<service_name>")
def program_page(service_name):
    from .programs import PROGRAMS
    if service_name not in PROGRAMS:
        abort(404)
    return render_template(
        "launch_program.html", program=PROGRAMS[service_name],
        start_url=url_for("home.start_program", service_name=service_name),
        status_url=url_for("home.program_status", service_name=service_name),
        workflow=request.args.get("workflow") if service_name == "comfyui" else None,
        service_image=url_for("static", filename="images/" + PROGRAMS[service_name]["image"]),
    )


@bp.post("/programs/<service_name>/start")
def start_program(service_name):
    from .programs import PROGRAMS, start_program
    if service_name not in PROGRAMS:
        abort(404)
    try:
        start_program(service_name)
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to start %s: %s", service_name, exc)
        return jsonify(error=f"Unable to start {PROGRAMS[service_name]['name']}. Please try again."), 503
    return jsonify(started=True)


@bp.post("/programs/workers/start")
def start_workers():
    from .programs import start_services

    services = ["video_worker", "websocket"]
    if (request.get_json(silent=True) or {}).get("comfyui") is True:
        services.append("comfyui")
    try:
        start_services(services, ("video_worker", "websocket", "comfyui"))
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to start generation services: %s", exc)
        return jsonify(error="Unable to start generation services. Please try again."), 503
    return jsonify(started=True)


@bp.post("/programs/panels/<panel_id>/members/<service_name>/stop")
def stop_member(panel_id, service_name):
    from flask import current_app
    from .programs import stop_service

    members = current_app.extensions["home_panel_members"].get(panel_id, ())
    if service_name not in members:
        abort(404)
    try:
        stop_service(service_name, members)
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to stop member %s: %s", service_name, exc)
        return jsonify(error="Unable to stop this service. Please try again."), 503
    return jsonify(stopped=True)


@bp.get("/programs/<service_name>/status")
def program_status(service_name):
    from .programs import PROGRAMS, program_ready
    if service_name not in PROGRAMS:
        abort(404)
    return jsonify(ready=program_ready(service_name))


@bp.route("/rerun_tests", methods=["POST"])
def rerun_tests():
    """
    We want to poke docker compose to trigger a rerun of the tests.

    we have the docker.socket mounted specifically to allow this flavor of mischief.
    """
    # this blocks until the tests are done, but that's fine for now.  We can
    # make it async later if we want.

    out = subprocess.run(
        [
            "docker",
            "compose",
            "--file", "/docker-compose.yaml",
            "--profile", "test",
            "--project-name", "bookadaptor",
            "run",
            "--rm",
            "--no-build",
            "test"
        ],
        capture_output=True,
        text=True,
    )

    log.info(f"Rerun tests completed with return code {out.returncode}")
    log.info(f"Rerun tests stdout: {out.stdout}")
    log.info(f"Rerun tests stderr: {out.stderr}")
    return "", 204


@bp.get("/programs/panels/<panel_id>/info")
def panel_info(panel_id):
    from flask import current_app, make_response
    from .service_info import snapshot

    services = current_app.extensions["home_panel_services"].get(panel_id)
    if services is None:
        abort(404)
    try:
        data = snapshot(services)
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        log.exception("Unable to read Compose information for %s", panel_id)
        response = make_response("Unable to read Compose information. Please try again.", 503)
    else:
        response = make_response(render_template("service_info.html", **data))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.get("/programs/panels/versions")
def versions():
    from flask import current_app
    from .service_info import panel_versions

    response = jsonify(panel_versions(current_app.extensions["home_panel_services"]))
    response.headers["Cache-Control"] = "no-store"
    return response


@bp.post("/programs/panels/<panel_id>/members/<service_name>/start")
def start_member(panel_id, service_name):
    from flask import current_app
    from .programs import start_service

    members = current_app.extensions["home_panel_members"].get(panel_id, ())
    if service_name not in members:
        abort(404)
    try:
        start_service(service_name, members)
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to start member %s: %s", service_name, exc)
        return jsonify(error="Unable to start this service. Please try again."), 503
    return jsonify(started=True)

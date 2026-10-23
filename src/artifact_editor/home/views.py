import glob
import os
import shutil
import subprocess

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    jsonify,
    url_for,
    abort,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)


log = logger.log(__name__)

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
        log.error("Unable to check program", service=service_name, error=str(exc))
        return jsonify(error="Unable to check program status."), 503


@bp.post("/programs/<service_name>/stop")
def stop_program(service_name):
    from .programs import PROGRAMS, stop_program
    if service_name not in PROGRAMS:
        abort(404)
    try:
        stop_program(service_name)
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to stop program", service=service_name, error=str(exc))
        return jsonify(error="Unable to stop program. Please try again."), 503
    return jsonify(stopped=True)


@bp.get("/programs/glances")
def glances():
    return program_page("glances")


def program_page(service_name):
    from .programs import PROGRAMS
    return render_template("launch_program.html", program=PROGRAMS[service_name],
                           start_url=url_for(f"home.start_{service_name}"),
                           status_url=url_for(f"home.{service_name}_status"))


@bp.get("/programs/redisinsight")
def redisinsight():
    return program_page("redisinsight")


@bp.post("/programs/redisinsight/start")
def start_redisinsight():
    from .programs import start_program
    try:
        start_program("redisinsight")
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to start Redis Insight", error=str(exc))
        return jsonify(error="Unable to start Redis Insight. Please try again."), 503
    return jsonify(started=True)


@bp.get("/programs/redisinsight/status")
def redisinsight_status():
    from .programs import program_ready
    return jsonify(ready=program_ready("redisinsight"))


@bp.post("/programs/glances/start")
def start_glances():
    from .programs import start_glances
    try:
        start_glances()
    except (OSError, RuntimeError, ValueError) as exc:
        log.error("Unable to start Glances", error=str(exc))
        return jsonify(error="Unable to start Glances. Please try again."), 503
    return jsonify(started=True)


@bp.get("/programs/glances/status")
def glances_status():
    from .programs import glances_ready
    return jsonify(ready=glances_ready())


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
            "--profile", "test",
            "--project-name", "bookadaptor",
            "run",
            "--rm",
            "test"
        ],
        capture_output=True,
        text=True,
    )

    log.info(f"Rerun tests completed with return code {out.returncode}")
    log.info(f"Rerun tests stdout: {out.stdout}")
    log.info(f"Rerun tests stderr: {out.stderr}")
    return "", 204

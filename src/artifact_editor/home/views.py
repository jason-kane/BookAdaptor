import glob
import os
import shutil
import subprocess

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)

# from artifact_editor.chapter.chapter import Chapter
# from artifact_editor.chapter import htmx as chapter_htmx
from artifact_editor.author.author import Author
# , Book


log = logger.log(__name__)

bp = Blueprint(
    "home",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

bp.route("/")(lambda: render_template("home.html"))


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

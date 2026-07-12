import glob
import os
import shutil

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
#from artifact_editor.chapter.chapter import Chapter
#from artifact_editor.chapter import htmx as chapter_htmx
from artifact_editor.author.author import Author
#, Book


log = logger.log(__name__)

bp = Blueprint(
    'home',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

bp.route("/")(lambda: render_template("home.html"))
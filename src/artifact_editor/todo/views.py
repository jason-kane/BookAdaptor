import glob
import hashlib
import os
import json
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
#from artifact_editor.author.author import Author
#, Book


log = logger.log(__name__)

os.makedirs(const.TODO_DIR, exist_ok=True)


bp = Blueprint(
    'todo',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

def key_to_filename(key):
    """
    Convert a key to a filename for storing the todo.
    """
    # basename to twart mischief
    return os.path.join(
        const.TODO_DIR,
        os.path.basename(key) + ".json"
    )

@bp.route("/", methods=["POST"])
def todo_save():
    """
    We are receiving a change to one of the todos.
    """
    # this was a bad idea.
    # referer = request.headers.get("Referer")
    
    delta = request.form.get("delta")   
    key = request.form.get("key")

    with open(key_to_filename(key), "w") as f:
        as_json = json.loads(delta)  # validate that it's valid JSON
        f.write(json.dumps(as_json, indent=2))

    return "", 204

@bp.route("/", methods=["GET"])
def todo_load():
    """
    Retrieve the todo for the current page, if it exists.
    We're identifying which todo by the "key" query parameter.
    """    
    key = request.args.get("key")
    filename = key_to_filename(key)

    if os.path.exists(filename):
        with open(filename, "r") as f:
            delta = f.read()
    else:
        with open(filename, "w") as f:
            f.write("{}")
        delta = "{}"
    return delta, 200

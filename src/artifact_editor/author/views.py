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
# from artifact_editor import (
#     config,
#     tools,
# )
# from artifact_editor.chapter.chapter import Chapter
# from artifact_editor.chapter import htmx as chapter_htmx


from .author import Author

log = logger.log(__name__)

bp = Blueprint(
    'author',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

@bp.route("/")
def author_base(author):
    author = Author(author)

    # some special cases to accomidate our
    # position at the top of the URL tree
    if author.name == "favicon.ico":
        log.warning('favicon')

        return send_from_directory(
            os.path.join(
                const.STATIC_DIR
            ), 
            "favicon.ico"
        )
    
    books = []

    for title in sorted(os.listdir(author.authordir)):
        if title in ['__pycache__', ]:
            continue

        bookdir = os.path.join(author.authordir, title)
        books.append((title, bookdir))           

    return render_template(
        "author.html", 
        author=author,
        books=books
    )


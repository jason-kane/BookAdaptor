import glob
import os
import shutil
import json

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
)

import const
import logger


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


def title_to_directory(book_title):
    book_title = book_title.replace(';', '')
    book_title = book_title.replace(',', '')
    return book_title

# http://localhost:8080/library/Mary%20Wollstonecraft%20Shelley/add
@bp.route("/add", methods=['POST'])
def add_book(author):
    author = Author(author)
    book_title = request.form.get('book_name')

    book_dir = os.path.join(
        author.authordir,
        title_to_directory(book_title),
    )
    os.makedirs(book_dir, exist_ok=True)

    with open(os.path.join(book_dir, 'config.json'), 'w') as h:
        h.write(json.dumps({}))

    return f"Created entry for {book_title}", 200

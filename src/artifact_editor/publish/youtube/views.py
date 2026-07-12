import os

from flask import Blueprint


bp = Blueprint(
    "youtube",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.route('/')
def base(author, title, chapter_number, language):
    return "GitHub Publish View"
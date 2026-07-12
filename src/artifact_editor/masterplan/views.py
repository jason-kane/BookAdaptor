
import os

from flask import Blueprint

from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
import logger
from artifact_editor import tools

from . import htmx, masterplan

log = logger.log(__name__)

bp = Blueprint(
    'masterplan', 
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)

# /H.%20P.%20Lovecraft/Cool%20Air/0001/masterplan/actions/regenerate_masterplan
@bp.route("regenerate", methods=["POST"])
def regenerate(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)
    
    masterplan.delete_masterplan(chapter)
    log.info("Regenerate master plan for %s", chapter)
    plan = masterplan.generate_masterplan(chapter)
    masterplan.save_masterplan(chapter, plan)

    return htmx.regenerate_masterplan_button(chapter)

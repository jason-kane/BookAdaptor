
import os

from flask import Blueprint, g

import logger
from artifact_editor import tools
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter

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
def regenerate():
    mp = masterplan.Masterplan(g.chapter)
    mp.delete()
    log.info("Regenerate master plan for %s", g.chapter)
    mp.get(force=True)
    mp.save()

    return htmx.regenerate_masterplan_button(g.chapter)

import os
from flask import Blueprint

from artifact_editor.publish.github.views import bp as github
from artifact_editor.publish.youtube.views import bp as youtube
from artifact_editor.publish.tiktok.views import bp as tiktok


bp = Blueprint(
    "publish",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

bp.register_blueprint(
    youtube,
    url_prefix="/youtube",
)

bp.register_blueprint(
    github,
    url_prefix="/github",
)

bp.register_blueprint(
    tiktok,
    url_prefix="/tiktok",
)
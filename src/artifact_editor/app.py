import typing as t

from flask import Flask, g
from flask_cors import CORS
from redis.maint_notifications import MaintNotificationsConfig
from werkzeug.routing import BaseConverter

from artifact_editor.author.author import Author

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from artifact_editor.chapter.chapter import Chapter

import logger
import text_to_image
from artifact_editor.cache import cache

from artifact_editor.phrases.views import bp as phrases
from artifact_editor.author.views import bp as author
from artifact_editor.book.views import bp as book
from artifact_editor.chapter.views import bp as chapter
from artifact_editor.characters.views import bp as characters
from artifact_editor.frames.views import bp as frames
from artifact_editor.home.views import bp as home
from artifact_editor.images.views import bp as images
from artifact_editor.library.views import bp as library
from artifact_editor.masterplan.views import bp as masterplan
from artifact_editor.music.views import bp as music
from artifact_editor.publish.views import bp as publish
from artifact_editor.publish.youtube.oauth import bp as youtube_oauth
from artifact_editor.publish.tiktok.oauth import bp as tiktok_oauth
from artifact_editor.static import bp as static
from artifact_editor.styles.views import bp as styles
from artifact_editor.therapy.views import bp as therapy
from artifact_editor.workflow_templates.views import bp as workflow_templates
from artifact_editor.todo.views import bp as todo
from artifact_editor.typography.views import bp as typography
from artifact_editor.video.views import bp as video
from artifact_editor.textoviva.views import bp as textoviva


if t.TYPE_CHECKING:
    # if you're going to have globals, at least
    # type them.
    class TypedGlobals:
        chapter: t.Optional[Chapter] = None
        author: t.Optional[Author] = None
        title: t.Optional[str] = None
        number: t.Optional[int] = None
        language: t.Optional[str] = None

    g: TypedGlobals

# # register nested blueprints
# chapter.register_blueprint(
#     text,
#     url_prefix="/text",
# )

chapter.register_blueprint(
    characters, 
    url_prefix="/characters",
)

chapter.register_blueprint(
    phrases,
    url_prefix="/phrases",
)

chapter.register_blueprint(
    images,
    url_prefix="/images",
)

chapter.register_blueprint(
    typography,
    url_prefix="/typography",
)

chapter.register_blueprint(
    masterplan,
    url_prefix="/masterplan",
)

chapter.register_blueprint(
    frames,
    url_prefix="/frames",
)

chapter.register_blueprint(
    music,
    url_prefix="/music",
)

chapter.register_blueprint(
    video,
    url_prefix="/video",
)

chapter.register_blueprint(
    publish,
    url_prefix="/publish",
)

chapter.register_blueprint(
    textoviva,
    url_prefix="/textoviva",
)

book.register_blueprint(
    chapter,
    url_prefix="/<chapter_number>/<language>"
)

library.register_blueprint(
    author,
    url_prefix="/<author>",
)

library.register_blueprint(
    book,
    url_prefix="/<author>/<title>"
)


class RegexConverter(BaseConverter):
    """
    default regex is "[^/]+"
    """
    def __init__(self, map, *args: t.Any, **kwargs: t.Any):
        super(RegexConverter, self).__init__(map)
        if args:
            self.regex = args[0]
        else:
            print('RegexConverter', args, kwargs)


def create_app(config_dict: t.Optional[dict] = None) -> Flask:
    app = Flask(__name__)
    app.register_blueprint(youtube_oauth)
    app.register_blueprint(tiktok_oauth)

    CORS(app, expose_headers=["HX-Push-Url", "HX-Trigger", "HX-Redirect"])

    app.config['MAX_CONTENT_LENGTH'] = None
    app.config['MAX_FORM_MEMORY_SIZE'] = 50 * 1024 * 1024
    app.config['CACHE_KEY_PREFIX'] = 'aec:'

    app.config['CACHE_TYPE'] = 'RedisCache'  # Simple in-memory cache
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # Cache timeout in seconds
    app.config['CACHE_REDIS_HOST'] = 'redis'  # Redis server host

    if config_dict:
        app.config.update(config_dict)

    app.log = logger.log(__name__)

    CACHE_OPTIONS = {
        "maint_notifications_config": MaintNotificationsConfig(enabled=False)
    }

    app.url_map.converters['regex'] = RegexConverter
    cache.init_app(app, config=CACHE_OPTIONS)

    print(app.url_map)
    print('Importing blueprints...')

    text_to_image.registry.search(gpu=False)
    app.register_blueprint(
        library,
        url_prefix="/library"
    )

    # top level blueprints
    app.register_blueprint(
        therapy,
        url_prefix="/therapy"
    )

    app.register_blueprint(
        workflow_templates,
        url_prefix="/workflow_templates"
    )
    
    app.register_blueprint(
        styles,
        url_prefix="/styles"
    )

    app.register_blueprint(static)

    app.register_blueprint(
        todo,
        url_prefix="/todo"
    )

    app.register_blueprint(
        home,
        url_prefix="/"
    )

    #print(app.url_map)
    print('Finished importing blueprints.')

    return app

from flask import Flask, Blueprint
from artifact_editor.cache import cache
import text_to_image
from flask_cors import CORS
from werkzeug.routing import BaseConverter
# import logger
from flask import request_started
import typing as t
import sys

import datetime, sys
import structlog
import logger

#from structlog import wrap_logger
#from structlog.stdlib import filter_by_level


#from rich.console import Console

# Create a console with a fixed wide width
# 255 is a common choice for logs, or use -1 to attempt auto-detection
#custom_console = Console(width=160, force_terminal=True)

# structlog.configure(
#     processors=[
#         structlog.dev.ConsoleRenderer(
#             # Pass the custom console to ensure the width is honored
            
#         )
#     ]
# )

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

# persistent_path = os.getenv("PERSISTENT_STORAGE_DIR", os.path.dirname(os.path.realpath(__file__)))
 

def create_app():
    # configure structlog _first_.
    # logging.basicConfig(
    #     format="%(message)s",
    #     stream=sys.stdout,
    #     level=logging.INFO
    # )

    # def add_timestamp(_, __, event_dict):
    #     event_dict["timestamp"] = datetime.datetime.now().isoformat()
    #     return event_dict

    # def censor_password(_, __, event_dict):
    #     pw = event_dict.get("password")
    #     if pw:
    #         event_dict["password"] = "*CENSORED*"
    #     return event_dict
    
    # structlog.configure(
    #     processors=[
    #         # structlog.dev.ConsoleRenderer(
    #         #     exception_formatter=structlog.dev.RichTracebackFormatter(console=custom_console),
    #         # ),
    #         filter_by_level,
    #         structlog.processors.MaybeTimeStamper(fmt="iso"),
    #         # censor_password,
    #         structlog.processors.dict_tracebacks,
    #         structlog.processors.KeyValueRenderer(
    #             key_order=["event", ]
    #         )

    #         # structlog.processors.JSONRenderer(),  # indent=2, sort_keys=True)
    #     ],
        
    #     # processors=[
    #     #     structlog.processors.KeyValueRenderer(
    #     #         key_order=["timestamp", "event", "request_id"]
    #     #     )
    #     # ],
    #     context_class=structlog.threadlocal.wrap_dict(dict),
    #     logger_factory=structlog.stdlib.LoggerFactory(),
    # )

    app = Flask(__name__)
    CORS(app, expose_headers=["HX-Push-Url", "HX-Trigger", "HX-Redirect"])
    # request_started.connect(bind_request_details, app)
    app.config['MAX_CONTENT_LENGTH'] = None
    app.config['MAX_FORM_MEMORY_SIZE'] = 50 * 1024 * 1024
    app.config['CACHE_KEY_PREFIX'] = 'aec:'

    app.config['CACHE_TYPE'] = 'RedisCache'  # Simple in-memory cache
    app.config['CACHE_DEFAULT_TIMEOUT'] = 300  # Cache timeout in seconds
    app.config['CACHE_REDIS_HOST'] = 'redis'  # Redis server host

    app.log = logger.log(__name__)
    # app.log = wrap_logger(
    #     logging.getLogger(__name__),
    #     processors=[
    #         filter_by_level,
    #         structlog.processors.MaybeTimeStamper(fmt="iso"),
    #         # censor_password,
    #         structlog.processors.dict_tracebacks,
    #         # structlog.processors.JSONRenderer() # indent=2, sort_keys=True
    #         structlog.processors.KeyValueRenderer(
    #             key_order=["timestamp", "event"]
    #         )
    #     ]
    # )
    # logger.log(__name__)

    app.url_map.converters['regex'] = RegexConverter
    cache.init_app(app)
    # db_path = os.path.join(persistent_path, "sqlite.db")

    # app.config["SQLALCHEMY_DATABASE_URI"] = f'sqlite:///{db_path}'
    # app.config["SQLALCHEMY_ECHO"] = False
    # app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # db = SQLAlchemy()
    print(app.url_map)
    print('Importing blueprints...')

    from artifact_editor.home.views import bp as home
    from artifact_editor.todo.views import bp as todo

    from artifact_editor.author.views import bp as author
    from artifact_editor.library.views import bp as library
    from artifact_editor.text.views import bp as text
    from artifact_editor.characters.views import bp as characters
    from artifact_editor.audio.views import bp as audio
    from artifact_editor.images.views import bp as images
    from artifact_editor.typography.views import bp as typography
    from artifact_editor.frames.views import bp as frames
    from artifact_editor.video.views import bp as video
    from artifact_editor.book.views import bp as book
    from artifact_editor.chapter.views import bp as chapter
    from artifact_editor.masterplan.views import bp as masterplan
    from artifact_editor.publish.views import bp as publish
    from artifact_editor.therapy.views import bp as therapy
    from artifact_editor.styles.views import bp as styles
    from artifact_editor.music.views import bp as music
    from artifact_editor.static import bp as static

    text_to_image.registry.search(gpu=False)

    app.register_blueprint(
        therapy,
        url_prefix="/therapy"
    )
    
    app.register_blueprint(
        styles,
        url_prefix="/styles"
    )

    app.register_blueprint(static)

    chapter.register_blueprint(
        text,
        url_prefix="/text",
    )

    chapter.register_blueprint(
        characters, 
        url_prefix="/characters",
    )
    
    chapter.register_blueprint(
        audio,
        url_prefix="/audio",
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

    app.register_blueprint(
        library,
        url_prefix="/library"
    )

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


# if __name__ == "__main__":
#     app = create_app()
#     app.run(debug=True)

# import artifact_editor.routes
#from artifact_editor.app import *
# from app import models

# db.init_app(app)

# with app.app_context():
#     db.create_all()
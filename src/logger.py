import os
import logging
import logging.config
import const
from rich.logging import RichHandler
from pyfiglet import Figlet

import structlog
import sys

logging.basicConfig(format="%(message)s", stream=sys.stdout, level=logging.DEBUG)

# pillow is noisy as hell.
logging.getLogger("PIL").setLevel(logging.INFO)

# if const.DEBUG:
#     level = logging.DEBUG
# else:
#     level = logging.INFO


# LOGGING_CONFIG = { 
#     'version': 1,
#     'disable_existing_loggers': False,
#     'formatters': { 
#         'standard': { 
#             'format': '%(asctime)s [%(levelname)s] %(prefix)s%(name)s: %(message)s'
#         },
#     },
#     'handlers': { 
#         'default': {
#             'level': 'DEBUG',
#             'formatter': 'standard',
#             'class': 'rich.logging.RichHandler',
#             'rich_tracebacks': True
#         },
#     },
#     'loggers': { 
#     # 'logger name': {
#     #     'level': str enum[DEBUG, INFO, etc..] }
#     #     'propagate': The propagation setting of the logger
#     #     'filters': A list of ids or filter instances of the filters for this logger
#     #     'handlers': A list of ids of the handlers for this logger
#     # }
#         '': {  # root logger
#             'handlers': ['default'],
#             'level': 'INFO',
#             'propagate': False
#         },
#         'artifact_editor': { 
#             'handlers': ['default'],
#             'level': 'INFO',
#             'propagate': False
#         },
#         'frames': { 
#             'handlers': ['default'],
#             'level': 'DEBUG',
#             'propagate': False
#         },
#         'neobreaker': { 
#             'handlers': ['default'],
#             'level': 'INFO',
#             'propagate': False
#         },
#         '__main__': {  # if __name__ == '__main__'
#             'handlers': ['default'],
#             'level': 'INFO',
#             'propagate': False
#         },
#     } 
# }

# from structlog import PrintLogger

# logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')
# logging.config.dictConfig(LOGGING_CONFIG)
# rich_handler = RichHandler(rich_tracebacks=True)
# logging.getLogger().handlers = [rich_handler]
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer(
            colors=True,
            force_colors=True,
            exception_formatter=structlog.dev.rich_traceback
        ),
    ],
    logger_factory=structlog.PrintLoggerFactory(),
)

try:
    columns, lines = os.get_terminal_size()
except OSError:
    columns = 80
    lines = 24

fig = Figlet(font='slant', width=columns)

logger = structlog.get_logger()
def log(name: None):
    return logger.bind(name=name)

    # # logger = logging.getLogger(name)
    # return structlog.wrap_logger(
    #     logger=PrintLogger(name),
    #     wrapper_class=structlog.stdlib.BoundLogger,
    #     # structlog.make_filtering_bound_logger(level)
    # )
    # logger.fig = lambda txt: logger.info('\n' + fig.renderText(txt)) 
    # return logger


log(__name__).info('Logger loaded.')
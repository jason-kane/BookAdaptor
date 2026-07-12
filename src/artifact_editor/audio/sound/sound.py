from const import SOUND_DIR
import os

import logger
log = logger.log(__name__)


def list_sounds():
    """List the sound files that have been loaded locally."""
    if os.path.exists(SOUND_DIR):
        return [
            f for f in os.listdir(SOUND_DIR)
            if os.path.isfile(os.path.join(SOUND_DIR, f))
            and f.lower().endswith(('.wav', '.mp3', '.ogg'))
        ]
    else:
        log.error(f"Sound directory does not exist: {SOUND_DIR}")
        return []
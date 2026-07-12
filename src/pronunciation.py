import json
import os
import logger
import const

log = logger.log(__name__)


def get_global_pronunciations(bookdir):
    """
    Load the global pronunciations from the book's global_pronunciations.json file.
    """
    global_pronunciations = {}
    global_pronunciations_file = os.path.join(
        const.LIBRARY_DIR, bookdir.lstrip('/'), "global_pronunciations.json"
    )
    
    if os.path.exists(global_pronunciations_file):
        with open(global_pronunciations_file, "r") as f:
            try:
                global_pronunciations = json.load(f)
            except json.JSONDecodeError:
                log.error("Failed to decode JSON from global_pronunciations.json")
    
    return global_pronunciations

def save_global_pronunciations(bookdir, pronunciations):
    """
    Save the global pronunciations to the book's global_pronunciations.json file.
    """
    global_pronunciations_file = os.path.join(
        const.LIBRARY_DIR, bookdir.lstrip('/'), "global_pronunciations.json"
    )
    
    with open(global_pronunciations_file, "w") as f:
        json.dump(pronunciations, f, indent=4)
        log.info("Global pronunciations saved to %s", global_pronunciations_file)
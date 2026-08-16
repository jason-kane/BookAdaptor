import os
import json
import logger
import const

log = logger.log(__name__)


def has_audio_cache(chapterdir):
    """
    Check if the audio cache file exists for the given chapter directory.
    """
    audio_cache_fn = get_audio_cache_fn(chapterdir)
    return os.path.exists(audio_cache_fn)


def get_audio_cache_fn(chapterdir):
    return os.path.join(
        const.LIBRARY_DIR,
        chapterdir,
        "audio_cache.json"
    )


def get_audio_cache(chapterdir):
    audio_cache = []
    audio_cache_fn = get_audio_cache_fn(chapterdir)
    
    if os.path.exists(audio_cache_fn):
        with open(audio_cache_fn) as h:
            audio_cache = json.load(h)

    return audio_cache


def save_audio_cache(chapterdir, contents):
    audio_cache_fn = get_audio_cache_fn(chapterdir)

    with open(audio_cache_fn, 'w') as h:
        json.dump(contents, h, indent=4)


def delete_audio_cache(chapterdir):
    audio_cache_fn = get_audio_cache_fn(chapterdir)
    if os.path.exists(audio_cache_fn):
        os.remove(audio_cache_fn)
        log.info(f"Deleted audio cache: {audio_cache_fn}")
    else:
        log.info(f"No audio cache to delete at: {audio_cache_fn}")


def get_audio_cache_phrase(chapterdir, phrase_id):
    """
    Get the audio cache for a specific phrase ID.
    Returns None if the phrase ID is not found in the cache.
    """
    audio_cache = get_audio_cache(chapterdir)
    
    for chapter in audio_cache:
        for segment in chapter.get("audio_segments", []):
            if segment.get("id") == phrase_id:
                return segment

    return None

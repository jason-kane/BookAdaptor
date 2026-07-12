import fcntl
import hashlib
import json
import re
import os
from time import sleep

from more_itertools import first

import const
import logger
log = logger.log(__name__)


def apply_global_pronunciations(chapter, text):
    """
    Apply the global pronunciations to the given text.

    This replaces words in the text with their IPA pronunciations
    as defined in the book's global_pronunciations.json file.
    """
    pronunciations = get_global_pronunciations(chapter)

    replacements = {}
    # longest to shortest
    for key, v in sorted(
        pronunciations.items(),
        key=lambda item: len(item[1]['word']),
        reverse=True
    ):
        word = v['word']
        pron = v['pronunciation']
        
        if pron.strip() == "":
            continue  # skip empty pronunciations?  this may be a misfeature.

        # for kokoro, this is how you tell it to pronounce
        # 'word' as 'pron' where pron is in IPA.
        final = f"[{word}](/{pron}/)"

        key = hashlib.sha256(word.encode()).hexdigest()[:8]
        replacements[key] = final

        # Replace whole words only, we are putting in a hash as a placeholder.
        text = re.sub(r'\b[_—]?' + re.escape(word) + r'[—_]?\b', key, text, flags=re.IGNORECASE)
    
    for key in replacements:
        text = text.replace(key, replacements[key])

    return text

 
def word_to_key(word):
    for (old, new) in [
        ("=", "-equals-"),
        ("?", "_"),
        ("—", "--"),
        ("'", "-h-"),
        (" ", "-s-"),
        (".", "-p-")
    ]:
        word = word.replace(old, new)

    return word.strip()

def add_word_pronunciation(chapter, word, pronunciation=""):
    """
    Add a word pronunciation in the book's global_pronunciations.json file.

    These are mapping from english words to their IPA pronunciations.  This is
    used to help the TTS engine pronounce unusual words correctly.

    Pronunciations can vary based on the previous word, or more precisely the
    previous syllable.  We aren't doing that yet, but 'after' is here
    to support that eventual feature.
    """
    pronunciations = get_global_pronunciations(chapter)
    
    key = word_to_key(word)

    # we need [word](/pronunciation/) to be safe for Kotoro.
    word = word.replace("[", "").replace("]", "").strip()
    pronunciation = pronunciation.replace("/", " ").strip()

    pronunciations[key] = {
        'word': word,
        'pronunciation': pronunciation,
        'after': ""
    }
    log.info(f'Added/Updated pronunciation for "{word}": "{pronunciation}"')
    save_global_pronunciations(chapter, pronunciations)


def global_pronunciation_list(chapter: 'Chapter'):
    """
    returns a list of dict containing:
    {
        "key": key
        "word": word,
        "pronunciation": pronunciation,
        "after": ""
    }
    """
    pronunciation_dict = get_global_pronunciations(chapter)

    with_complete_pronunciation = []
    missing_pronunciation = []
    for key in sorted(list(pronunciation_dict.keys())):
        new_key = word_to_key(pronunciation_dict[key]['word'])

        if key != new_key:
            # what _should_ the key be with the current algorithm?
            log.warning(f'Key "{key}" does not match calculated key for word "{pronunciation_dict[key]["word"]}".  This entry must be corrected.')
            
            # create a new entry with the data from the current entry
            pronunciation_dict[new_key] = pronunciation_dict.pop(key)

            # then fix the key inside the entry.
            pronunciation_dict[new_key]['key'] = new_key
            key = new_key

            save_global_pronunciations(chapter, pronunciation_dict)

        pronunciation_entry = {
            "key": key,
            "word": pronunciation_dict[key]['word'],
            "pronunciation": pronunciation_dict[key]['pronunciation'],
            "after": pronunciation_dict[key].get('after', "")
        }

        if pronunciation_entry["pronunciation"].strip() == "":
            missing_pronunciation.append(pronunciation_entry)
        else:
            with_complete_pronunciation.append(pronunciation_entry)

    # we want the incomplete grouped up at the bottom, nearest the action
    # buttons.  These are sorted by the values of 'key', TODO: that isn't quite
    # right, it should be sorted by the same value we display.
    return with_complete_pronunciation + missing_pronunciation


def get_global_pronunciations(chapter):
    """
    Load the global pronunciations from the book's global_pronunciations.json file.
    """
    global_pronunciations = {}
    global_pronunciations_file = os.path.join(
        const.LIBRARY_DIR, 
        chapter.bookdir.lstrip('/'),
        "global_pronunciations.json"
    )

    if not os.path.exists(global_pronunciations_file):
        log.info('No global_pronunciations.json file found at %s', global_pronunciations_file)
        save_global_pronunciations(chapter, {})
        return {}

    while not global_pronunciations:
        # reading, a shared lock is fine.
        descriptor = open(global_pronunciations_file, 'r')
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        log.info('Loading global pronunciations from %s', global_pronunciations_file)   
        if os.path.exists(global_pronunciations_file):
            with open(global_pronunciations_file, "r") as f:
                try:
                    global_pronunciations = json.load(f)
                    break
                except json.JSONDecodeError:
                    log.error("Failed to decode JSON from global_pronunciations.json")
                    raw = f.read()
                    if raw.strip() == "":
                        log.info("File is empty, initializing to empty dictionary.")
                        global_pronunciations = {}
                        save_global_pronunciations(chapter, global_pronunciations)
                    else:
                        log.error(f"Raw content: {raw}")
        else:
            log.info('global_pronunciations.json file not found during read, retrying...')
            sleep(0.1)  # wait a bit before retrying

        # release our lock
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        log.info('Lock released')

    if not isinstance(global_pronunciations, dict):
        log.error("global_pronunciations.json is not a dictionary, resetting.")
        global_pronunciations = {}
        save_global_pronunciations(chapter, global_pronunciations)
    
    all_keys = sorted(list(global_pronunciations.keys()))
    log.info(f'keys(): {all_keys}')
    # if global_pronunciations:
    #     log.info(f'type of first entry: {type(global_pronunciations[all_keys[0]])}')
    #     log.info(f'first entry: {global_pronunciations[all_keys[0]]}')
        
    #     # if isinstance(global_pronunciations[all_keys[0]], str):
    #     #     # old format.  convert it.
    #     #     log.info("Converting old format global_pronunciations.json to new format.") 
    #     #     new_format = {}
    #     #     for key in all_keys:
    #     #         word = key
    #     #         pron = global_pronunciations[key]
    #     #         key = key.strip().replace("'", r"").replace(" ", "_").lower()
    #     #         new_format[key] = {
    #     #             'word': word,
    #     #             'pronunciation': pron,
    #     #             'after': ""
    #     #         }
    #     #     global_pronunciations = new_format

    #     save_global_pronunciations(chapter, global_pronunciations)

    log.info('Returning %d global pronunciations', len(global_pronunciations))
    return global_pronunciations


def save_global_pronunciations(chapter, pronunciations):
    """
    Save the global pronunciations to the book's global_pronunciations.json file.
    """
    global_pronunciations_file = os.path.join(
        const.LIBRARY_DIR, chapter.bookdir.lstrip('/'), "global_pronunciations.json"
    )

    # get an exclusive lock for writing
    log.info('Acquiring lock to save global pronunciations to %s', global_pronunciations_file)
    descriptor = open(global_pronunciations_file + ".lock", 'w')
    fcntl.flock(descriptor, fcntl.LOCK_EX)
    log.info('Lock acquired.')

    with open(global_pronunciations_file, "w") as f:
        json.dump(pronunciations, f, indent=4)
        log.info("Global pronunciations saved to %s", global_pronunciations_file)
    
    # release the lock
    fcntl.flock(descriptor, fcntl.LOCK_UN)
    log.info('Lock released')


# def save_pronunciation_filter(chapter, phrase_id, pronunciation_filter):
#     """
#     Cache the pronunciation for a specific phrase to the phrase XML

#     # TODO: get rid of this.  doing a get_book/save is a serious problem.
#     """
   
#     log.info(f'Saving pronunciation filter for {phrase_id}: {pronunciation_filter}')
#     phrase_xml = chapter.get_xml().find('phrase', attrs={'id': phrase_id})
#     if phrase_xml is not None:
#         phrase_xml.attrs['pronunciation'] = pronunciation_filter
#     else:
#         log.error(f'Phrase {phrase_id} not found in chapter {chapter}')
#     chapter.save_xml()

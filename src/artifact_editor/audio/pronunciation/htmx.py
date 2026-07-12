import base64
import hashlib
import os

from flask import (
    render_template,
    url_for,
)

import const
import logger
from artifact_editor.audio import utterances
from artifact_editor.tools import (
    generic_button,
)

from . import pronunciation

log = logger.log(__name__)


def find_unpronouncable_words_button(chapter):
    return generic_button(
        chapter.url,
        category="audio",
        tag="find_unpronouncable_words",
        cosmetic="Find Unpronouncable Words"
    )

def try_cmu_dict_button(chapter):
    return generic_button(
        chapter.url,
        category="audio",
        tag="try_cmu_dict",
        cosmetic="Try CMU Dictionary"
    )


# def plain_row(chapter, word_dict):
#     return render_template(
#         "plain_row.html",
#         chapter=chapter,
#         word=word_dict
#     )


def edit_row(chapter, key):
    """
    Present the editor for the given word.
    """
    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)
    if key in pronunciation_dict:
        p = pronunciation_dict[key]
    else:
        log.error(f"Key '{key}' not found in global pronunciations.")
        return "", 404

    syllables = []
    index = 0
    for syllable in utterances.syllables(p['pronunciation']):
        log.info(f'Syllable: {syllable}')
        syllables.append({
            "syllable": syllable,
            "index": index
        })
        index += 1

    pronunciation_wav = os.path.join(
        const.LIBRARY_DIR,
        chapter.bookdir.lstrip("/"),
        "pronunciation",
        f"{key}.wav"
    )
    
    cachebuster = ""
    if os.path.exists(pronunciation_wav):
        # cache buster is the modify time
        cachebuster = os.path.getmtime(pronunciation_wav)

    syllables_str = "-".join([s['syllable'] for s in syllables])

    # chapterurl, key, word, pronun
    return render_template(
        "edit_row.html",
        language=chapter.language,
        chapter=chapter,
        syllables=syllables_str,
        word=p['word'],
        pronun=p['pronunciation'],
        key=key,
        cachebuster=str(cachebuster),
    )


def global_pronunciation_table(chapter):
    pronunciation_dict = pronunciation.get_global_pronunciations(chapter)
    
    add_global_pronunciation_url = url_for(
        'library.book.chapter.audio.pronunciation.add_global_pronunciation',
        **chapter.kwargs,
    )

    return render_template(
        "plain_table.html",
        chapter=chapter,
        pronunciation_dict=pronunciation_dict,
        global_pronunciation_list=pronunciation.global_pronunciation_list(chapter),
        add_global_pronunciation_url=add_global_pronunciation_url
    )


# def global_pronunciation_table(chapterurl, chapterdir):
#     library.book.ir = os.path.join(chapterdir, "..", "..")
#     pronunciation_dict = pronunciation.get_global_pronunciations(library.book.ir)
   
#     for word, pron in sorted(pronunciation_dict.items()):
#         # escaped = word.replace("'", r"").replace(" ", "_").lower()
#         # ^ this hides problems.

#         # hx-vals='{{"word": "{escaped}", "pronunciation": this.value}}'
#         out = f"""
#             <tr id="pronounce_{escaped}">
#                 <input type="hidden" name="word" value="{word}">
#                 <td name="word">{word}</td>
#                 <td>
#                     <wa-input
#                         hx-post="/{chapterurl}/audio/actions/update_global_pronunciation"
#                         hx-target="#global_pronunciation"
#                         hx-swap="outerHTML"                        
#                         name="pronunciation"
#                         hx-include="#pronounce_{escaped}"
#                         value="{pron}"
#                         hx-trigger="change delay:125ms">
#                     </wa-input>
#                 </td>
#                 <td>
#                     <div class="wa-cluster">
#                     <wa-button
#                         hx-post="/{chapterurl}/audio/actions/delete_global_pronunciation"
#                         hx-target="#global_pronunciation"
#                         hx-swap="outerHTML"
#                         hx-vals='{{"word": "{escaped}"}}'
#                         value="{escaped}">Delete</wa-button>
#                     </div>
#                 </td>
#             </tr>"""

#     # and the widgets to add new entries
#     out += f"""
#             <tr>
#                 <td><wa-input name="add_word" placeholder="Word"></wa-input></td>
#                 <td><wa-input name="add_pron" placeholder="IPA Pronunciation"></wa-input></td>
#                 <td><wa-button
#                         hx-post="/{chapterurl}/audio/actions/add_global_pronunciation"
#                         hx-target="#global_pronunciation"
#                         hx-include="[name='add_word'], [name='add_pron']"
#                         hx-swap="outerHTML">Add</wa-button></td>
#             </tr>
#         </table>"""

#     return out


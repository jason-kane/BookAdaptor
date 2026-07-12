from flask import Blueprint, make_response, render_template, request, send_file
import os

from artifact_editor.chapter.chapter import Chapter
from artifact_editor.author.author import Author

bp = Blueprint(
    "sound",
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    )
)

@bp.route("/select_local_sound", methods=["POST"])
def select_local_sound(author, title, chapter_number, language):
    author = Author(author)
    chapter = Chapter(author=author, title=title, number=chapter_number, language=language)
                      
    sound_file = request.form.get("sound_file")

    # choose the selected sound file for this <sound> element.
    sound_xml = chapter.get_sound(sound_index=request.form.get("index"))
    sound_xml.attrs["src"] = sound_file
    chapter.save_xml()
    
    if not sound_file:
        return make_response("No sound file specified", 400)
    
    return make_response("Sound file selected successfully", 200)
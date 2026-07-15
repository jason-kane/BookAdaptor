import glob
import os
import shutil

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
)

import const
import logger
from artifact_editor import (
    config,
    tools,
)
#from artifact_editor.chapter.chapter import Chapter
#from artifact_editor.chapter import htmx as chapter_htmx
from artifact_editor.author.author import Author
#, Book


log = logger.log(__name__)

bp = Blueprint(
    'library',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)


@bp.route("/")
def library():
    authors = []
    for author_name in sorted(os.listdir(const.LIBRARY_DIR)):
        if (
            author_name[0] == "." or
            author_name in ['home', 'active', 'lost+found', 'global_characters']
        ):
            continue

        if not os.path.isdir(os.path.join(const.LIBRARY_DIR, author_name)):
            # file, not a directory.
            continue

        author = Author(author_name)
        author.inventory_books()
       
        authors.append(author)

    return render_template(
        "library.html", 
        authors=authors,
        section="library"
    )

    
    # return 
    #     <h2>Plato</h2>
    #     <ul>
    #         <li><a href="/Plato/Meno/text/">Meno</a></li>
    #     </ul>
    #     <h2>W. W. Jacobs</h2>
    #     <ul>
    #         <li><a href="/W. W. Jacobs/The Lady of the Barge/text/">The Lady of the Barge</a></li>
    #         <li><a href="/W. W. Jacobs/The Monkeys Paw/text/">The Monkeys Paw</a></li>
    #         <li><a href="/W. W. Jacobs/Bills Paper Chase/text/">Bill's Paper Chase</a></li>
    #         <li><a href="/W. W. Jacobs/The Well/text/">The Well</a></li>
    #         <li><a href="/W. W. Jacobs/Cupboard Love/text/">Cupboard Love</a></li>
    #         <li><a href="/W. W. Jacobs/In The Library/text/">In The Library</a></li>
    #         <li><a href="/W. W. Jacobs/Captain Rogers/text/">Captain Rogers</a></li>
    #         <li><a href="/W. W. Jacobs/A Tigers Skin/text/">A Tiger's Skin</a></li>
    #         <li><a href="/W. W. Jacobs/A Mixed Proposal/text/">A Mixed Proposal</a></li>
    #         <li><a href="/W. W. Jacobs/An Adulteration Act/text/">An Adulteration Act</a></li>
    #         <li><a href="/W. W. Jacobs/A Golden Venture/text/">A Golden Venture</a></li>
    #         <li><a href="/W. W. Jacobs/Three at Table/text/">Three At Table</a></li>
    #     </ul>
    #     <h2>Jason Kane</h2>
    #     <ul>
    #         <li><a href="/Jason Kane/PROMPT/text/">PROMPT</a></li>
    #     </ul>
    #     <h2>R. M. Kane</h2>
    #     <ul>
    #         <li><a href="/R. M. Kane/Nessie vs Navy/text/">Nessie vs. Navy</a></li>
    #     </ul>

    #     <h2>Bible</h2>
    #     <ul>
    #         <li><a href="/Bible/Old Testament/01 Torah/01 Genesis/text/">Genesis</a></li>


@bp.route("/<author>/<title>/cover.png")
def book_cover(author, title):
    bookdir = tools.get_bookdir(author, title)
    log.info('bookdir: %s', bookdir)
    
    coverimage_fn = os.path.join(
        const.LIBRARY_DIR, bookdir, "cover.png"
    )

    if not os.path.exists(coverimage_fn):       
        first_chapter_image = os.path.join(
            const.LIBRARY_DIR,
            bookdir,
            "chapter",
            "0001",
            "cover.png"
        )
        if os.path.exists(first_chapter_image):
            shutil.copyfile(
                first_chapter_image,
                coverimage_fn
            )
        else:
            log.warning(f"No cover image found {first_chapter_image}")
            return send_from_directory(
                os.path.join(
                    os.path.dirname(__file__), 
                    "static"
                ),
                "placeholder_cover.png"
            )

    return send_from_directory(
        os.path.join(const.LIBRARY_DIR, bookdir),
        "cover.png"
    )



@bp.route("/<author>/<title>/<chapter_number>/video/cover.png")
@bp.route("/<author>/<title>/<chapter_number>/cover.png")
def chapter_cover(author, title, chapter_number):
    """if we have a cover image, use it."""
    chapter = int(chapter_number.lstrip("0"))
    chapterdir = tools.get_chapterdir(author, title, chapter)
    
    if not chapterdir:
        return render_template("404.html"), 404

    cover_path = os.path.join(const.LIBRARY_DIR, chapterdir, "cover.png")
    
    if not os.path.exists(cover_path):
        first_paragraph_dir = os.path.join(
            const.LIBRARY_DIR,
            chapterdir,
            "paragraphs", 
            "000000"
        )
        
        try:
            first_frame = glob.glob(
                os.path.join(
                    first_paragraph_dir,
                    "img*.png"
                )
            )[0]
        except IndexError:
            log.error(f"No images found in {first_paragraph_dir} to use as cover.")
            return send_from_directory(
                os.path.join(
                    os.path.dirname(__file__), 
                    "static"
                ),
                "placeholder_cover.png"
            )

        # copy the first image as the cover image   
        shutil.copyfile(
            os.path.join(first_paragraph_dir, first_frame),
            cover_path
        )
    
    return send_from_directory(
        os.path.join(const.LIBRARY_DIR, chapterdir),
        "cover.png"
    )


# http://localhost:5000/L.%20Frank%20Baum/The%20Marvelous%20Land%20of%20Oz/actions/save_book_style
@bp.route("/<author>/<title>/actions/save_book_style", methods=["PUT"])
def save_book_style(author, title):
    """
    Save the selected style to the book config.
    """
    bookdir = tools.get_bookdir(author, title)
    bookurl = tools.get_bookurl(author, title)
    book_config = config.get_config(bookdir)

    style = request.form.get("style", "").strip()
    
    if style:
        book_config['default_style'] = style
        config.save_config(bookdir, book_config)

    return "", 200


@bp.route("/sounds/<src>")
def sounds_src(src):
    sound_path = os.path.join(const.SOUND_DIR, src)

    if not os.path.exists(sound_path):
        log.error(f"Sound file not found: {sound_path}")
        return "Sound file not found", 404

    return send_from_directory(
        const.SOUND_DIR,
        src
    )
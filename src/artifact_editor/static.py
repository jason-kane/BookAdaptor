import os

from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from flask import (
    Blueprint,
    send_file,
    send_from_directory,
)

import const
import logger
from artifact_editor import tools

log = logger.log(__name__)


bp = Blueprint(
    'static',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

#log = app.logger

@bp.route("/<author>/<title>/<chapter_number>/<language>/paragraphs/<paragraph>/<filename>", methods=["GET"])
def get_binary_file(author, title, chapter_number, language, paragraph, filename):
    """
    Serve a binary file for a given chapter and paragraph.
    """
    author = Author(author)
    chapter = Chapter(author, title, chapter_number, language)
    
    log.info(f"get_binary_file({author}, {title}, {chapter_number}, {language}, {paragraph}, {filename})")
    
    filename_pfn = os.path.join(
        const.LIBRARY_DIR, 
        chapter.chapterdir,
        'paragraphs',
        f'{paragraph:0>6}',
        filename
    )

    if not os.path.exists(filename_pfn):
        log.error(f"Binary file {filename_pfn} does not exist")
        return "File not found", 404

    return send_from_directory(
        os.path.dirname(filename_pfn),
        os.path.basename(filename_pfn)
    )


@bp.route("/static/<path:pathname>/<filename>")
def static_dir(pathname, filename):
    # this is a security check to prevent directory traversal attacks
    if ".." in pathname or ".." in filename:
        return "Invalid filename", 400
    
    pfn = os.path.abspath(
        os.path.join("artifact_editor", "static", pathname, filename)
    )

    if os.path.exists(pfn):
        # return send_from_directory(f"artifact_editor/static/{pathname}", filename)
        log.info('Sending file %s', pfn)
        return send_file(
            pfn,
            mimetype=None,
            as_attachment=False,
            etag=True,
            max_age=3600  # one hour
        )

    # else:
    #     print(f"File {filename} not found in static directory")
    #     for url in [
    #         f"https://early.webawesome.com/webawesome@3.0.0-alpha.10/dist/{pathname}/{filename}",
    #         f"https://site-assets.fontawesome.com/releases/v6.7.2/{pathname}/{filename}"
    #     ]:
    #         r = requests.get(url)
            
    #         if r.status_code != 200:
    #             print(f"File {filename} not found at {url}")
    #             continue

    #         if "AccessDenied" in str(r.content):
    #             print(f"File {filename} AccessDenied at {url}")
    #             continue
            
    #         log.info('Retrieved file %s', pfn)
    #         break
        
    #     os.makedirs(
    #         os.path.dirname(pfn), 
    #         exist_ok=True
    #     )

    #     with open(pfn, "wb") as h:
    #         h.write(r.content)
    #     log.info('Saved as %s', pfn)
        
    return send_from_directory(f"artifact_editor/static/{pathname}", filename)

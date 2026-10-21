from artifact_editor.tools import (
    generic_button,
)
from flask import (
    render_template,
    url_for,
)
import const
import logger
import os
from markupsafe import escape

from .chapter import Chapter

log = logger.log(__name__)



def import_button(chapter: Chapter):
    import json
    import uuid

    job_id = uuid.uuid4().hex
    task = escape(json.dumps({
        "action": "chapter_import", "job_id": job_id,
        "author": chapter.author.name, "title": chapter.title,
        "chapter_number": chapter.number, "language": chapter.language,
    }))
    return f"""<div class="wa-stack" hx-ext="ws" ws-connect="ws://localhost:5001/ws"
        data-chapter-import="{job_id}">
        <wa-button id="import_btn" ws-send hx-trigger="click" hx-vals='{task}'
            variant="neutral" appearance="accent" size="medium">Import Chapter</wa-button>
        <div id="video-progress-{job_id}" role="status" aria-live="polite"></div>
    </div>"""



def generate_missing_metadata_button(chapter: Chapter):
    import json
    import uuid

    job_id = uuid.uuid4().hex
    task = escape(json.dumps({
        "action": "chapter_missing_metadata", "job_id": job_id,
        "author": chapter.author.name, "title": chapter.title,
        "chapter_number": chapter.number, "language": chapter.language,
    }))
    return f"""<div class="wa-stack" hx-ext="ws" ws-connect="ws://localhost:5001/ws"
        data-chapter-metadata="{job_id}">
        <wa-button ws-send hx-trigger="click" hx-vals='{task}' data-metadata-button
            variant="neutral" appearance="accent" size="medium">Generate Missing Metadata</wa-button>
        <div id="video-progress-{job_id}" role="status" aria-live="polite"></div>
    </div>"""


def time_align_button(chapter: Chapter):
    import json
    import uuid

    job_id = uuid.uuid4().hex
    task = escape(json.dumps({
        "action": "textoviva_time_align", "job_id": job_id,
        "author": chapter.author.name, "title": chapter.title,
        "chapter_number": chapter.number, "language": chapter.language,
    }))
    return f"""<div class="wa-stack" hx-ext="ws" ws-connect="ws://localhost:5001/ws">
        <wa-button ws-send hx-trigger="click" hx-vals='{task}'
            variant="neutral" appearance="accent" size="medium">Time Align</wa-button>
        <div id="video-progress-{job_id}" role="status" aria-live="polite"></div>
    </div>"""


def generate_missing_images_button(chapter):
    return missing_asset_button(chapter, "chapter_missing_images", "Generate Missing Images")


def generate_missing_animation_button(chapter):
    return missing_asset_button(chapter, "chapter_missing_animations", "Generate Missing Animations")


def missing_asset_button(chapter, action, label):
    import json
    import uuid

    job_id = uuid.uuid4().hex
    task = escape(json.dumps({
        "action": action, "job_id": job_id,
        "author": chapter.author.name, "title": chapter.title,
        "chapter_number": chapter.number, "language": chapter.language,
    }))
    return f"""<div class="wa-stack" hx-ext="ws" ws-connect="ws://localhost:5001/ws"
        data-chapter-assets="{job_id}">
        <wa-button ws-send hx-trigger="click" hx-vals='{task}' data-metadata-button
            variant="neutral" appearance="accent" size="medium">{label}</wa-button>
        <div id="video-progress-{job_id}" role="status" aria-live="polite"></div>
    </div>"""



def chapter_list(author, title):
    """Generate a wa-chapter containing cards for each chapter of this book."""

    chapters_dir = os.path.join(const.LIBRARY_DIR, author.name, title, "chapter")
    chapter_list = []
    # every directory inside 'chapter' that exists shows up as a chapter.
    if os.path.exists(chapters_dir):
        for chapterdir in sorted(os.listdir(chapters_dir)):
            if chapterdir.startswith("."):
                continue

            chapter_path = os.path.join(chapters_dir, chapterdir)

            if not os.path.isdir(chapter_path):
                continue

            # get the chapter index from the directory name
            try:
                index = int(chapterdir.split("_")[0])
            except ValueError:
                log.error(f"Invalid chapter directory name: {chapterdir}")
                continue

            chapter = Chapter(author, title, index)

            # create a chapter entry
            chapter_list.append(chapter.card())

    else:
        log.warning("No chapters found in %s", chapters_dir)

    out = ['<div id="chapter_list">']
    if chapter_list:
        out.append('<div class="wa-grid">')
        for chapter_card in chapter_list:
            chapter = Chapter(author, title, chapter_card["index"], language="english")

            log.debug("Chapter: %s", chapter.number)
            chapter_title = chapter.config["chapter_title"]

            published = ""
            if chapter.config.get("youtube", False):
                published = """<wa-badge class="youtube-badge" appearance="outlined" variant="success">YouTube</wa-badge>"""

            url_for_text = url_for(
                "library.book.chapter.chapter_base",
                **chapter.kwargs
            )
            if True:
                out.append(f"""
                    <a href="{url_for_text}">
                        <wa-card class="chapter-container">
                            {published}
                            <img slot="media" src="{chapter.cover_url}" alt="Cover of {title}" />
                            {chapter.number}: {chapter_title}
                        </wa-card>
                    </a>
                                    """)
            else:
                out.append(f"""
        <div class="chapter-container">
            {published}
            <a href="{url_for_text}">
                <div class="chapter-card wa-stack wa-gap-2xs" style="max-width: 200px;">
                    <div class="wa-frame:square" slot="image">
                        <img src="{chapter.cover_url}" alt="Cover of {title}" />
                    </div>

                    <div class="wa-stack">
                        <wa-button 
                        href="{url_for_text}" size="small" 
                        appearance="filled" 
                        variant="brand">{chapter.number}: {chapter_title}</wa-button>
                    </div>
                </div>
            </a>
        </div>
                        """)
        out.append("</div>")
    else:
        # if we don't have any chapters we haven't done the breakdown yet.
        book_filename = os.path.join(
            const.LIBRARY_DIR,
            author.name,
            title,
            "book.txt",
        )

        if os.path.exists(book_filename):
            with open(book_filename, "r") as f:
                book = f.read()
        else:
            book = ""

        out.append(
            render_template(
                "breakdown_chapters.html",
                author=author,
                title=title,
                book=book,
                language="english",
            )
        )

    out.append("</div>")
    return "\n".join(out)


def chapter_mood_widget(chapter):
    """Generate a wa-chapter-mood-widget for the chapter."""
    mood_set_url = url_for(
        "library.book.chapter.set_mood",
        **chapter.kwargs
    )
    return f"""
        <wa-textarea
            name="mood"
            hx-vals='js:{{"mood": this.value}}'
            hx-put="{mood_set_url}"
            hx-trigger="change"
            hx-swap="none"
            label="Mood"
            value="{escape(chapter.mood)}">
        </wa-textarea>
    """


def chapter_theme_widget(chapter):
    """Generate a wa-chapter-theme-widget for the chapter."""
    theme_set_url = url_for(
        "library.book.chapter.set_theme",
        **chapter.kwargs
    )
    return f"""
        <wa-textarea
            name="theme"
            hx-vals='js:{{"theme": this.value}}'
            hx-trigger="change"
            hx-swap="none"
            hx-put="{theme_set_url}"
            label="Theme"
            value="{escape(chapter.theme)}">
        </wa-textarea>
    """

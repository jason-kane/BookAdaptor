import os

from flask import Blueprint, current_app, g, jsonify, render_template, request, url_for

from artifact_editor.publish.youtube.oauth import connected, get_youtube_client, start_connection
from artifact_editor.publish.youtube.upload import upload_body, upload_video
from artifact_editor.publish.youtube.metadata import description_default, title_defaults
from artifact_editor.publish.youtube.settings import DEFAULTS, load_settings, save_settings

from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

bp = Blueprint(
    "youtube",
    __name__,
    template_folder=os.path.join(os.path.dirname(__file__), "templates"),
)

@bp.url_defaults
def my_url_defaults(endpoint, values):
    chapter_url_defaults_helper(endpoint, values)

@bp.url_value_preprocessor
def my_url_value_preprocessor(endpoint, values):
    chapter_url_value_preprocessor(endpoint, values) 

@bp.route('/')
def base():
    chapter = g.chapter
    book_title = chapter.config.get("title") or chapter.title.replace('_', ' ')
    chapter_title = chapter.chapter_title or f"Chapter {chapter.number}"
    author_name = chapter.config.get("author") or g.author.pretty_name
    defaults = title_defaults(book_title, author_name, chapter.config, chapter_title)
    settings = dict(DEFAULTS, title=defaults['upload_title'], tags=defaults['upload_tags'],
                    title_category=defaults['title_category'], title_format=defaults['title_format'],
                    description=description_default(book_title, author_name, chapter_title,
                                                    chapter.number, chapter.load_txt(), chapter.config))
    settings.update(load_settings(chapter))
    for field, options in (('title_category', 'title_categories'), ('title_format', 'title_formats')):
        if settings[field] and settings[field] not in defaults[options]:
            defaults[options] += (settings[field],)
    return render_template(
        "youtube.html",
        author=g.author,
        title=g.chapter.title,
        chapter=g.chapter,
        language=g.chapter.language,
        pretty_language=g.chapter.pretty_language,
        section="publish.youtube",
        youtube_connected=connected(),
        youtube_connection=request.args.get("youtube_connection"),
        settings=settings,
        **defaults,
    )


@bp.get('/connect')
def connect():
    return start_connection(url_for('.base'))


@bp.post('/upload')
def upload():
    # A custom same-origin header prevents cross-site form submissions from publishing.
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest':
        return jsonify(error='Submit uploads from the YouTube tab.'), 403
    if request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/'):
        return jsonify(error='Submit uploads from the same editor origin.'), 403
    if not connected():
        return jsonify(error='Connect to YouTube before uploading.'), 401
    try:
        body = upload_body(request.form)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    filename = g.chapter.get_video_filename()
    if not os.path.isfile(filename) or os.path.getsize(filename) == 0:
        return jsonify(error='Render the chapter video before uploading.'), 400
    try:
        save_settings(g.chapter, request.form, body)
    except OSError:
        return jsonify(error='Could not save chapter upload settings. No upload was started.'), 500
    try:
        video_id = upload_video(get_youtube_client(), filename, body)
    except Exception:
        current_app.logger.warning('YouTube upload failed')
        return jsonify(error='Upload could not be confirmed. Check YouTube Studio before retrying to avoid a duplicate. Check the connection, permissions, and upload quota.'), 502
    url = 'https://www.youtube.com/watch?v=' + video_id
    g.chapter.config['youtube'] = url
    try:
        g.chapter.save_config()
    except OSError:
        current_app.logger.exception('Could not save YouTube link for chapter')
        # The upload succeeded: keep the client from offering a duplicate upload.
        return jsonify(video_id=video_id, url=url,
                       warning='Upload complete, but the chapter badge could not be saved. '
                               'Save this YouTube link before leaving the page.')
    return jsonify(video_id=video_id, url=url)

import os

from pathlib import Path
from flask import Blueprint, g, jsonify, render_template, request, url_for
from . import client
from .oauth import start

from artifact_editor.chapter.chapter import (
    Chapter,
    chapter_url_defaults_helper,
    chapter_url_value_preprocessor,
)

bp = Blueprint(
    "tiktok",
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
    return render_template('tiktok.html', author=g.author, chapter=g.chapter,
                           title=g.chapter.title, language=g.chapter.language,
                           pretty_language=g.chapter.pretty_language, section='publish.tiktok',
                           connected=client.connected())


@bp.get('/connect')
def connect():
    return start(url_for('.base'))


@bp.post('/upload')
def upload():
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (
        request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/')):
        return jsonify(error='Submit uploads from the TikTok tab on this editor.'), 403
    if not client.connected():
        return jsonify(error='Connect to TikTok first.'), 401
    filename = g.chapter.get_video_filename()
    if not os.path.isfile(filename) or not os.path.getsize(filename):
        return jsonify(error='Render the chapter video first.'), 400
    record = Path(filename).parent / 'tiktok-upload.json'
    def initialized(publish_id):
        client.save(record, {'publish_id': publish_id, 'status': 'transferring'})
    try:
        publish_id = client.upload_video(filename, initialized)
        client.save(record, {'publish_id': publish_id, 'status': 'transferred'})
    except Exception:
        return jsonify(error='Upload could not be confirmed. Check your TikTok inbox before retrying. Verify app permissions and video requirements.'), 502
    return jsonify(publish_id=publish_id)


@bp.post('/status')
def status():
    if request.headers.get('X-Requested-With') != 'XMLHttpRequest' or (
        request.headers.get('Origin') and request.headers['Origin'] != request.host_url.rstrip('/')):
        return jsonify(error='Use the TikTok tab on this editor.'), 403
    try:
        record = Path(g.chapter.get_video_filename()).parent / 'tiktok-upload.json'
        import json
        publish_id = json.loads(record.read_text())['publish_id']
        data = client.post('post/publish/status/fetch/', client.access_token(), {'publish_id': publish_id})
        return jsonify(status=data['status'])
    except FileNotFoundError:
        return jsonify(error='No TikTok upload has been started for this chapter.'), 404
    except Exception:
        return jsonify(error='Could not retrieve TikTok processing status.'), 502
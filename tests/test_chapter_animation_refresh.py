"""Completed animation refreshes use the editor's current video source."""
from pathlib import Path
from types import SimpleNamespace

from bs4 import BeautifulSoup
from flask import Flask, render_template
import pytest


@pytest.mark.parametrize('animation_url', [None, '/animation/shot.mp4?v=new-render'])
def test_animation_panel_refresh_target_and_new_source(animation_url):
    templates = Path(__file__).resolve().parents[1] / 'src/artifact_editor/chapter/templates'
    app = Flask(__name__, template_folder=str(templates))
    app.jinja_env.globals['url_for'] = lambda endpoint, **kwargs: (
        '/timeline/image/2' if endpoint.endswith('.timeline_image_editor') else '/asset'
    )
    app.jinja_env.globals['websocket_url'] = lambda: '/ws'
    with app.app_context():
        html = render_template('chapter_timeline_image_editor.html',
            chapter=SimpleNamespace(kwargs={}), image={'index': '2', 'src': 'still.png'},
            animation_url=animation_url, active_editor_tab='animation', timing=None,
            thumbnails=[], i2v_templates=[], animation_quality_options=[],
            selected_i2v_template='i2v.h3turbo', preview_version='new-render')
    soup = BeautifulSoup(html, 'html.parser')
    status = soup.select_one('#timeline-animation-status-2')
    assert status['data-image-editor-url'] == '/timeline/image/2?tab=animation'
    assert status['data-image-editor-target'] == '#timeline-image-editor-2'
    assert soup.select_one('wa-tab-group')['active'] == 'animation'
    source = soup.select_one('#timeline-animation-player-2 source')
    if animation_url:
        assert source['src'] == animation_url
    else:
        assert source is None
        assert 'No animation generated yet.' in soup.text

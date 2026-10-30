"""Library image previews expose only completed stills with generation identities."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys
import uuid

from bs4 import BeautifulSoup
import flask


ROOT = Path(__file__).resolve().parents[1]


def load_function(path, name, namespace):
    source = ROOT / path
    node = next(n for n in ast.parse(source.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == name)
    node.decorator_list = []
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


def test_completion_snapshot_only_includes_existing_generated_images(tmp_path):
    (tmp_path / 'finished.png').touch()
    soup = BeautifulSoup('<chapter><paragraph>'
        '<image index="0" src="finished.png" image_generation_id="new-id"/>'
        '<image index="1" src="absent.png" image_generation_id="missing-file"/>'
        '<image index="2" src="finished.png"/>'
        '<image index="3" image_generation_id="no-source"/>'
        '</paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(get_xml=lambda: soup, kwargs={'author': 'Author', 'title': 'Book'},
                              get_image_filename=lambda image: str(tmp_path / image['src']))
    url = Mock(return_value='/image.png?v=new-id')
    endpoint = load_function('src/artifact_editor/chapter/views.py', 'generated_images',
                            dict(g=SimpleNamespace(chapter=chapter), os=os, flask=flask, url_for=url))
    with flask.Flask(__name__).app_context():
        response = endpoint()
    assert response.json == [{'id': 'new-id', 'index': 0, 'url': '/image.png?v=new-id'}]
    assert response.headers['Cache-Control'] == 'no-store'
    assert url.call_args.kwargs['v'] == 'new-id'
    assert url.call_args.kwargs['height'] == 640


def test_manual_redraw_gets_a_new_generation_identity(monkeypatch):
    image = BeautifulSoup('<image index="4"/>', 'xml').image
    chapter = SimpleNamespace(get_image=lambda _: image, get_workflow=Mock(return_value='workflow'), save_xml=Mock())
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.selector.htmx',
                        SimpleNamespace(gather_candidate_images=lambda *_: ['/chapter/finished.png']))
    monkeypatch.setitem(sys.modules, 'artifact_editor.textoviva.workflow',
                        SimpleNamespace(image_workflow_name=lambda template: template))
    notify = Mock(side_effect=lambda *args: chapter.save_xml.assert_called())
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.completions',
                        SimpleNamespace(publish_image_completion=notify))
    generate = load_function('src/artifact_editor/images/jobs.py', 'generate_timeline_image', dict(
        os=os, uuid=uuid, const=SimpleNamespace(DEFAULT_IMAGE_TEMPLATE='default'),
        comfy=SimpleNamespace(run_workflow_fn=Mock())))
    generate(chapter, 4)
    first = image['image_generation_id']
    generate(chapter, 4)
    assert first != image['image_generation_id']
    assert image['src'] == 'finished.png'
    assert chapter.save_xml.call_count == 2
    assert notify.call_count == 2

"""Final asset sizing must not inherit legacy diffusion dimensions."""
import importlib.util
import ast
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
from bs4 import BeautifulSoup, Tag

from PIL import Image
import pytest

spec = importlib.util.spec_from_file_location(
    'animation_quality_dimensions', Path(__file__).parents[1] /
    'src/artifact_editor/images/animation_quality.py')
quality = importlib.util.module_from_spec(spec)
spec.loader.exec_module(quality)


@pytest.fixture
def geometry(monkeypatch):
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(
        IMG_TARGET_WIDTH=1080, IMG_TARGET_HEIGHT=1080,
        GEOMETRY={'portrait': {'SIZE': (1080, 1920)}}))


def test_library_upscales_legacy_still_before_animation(tmp_path, geometry):
    source = tmp_path / 'still.png'
    Image.new('RGB', (1024, 1024), 'red').save(source)
    chapter = SimpleNamespace(get_image_filename=lambda _: str(source))
    assert quality.prepare_animation_source(chapter, {}) == (1080, 1080)
    with Image.open(source) as still:
        assert still.size == (1080, 1080)
    original = source.read_bytes()
    quality.prepare_animation_source(chapter, {})
    assert source.read_bytes() == original
    assert quality.dimensions_for_quality('low', 1080, 1080) == (480, 480)


def test_theater_keeps_its_film_dimensions(geometry):
    chapter = SimpleNamespace(get_aspect=lambda: 'portrait', get_image_filename=lambda _: None)
    assert quality.prepare_animation_source(chapter, {'render_profile': 'theater'}) == (1080, 1920)


@pytest.mark.parametrize('overrides,target', [({}, (1080, 1080)),
    ({'UPSCALE_WIDTH': 1080, 'UPSCALE_HEIGHT': 1920}, (1080, 1920))])
def test_generate_image_graph_scales_after_generation(tmp_path, overrides, target):
    source = Path(__file__).parents[1] / 'src/artifact_editor/chapter/chapter.py'
    tree = ast.parse(source.read_text())
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == 'get_workflow_by_filename')
    namespace = dict(os=os, json=json, Tag=Tag, as_int=int, log=Mock(), const=SimpleNamespace(
        FPS=25, IMG_TARGET_WIDTH=1080, IMG_TARGET_HEIGHT=1080,
        COMFY_DIRS={'artifactserver': {'WORKFLOWS_DIR': str(tmp_path)}}))
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), 'exec'), namespace)
    template = tmp_path / 'ui.t2i.fixture.json'
    template.write_text(json.dumps({'nodes': [
        {'id': 1, 'type': 'PrimitiveInt', 'title': 'WIDTH'},
        {'id': 2, 'type': 'PrimitiveInt', 'title': 'HEIGHT'},
        {'id': 3, 'type': 'VAEDecode', 'outputs': [{'links': [1]}]},
        {'id': 4, 'type': 'SaveImage', 'inputs': [{'name': 'images', 'link': 1}]},
    ], 'links': [[1, 3, 0, 4, 0, 'IMAGE']]}))
    chapter = SimpleNamespace(nice='fixture', get_image_frames=lambda _: 25,
        get_image_filename=lambda _: None, get_image=lambda _: None)
    image = BeautifulSoup('<image index="0"/>', 'xml').image
    name = namespace['get_workflow_by_filename'](chapter, image, str(template),
        template_environment=overrides)
    workflow = json.loads((tmp_path / name).read_text())
    nodes = workflow['nodes']
    assert [n['widgets_values'][0] for n in nodes[:2]] == [1024, 1024]
    scale = next(n for n in nodes if n['type'] == 'ImageScale')
    assert tuple(scale['widgets_values'][1:3]) == target
    save = next(n for n in nodes if n['type'] == 'SaveImage')
    link = next(link for link in workflow['links'] if link[0] == save['inputs'][0]['link'])
    assert link[1] == scale['id']


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='ffmpeg required')
def test_animation_upscale_preserves_frame_count_and_is_idempotent(tmp_path):
    video = tmp_path / 'animation.mp4'
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'color=c=red:s=32x32:r=24', '-frames:v', '6', '-pix_fmt', 'yuv420p',
                    str(video)], check=True)
    quality.upscale_animation(video, (1080, 1080))
    output = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,nb_frames,r_frame_rate', '-of', 'json', str(video)], text=True)
    stream = json.loads(output)['streams'][0]
    assert (stream['width'], stream['height']) == (1080, 1080)
    assert stream['nb_frames'] == '6'
    assert stream['r_frame_rate'] == '24/1'
    modified = video.stat().st_mtime_ns
    quality.upscale_animation(video, (1080, 1080))
    assert video.stat().st_mtime_ns == modified

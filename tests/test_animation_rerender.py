"""Export actual animation templates without requiring generation services."""
import ast
import json
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize('template_name', [
    'ui.i2v.minimax_h3-MiniMax H3.json', 'ui.i2v.ltx_2.3-LTX 2.3.json',
])
def test_rerender_exports_edited_prompt_and_new_seed(tmp_path, template_name):
    path = ROOT / 'src/artifact_editor/chapter/chapter.py'
    method = next(n for n in ast.walk(ast.parse(path.read_text()))
                  if isinstance(n, ast.FunctionDef) and n.name == 'get_workflow_by_filename')
    dirs = {}
    for key in ('INPUT_DIR', 'OUTPUT_DIR', 'WORKFLOWS_DIR'):
        directory = tmp_path / key
        directory.mkdir()
        dirs[key] = str(directory)
    still = tmp_path / 'still.png'
    still.write_bytes(b'still')
    ns = dict(json=json, os=os, shutil=shutil, Tag=object, as_int=int,
              const=SimpleNamespace(FPS=25, COMFY_DIRS={'artifactserver': dirs, 'comfyui': dirs}),
              log=Mock(), styles=SimpleNamespace(Style=lambda _: SimpleNamespace(name='none')))
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), 'exec'), ns)
    image = BeautifulSoup('<image index="1" animation_prompt="The bird lifts its head." '
                          'animation_prompt_00="Old motion"/>', 'xml').image
    chapter = SimpleNamespace(nice='book', get_image_frames=lambda _: 100,
                              get_image_filename=lambda _: str(still), get_image=lambda _: None,
                              get_meta_dict=lambda: {}, get_scene=lambda _: {},
                              get_prompt=lambda _: 'Still image description', get_image_style=lambda _: 'none')
    template = ROOT / 'templates' / template_name
    for seed, prompt in [(101, 'The bird lifts its head.'), (202, 'The bird spreads its wings.')]:
        image['animation_prompt'] = prompt
        filename = ns['get_workflow_by_filename'](
            chapter, image, str(template), video_index=0,
            template_environment={'SEED': seed}, use_image_source=True,
        )
        data = json.loads((Path(dirs['WORKFLOWS_DIR']) / filename).read_text())
        animation = next(n for n in data['nodes'] if n.get('title') == 'ANIMATION_PROMPT')
        assert animation['widgets_values'] == [prompt]
        assert animation['widgets_values_named']['value'] == prompt
        seeded = [n for n in data['nodes'] if any(
            key in n.get('widgets_values_named', {}) for key in ('seed', 'noise_seed'))]
        assert seeded
        for node in seeded:
            named = node['widgets_values_named']
            for key in ('seed', 'noise_seed'):
                if key in named:
                    assert named[key] == seed
                    assert node['widgets_values'][list(named).index(key)] == seed
    del image.attrs['animation_prompt']
    filename = ns['get_workflow_by_filename'](chapter, image, str(template), video_index=0, use_image_source=True)
    data = json.loads((Path(dirs['WORKFLOWS_DIR']) / filename).read_text())
    animation = next(n for n in data['nodes'] if n.get('title') == 'ANIMATION_PROMPT')
    assert animation['widgets_values'] == ['Old motion']

"""Pose generation stays within UI choices, including the reported hobbling case."""
import ast
from contextvars import ContextVar
import importlib.util
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def pose_generator(monkeypatch):
    path = ROOT / 'src/artifact_editor/images/images.py'
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'generate_metadata_character_pose')
    spec = importlib.util.spec_from_file_location('pose_choices', ROOT / 'src/artifact_editor/images/scene/const.py')
    choices = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(choices)
    prompt = Mock()
    client = Mock()
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completion=prompt))
    monkeypatch.setitem(sys.modules, 'gpu_tasks', SimpleNamespace(gpu_label=ContextVar('pose_label', default=None)))
    scope = dict(log=Mock(), scene_const=choices, json=json, os=os,
        redis=SimpleNamespace(Redis=Mock(return_value=client)),
        get_image_narration_context=lambda image: 'The old woman hobbled to the door.',
        get_text_to_next=lambda **kw: 'She was hobbling toward the gate.')
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    return SimpleNamespace(generate=scope[node.name], prompt=prompt, client=client, choices=choices)


@pytest.mark.parametrize('result,expected', [
    ({'pose': 'Flying'}, 'flying'),
    ({'character_pose': 'flying', 'pose_selection': 'flying'}, 'flying'),
    ({'pose_selection': 'lying-down'}, 'lying_down'),
    ('flying', 'flying'),
    ({'pose': None, 'character_pose': 'flying'}, 'flying'),
    ({'pose': 'hobbling'}, 'walking'),
    ({'pose': ' Lim ping '}, None),
    ({'pose': 'Limping'}, 'walking'),
    ({'pose': 'teleporting'}, None),
    ({'pose': []}, None), (None, None),
])
def test_pose_response_shapes_and_gaits(pose_generator, result, expected):
    fixture = pose_generator
    fixture.prompt.return_value = json.dumps(result)
    image = BeautifulSoup('<image index="3" keeper_action="Hobbling to the gate"/>', 'xml').image
    original = dict(image.attrs)
    if expected is None:
        with pytest.raises(ValueError, match='Unknown pose response'):
            fixture.generate({'name': 'Keeper', 'tag': 'keeper'}, image, None)
        assert image.attrs == original
    else:
        fixture.generate({'name': 'Keeper', 'tag': 'keeper'}, image, None)
        assert image['keeper_pose'] == expected
        assert image['keeper_action'] == 'Hobbling to the gate'
    fixture.client.close.assert_called_once()


def test_request_constrains_pose_to_actual_ui_choices(pose_generator):
    fixture = pose_generator
    fixture.prompt.return_value = '{"pose":"walking"}'
    image = BeautifulSoup('<image/>', 'xml').image
    fixture.generate({'name': 'Keeper', 'tag': 'keeper'}, image, None)
    request = fixture.prompt.call_args.kwargs
    schema = request['response_format']['schema']
    assert schema['properties']['pose'] == {'type': 'string', 'enum': [key for key, _ in fixture.choices.POSE_CHOICES]}
    assert schema['required'] == ['pose'] and schema['additionalProperties'] is False
    assert 'hobbling' not in schema['properties']['pose']['enum']
    assert request['thinking_budget_tokens'] == 0


@pytest.mark.parametrize('force', [False, True])
def test_existing_pose_is_preserved_unless_force_is_selected(pose_generator, force):
    fixture = pose_generator
    fixture.prompt.return_value = '{"pose":"walking"}'
    image = BeautifulSoup('<image keeper_pose="sitting"/>', 'xml').image
    fixture.generate({'name': 'Keeper', 'tag': 'keeper'}, image, None, force=force)
    assert image['keeper_pose'] == ('walking' if force else 'sitting')
    assert fixture.prompt.call_count == int(force)


def test_invalid_json_still_closes_connection_and_preserves_image(pose_generator):
    fixture = pose_generator
    fixture.prompt.return_value = 'not json'
    image = BeautifulSoup('<image/>', 'xml').image
    with pytest.raises(json.JSONDecodeError):
        fixture.generate({'name': 'Keeper', 'tag': 'keeper'}, image, None)
    assert 'keeper_pose' not in image.attrs
    fixture.client.close.assert_called_once()

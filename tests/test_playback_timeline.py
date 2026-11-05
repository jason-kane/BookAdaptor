"""Playback metadata is available for incomplete chapters without render side effects."""
import ast
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup


def playback(xml):
    path = Path(__file__).parents[1] / 'src/artifact_editor/textoviva/views.py'
    node = next(node for node in ast.parse(path.read_text()).body
                if isinstance(node, ast.FunctionDef) and node.name == 'playback_timeline')
    node.decorator_list = []
    chapter = SimpleNamespace(get_xml=lambda: BeautifulSoup(xml, 'xml'),
                              get_masterplan=Mock(side_effect=AssertionError('Must not render')))
    scope = {'g': SimpleNamespace(chapter=chapter), 'const': SimpleNamespace(FPS=25)}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), scope)
    result = scope['playback_timeline']()
    chapter.get_masterplan.assert_not_called()
    return result


def test_unrendered_slots_preserve_indices_times_and_silence_highlighting():
    result = playback('<chapter><paragraph><image index="5"/>'
                      '<phrase index="0" frames="50">Words</phrase>'
                      '<silence index="0" frames="125"/>'
                      '<image index="8" src="ready.png"/><phrase index="3" frames="25">More</phrase>'
                      '<image index="9"/><image index="10"/></paragraph></chapter>')
    assert result == {'fps': 25,
                      'phrases': [{'index': '0', 'start': 0, 'end': 174},
                                  {'index': '3', 'start': 175, 'end': 199}],
                      'images': [{'index': '5', 'start': 0, 'end': 174},
                                 {'index': '8', 'start': 175, 'end': 199}]}


def test_silent_image_runs_and_empty_chapters():
    assert playback('<chapter/>') == {'fps': 25, 'phrases': [], 'images': []}
    result = playback('<chapter><paragraph><image index="2"/><silence index="0" frames="125"/>'
                      '<image index="3"/><silence index="1" frames="125"/></paragraph></chapter>')
    assert result['phrases'] == []
    assert result['images'] == [{'index': '2', 'start': 0, 'end': 124},
                                {'index': '3', 'start': 125, 'end': 249}]

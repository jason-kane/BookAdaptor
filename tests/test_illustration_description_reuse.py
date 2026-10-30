"""Descriptions must survive retries and must never treat empty output as complete."""
import ast
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_functions(relative, names, namespace):
    path = ROOT / relative
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def description_generator(monkeypatch, cached=None, generated='A boy leaves the room.'):
    cache_get, cache_set = Mock(return_value=cached), Mock()
    model, client = Mock(return_value=generated), Mock()
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.scenes', SimpleNamespace(
        setting_description=lambda _: 'Room', time_of_day=lambda _: 'night', lighting=lambda _: {}))
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completion=model))
    monkeypatch.setitem(sys.modules, 'gpu_tasks', SimpleNamespace(gpu_label=SimpleNamespace(get=lambda: 'Description')))
    namespace = dict(os=os, llm=SimpleNamespace(get_cached_prompt=cache_get, set_cached_prompt=cache_set),
                     redis=SimpleNamespace(Redis=Mock(return_value=client)),
                     get_text_to_next=lambda **_: 'The boy leaves the room.',
                     get_image_narration_context=lambda _: 'Nighttime')
    function = load_functions('src/artifact_editor/images/images.py',
                              {'generate_image_description'}, namespace)['generate_image_description']
    return function, model, cache_get, cache_set


def test_existing_description_needs_no_model_or_cache_work(monkeypatch):
    function, model, cache_get, _ = description_generator(monkeypatch)
    image = BeautifulSoup('<image description="Already described."/>', 'xml').image
    assert function(image) == 'Already described.'
    model.assert_not_called()
    cache_get.assert_not_called()


def test_cached_description_is_restored_after_interrupted_chapter_save(monkeypatch):
    function, model, _, _ = description_generator(monkeypatch, cached='A boy leaves.')
    image = BeautifulSoup('<image description=""/>', 'xml').image
    assert function(image) == 'A boy leaves.'
    assert image['description'] == 'A boy leaves.'
    model.assert_not_called()


def test_legacy_cache_discards_reasoning_control_prefix(monkeypatch):
    function, model, _, _ = description_generator(
        monkeypatch, cached='Budget is finished. </think> A boy leaves.')
    image = BeautifulSoup('<image/>', 'xml').image
    assert function(image) == 'A boy leaves.'
    model.assert_not_called()


def test_new_description_uses_final_answer_budget_and_is_cached(monkeypatch):
    function, model, _, cache_set = description_generator(monkeypatch)
    image = BeautifulSoup('<image/>', 'xml').image
    assert function(image) == 'A boy leaves the room.'
    assert model.call_args.kwargs['max_tokens'] == 2048
    assert model.call_args.kwargs['thinking_budget_tokens'] == 0
    assert cache_set.call_args.args[1] == image['description']
    prompt = model.call_args.args[1].lower()
    assert all(word not in prompt for word in ('signboard', 'goblet', 'pigeon'))


def test_story_objects_are_allowed_when_present_in_source(monkeypatch):
    function, model, _, _ = description_generator(monkeypatch)
    function.__globals__['get_text_to_next'] = lambda **_: 'A weathered signboard marks the entrance.'
    function(BeautifulSoup('<image/>', 'xml').image)
    assert 'A weathered signboard marks the entrance.' in model.call_args.args[1]


def test_narration_constraints_do_not_introduce_unrelated_objects():
    function = load_functions('src/artifact_editor/tools.py', {'get_image_narration_context'},
                              {})['get_image_narration_context']
    soup = BeautifulSoup('<paragraph><image/><phrase>A boy walks through a room.</phrase></paragraph>', 'xml')
    context = function(soup.image).lower()
    assert 'a boy walks through a room.' in context
    assert all(word not in context for word in ('signboard', 'goblet', 'pigeon'))


def test_empty_description_is_never_saved_as_complete(monkeypatch):
    function, _, _, cache_set = description_generator(monkeypatch, generated='   ')
    image = BeautifulSoup('<image/>', 'xml').image
    with pytest.raises(ValueError, match='empty illustration description'):
        function(image)
    assert 'description' not in image.attrs
    cache_set.assert_not_called()


def test_force_bypasses_existing_description_and_cache(monkeypatch):
    function, model, cache_get, _ = description_generator(monkeypatch)
    image = BeautifulSoup('<image description="Old description."/>', 'xml').image
    assert function(image, force=True) == 'A boy leaves the room.'
    assert cache_get.call_args.kwargs['force'] is True
    model.assert_called_once()


@pytest.mark.parametrize('format,value', [('txt', 'A saved setting.'), ('json', ['Jack'])])
def test_prompt_cache_writer_and_reader_agree_on_format(tmp_path, format, value):
    namespace = load_functions('src/artifact_editor/llm.py', {'get_cached_prompt', 'set_cached_prompt'},
        dict(os=os, json=json, tempfile=tempfile, LLM_CACHE_DIR=str(tmp_path), log=Mock(),
             cache_tag=lambda prompt: hashlib.sha256(prompt.encode()).hexdigest()))
    namespace['set_cached_prompt']('prompt', value, format=format)
    assert namespace['get_cached_prompt']('prompt', format=format) == value
    assert namespace['get_cached_prompt']('prompt', format=format, force=True) is None


def test_prompt_cache_discards_truncated_json_and_reports_a_miss(tmp_path):
    cache_tag = lambda prompt: hashlib.sha256(prompt.encode()).hexdigest()
    cache_fn = tmp_path / f"{cache_tag('json_prompt')}.json"
    cache_fn.write_text('{"truncated":', encoding='utf-8')
    namespace = load_functions('src/artifact_editor/llm.py', {'get_cached_prompt'},
        dict(os=os, json=json, LLM_CACHE_DIR=str(tmp_path), log=Mock(), cache_tag=cache_tag))

    assert namespace['get_cached_prompt']('prompt') is None
    assert not cache_fn.exists()


def test_prompt_cache_replaces_files_atomically(tmp_path):
    cache_tag = lambda prompt: hashlib.sha256(prompt.encode()).hexdigest()
    namespace = load_functions('src/artifact_editor/llm.py', {'get_cached_prompt', 'set_cached_prompt'},
        dict(os=os, json=json, tempfile=tempfile, LLM_CACHE_DIR=str(tmp_path), log=Mock(), cache_tag=cache_tag))

    namespace['set_cached_prompt']('prompt', {'complete': True})

    assert namespace['get_cached_prompt']('prompt') == {'complete': True}
    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{cache_tag('json_prompt')}.json"]

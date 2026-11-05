"""Incomplete JSON is retried, never cached as a successful response."""
import ast
import json
import logging
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def prompt_function(monkeypatch, responses, cached=None):
    inference = Mock(side_effect=responses)
    monkeypatch.setitem(sys.modules, 'llama_inference', SimpleNamespace(chat_completion=inference))
    monkeypatch.setitem(sys.modules, 'gpu_tasks', SimpleNamespace(gpu_label=SimpleNamespace(get=lambda: 'test')))
    cache = Mock()
    scope = dict(json=json, log=logging.getLogger(__name__),
                 get_cached_prompt=Mock(return_value=cached), set_cached_prompt=cache)
    path = Path(__file__).parents[1] / 'src/artifact_editor/llm.py'
    tree = ast.parse(path.read_text())
    tree.body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'json_prompt']
    exec(compile(tree, str(path), 'exec'), scope)
    return scope['json_prompt'], inference, cache


def test_truncated_json_retries_with_more_space_and_caches_complete_result(monkeypatch):
    run, inference, cache = prompt_function(monkeypatch, ['[{"name":"Cut off', '[{"name":"Boy"}]'])
    assert run('Extract characters', max_output_tokens=4096, label='Characters') == [{'name': 'Boy'}]
    assert [c.kwargs['max_tokens'] for c in inference.call_args_list] == [4096, 8192]
    assert all(c.kwargs['thinking_budget_tokens'] == 0 for c in inference.call_args_list)
    assert inference.call_args.args[1].startswith('Extract characters')
    cache.assert_called_once_with('Extract characters', [{'name': 'Boy'}], format='json')


def test_repeated_malformed_json_fails_without_caching(monkeypatch):
    run, inference, cache = prompt_function(monkeypatch, ['[', '['])
    with pytest.raises(json.JSONDecodeError):
        run('Extract characters')
    assert inference.call_count == 2
    cache.assert_not_called()


def test_cached_json_does_not_run_inference(monkeypatch):
    run, inference, cache = prompt_function(monkeypatch, [], cached=[])
    assert run('Extract characters') == []
    inference.assert_not_called()
    cache.assert_not_called()

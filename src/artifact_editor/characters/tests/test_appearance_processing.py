"""Bulk character appearance processing and concurrent book edits."""
import ast
from copy import deepcopy
import json
from pathlib import Path
import textwrap
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from bs4 import BeautifulSoup


def load_functions(names, namespace):
    source = Path(__file__).parents[1] / "characters.py"
    functions = [node for node in ast.parse(source.read_text()).body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.Module(body=functions, type_ignores=[]), str(source), "exec"), namespace)
    return namespace


class AppearanceProcessingTests(unittest.TestCase):
    def test_appearance_json_prompt_retries_once_after_malformed_response(self):
        llm = Mock()
        llm.json_prompt.side_effect = [json.JSONDecodeError("bad JSON", "[", 1), []]
        progress = Mock()
        ns = load_functions({"_appearance_json_prompt"}, {
            "llm": llm,
            "json": json,
            "log": Mock(),
        })

        result = ns["_appearance_json_prompt"]("same request", "extraction", progress)

        self.assertEqual(result, [])
        self.assertEqual(llm.json_prompt.call_count, 2)
        self.assertEqual(llm.json_prompt.call_args_list[0].args, ("same request",))
        self.assertEqual(llm.json_prompt.call_args_list[0].kwargs, {"force": True})
        self.assertEqual(llm.json_prompt.call_args_list[1].args, ("same request",))
        self.assertEqual(llm.json_prompt.call_args_list[1].kwargs, {"force": True})
        progress.assert_called_once_with(0, "Retrying extraction after invalid JSON")

    def test_appearance_json_prompt_reports_second_malformed_response(self):
        error = json.JSONDecodeError("bad JSON", "[", 1)
        llm = Mock()
        llm.json_prompt.side_effect = [error, error]
        ns = load_functions({"_appearance_json_prompt"}, {
            "llm": llm,
            "json": json,
            "log": Mock(),
        })

        with self.assertRaises(json.JSONDecodeError):
            ns["_appearance_json_prompt"]("same request", "review")
        self.assertEqual(llm.json_prompt.call_count, 2)

    def test_bulk_processes_every_character_except_narrator_and_reports_progress(self):
        catalog = {"Narrator": {"name": "Narrator"}, "Tip": {"name": "Tip"},
                   "Jack": {"name": "Jack Pumpkinhead"}}
        def extract(chapter, tag, progress):
            progress(50, "Reviewing evidence")
            return [{"phrase": 4}] if tag == "Tip" else []
        processor = Mock(side_effect=extract)
        progress = Mock()
        ns = load_functions({"extract_all_description_timelines"}, {
            "get_all_characters": lambda *args, **kwargs: catalog,
            "extract_description_timeline": processor,
            "log": Mock(),
        })
        result = ns["extract_all_description_timelines"](object(), progress=progress)
        self.assertEqual([call.args[1] for call in processor.call_args_list], ["Tip", "Jack"])
        self.assertEqual(result, {"characters": 2, "changes": 1, "failures": []})
        percentages = [call.args[0] for call in progress.call_args_list]
        self.assertEqual(percentages, sorted(percentages))
        self.assertEqual(percentages[-1], 100)

    def test_bulk_retains_successes_and_reports_failed_characters(self):
        catalog = {"Tip": {"name": "Tip"}, "Jack": {"name": "Jack Pumpkinhead"}}
        processor = Mock(side_effect=[RuntimeError("Model unavailable"), [{"phrase": 4}]])
        ns = load_functions({"extract_all_description_timelines"}, {
            "get_all_characters": lambda *args, **kwargs: catalog,
            "extract_description_timeline": processor,
            "log": Mock(),
        })
        result = ns["extract_all_description_timelines"](object())
        self.assertEqual(processor.call_count, 2)
        self.assertEqual(result["characters"], 1)
        self.assertEqual(result["changes"], 1)
        self.assertEqual(result["failures"], ["Tip: Model unavailable"])

    def test_model_results_preserve_book_edits_made_during_processing(self):
        catalog = {"Tip": {"tag": "Tip", "name": "Tip", "description": "Red shirt",
                           "description_timeline": []}}
        event = {"phrase": 4, "description": "Blue shirt", "evidence": "Tip put on a blue shirt."}
        def prompt(text, force=False):
            catalog["Tip"]["description"] = "User's updated book description"
            catalog["Tip"]["voices"] = [{"id": "new-voice"}]
            return [event]
        def save(chapter, records, is_global):
            catalog.clear()
            catalog.update(deepcopy(records))
        ns = load_functions({"_appearance_json_prompt", "_extract_chapter_description_timeline", "character_description_at",
                             "_appearance_description"}, {
            "get_all_characters": lambda *args, **kwargs: deepcopy(catalog),
            "save_characters": save,
            "llm": SimpleNamespace(json_prompt=Mock(side_effect=prompt)),
            "json": json, "textwrap": textwrap,
        })
        chapter = SimpleNamespace(number=3, get_xml=lambda: BeautifulSoup(
            '<chapter><phrase index="4">Tip put on a blue shirt.</phrase></chapter>', 'xml'))
        result = ns["_extract_chapter_description_timeline"](chapter, deepcopy(catalog["Tip"]), 60)
        self.assertEqual(catalog["Tip"]["description"], "User's updated book description")
        self.assertEqual(catalog["Tip"]["voices"], [{"id": "new-voice"}])
        self.assertEqual(catalog["Tip"]["description_timeline"], result)
        self.assertEqual(result[0]["description"], "Blue shirt")
        self.assertEqual(catalog["Tip"]["description_timeline_scanned"], [3])

"""Characterize the public inputs and outputs of the ComfyUI adapter."""

import ast
import glob
import importlib.util
import json
import os
import tempfile
import unittest
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]


def load_comfy_definitions(namespace):
    """Compile comfy.py declarations with controlled external dependencies."""
    source = ROOT / "src/comfy.py"
    tree = ast.parse(source.read_text())
    declarations = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    ]
    exec(compile(ast.Module(body=declarations, type_ignores=[]), str(source), "exec"), namespace)  # noqa: S102


class ComfyContractTests(unittest.TestCase):
    """Pin behaviors consumed by application code before the rewrite."""

    def namespace(self, **overrides):
        """Provide deterministic stand-ins for module globals and services."""
        namespace = {
            "Any": object,
            "DEFAULT_MINIMUM_FREE_VRAM_GIB": 8.0,
            "DEFAULT_VRAM_WAIT_SECONDS": 90.0,
            "WORKFLOW_CONVERSION_RETRY_SECONDS": 60.0,
            "WORKFLOW_CONVERSION_RETRY_INTERVAL_SECONDS": 2.0,
            "WEBSOCKET_PING_INTERVAL_SECONDS": 25,
            "WORKFLOW_HTTP_TIMEOUT": object(),
            "glob": glob,
            "html": __import__("html"),
            "tempfile": tempfile,
            "json": json,
            "os": os,
            "re": __import__("re"),
            "shutil": __import__("shutil"),
            "subprocess": __import__("subprocess"),
            "time": SimpleNamespace(monotonic=lambda: 0, sleep=lambda _: None),
            "uuid": SimpleNamespace(uuid4=lambda: SimpleNamespace(hex="client")),
            "urlsplit": __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit,
            "urlunsplit": __import__("urllib.parse", fromlist=["urlunsplit"]).urlunsplit,
            "httpx": SimpleNamespace(
                Client=MagicMock(), Timeout=MagicMock(), HTTPError=Exception
            ),
            "simple_websocket": SimpleNamespace(
                Client=SimpleNamespace(connect=MagicMock())
            ),
            "const": SimpleNamespace(
                COMFYUI_API_URL="http://comfy:8188/",
                COMFYUI_JOB_TIMEOUT_SECONDS=30,
                COMFY_DIRS={"artifactserver": {
                    "WORKFLOW_TEMPLATES_DIR": "/templates",
                    "WORKFLOWS_DIR": "/workflows",
                }},
            ),
            "log": MagicMock(),
            "workflow_progress": SimpleNamespace(get=lambda: None),
        }
        namespace.update(overrides)
        load_comfy_definitions(namespace)
        return namespace

    def test_workflow_wrapper_and_format_detection(self):
        ns = self.namespace()
        ui = {"nodes": []}
        api = {"1": {"class_type": "Node", "inputs": {}}}
        self.assertIs(ns["Workflow"](ui).serialize(), ui)
        self.assertEqual(ns["workflow_type"](ns["Workflow"](ui)), "ui")
        self.assertEqual(ns["workflow_type"](ns["Workflow"](api)), "api")
        for invalid in ({}, {"1": {"class_type": "Node"}}, {"1": None}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                ns["workflow_type"](ns["Workflow"](invalid))

    def test_module_initializes_its_configuration_and_progress_context(self):
        import sys

        module_path = ROOT / "src/comfy.py"
        spec = importlib.util.spec_from_file_location("comfy_contract_import", module_path)
        module = importlib.util.module_from_spec(spec)
        fake_logger = SimpleNamespace(log=lambda _: MagicMock())
        fake_const = SimpleNamespace()
        with unittest.mock.patch.dict(sys.modules, {"const": fake_const, "logger": fake_logger}):
            spec.loader.exec_module(module)
        self.assertEqual(module.DEFAULT_MINIMUM_FREE_VRAM_GIB, 8.0)
        self.assertEqual(module.WORKFLOW_CONVERSION_RETRY_SECONDS, 60.0)
        self.assertIsNone(module.workflow_progress.get())

    def test_template_loads_first_match_and_wraps_json(self):
        ns = self.namespace()
        ns["glob"].glob = MagicMock(return_value=["/templates/api.i2json.sample.json"])
        # json.load consumes a file-like object; use a real temporary fixture.
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as fixture:
            fixture.write('{"nodes": []}')
            fixture.flush()
            ns["glob"].glob.return_value = [fixture.name]
            result = ns["load_workflow_template"]("api", "i2json", "sample")
        self.assertEqual(result.serialize(), {"nodes": []})

    def test_template_missing_file_raises_domain_error(self):
        ns = self.namespace()
        ns["glob"].glob = MagicMock(return_value=[])
        with self.assertRaisesRegex(ValueError, "Workflow file not found"):
            ns["load_workflow_template"]("api", "t2i", "missing")

    def test_template_path_disappearing_after_lookup_is_reported(self):
        ns = self.namespace()
        ns["glob"].glob = MagicMock(return_value=["/templates/gone.json"])
        ns["os"] = SimpleNamespace(path=SimpleNamespace(join=os.path.join, exists=lambda _: False))
        with self.assertRaisesRegex(ValueError, "does not exist"):
            ns["load_workflow_template"]("api", "t2i", "gone")

    def test_run_workflow_fn_loads_configured_file_and_forwards_timeout(self):
        ns = self.namespace()
        ns["open"] = MagicMock()
        ns["open"].return_value.__enter__.return_value.read.return_value = '{"nodes": []}'
        # json.load requires a file-like object; a StringIO behaves like open().
        from io import StringIO
        ns["open"] = MagicMock(return_value=StringIO('{"nodes": []}'))
        ns["run_workflow"] = MagicMock(return_value="done")
        self.assertEqual(ns["run_workflow_fn"]("sample.json", 12), "done")
        ns["open"].assert_called_once_with("/workflows/sample.json", "r")
        ns["run_workflow"].assert_called_once_with({"nodes": []}, duration_seconds=12)

    def test_free_vram_posts_and_returns_when_cuda_memory_is_sufficient(self):
        ns = self.namespace()
        response = MagicMock()
        response.json.return_value = {"devices": [
            {"type": "cuda", "vram_free": 9 * 1024**3},
            {"type": "cpu", "vram_free": 100 * 1024**3},
        ]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = response
        ns["httpx"].Client.return_value = client
        ns["free_vram"]()
        client.post.assert_called_once_with(
            "http://comfy:8188/free", json={"unload_models": True, "free_memory": True}
        )
        client.get.assert_called_once_with("http://comfy:8188/system_stats")

    def test_free_vram_waits_briefly_for_memory_to_become_available(self):
        ns = self.namespace()
        low = MagicMock()
        low.json.return_value = {"devices": [{"type": "cuda", "vram_free": 0}]}
        enough = MagicMock()
        enough.json.return_value = {"devices": [{"type": "cuda", "vram_free": 9 * 1024**3}]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = [low, enough]
        ns["httpx"].Client.return_value = client
        sleep = MagicMock()
        ns["time"] = SimpleNamespace(monotonic=lambda: 0, sleep=sleep)

        ns["free_vram"](minimum_free_gib=8, timeout_seconds=10)

        sleep.assert_called_once_with(0.5)
        self.assertEqual(client.get.call_count, 2)

    def test_free_vram_restarts_once_then_times_out_if_still_low(self):
        import sys

        ns = self.namespace()
        response = MagicMock()
        response.json.return_value = {"devices": [{"type": "cuda", "vram_free": 0}]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.return_value = response
        ns["httpx"].Client.return_value = client
        restart = MagicMock(return_value="restarted")
        recovery = SimpleNamespace(restart_comfyui=restart)
        with (
            unittest.mock.patch.dict(sys.modules, {"comfy_recovery": recovery}),
            self.assertRaises(TimeoutError),
        ):
            ns["free_vram"](minimum_free_gib=8, timeout_seconds=0)
        restart.assert_called_once_with()
        self.assertEqual(client.get.call_count, 2)

    def test_free_vram_tolerates_a_temporary_stats_failure_after_restart(self):
        import sys

        ns = self.namespace()
        low = MagicMock()
        low.json.return_value = {"devices": [{"type": "cuda", "vram_free": 0}]}
        enough = MagicMock()
        enough.json.return_value = {"devices": [{"type": "cuda", "vram_free": 9 * 1024**3}]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = [low, RuntimeError("service restarting"), enough]
        ns["httpx"].Client.return_value = client
        ns["time"] = SimpleNamespace(
            monotonic=MagicMock(side_effect=[0, 1, 1, 1]), sleep=MagicMock()
        )
        recovery = SimpleNamespace(restart_comfyui=MagicMock(return_value="restarted"))

        with unittest.mock.patch.dict(sys.modules, {"comfy_recovery": recovery}):
            ns["free_vram"](minimum_free_gib=8, timeout_seconds=1)

        recovery.restart_comfyui.assert_called_once_with()
        ns["time"].sleep.assert_called_once_with(1.0)
        self.assertEqual(client.get.call_count, 3)

    def test_free_vram_propagates_stats_failure_before_recovery(self):
        ns = self.namespace()
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = RuntimeError("statistics unavailable")
        ns["httpx"].Client.return_value = client

        with self.assertRaisesRegex(RuntimeError, "statistics unavailable"):
            ns["free_vram"]()

    def test_free_vram_propagates_stats_failure_after_recovery_deadline(self):
        import sys

        ns = self.namespace()
        low = MagicMock()
        low.json.return_value = {"devices": [{"type": "cuda", "vram_free": 0}]}
        client = MagicMock()
        client.__enter__.return_value = client
        client.get.side_effect = [low, RuntimeError("restarted service unavailable")]
        ns["httpx"].Client.return_value = client
        ns["time"] = SimpleNamespace(monotonic=lambda: 0, sleep=MagicMock())
        recovery = SimpleNamespace(restart_comfyui=MagicMock(return_value="restarted"))

        with (
            unittest.mock.patch.dict(sys.modules, {"comfy_recovery": recovery}),
            self.assertRaisesRegex(RuntimeError, "restarted service unavailable"),
        ):
            ns["free_vram"](minimum_free_gib=8, timeout_seconds=0)
        recovery.restart_comfyui.assert_called_once_with()

    def test_run_workflow_substitutes_values_and_keeps_global_job_timeout(self):
        ns = self.namespace()
        connection = MagicMock()
        connection.receive.return_value = json.dumps({
            "type": "execution_success", "data": {"prompt_id": "job"}
        })
        ns["simple_websocket"].Client.connect.return_value = connection
        response = SimpleNamespace(status_code=200, json=lambda: {"prompt_id": "job"})
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response
        ns["httpx"].Client.return_value = client
        progress = ContextVar("contract_progress", default=None)
        ns["workflow_progress"] = progress
        workflow = {"1": {"class_type": "Node", "inputs": {
            "text": "Hello {{NAME}}", "nested": ["{{COUNT}}", 7]
        }}}

        ns["run_workflow"](
            workflow,
            template_environment={"NAME": "Ada", "COUNT": 3},
            duration_seconds=12,
        )

        submitted = client.post.call_args.kwargs["json"]
        self.assertEqual(submitted["prompt"]["1"]["inputs"], {
            "text": "Hello Ada", "nested": ["3", 7]
        })
        self.assertEqual(ns["simple_websocket"].Client.connect.call_args.args[0],
                         "ws://comfy:8188/ws?clientId=client")
        self.assertEqual(ns["simple_websocket"].Client.connect.call_args.kwargs["ping_interval"], 25)
        connection.close.assert_called_once_with()

    def test_run_workflow_rejects_http_failure_and_closes_websocket(self):
        ns = self.namespace()
        connection = MagicMock()
        ns["simple_websocket"].Client.connect.return_value = connection
        response = SimpleNamespace(status_code=503, text="busy")
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response
        ns["httpx"].Client.return_value = client

        with self.assertRaisesRegex(ValueError, "busy"):
            ns["run_workflow"]({"1": {"class_type": "Node", "inputs": {}}})
        connection.close.assert_called_once_with()

    def test_run_workflow_rejects_missing_prompt_id_and_closes_websocket(self):
        ns = self.namespace()
        connection = MagicMock()
        ns["simple_websocket"].Client.connect.return_value = connection
        response = SimpleNamespace(status_code=200, json=dict)
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response
        ns["httpx"].Client.return_value = client

        with self.assertRaisesRegex(RuntimeError, "prompt_id"):
            ns["run_workflow"]({"1": {"class_type": "Node", "inputs": {}}})
        connection.close.assert_called_once_with()

    def test_run_workflow_converts_ui_graph_before_submitting(self):
        ns = self.namespace()
        connection = MagicMock()
        connection.receive.return_value = json.dumps({
            "type": "execution_success", "data": {"prompt_id": "job"}
        })
        ns["simple_websocket"].Client.connect.return_value = connection
        response = SimpleNamespace(status_code=200, json=lambda: {"prompt_id": "job"})
        client = MagicMock()
        client.__enter__.return_value = client
        client.post.return_value = response
        ns["httpx"].Client.return_value = client
        ns["ui_to_api_workflow"] = lambda _: ns["Workflow"]({
            "converted": {"class_type": "Node", "inputs": {}}
        })

        ns["run_workflow"]({"nodes": []})

        self.assertIn("converted", client.post.call_args.kwargs["json"]["prompt"])


if __name__ == "__main__":
    unittest.main()

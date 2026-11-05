"""Every archived obsolete entry point emits its searchable ID before work."""
import ast
import inspect
import json
import logging
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = json.loads((ROOT / 'docs/ui-obsolete-code.json').read_text())


def test_obsolete_ids_are_unique():
    ids = [entry['id'] for entry in REGISTRY]
    assert len(ids) == len(set(ids))
    assert all(entry['evidence'] for entry in REGISTRY)


@pytest.mark.parametrize('entry', REGISTRY, ids=lambda entry: entry['id'])
def test_obsolete_warning_runs_before_legacy_work(entry, caplog):
    tree = ast.parse((ROOT / entry['path']).read_text())
    nodes = tree.body
    for part in entry['symbol'].split('.'):
        definition = next(n for n in nodes if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name == part)
        nodes = definition.body
    # Execute the archived entry point, interrupting immediately after logging
    # so legacy GPU/filesystem operations cannot run in this warning contract test.
    class Observed(Exception):
        pass
    class Log:
        def warning(self, message):
            logging.getLogger('obsolete-test').warning(message)
            raise Observed
    definition.decorator_list = []
    namespace = {'log': Log()}
    exec(compile(ast.Module(body=[definition], type_ignores=[]), entry['path'], 'exec'), namespace)
    fn = namespace[definition.name]
    args = [None for p in inspect.signature(fn).parameters.values()
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD) and p.default is p.empty]
    with caplog.at_level(logging.WARNING), pytest.raises(Observed):
        fn(*args)
    assert caplog.messages == [f"Obsolete code <{entry['id']}>"]

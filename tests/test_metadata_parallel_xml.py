"""Concurrent metadata jobs must never share BeautifulSoup's XML builder."""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import copy
from pathlib import Path
from threading import Barrier, Lock
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup


def test_parallel_metadata_parses_independent_snapshots_and_merges_results(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.scenes',
                        SimpleNamespace(active_scene=lambda image: object()))
    path = Path('src/artifact_editor/images/images.py')
    node = next(n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'generate_missing_metadata')
    xml = '<chapter><paragraph><phrase>Narrative context</phrase>' + ''.join(
        f'<image index="{index}" style="ink" setting="palace"/>' for index in range(6)
    ) + '</paragraph></chapter>'
    soup = BeautifulSoup(xml, 'xml')
    chapter = SimpleNamespace(soup=soup)
    chapter.get_xml = lambda: chapter.soup
    chapter.save_image = Mock()
    # An attempted deepcopy is a deterministic regression failure even on
    # versions/platforms where the parser race happens less frequently.
    def forbidden_copy(*args, **kwargs):
        raise AssertionError('Document deepcopy shares the XML builder')
    monkeypatch.setattr(BeautifulSoup, '__deepcopy__', forbidden_copy)
    barrier = Barrier(3)
    builders = []
    seen_snapshots = []
    def metadata(local, image, **kwargs):
        assert local is not chapter
        assert local.soup is not soup
        assert local.soup.builder is not soup.builder
        assert local.soup.find('phrase').get_text() == 'Narrative context'
        builders.append(local.soup.builder)
        seen_snapshots.append(str(local.soup))
        barrier.wait(timeout=5)
        image['camera'] = 'wide ' + image['index']
        del image['setting']
        local.save_image(image)  # This must be isolated from actual disk saves.
    def describe(image):
        image['description'] = 'drawing ' + image['index']
    namespace = dict(BeautifulSoup=BeautifulSoup, copy=copy, Lock=Lock,
        ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed,
        copy_context=copy_context, characters=SimpleNamespace(get_all_characters=Mock()),
        generate_metadata_from_text=metadata, generate_image_description=describe)
    monkeypatch.setitem(sys.modules, 'gpu_tasks', SimpleNamespace(gpu_label=SimpleNamespace(set=Mock())))
    exec(compile(ast.Module(body=[node], type_ignores=[]),str(path),'exec'),namespace)
    progress = Mock()
    namespace['generate_missing_metadata'](chapter,progress=progress)
    assert len({id(builder) for builder in builders}) == 6
    # Every task in a batch sees the same stable pre-merge snapshot.
    assert len(set(seen_snapshots[:3])) == 1
    assert len(set(seen_snapshots[3:])) == 1
    assert chapter.save_image.call_count == 6
    for image in soup.find_all('image'):
        assert image['camera'] == 'wide ' + image['index']
        assert image['description'] == 'drawing ' + image['index']
        assert 'setting' not in image.attrs
    assert progress.call_args.args[0] == 100

"""Retries reuse finished stills before spending GPU time on their metadata."""
import ast
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextvars import copy_context
from copy import copy, deepcopy
import os
from pathlib import Path
from threading import Lock
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

from bs4 import BeautifulSoup
import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('book_default, image_workflow, expected', [
    (None, None, 'default'),
    ('t2i.qwen', None, 'qwen'),
    ('t2i.qwen', 't2i.zit-paragraph', 'zit-paragraph'),
])
def test_each_completed_batch_image_notifies_after_being_saved(monkeypatch, book_default, image_workflow, expected):
    import sys
    from artifact_editor.textoviva.workflow import image_workflow_name
    image = BeautifulSoup('<image index="3" prompt="Prepared prompt"/>', 'xml').image
    if image_workflow:
        image['workflow_image_template'] = image_workflow
    notifications = []
    chapter = SimpleNamespace(title='Book', images=lambda: [image], get_workflow=Mock(return_value='workflow'))
    chapter.config = {'default_image_template': book_default}
    chapter.save_image = Mock(side_effect=lambda image: notifications.append('saved'))
    chapter.save_images = Mock(side_effect=lambda: notifications.append('saved'))
    def notify(*args):
        assert image.get('image_generation_id')
        assert notifications[-1] == 'saved'
        notifications.append('notified')
    def batch(requests, on_complete, **kwargs):
        on_complete(0, None)
        return [None]
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.images',
                        SimpleNamespace(reuse_existing_image=lambda *_: None))
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.completions',
                        SimpleNamespace(publish_image_completion=notify))
    function = load_function('src/artifact_editor/textoviva/textoviva.py', 'generate_missing_images', dict(
        const=SimpleNamespace(DEFAULT_IMAGE_TEMPLATE='default'), os=os, log=Mock(), uuid=uuid,
        image_workflow_name=image_workflow_name,
        comfy=SimpleNamespace(run_workflow_fn_batch=batch),
        selector_htmx=SimpleNamespace(gather_candidate_images=lambda *_: ['finished.png'])))
    function(SimpleNamespace(chapter=chapter))
    assert notifications == ['saved', 'notified', 'saved']
    assert chapter.get_workflow.call_args.kwargs['workflow_name'] == expected


def load_function(relative, name, namespace):
    source = ROOT / relative
    function = next(node for node in ast.walk(ast.parse(source.read_text()))
                    if isinstance(node, ast.FunctionDef) and node.name == name)
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(source), 'exec'), namespace)
    return namespace[name]


def image_reuser(monkeypatch, candidates):
    monkeypatch.setitem(__import__('sys').modules, 'artifact_editor.images.selector.htmx',
                        SimpleNamespace(gather_candidate_images=candidates))
    return load_function('src/artifact_editor/images/images.py', 'reuse_existing_image', dict(os=os))


def test_reuse_preserves_selected_image_without_search(monkeypatch, tmp_path):
    selected = tmp_path / 'selected.png'
    selected.touch()
    candidates = Mock(side_effect=AssertionError('Do not replace an existing selection'))
    reuse = image_reuser(monkeypatch, candidates)
    chapter = SimpleNamespace(get_image_filename=lambda _: str(selected), save_image=Mock(), save_images=Mock())
    image = BeautifulSoup('<image src="selected.png"/>', 'xml').image
    assert reuse(chapter, image) == str(selected)
    chapter.save_image.assert_not_called()


def test_reuse_recovers_output_after_missing_src_save(monkeypatch, tmp_path):
    completed = tmp_path / 'img_3_finished.png'
    completed.touch()
    reuse = image_reuser(monkeypatch, Mock(return_value=[str(completed)]))
    chapter = SimpleNamespace(get_image_filename=lambda _: None, save_image=Mock(), save_images=Mock())
    image = BeautifulSoup('<image index="3"/>', 'xml').image
    assert reuse(chapter, image) == str(completed)
    assert image['src'] == completed.name
    chapter.save_image.assert_called_once()


def test_add_all_metadata_only_processes_images_without_stills(monkeypatch, tmp_path):
    selected = tmp_path / 'selected.png'
    selected.touch()
    soup = BeautifulSoup('<chapter><paragraph><image index="0"/><image index="1"/></paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(get_xml=lambda: soup, save_image=Mock(), save_images=Mock(),
                              get_image_filename=lambda image: str(selected) if image['index'] == '0' else None)
    reuse = image_reuser(monkeypatch, Mock(return_value=[]))
    metadata, description = Mock(), Mock()
    monkeypatch.setitem(__import__('sys').modules, 'artifact_editor.chapter.scenes',
                        SimpleNamespace(active_scene=lambda _: object()))
    monkeypatch.setitem(__import__('sys').modules, 'gpu_tasks',
                        SimpleNamespace(gpu_label=SimpleNamespace(set=Mock())))
    function = load_function('src/artifact_editor/images/images.py', 'generate_missing_metadata', dict(
        characters=SimpleNamespace(get_all_characters=Mock()), Lock=Lock,
        ThreadPoolExecutor=ThreadPoolExecutor, as_completed=as_completed, copy_context=copy_context,
        copy=copy, deepcopy=deepcopy, reuse_existing_image=reuse,
        generate_metadata_from_text=metadata, generate_image_description=description))
    function(chapter, skip_existing_images=True)
    metadata.assert_called_once()
    assert metadata.call_args.args[1]['index'] == '1'
    description.assert_called_once()


def test_missing_stills_recovers_candidate_without_queueing_generation(monkeypatch, tmp_path):
    completed = tmp_path / 'img_3_finished.png'
    completed.touch()
    reuse = image_reuser(monkeypatch, Mock(return_value=[str(completed)]))
    monkeypatch.setitem(__import__('sys').modules, 'artifact_editor.images.images',
                        SimpleNamespace(reuse_existing_image=reuse))
    image = BeautifulSoup('<image index="3"/>', 'xml').image
    chapter = SimpleNamespace(images=lambda: [image], get_image_filename=lambda _: None,
                              get_workflow=Mock(), save_image=Mock(), save_images=Mock())
    comfy = SimpleNamespace(run_workflow_fn_batch=Mock())
    function = load_function('src/artifact_editor/textoviva/textoviva.py', 'generate_missing_images', dict(
        const=SimpleNamespace(DEFAULT_IMAGE_TEMPLATE='default'), os=os, log=Mock(), comfy=comfy))
    function(SimpleNamespace(chapter=chapter))
    assert image['src'] == completed.name
    chapter.get_workflow.assert_not_called()
    comfy.run_workflow_fn_batch.assert_not_called()


def test_reserved_selected_frame_is_replaced_by_real_output(monkeypatch, tmp_path):
    reserved = tmp_path / 'img_10_last_frame_of_previous.png'
    completed = tmp_path / 'img_10_generated.png'
    reserved.touch()
    completed.touch()
    reuse = image_reuser(monkeypatch, Mock(return_value=[str(reserved), str(completed)]))
    chapter = SimpleNamespace(get_image_filename=lambda _: str(reserved), save_image=Mock())
    image = BeautifulSoup('<image index="10"/>', 'xml').image
    assert reuse(chapter, image) == str(completed)
    assert image['src'] == completed.name


def test_candidate_discovery_excludes_reserved_current_and_globbed_frames(tmp_path):
    import glob
    import shutil
    reserved = tmp_path / 'img_10_last_frame_of_previous.png'
    completed = tmp_path / 'img_10_generated.png'
    reserved.touch()
    completed.touch()
    image = BeautifulSoup('<paragraph index="5"><image index="10"/></paragraph>', 'xml').image
    chapter = SimpleNamespace(nice='book', get_paragraph_dir=lambda _: '.',
                              get_image_filename=lambda _: str(reserved),
                              get_last_frame=Mock(side_effect=AssertionError('Do not create continuation frames')))
    gather = load_function('src/artifact_editor/images/selector/htmx.py', 'gather_candidate_images', dict(
        Tag=object, Chapter=object, os=os, glob=glob, shutil=shutil, as_int=int, log=Mock(),
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path), COMFY_DIRS={'artifactserver': {'OUTPUT_DIR': str(tmp_path)}}),
        images=SimpleNamespace(all_images_by_image_index=lambda *_: [str(reserved), str(completed)])))
    assert gather(image, chapter) == [str(completed)]
    chapter.get_last_frame.assert_not_called()


def test_reserved_frames_are_not_image_outputs(tmp_path):
    import glob
    (tmp_path / 'img_10_last_frame_of_previous.png').touch()
    completed = tmp_path / 'img_10_generated.png'
    completed.touch()
    discover = load_function('src/artifact_editor/images/images.py', 'all_images_by_image_index', dict(
        os=os, glob=glob, const=SimpleNamespace(LIBRARY_DIR=str(tmp_path)), log=Mock()))
    assert [Path(path) for path in discover('.', 10)] == [completed]


def test_image_selection_rejects_reserved_frame():
    from urllib.parse import unquote_plus
    chapter = SimpleNamespace(get_image=Mock(return_value=BeautifulSoup('<image/>', 'xml').image), save_image=Mock())
    choose = load_function('src/artifact_editor/images/selector/views.py', 'use_image', dict(
        os=os, unquote_plus=unquote_plus, log=Mock(), g=SimpleNamespace(chapter=chapter),
        bp=SimpleNamespace(route=lambda *a, **kw: lambda fn: fn),
        request=SimpleNamespace(form={'selectedImage': 'img_10_last_frame_of_previous.png', 'image_index': '10'})))
    assert choose()[1] == 400
    chapter.save_image.assert_not_called()

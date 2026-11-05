"""Old completion markers must not enable video while media is missing."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

from bs4 import BeautifulSoup
import pytest

spec = importlib.util.spec_from_file_location('video_readiness', Path(__file__).resolve().parents[1] / 'src/artifact_editor/chapter/readiness.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_incomplete_stage_blocks_without_reading_assets():
    chapter = SimpleNamespace(images=Mock())
    assert module.video_prerequisite(chapter, 'Must add missing images first') == 'Must add missing images first'
    chapter.images.assert_not_called()


def test_missing_still_blocks_even_with_completed_milestones():
    chapter = SimpleNamespace(images=lambda: [{}], get_image_filename=lambda _: None)
    assert module.video_prerequisite(chapter) == 'Must finish generating missing images first'


@pytest.mark.parametrize('skip,count,expected', [(False,0,True), (False,10,True), (False,24,False), (True,0,False)])
def test_video_requires_sufficient_animation_unless_image_opts_out(monkeypatch, tmp_path, skip, count, expected):
    image = BeautifulSoup('<paragraph index="0"><image index="0" frames="24"/></paragraph>', 'xml').image
    if skip:
        image['do_not_animate'] = '1'
    still = tmp_path / 'still.png'
    still.touch()
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=24))
    monkeypatch.setitem(sys.modules, 'artifact_editor.chapter.timeline', SimpleNamespace(image_timings=lambda _: {'0': {'frames':24}}))
    monkeypatch.setitem(sys.modules, 'artifact_editor.images.animation_source', SimpleNamespace(animation_frame_count=lambda *args: count))
    chapter = SimpleNamespace(images=lambda: [image], get_image_filename=lambda _: str(still),
        get_xml=lambda: None, get_paragraph_dir=lambda _: 'paragraph')
    reason = module.video_prerequisite(chapter)
    assert bool(reason) == expected

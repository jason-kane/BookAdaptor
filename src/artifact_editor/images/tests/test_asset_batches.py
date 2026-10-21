from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from artifact_editor.textoviva import textoviva


def test_images_only_generates_missing_stills(monkeypatch, tmp_path):
    soup = BeautifulSoup('<chapter><paragraph><image index="0"/><image index="1"/></paragraph></chapter>', 'xml')
    existing = tmp_path / 'existing.png'
    existing.touch()
    chapter = SimpleNamespace(images=lambda: iter(soup.find_all('image')),
        get_image_filename=lambda image: str(existing) if image['index'] == '0' else None,
        get_video_filename=lambda: str(tmp_path / 'video.mp4'),
        get_workflow=Mock(return_value='still-workflow'), save_xml=Mock())
    workflow = Mock()
    monkeypatch.setattr(textoviva.comfy, 'run_workflow_fn', workflow)
    monkeypatch.setattr(textoviva.selector_htmx, 'gather_candidate_images', lambda *args: ['new.png'])
    instance = textoviva.Textoviva(chapter)
    instance.generate_missing_animations = Mock(side_effect=AssertionError('Unexpected animation'))
    instance.generate_missing_images()
    workflow.assert_called_once_with('still-workflow')
    instance.generate_missing_animations.assert_not_called()
    assert soup.find('image', index='1')['src'] == 'new.png'


def test_animations_only_requires_stills_and_never_draws_them(tmp_path):
    soup = BeautifulSoup('<chapter><paragraph><image index="0"/></paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(images=lambda: iter(soup.find_all('image')),
        get_image_filename=lambda image: None,
        get_video_filename=lambda: str(tmp_path / 'video.mp4'), get_workflow=Mock())
    instance = textoviva.Textoviva(chapter)
    with pytest.raises(ValueError, match='Generate missing images'):
        instance.generate_missing_animations()
    chapter.get_workflow.assert_not_called()

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bs4 import BeautifulSoup
from artifact_editor.textoviva import textoviva


def test_images_only_generates_missing_stills(monkeypatch, tmp_path):
    soup = BeautifulSoup('<chapter><paragraph><image index="0"/><image index="1" prompt="Prepared prompt"/></paragraph></chapter>', 'xml')
    existing = tmp_path / 'existing.png'
    existing.touch()
    chapter = SimpleNamespace(title='Test', images=lambda: iter(soup.find_all('image')),
        get_image_filename=lambda image: str(existing) if image['index'] == '0' else None,
        get_video_filename=lambda: str(tmp_path / 'video.mp4'),
        get_workflow=Mock(return_value='still-workflow'), save_image=Mock(), save_images=Mock())
    def complete(workflows, on_complete, **kwargs):
        for position in range(len(workflows)):
            on_complete(position, [])
        return [[] for _ in workflows]
    workflow = Mock(side_effect=complete)
    monkeypatch.setattr(textoviva.comfy, 'run_workflow_fn_batch', workflow)
    monkeypatch.setattr('artifact_editor.images.completions.publish_image_completion', Mock())
    monkeypatch.setattr(textoviva.selector_htmx, 'gather_candidate_images', lambda *args: ['new.png'])
    instance = textoviva.Textoviva(chapter)
    instance.generate_missing_animations = Mock(side_effect=AssertionError('Unexpected animation'))
    instance.generate_missing_images()
    workflow.assert_called_once()
    assert [item['workflow_fn'] for item in workflow.call_args.args[0]] == ['still-workflow']
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

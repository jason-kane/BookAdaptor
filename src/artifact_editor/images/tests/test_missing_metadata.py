from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup

from artifact_editor.images import images


def test_missing_metadata_visits_all_images_preserves_descriptions_and_reports_progress(monkeypatch):
    soup = BeautifulSoup('<chapter><paragraph><image index="2" description="Keep this"/>'
                         '<image index="7"/><image index="9" description=" "/>'
                         '</paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(get_xml=lambda: soup, save_xml=Mock())
    generate = Mock()
    describe = Mock(side_effect=lambda image: image.__setitem__('description', 'Generated'))
    monkeypatch.setattr(images, 'generate_metadata_from_text', generate)
    monkeypatch.setattr(images, 'generate_image_description', describe)
    progress = Mock()
    images.generate_missing_metadata(chapter, progress=progress)
    assert generate.call_count == 3
    assert all(call.kwargs == {'force_all': False} for call in generate.call_args_list)
    assert [call.args[1]['index'] for call in generate.call_args_list] == ['2', '7', '9']
    assert describe.call_count == 2
    assert soup.find('image', index='2')['description'] == 'Keep this'
    assert chapter.save_xml.call_count == 3
    progress.assert_called_with(100, 'Missing metadata complete')


def test_missing_metadata_handles_empty_chapter(monkeypatch):
    chapter = SimpleNamespace(get_xml=lambda: BeautifulSoup('<chapter/>', 'xml'), save_xml=Mock())
    generate = Mock()
    monkeypatch.setattr(images, 'generate_metadata_from_text', generate)
    progress = Mock()
    images.generate_missing_metadata(chapter, progress=progress)
    generate.assert_not_called()
    progress.assert_called_once_with(100, 'Missing metadata complete')

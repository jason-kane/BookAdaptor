"""Slot placement uses audio timing and preserves existing image identities."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

spec = importlib.util.spec_from_file_location('slot_timeline', Path(__file__).resolve().parents[1] / 'src/artifact_editor/chapter/timeline.py')
timeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(timeline)


def test_slot_placement_requires_audio_timing():
    soup = BeautifulSoup('<chapter><paragraph><phrase index="0">Hello</phrase></paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(get_xml=lambda: soup, save_timeline=Mock())
    with pytest.raises(ValueError, match='Add missing audio'):
        timeline.place_image_slots(chapter)
    assert not soup.find('image')
    chapter.save_timeline.assert_not_called()


def test_slot_placement_is_repeatable_and_preserves_source_images():
    soup = BeautifulSoup('<chapter><paragraph><image index="7" source_image="book.png" src="book.png"/>'
        + ''.join(f'<phrase index="{i}" duration="4" frames="96">Line</phrase>' for i in range(8))
        + '</paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(get_xml=lambda: soup, save_timeline=Mock())
    timeline.place_image_slots(chapter)
    first = str(soup)
    assert len(soup.find_all('image')) > 1
    assert soup.find('image', index='7')['src'] == 'book.png'
    timeline.place_image_slots(chapter)
    assert str(soup) == first
    assert all(int(image['frames']) > 0 for image in soup.find_all('image'))

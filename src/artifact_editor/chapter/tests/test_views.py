# test cases for artifact_editor/chapter/views.py

import pytest
import logging
from artifact_editor.chapter import views

log = logging.getLogger(__name__)

def test_import_chapter_runs_without_http_context_and_places_images_after_audio(monkeypatch, tmp_path):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from artifact_editor.phrases import phrases

    monkeypatch.setattr(views.const, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(views, "sentence_pipeline", lambda text: SimpleNamespace(
        sents=[SimpleNamespace(text=text.strip(), text_with_ws=text)]))
    monkeypatch.setattr(views.subprocess, "run", Mock())
    monkeypatch.setattr(views, "crop_chapter_text_images", Mock())
    chapter = SimpleNamespace(
        language="en", aspect=next(iter(views.const.GEOMETRY)),
        title="Test", author=SimpleNamespace(name="Author"),
        get_chapterdir=lambda: "chapter", get_paragraph_dir=lambda index: f"paragraphs/{index}",
        save_delta=Mock(), save_xml=Mock(),
    )
    chapter.get_xml = lambda: chapter.soup

    def generate_audio(chapter, force, progress):
        assert force is False
        assert not chapter.soup.find_all("image")
        for tag in chapter.soup.find_all("phrase"):
            assert tag["speaker"] == "Narrator"
            assert "index" in tag.find_parent("paragraph").attrs
            tag["duration"], tag["frames"] = "4", "96"
        progress(100, "Audio complete")

    monkeypatch.setattr(phrases, "generate_all_audio", generate_audio)
    mood, theme = Mock(), Mock()
    monkeypatch.setattr(views, "generate_chapter_mood", mood)
    monkeypatch.setattr(views, "generate_chapter_theme", theme)
    progress = Mock()
    views.import_chapter_content(chapter, json.dumps([
        {"typesetting_method": "verse", "ops": [{"insert": "Hello world.\n"}]}
    ]), progress=progress)
    assert chapter.soup.find("image") is not None
    assert chapter.soup.find("image").find_next_sibling("phrase") is not None
    mood.assert_called_once_with(chapter)
    theme.assert_called_once_with(chapter)
    progress.assert_called_with(100, "Chapter import complete")


def test_compress_ops():
    # Test the compress_ops function
    ops = [
        {"insert": "Hello", "attributes": {"bold": True}},
        {"insert": " World", "attributes": {"bold": True}},
        {"insert": "!", "attributes": {}},
    ]
    compressed = views.compress_ops(ops)
    log.info("Compressed ops: %s", compressed)
    assert len(compressed) == 2  # The first two ops should be merged
    assert compressed[0]["insert"] == "Hello World"
    assert compressed[0]["attributes"]["bold"] is True
    assert compressed[1]["insert"] == "!"
    assert compressed[1]["attributes"] == {}

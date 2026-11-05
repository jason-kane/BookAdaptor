# test cases for artifact_editor/chapter/views.py

import pytest
import logging
from artifact_editor.chapter import views

log = logging.getLogger(__name__)

@pytest.mark.parametrize('illustration', [False, True])
@pytest.mark.parametrize('method', ['plain', 'verse'])
def test_import_chapter_runs_without_http_context_and_places_images_after_audio(monkeypatch, tmp_path, method, illustration):
    import json
    from types import SimpleNamespace
    from unittest.mock import Mock
    from artifact_editor.phrases import phrases

    monkeypatch.setattr(views.const, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(views, "sentence_pipeline", lambda text: SimpleNamespace(
        sents=[SimpleNamespace(text=text.strip(), text_with_ws=text)]))
    from artifact_editor.images import source
    monkeypatch.setattr(source, "source_images", lambda _: ["images/image053.jpg"])
    choose = Mock()
    monkeypatch.setattr(source, "choose_source_image", choose)
    typeset = Mock()
    monkeypatch.setattr(views, "enqueue_typesetting", typeset)
    chapter = SimpleNamespace(
        language="en", aspect=next(iter(views.const.GEOMETRY)),
        title="Test", author=SimpleNamespace(name="Author"),
        get_chapterdir=lambda: "chapter", get_paragraph_dir=lambda index: f"paragraphs/{index}",
        save_delta=Mock(), replace_content=Mock(), save_timeline=Mock(),
    )
    chapter.get_xml = lambda: chapter.soup
    chapter.replace_content.side_effect = lambda content: setattr(chapter, 'soup', content)

    def generate_audio(chapter, force, progress, batch):
        assert force is False
        assert batch is True
        assert len(chapter.soup.find_all("image")) == int(illustration)
        assert not chapter.soup.find_all("illustration")
        chapter.save_timeline.assert_called_once()
        assert choose.call_count == int(illustration)
        assert "Illustration:" not in chapter.soup.get_text()
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
        {"typesetting_method": method, "ops": [{"insert": "Hello world.\n" + ("[Illustration: image053]\nAfter image.\n" if illustration else "")}]}
    ]), progress=progress)
    assert chapter.soup.find("image") is not None
    assert chapter.soup.find("image").find_next_sibling("phrase") is not None
    assert all(phrase['typesetting_method'] == method for phrase in chapter.soup.find_all('phrase'))
    if illustration:
        image = chapter.soup.find("image", source_image="images/image053.jpg")
        assert image.find_next_sibling("phrase").get_text().strip() == "After image."
        choose.assert_called_once_with(chapter, image, "images/image053.jpg")
    else:
        choose.assert_not_called()
    typeset.assert_called_once_with(chapter, wait=True, source='latex')
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

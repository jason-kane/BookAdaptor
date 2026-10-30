"""Book illustrations can be browsed safely and used as ordinary image assets."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image
import pytest

from artifact_editor.images import source
from artifact_editor.images.panel import panel_controls


@pytest.fixture
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(source, "output_size", lambda *_: (160, 160))
    root = tmp_path / "Author" / "Book" / "source"
    (root / "images").mkdir(parents=True)
    chapter = SimpleNamespace(
        bookdir="Author/Book", kwargs={}, save_xml=Mock(),
        get_paragraph_dir=lambda index: f"Author/Book/chapter/0001/paragraphs/{int(index):06}",
    )
    image = BeautifulSoup('<paragraph index="3"><image index="4" src="previous.png"/></paragraph>', "xml").image
    Image.new("RGB", (240, 400), "red").save(root / "images" / "illustration.jpg")
    return tmp_path, root, chapter, image


def test_lists_nested_images_and_excludes_files_outside_book(book):
    library, root, chapter, _ = book
    (root / "book.html").write_text("book")
    Image.new("RGB", (2, 2)).save(root / "front.PNG")
    outside = library / "outside.png"
    Image.new("RGB", (2, 2)).save(outside)
    (root / "escaped.png").symlink_to(outside)
    assert source.source_images(chapter, library) == ["front.PNG", "images/illustration.jpg"]
    chapter.bookdir = "Author/Without Source"
    assert source.source_images(chapter, library) == []


@pytest.mark.parametrize("filename", ["../outside.png", "/tmp/outside.png", "images/../../outside.png", "book.html"])
def test_rejects_invalid_selection_without_changing_image(book, filename):
    library, _, chapter, image = book
    with pytest.raises(ValueError):
        source.choose_source_image(chapter, image, filename, library)
    assert image["src"] == "previous.png"
    chapter.save_xml.assert_not_called()


def test_choosing_jpeg_preserves_original_and_creates_full_size_png(book):
    library, root, chapter, image = book
    original = (root / "images/illustration.jpg").read_bytes()
    destination = source.choose_source_image(chapter, image, "images/illustration.jpg", library)
    assert destination.parent == library / chapter.get_paragraph_dir("3")
    assert destination.name.startswith("img_4_source_")
    with Image.open(destination) as chosen:
        assert chosen.format == "PNG"
        assert chosen.size == (240, 400)
    assert (root / "images/illustration.jpg").read_bytes() == original
    assert image["src"] == destination.name
    assert image["source_image"] == "images/illustration.jpg"
    assert image["workflow_image_template"] == source.SOURCE_WORKFLOW
    assert image["image_generation_id"]
    chapter.save_xml.assert_called_once()


def test_transparency_and_same_named_illustrations_are_preserved(book):
    library, root, chapter, image = book
    (root / "other").mkdir()
    for relative, color in (("images/art.png", (255, 0, 0, 0)), ("other/art.png", (0, 255, 0, 127))):
        Image.new("RGBA", (5, 9), color).save(root / relative)
    first = source.choose_source_image(chapter, image, "images/art.png", library)
    second = source.choose_source_image(chapter, image, "other/art.png", library)
    assert first != second
    with Image.open(first) as chosen:
        assert chosen.getpixel((0, 0)) == (255, 0, 0, 0)
    with Image.open(second) as chosen:
        assert chosen.getpixel((0, 0)) == (0, 255, 0, 127)


def test_broken_image_does_not_replace_current_selection(book):
    library, root, chapter, image = book
    (root / "broken.png").write_text("not an image")
    with pytest.raises(OSError):
        source.choose_source_image(chapter, image, "broken.png", library)
    assert image["src"] == "previous.png"
    chapter.save_xml.assert_not_called()


def picker_app():
    app = Flask(__name__, template_folder=str(Path(__file__).parents[1] / "src/artifact_editor/images/templates"))
    for endpoint, rule in (
        ("chapter_source_image", "/images/<int:image_index>/source_image/<path:filename>"),
        ("choose_chapter_source", "/images/<int:image_index>/source_choose"),
    ):
        app.add_url_rule(rule, endpoint="library.book.chapter.images." + endpoint, view_func=lambda **_: "")
    return app


def test_browsing_only_changes_preview_and_inline_choice_targets_own_slot(book, monkeypatch):
    _, root, chapter, image = book
    Image.new("RGB", (3, 4)).save(root / "other.png")
    monkeypatch.setattr(source, "source_directory", lambda *_: root)
    with picker_app().test_request_context():
        markup = source.source_picker(chapter, image, "other.png")
    assert image["src"] == "previous.png"
    chapter.save_xml.assert_not_called()
    for index in (4, 7):
        parsed = BeautifulSoup(panel_controls(markup, index, f"/images/{index}/", f"/timeline/image/{index}"), "html.parser")
        assert parsed.select_one('input[name="image_source"]')["value"] == "other.png"
        assert parsed.select_one('button[aria-pressed="true"]')["aria-label"] == "Preview other.png"
        choose = parsed.select_one(f"#timeline-prompt-{index}-source-image-choose-button")
        assert choose["hx-post"] == f"/timeline/image/{index}"
        assert choose["hx-target"] == f"#timeline-image-editor-{index}"
        assert choose["hx-include"] == "closest .source-image-picker"
        assert '"source"' in choose["hx-vals"]


def test_legacy_source_copy_is_preselected_and_empty_books_have_no_choose_button(book, monkeypatch):
    _, root, chapter, image = book
    monkeypatch.setattr(source, "source_directory", lambda *_: root)
    image["src"] = "img_2_illustration.jpg"
    with picker_app().test_request_context():
        parsed = BeautifulSoup(source.source_picker(chapter, image), "html.parser")
        assert parsed.input["value"] == "images/illustration.jpg"
        monkeypatch.setattr(source, "source_directory", lambda *_: root / "missing")
        parsed = BeautifulSoup(source.source_picker(chapter, image), "html.parser")
        assert "No source images" in parsed.text
        assert parsed.find("wa-button") is None

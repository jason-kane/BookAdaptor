"""Exercise Chapter's persistence boundary without importing model services."""
import ast
import importlib.util
from pathlib import Path
import traceback
from unittest.mock import Mock

from bs4 import BeautifulSoup, Tag
import pytest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "chapter_persistence", ROOT / "src/artifact_editor/chapter/legacy_persistence.py"
)
persistence = importlib.util.module_from_spec(spec)
spec.loader.exec_module(persistence)

tree = ast.parse((ROOT / "src/artifact_editor/chapter/chapter.py").read_text())
chapter_class = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Chapter")
methods = {
    "save_xml", "replace_content", "save_timeline", "_save_entities", "save_image", "save_images",
    "save_phrase", "save_phrases", "save_scene", "save_sound", "save_paragraph", "save_paragraphs",
    "save_chapter_metadata", "save_layout", "save_timing",
}
boundary = ast.ClassDef(name="Boundary", bases=[], keywords=[], decorator_list=[],
                        body=[node for node in chapter_class.body
                              if isinstance(node, ast.FunctionDef) and node.name in methods])
log = Mock()
namespace = dict(Tag=Tag, traceback=traceback, log=log,
                 replace_content=persistence.replace_content, save_records=persistence.save_records)
exec(compile(ast.fix_missing_locations(ast.Module(body=[boundary], type_ignores=[])),
             str(ROOT / "src/artifact_editor/chapter/chapter.py"), "exec"), namespace)


class Chapter(namespace["Boundary"]):
    def __init__(self, path):
        self.path = path
        self.soup = BeautifulSoup(path.read_text(), "xml")
        persistence.remember_content(self)

    def get_xml_fn(self):
        return str(self.path)

    def get_xml(self):
        return self.soup


@pytest.fixture
def chapter_path(tmp_path):
    path = tmp_path / "chapter.xml"
    path.write_text('<chapter><paragraph index="0"><image index="3" src="old.png" mood="old"/>'
                    '<phrase index="4" speaker="Narrator">Hello</phrase>'
                    '<image index="8" src="other.png"/></paragraph></chapter>')
    return path


def read(path):
    return BeautifulSoup(path.read_text(), "xml")


def test_save_image_does_not_commit_pending_phrase_edits(chapter_path):
    chapter = Chapter(chapter_path)
    chapter.soup.image["src"] = "new.png"
    chapter.soup.phrase["speaker"] = "Tip"
    chapter.save_image(chapter.soup.image)
    assert read(chapter_path).image["src"] == "new.png"
    assert read(chapter_path).phrase["speaker"] == "Narrator"
    chapter.save_phrase(chapter.soup.phrase)
    assert read(chapter_path).phrase["speaker"] == "Tip"


def test_workers_merge_changes_to_different_images(chapter_path):
    first, second = Chapter(chapter_path), Chapter(chapter_path)
    first.soup.image["animated_src"] = "clip.mp4"
    second.soup.find("image", index="8")["src"] = "new-other.png"
    first.save_image(first.soup.image)
    second.save_images()
    actual = read(chapter_path)
    assert actual.image["animated_src"] == "clip.mp4"
    assert actual.find("image", index="8")["src"] == "new-other.png"


def test_workers_merge_independent_fields_and_removals_on_same_image(chapter_path):
    first, second = Chapter(chapter_path), Chapter(chapter_path)
    first.soup.image["src"] = "new.png"
    second.soup.image.attrs.pop("mood")
    first.save_image(first.soup.image)
    second.save_image(second.soup.image)
    assert read(chapter_path).image["src"] == "new.png"
    assert "mood" not in read(chapter_path).image.attrs
    # A second edit from the stale first worker must not restore deleted mood.
    first.soup.image["animation_prompt"] = "Walk"
    first.save_image(first.soup.image)
    assert "mood" not in read(chapter_path).image.attrs


def test_save_phrase_preserves_text_and_concurrent_image_changes(chapter_path):
    first, second = Chapter(chapter_path), Chapter(chapter_path)
    first.soup.image["src"] = "new.png"
    first.save_image(first.soup.image)
    second.soup.phrase.string = "Tip & Jack <talk>"
    second.save_phrase(second.soup.phrase)
    assert read(chapter_path).phrase.get_text().strip() == "Tip & Jack <talk>"
    assert read(chapter_path).image["src"] == "new.png"


def test_layout_save_does_not_commit_or_forget_pending_image_changes(chapter_path):
    chapter = Chapter(chapter_path)
    chapter.soup.paragraph["bottom"] = 42
    chapter.soup.phrase["top"] = 3
    chapter.soup.image["src"] = "pending.png"
    chapter.save_layout()
    assert read(chapter_path).paragraph["bottom"] == "42"
    assert read(chapter_path).phrase["top"] == "3"
    assert read(chapter_path).image["src"] == "old.png"
    chapter.save_image(chapter.soup.image)
    assert read(chapter_path).image["src"] == "pending.png"


def test_timeline_changes_require_explicit_structural_save(chapter_path):
    chapter = Chapter(chapter_path)
    image = chapter.soup.new_tag("image", attrs={"index": "10"})
    chapter.soup.phrase.insert_after(image)
    with pytest.raises(ValueError, match="save_timeline"):
        chapter.save_image(image)
    chapter.save_timeline()
    assert [tag["index"] for tag in read(chapter_path).find_all(["image", "phrase"])] == ["3", "4", "10", "8"]
    image["src"] = "added.png"
    chapter.save_image(image)
    assert read(chapter_path).find("image", index="10")["src"] == "added.png"


def test_record_save_does_not_resurrect_concurrently_deleted_image(chapter_path):
    first, second = Chapter(chapter_path), Chapter(chapter_path)
    first.soup.image.decompose()
    first.save_timeline()
    second.soup.image["src"] = "stale.png"
    with pytest.raises(ValueError, match="not persisted"):
        second.save_image(second.soup.image)
    assert read(chapter_path).find("image", index="3") is None


def test_import_replaces_content_and_establishes_record_baseline(chapter_path):
    chapter = Chapter(chapter_path)
    replacement = BeautifulSoup('<chapter><paragraph index="1"><image index="9"/></paragraph></chapter>', "xml")
    chapter.replace_content(replacement)
    assert read(chapter_path).find("image", index="3") is None
    replacement.image["src"] = "imported.png"
    chapter.save_image(replacement.image)
    assert read(chapter_path).image["src"] == "imported.png"


def test_failed_atomic_write_keeps_previous_content_and_baseline(chapter_path, monkeypatch):
    chapter = Chapter(chapter_path)
    before = chapter_path.read_bytes()
    chapter.soup.image["src"] = "new.png"
    with monkeypatch.context() as patch:
        patch.setattr(persistence.os, "replace", Mock(side_effect=OSError("disk failure")))
        with pytest.raises(OSError, match="disk failure"):
            chapter.save_image(chapter.soup.image)
    assert chapter_path.read_bytes() == before
    assert [path.name for path in chapter_path.parent.glob("chapter.xml.*")] == ["chapter.xml.lock"]
    chapter.save_image(chapter.soup.image)
    assert read(chapter_path).image["src"] == "new.png"


def test_obsolete_save_logs_error_with_caller_traceback_without_writing(chapter_path):
    chapter = Chapter(chapter_path)
    before = chapter_path.read_bytes()
    chapter.soup.image["src"] = "must-not-save.png"
    log.reset_mock()
    chapter.save_xml()
    log.error.assert_called_once()
    assert "test_obsolete_save_logs_error_with_caller_traceback_without_writing" in log.error.call_args.kwargs["traceback"]
    assert chapter_path.read_bytes() == before


def test_record_kind_is_validated(chapter_path):
    chapter = Chapter(chapter_path)
    with pytest.raises(TypeError, match="image"):
        chapter.save_image(chapter.soup.phrase)

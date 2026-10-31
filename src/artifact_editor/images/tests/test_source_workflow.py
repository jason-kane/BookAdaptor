"""Exercise source selection through the workshop and chapter HTTP actions."""
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
from flask import g
from PIL import Image
import pytest

import const
from artifact_editor.images.source import SOURCE_WORKFLOW


@pytest.fixture
def source_editor(app, tmp_path, monkeypatch):
    from artifact_editor.cache import cache
    from artifact_editor.images import completions
    from text_to_image.comfyui.workflow import ui

    monkeypatch.setattr(const, "LIBRARY_DIR", str(tmp_path))
    monkeypatch.setattr(cache, "clear", Mock())
    monkeypatch.setattr(completions, "publish_image_completion", Mock())
    monkeypatch.setattr(ui, "image_scene_workshop", lambda *_: "<p>Generated image controls</p>")
    soup = BeautifulSoup('<chapter><paragraph index="3"><image index="4"/></paragraph></chapter>', "xml")
    image = soup.image
    kwargs = dict(author="Source Tester", title="Example", chapter_number=1, language="english")
    chapter = SimpleNamespace(
        author=SimpleNamespace(name=kwargs["author"]), title=kwargs["title"],
        number=1, language="english", kwargs=kwargs, bookdir="Source Tester/Example",
        chapterdir="Source Tester/Example/chapter/0001", nice="source_workflow_test",
        is_first_image=lambda _: True, timeline=lambda: [],
        get_image=lambda index: image if index == 4 else None,
        get_paragraph_dir=lambda index: f"Source Tester/Example/chapter/0001/paragraphs/{int(index):06}",
    )
    chapter.get_image_filename = lambda element: (
        str(tmp_path / chapter.get_paragraph_dir("3") / element["src"])
        if element.get("src") else None
    )
    saved = tmp_path / "saved.xml"
    chapter.save_image = lambda image: saved.write_text(str(soup))
    chapter.save_images = lambda: saved.write_text(str(soup))
    chapter.save_timing = lambda: saved.write_text(str(soup))
    root = tmp_path / chapter.bookdir / "source" / "images"
    root.mkdir(parents=True)
    Image.new("RGB", (240, 400), "red").save(root / "art.jpg")
    Image.new("RGBA", (300, 150), (0, 50, 0, 127)).save(root / "art.png")

    def bind_chapter(endpoint, values):
        g.chapter = chapter
        for key in kwargs:
            if values:
                values.pop(key, None)

    monkeypatch.setattr(app, "url_value_preprocessors", {None: [bind_chapter]})
    return SimpleNamespace(
        client=app.test_client(), image=image, chapter=chapter, saved=saved,
        base="/library/Source%20Tester/Example/1/english", root=root,
    )


def test_switching_workflows_and_choosing_in_chapter_updates_gallery(source_editor):
    editor = source_editor
    image_url = editor.base + "/images/4"
    headers = {"X-Chapter-Image-Panel": "4"}
    response = editor.client.put(image_url + "/workflow_choose", data={"workflow_image_template": SOURCE_WORKFLOW}, headers=headers)
    assert response.status_code == 200
    picker = BeautifulSoup(response.data, "html.parser")
    choose = picker.select_one("#timeline-prompt-4-source-image-choose-button")
    assert choose["hx-post"] == editor.base + "/timeline/image/4"
    assert choose["hx-target"] == "#timeline-image-editor-4"
    assert len(picker.select(".source-image-thumbnail")) == 2
    assert not editor.image.get("src")

    response = editor.client.post(editor.base + "/timeline/image/4", data={"action": "source", "image_source": "images/art.png"})
    assert response.status_code == 200
    assert "HX-Refresh" not in response.headers
    chosen = Path(editor.chapter.get_image_filename(editor.image))
    with Image.open(chosen) as illustration:
        assert illustration.size == (300, 150)
        assert illustration.mode == "RGBA"
    saved_image = BeautifulSoup(editor.saved.read_text(), "xml").image
    assert saved_image["src"] == chosen.name
    page = BeautifulSoup(response.data, "html.parser")
    assert page.select_one(f'.chapter-image-thumbnail img[alt="{chosen.name}"]')
    assert page.select_one('input[name="image_source"]')["value"] == "images/art.png"
    assert page.select_one("#timeline-prompt-4-source-image-choose-button")

    response = editor.client.put(image_url + "/workflow_choose", data={"workflow_image_template": "t2i.zit-paragraph"}, headers=headers)
    assert response.status_code == 200
    assert b"Generate Image" in response.data
    assert b"source-image-picker" not in response.data
    assert editor.image["src"] == chosen.name


def test_workshop_choice_refreshes_and_source_workflow_cannot_generate(source_editor):
    editor = source_editor
    url = editor.base + "/images/4"
    response = editor.client.post(url + "/source_choose", data={"image_source": "images/art.jpg"})
    assert response.status_code == 200
    assert response.headers["HX-Refresh"] == "true"
    assert editor.image["workflow_image_template"] == SOURCE_WORKFLOW
    assert Path(editor.chapter.get_image_filename(editor.image)).is_file()
    response = editor.client.post(url + "/workflow_run")
    assert response.status_code == 400
    assert b"Choose a source image" in response.data
    response = editor.client.post(editor.base + "/timeline/image/4", data={"action": "generate"})
    assert response.status_code == 400


def test_source_previews_are_bounded_and_outside_files_are_not_served(source_editor):
    editor = source_editor
    url = editor.base + "/images/4/source_image/"
    response = editor.client.get(url + "images/art.jpg?height=160")
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.data)) as thumbnail:
        assert thumbnail.size == (96, 160)
    assert editor.client.get(url + "../outside.png").status_code == 404
    assert editor.client.get(url + "images/art.jpg?height=99999").status_code == 400
    response = editor.client.post(editor.base + "/images/4/source_choose", data={"image_source": "../outside.png"})
    assert response.status_code == 400
    assert not editor.saved.exists()


def test_crop_creates_selectable_source_option_at_target_aspect(source_editor):
    editor = source_editor
    response = editor.client.post(
        editor.base + "/images/4/source_picker/crop",
        data={"image_source": "images/art.jpg"},
    )
    assert response.status_code == 200
    picker = BeautifulSoup(response.data, "html.parser")
    crop = picker.select_one('input[name="image_source"]')
    assert crop["value"].startswith("cropped/art_")
    assert crop["value"].endswith(".png")
    assert len(picker.select(".source-image-thumbnail")) == 3
    crop_path = editor.root / crop["value"]
    with Image.open(crop_path) as cropped:
        assert cropped.size == (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
    assert (editor.root / "images/art.jpg").is_file()
    assert editor.client.get(
        editor.base + "/images/4/source_image/" + crop["value"] + "?height=160"
    ).status_code == 200


@pytest.mark.parametrize("aspect,size", [("widescreen", (160, 80)), ("portrait", (80, 160))])
def test_saved_motion_reaches_video_frames_and_invalidates_cached_frames(source_editor, monkeypatch, tmp_path, aspect, size):
    from artifact_editor.masterplan.masterplan import generate_masterplan
    from artifact_editor.frames.frames import draw_frame

    editor = source_editor
    monkeypatch.setattr(const, "FPS", 4)
    monkeypatch.setattr(const, "GEOMETRY", {aspect: {"SIZE": size, "HSIZE": size[0], "VSIZE": size[1]}})
    editor.chapter.get_aspect = lambda: aspect
    editor.image["fullscreen"] = "true"
    paragraph = editor.image.parent
    soup = paragraph.parent.parent
    pause = soup.new_tag("silence", attrs={"duration": "1"})
    paragraph.append(pause)
    editor.chapter.paragraphs = lambda: [paragraph]
    artwork = Image.new("RGB", (400, 200), "red")
    artwork.paste("blue", (200, 0, 400, 200))
    artwork.save(editor.root / "art.png")
    original = (editor.root / "art.png").read_bytes()
    settings = {"first": {"x": .25, "y": .25, "zoom": 2}, "last": {"x": .75, "y": .75, "zoom": 2}}

    def save_and_plan():
        response = editor.client.post(editor.base + "/timeline/image/4", data={
            "action": "source", "image_source": "images/art.png", "source_motion": json.dumps(settings)})
        assert response.status_code == 200
        assert b'name="animation"' not in response.data
        plan = generate_masterplan(editor.chapter, force=True)
        result = plan.images[0].as_dict()
        result["image_pfn"] = editor.chapter.get_image_filename(editor.image)
        assert result["frames"] == 4
        return result

    def render(index, config):
        filename = tmp_path / f"frame_{index:06}.png"
        draw_frame(editor.chapter, editor.chapter.chapterdir, aspect, str(filename), None,
                   0, index, 3, config, None, None, 3, None)
        with Image.open(filename) as frame:
            assert frame.size == size
            return frame.getpixel((size[0] // 2, size[1] // 2))[:3]

    config = save_and_plan()
    assert render(0, config) == (255, 0, 0)
    assert render(3, config) == (0, 0, 255)
    settings["last"]["x"] = .25
    assert render(3, save_and_plan()) == (255, 0, 0)
    assert (editor.root / "art.png").read_bytes() == original


def test_source_pan_zoom_does_not_request_ai_animation(source_editor, monkeypatch):
    from artifact_editor.images import images
    from artifact_editor.textoviva.textoviva import Textoviva
    import comfy

    editor = source_editor
    editor.client.post(editor.base + "/images/4/source_choose", data={
        "image_source": "images/art.jpg",
        "source_motion": json.dumps({frame: {"x": .5, "y": .5, "zoom": 1} for frame in ("first", "last")}),
    })
    editor.chapter.images = lambda: [editor.image]
    prompt, workflow = Mock(), Mock()
    monkeypatch.setattr(images, "generate_animation_prompt", prompt)
    monkeypatch.setattr(comfy, "run_workflow_fn", workflow)
    Textoviva.generate_missing_animations(SimpleNamespace(chapter=editor.chapter))
    prompt.assert_not_called()
    workflow.assert_not_called()

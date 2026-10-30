"""Source camera regions preserve aspect and reach both selected endpoints."""
import json
import math
import sys
from types import SimpleNamespace

from bs4 import BeautifulSoup
from PIL import Image
import pytest

from artifact_editor.images import source_motion as motion
from artifact_editor.images import source


@pytest.mark.parametrize("size", [(300, 900), (1200, 400), (600, 600)])
@pytest.mark.parametrize("target", [(160, 90), (90, 160), (100, 100)])
def test_regions_keep_output_aspect_and_stay_inside_original(size, target):
    for x, y, zoom in ((0, 0, 1), (1, 1, 8), (.75, .25, 2.5)):
        left, top, right, bottom = motion.crop_box(size, target, dict(x=x, y=y, zoom=zoom))
        assert left >= -1e-9 and top >= -1e-9
        assert right <= size[0] + 1e-9 and bottom <= size[1] + 1e-9
        assert (right - left) / (bottom - top) == pytest.approx(target[0] / target[1])


def test_render_reaches_first_and_last_regions_without_distortion():
    original = Image.new("RGB", (400, 200), "red")
    original.paste("blue", (200, 0, 400, 200))
    settings = {"first": {"x": .25, "y": .25, "zoom": 2},
                "last": {"x": .75, "y": .75, "zoom": 2}}
    first = motion.render_frame(original, settings, 0, (160, 80))
    last = motion.render_frame(original, settings, 1, (160, 80))
    assert first.getpixel((80, 40)) == (255, 0, 0)
    assert last.getpixel((80, 40)) == (0, 0, 255)
    for progress in (.1, .5, .9):
        assert motion.render_frame(original, settings, progress, (160, 80)).size == (160, 80)
    assert original.size == (400, 200)


@pytest.mark.parametrize("region", [dict(x=math.nan, y=.5, zoom=1), dict(x=.5, y=.5, zoom=0),
    dict(x=.5, y=.5, zoom=9), dict(x=True, y=.5, zoom=1), dict(x=".5", y=.5, zoom=1)])
def test_invalid_motion_is_rejected(region):
    with pytest.raises(ValueError):
        motion.normalize_motion({"first": region, "last": region})


def test_output_aspect_tracks_fullscreen_and_text_panel(monkeypatch):
    monkeypatch.setitem(sys.modules, "const", SimpleNamespace(
        IMG_TARGET_WIDTH=100, IMG_TARGET_HEIGHT=100,
        GEOMETRY={"widescreen": {"SIZE": (160, 90)}, "portrait": {"SIZE": (90, 160)}}))
    chapter = SimpleNamespace(get_aspect=lambda: "widescreen")
    image = BeautifulSoup('<paragraph><image/></paragraph>', "xml").image
    assert motion.output_size(chapter, image) == (100, 100)
    image["fullscreen"] = "true"
    assert motion.output_size(chapter, image) == (160, 90)
    chapter.get_aspect = lambda: "portrait"
    assert motion.output_size(chapter, image) == (90, 160)
    image.attrs.pop("fullscreen")
    image.parent["tags"] = "has-text=false"
    assert motion.output_size(chapter, image) == (90, 160)


def test_saving_regions_preserves_original_and_makes_first_frame(tmp_path, monkeypatch):
    root = tmp_path / "Book" / "source"
    root.mkdir(parents=True)
    original = root / "art.png"
    Image.new("RGBA", (200, 400), (255, 0, 0, 100)).save(original)
    unchanged = original.read_bytes()
    image = BeautifulSoup('<paragraph index="0"><image index="0"/></paragraph>', "xml").image
    chapter = SimpleNamespace(bookdir="Book", get_paragraph_dir=lambda _: "Book/paragraphs/0", save_xml=lambda: None)
    monkeypatch.setattr(source, "output_size", lambda *_: (160, 90))
    settings = motion.default_motion()
    settings["last"] = {"x": .8, "y": .8, "zoom": 3}
    chosen = source.choose_source_image(chapter, image, "art.png", tmp_path, json.dumps(settings))
    with Image.open(chosen) as first:
        assert first.size == (160, 90)
        assert first.mode == "RGBA"
    assert original.read_bytes() == unchanged
    assert motion.active_motion(image)["last"]["zoom"] == 3
    image["src"] = "a-generated-image.png"
    assert motion.active_motion(image) is None

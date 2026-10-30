"""Original book illustrations available to the Chapter Source workflow."""
import hashlib
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import uuid

from PIL import Image, ImageOps
from .source_motion import active_motion, constrained_motion, default_motion, normalize_motion, output_size, render_frame


SOURCE_WORKFLOW = "source.chapter"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def source_directory(chapter, library_dir=None):
    if library_dir is None:
        import const

        library_dir = const.LIBRARY_DIR
    return Path(library_dir) / chapter.bookdir / "source"


def source_image_path(chapter, filename, library_dir=None):
    """Resolve only image files inside this book's source directory."""
    root = source_directory(chapter, library_dir).resolve()
    relative = Path(filename)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Invalid source image")
    path = root / relative
    if (not path.resolve().is_relative_to(root)
            or path.suffix.lower() not in IMAGE_SUFFIXES):
        raise ValueError("Invalid source image")
    if not path.is_file():
        raise FileNotFoundError("Source image not found")
    return path


def source_images(chapter, library_dir=None):
    root = source_directory(chapter, library_dir)
    if not root.is_dir():
        return []
    filenames = []
    for path in root.rglob("*"):
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        filename = path.relative_to(root).as_posix()
        try:
            source_image_path(chapter, filename, library_dir)
        except (ValueError, FileNotFoundError):
            continue
        filenames.append(filename)
    return sorted(filenames, key=str.casefold)


def source_picker(chapter, image, selected=None):
    from flask import render_template

    filenames = source_images(chapter)
    if selected not in filenames:
        selected = image.get("source_image")
    if selected not in filenames:
        # Earlier imports copied illustrations as img_<index>_<original name>.
        current = os.path.basename(image.get("src") or "")
        selected = next((filename for filename in filenames
                         if current.endswith("_" + Path(filename).name)), None)
    if selected not in filenames:
        selected = filenames[0] if filenames else None
    motion = default_motion()
    if selected == image.get("source_image") and image.get("source_motion"):
        motion = normalize_motion(image["source_motion"])
    target_size = output_size(chapter, image)
    return render_template(
        "source_image_picker.html", chapter=chapter, image=image,
        filenames=filenames, selected=selected, motion=motion,
        target_size=target_size, motion_active=bool(active_motion(image)),
    )


def choose_source_image(chapter, image, filename, library_dir=None, motion=None):
    """Save an illustration, optionally framed with a smooth camera move."""
    source = source_image_path(chapter, filename, library_dir)
    if library_dir is None:
        import const

        library_dir = const.LIBRARY_DIR
    paragraph = image.find_parent("paragraph")
    destination_dir = Path(library_dir) / chapter.get_paragraph_dir(paragraph["index"])
    fingerprint_input = filename.encode() + b"\0" + source.read_bytes()
    destination_dir.mkdir(parents=True, exist_ok=True)
    # Convert to the PNG format expected by image candidates and adjustments.
    # The book source remains untouched; framing produces a separate first frame.
    with Image.open(source) as original:
        illustration = ImageOps.exif_transpose(original)
        mode = "RGBA" if "A" in illustration.getbands() or "transparency" in illustration.info else "RGB"
        illustration = illustration.convert(mode)
        if motion is not None:
            target_size = output_size(chapter, image)
            motion = constrained_motion(motion, illustration.size, target_size)
            fingerprint_input += json.dumps([motion, target_size], sort_keys=True).encode()
            illustration = render_frame(illustration, motion, 0, target_size)
        fingerprint = hashlib.sha256(fingerprint_input).hexdigest()[:16]
        destination = destination_dir / f"img_{int(image['index'])}_source_{fingerprint}.png"
        with TemporaryDirectory(dir=destination_dir) as temporary:
            output = Path(temporary) / "illustration.png"
            illustration.save(output, format="PNG")
            os.replace(output, destination)
    image["src"] = destination.name
    image["source_image"] = filename
    image["workflow_image_template"] = SOURCE_WORKFLOW
    image["image_generation_id"] = uuid.uuid4().hex
    if motion is not None:
        image["source_motion"] = json.dumps(motion, sort_keys=True)
        image["source_motion_src"] = destination.name
    else:
        image.attrs.pop("source_motion", None)
        image.attrs.pop("source_motion_src", None)
    chapter.save_xml()
    return destination

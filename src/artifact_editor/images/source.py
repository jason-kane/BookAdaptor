"""Original book illustrations available to the Chapter Source workflow."""
import hashlib
import json
import math
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


def crop_source_image(chapter, filename, image, crop, library_dir=None):
    """Create a reusable source option from a user-positioned crop rectangle."""
    source = source_image_path(chapter, filename, library_dir)
    if library_dir is None:
        import const

        library_dir = const.LIBRARY_DIR
    from .source_motion import output_size

    target_width, target_height = output_size(chapter, image)
    if not isinstance(crop, dict):
        raise TypeError("Choose a crop region")
    try:
        x, y, width, height = (float(crop[key]) for key in ("x", "y", "width", "height"))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid crop region") from exc
    if (not all(map(math.isfinite, (x, y, width, height)))
            or x < 0 or y < 0 or width <= 0 or height <= 0
            or x + width > 1 or y + height > 1):
        raise ValueError("Crop region is outside the source image")
    with Image.open(source) as original:
        illustration = ImageOps.exif_transpose(original)
        crop_aspect = width * illustration.width / (height * illustration.height)
        if abs(crop_aspect - target_width / target_height) > 0.02:
            raise ValueError("Crop region does not match the output aspect ratio")
        left = round(x * illustration.width)
        top = round(y * illustration.height)
        right = round((x + width) * illustration.width)
        bottom = round((y + height) * illustration.height)
        cropped = illustration.crop((left, top, right, bottom)).resize(
            (target_width, target_height), Image.Resampling.LANCZOS
        )
        mode = "RGBA" if "A" in cropped.getbands() or "transparency" in cropped.info else "RGB"
        cropped = cropped.convert(mode)
        digest = hashlib.sha256(
            source.read_bytes() + json.dumps(
                [x, y, width, height, target_width, target_height],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:16]
        destination_name = f"cropped/{source.stem}_{target_width}x{target_height}_{digest}.png"
        root = source_directory(chapter, library_dir)
        destination = root / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            with TemporaryDirectory(dir=destination.parent) as temporary:
                output = Path(temporary) / "crop.png"
                cropped.save(output, format="PNG")
                os.replace(output, destination)
    return destination_name


def source_picker(chapter, image, selected=None, motion=None, expanded=False):
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
    if motion:
        try:
            motion = normalize_motion(motion)
        except (TypeError, ValueError):
            motion = default_motion()
    elif selected == image.get("source_image") and image.get("source_motion"):
        motion = normalize_motion(image["source_motion"])
    else:
        motion = default_motion()
    target_size = output_size(chapter, image)
    return render_template(
        "source_image_picker.html", chapter=chapter, image=image,
        filenames=filenames, selected=selected, motion=motion,
        target_size=target_size, motion_active=bool(active_motion(image)),
        expanded=expanded,
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
    chapter.save_image(image)
    return destination

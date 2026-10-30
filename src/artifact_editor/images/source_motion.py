"""Aspect-locked regions and smooth camera motion over an original illustration."""
import json
import math

from PIL import Image, ImageOps


def default_motion():
    return {frame: {"x": 0.5, "y": 0.5, "zoom": 1.0} for frame in ("first", "last")}


def normalize_motion(value):
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise ValueError("Invalid source frame regions")
    result = {}
    for frame in ("first", "last"):
        region = value.get(frame)
        if not isinstance(region, dict):
            raise ValueError("Choose both first and last frame regions")
        result[frame] = {}
        for key, low, high in (("x", 0, 1), ("y", 0, 1), ("zoom", 1, 8)):
            number = region.get(key)
            if isinstance(number, bool) or not isinstance(number, (int, float)):
                raise ValueError("Invalid source frame region")
            if not math.isfinite(number) or not low <= number <= high:
                raise ValueError("Source frame region is out of range")
            result[frame][key] = float(number)
    return result


def active_motion(image):
    if (not image.get("src") or not image.get("source_motion")
            or image.get("source_motion_src") != image.get("src")):
        return None
    return normalize_motion(image["source_motion"])


def output_size(chapter, image):
    import const
    paragraph = image.find_parent("paragraph")
    tags = (paragraph.get("tags") or "").split(",") if paragraph else []
    textless = any(tag.partition("=")[0] == "has-text" and tag.partition("=")[2].lower() == "false" for tag in tags)
    fullscreen = str(image.get("fullscreen", "false")).lower() == "true" or textless
    if fullscreen or image.get("render_profile") == "theater":
        return tuple(const.GEOMETRY[chapter.get_aspect()]["SIZE"])
    return const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT


def crop_box(image_size, target_size, region):
    width, height = image_size
    aspect = target_size[0] / target_size[1]
    crop_width = min(width, height * aspect) / region["zoom"]
    crop_height = crop_width / aspect
    x = min(width - crop_width / 2, max(crop_width / 2, region["x"] * width))
    y = min(height - crop_height / 2, max(crop_height / 2, region["y"] * height))
    return x - crop_width / 2, y - crop_height / 2, x + crop_width / 2, y + crop_height / 2


def constrained_motion(motion, image_size, target_size):
    motion = normalize_motion(motion)
    for region in motion.values():
        left, top, right, bottom = crop_box(image_size, target_size, region)
        region["x"] = (left + right) / (2 * image_size[0])
        region["y"] = (top + bottom) / (2 * image_size[1])
    return motion


def render_frame(illustration, motion, progress, target_size):
    """Render subpixel crops; smoothstep eases into and out of the camera move."""
    illustration = ImageOps.exif_transpose(illustration)
    if illustration.mode not in ("RGB", "RGBA"):
        illustration = illustration.convert("RGBA" if "transparency" in illustration.info else "RGB")
    first = crop_box(illustration.size, target_size, motion["first"])
    last = crop_box(illustration.size, target_size, motion["last"])
    progress = min(1.0, max(0.0, float(progress)))
    blend = progress * progress * (3 - 2 * progress)
    box = tuple(start + (end - start) * blend for start, end in zip(first, last))
    return illustration.resize(tuple(target_size), Image.Resampling.LANCZOS, box=box)

"""Quality presets and empirical timing estimates for MiniMax H3 Turbo."""

import math
import json
import os
import subprocess
from pathlib import Path
import tempfile


# Calibrated on an RTX 3060 12 GB with the same Turbo workflow, seed, and
# portrait aspect ratio used for the local duration sweep. Dimensions are
# reference pixels; actual presets preserve the still image's aspect ratio.
QUALITY_REFERENCE_SIZES = {
    "low": (360, 640),
    "medium": (576, 1024),
    "high": (864, 1536),
}

# Least-squares estimates from completed samples, in seconds per generated
# video second and fixed intercept. Lower bounds preserve measured short-clip
# startup times. These are displayed as estimates, not hard guarantees.
_WALL_TIME_MODELS = {
    "low": (21.0, -9.2, 45.9),
    "medium": (73.6, -66.3, 87.1),
    "high": (190.8, -115.1, 215.0),
}


def is_turbo_workflow(value):
    """Recognize the Turbo selector id and legacy template filenames."""
    if not value:
        return False
    from artifact_editor.textoviva.workflow import animation_workflow_name

    return animation_workflow_name(value) == "h3turbo"


def dimensions_for_quality(quality, target_width, target_height):
    """Scale the calibrated pixel budget to a target aspect ratio, aligned to 8."""
    quality = quality if quality in QUALITY_REFERENCE_SIZES else "low"
    ref_width, ref_height = QUALITY_REFERENCE_SIZES[quality]
    pixel_budget = ref_width * ref_height
    aspect = max(1, int(target_width)) / max(1, int(target_height))
    width = max(8, int(round(math.sqrt(pixel_budget * aspect) / 8) * 8))
    height = max(8, int(round(math.sqrt(pixel_budget / aspect) / 8) * 8))
    return width, height


def animation_output_size(chapter, image):
    """Final asset dimensions are independent of the model's working resolution."""
    import const
    return (
        tuple(const.GEOMETRY[chapter.get_aspect()]["SIZE"])
        if image.get("render_profile") == "theater"
        else (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
    )


def prepare_animation_source(chapter, image):
    """Normalize the selected still before choosing model and output dimensions."""
    from PIL import Image
    from artifact_editor.images.source_motion import active_motion, output_size

    if active_motion(image):
        return output_size(chapter, image)

    target = animation_output_size(chapter, image)
    source = chapter.get_image_filename(image)
    if not source or not Path(source).is_file():
        return target
    source = Path(source)
    with Image.open(source) as still:
        if still.size == target:
            return target
        resized = still.resize(target, Image.Resampling.LANCZOS)
        image_format = still.format
    with tempfile.NamedTemporaryFile(dir=source.parent, suffix=source.suffix, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        resized.save(temporary, format=image_format)
        os.replace(temporary, source)
    finally:
        resized.close()
        temporary.unlink(missing_ok=True)
    return target


def output_duration_seconds(source_duration_seconds):
    """Return the model duration after its 17k+5 frame-grid padding."""
    requested_frames = max(5, math.ceil(float(source_duration_seconds) * 24))
    output_frames = requested_frames + (5 - requested_frames % 17) % 17
    return output_frames / 24


def estimate_wall_seconds(source_duration_seconds, quality):
    """Estimate local wall time from padded H3 duration and measured samples."""
    quality = quality if quality in _WALL_TIME_MODELS else "low"
    slope, intercept, minimum = _WALL_TIME_MODELS[quality]
    padded_seconds = output_duration_seconds(source_duration_seconds)
    return round(max(minimum, slope * padded_seconds + intercept))


def format_wall_time(seconds):
    """Format seconds as a compact approximate duration for the UI."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"~{seconds}s"
    minutes, remaining = divmod(seconds, 60)
    if not remaining:
        return f"~{minutes}m"
    return f"~{minutes}m {remaining}s"


def upscale_animation(path, target_size):
    """Normalize MP4 dimensions, leaving an already correct clip untouched."""
    video_path = Path(path)
    width, height = (int(target_size[0]), int(target_size[1]))
    width -= width % 2
    height -= height % 2
    if width < 2 or height < 2:
        raise ValueError("Animation target dimensions must be at least 2x2 pixels")
    probe = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height", "-of", "json", str(video_path),
    ], check=True, capture_output=True, text=True, timeout=30)
    streams = json.loads(probe.stdout).get("streams", [])
    if streams and (streams[0].get("width"), streams[0].get("height")) == (width, height):
        return
    temporary_path = video_path.with_name(video_path.stem + ".upscaled.mp4")
    command = [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(video_path), "-map", "0:v:0", "-map", "0:a?",
        "-vf", f"scale={width}:{height}:flags=lanczos,setsar=1", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(temporary_path),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=1800)
        os.replace(temporary_path, video_path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()

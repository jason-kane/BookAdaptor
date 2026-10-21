"""Locate an image's animation within its paragraph assets."""
from pathlib import Path


def animation_filename(image, paragraph_dir):
    paragraph_dir = Path(paragraph_dir)
    explicit = image.get('animated_src')
    if explicit and (paragraph_dir / Path(explicit).name).is_file():
        return Path(explicit).name
    source = image.get('src')
    if not source:
        return None
    companion = Path(source).with_suffix('.mp4').name
    # Workflow output names differ from the still image's name. Prefer the
    # generated output over the compatibility copy named after the still.
    candidates = [path for path in paragraph_dir.glob(f"img_{int(image['index'])}_*.mp4")
                  if path.is_file() and path.name != companion]
    if candidates:
        return max(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name)).name
    for filename in (companion, 'camera.mp4'):
        if (paragraph_dir / filename).is_file():
            return filename
    return None


def animation_frame_count(image, paragraph_dir, fps=None):
    """Measure the selected video at output FPS; use PNGs only without a video."""
    paragraph_dir = Path(paragraph_dir)
    directories = list((paragraph_dir / "animation").glob(f"image_{int(image['index']):06d}_*"))
    count = sum(1 for directory in directories if directory.is_dir()
                for path in directory.glob("*.png") if path.is_file())
    filename = animation_filename(image, paragraph_dir)
    if filename:
        path = paragraph_dir / filename
        stat = path.stat()
        if fps is None:
            import const
            fps = const.FPS
        return _video_frame_count(str(path), stat.st_mtime_ns, stat.st_size, fps)
    return count


from functools import lru_cache


@lru_cache(maxsize=256)
def _video_frame_count(path, modified, size, fps):
    import json
    from fractions import Fraction
    import subprocess

    try:
        result = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=duration,nb_frames,avg_frame_rate", "-of", "json", path,
        ], capture_output=True, text=True, check=True, timeout=10)
        stream = json.loads(result.stdout)["streams"][0]
        # Extracted PNGs are resampled by ffmpeg to const.FPS. Match that
        # time base when measuring a video that has not been extracted yet.
        duration = stream.get("duration")
        if duration not in (None, "N/A"):
            seconds = Fraction(duration)
        else:
            seconds = Fraction(int(stream["nb_frames"])) / Fraction(stream["avg_frame_rate"])
        return round(seconds * fps)
    except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
        return None

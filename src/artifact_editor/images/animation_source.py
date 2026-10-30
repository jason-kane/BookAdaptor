"""Locate an image's animation within its paragraph assets."""
from pathlib import Path
import json
import time


def animation_filename(image, paragraph_dir):
    from artifact_editor.images.source_motion import active_motion

    if active_motion(image):
        return None
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
    from artifact_editor.images.source_motion import active_motion

    if active_motion(image):
        return max(0, int(image.get("frames", 0)))
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


def extracted_frames_are_current(video_path, frame_dir, fps, target_size):
    """Return whether extracted frames match this source and render setup."""
    video_path = Path(video_path)
    frame_dir = Path(frame_dir)
    marker = frame_dir / ".source.json"
    try:
        source = video_path.stat()
        saved = json.loads(marker.read_text())
        return (
            saved == {
                "source": video_path.name,
                "mtime_ns": source.st_mtime_ns,
                "size": source.st_size,
                "fps": int(fps),
                "target_size": [int(target_size[0]), int(target_size[1])],
            }
            and any(frame.is_file() for frame in frame_dir.glob("*.png"))
        )
    except (OSError, ValueError, TypeError):
        return False


def mark_extracted_frames_current(video_path, frame_dir, fps, target_size):
    """Record the source state only after extraction produced usable frames."""
    video_path = Path(video_path)
    frame_dir = Path(frame_dir)
    source = video_path.stat()
    if not any(frame.is_file() for frame in frame_dir.glob("*.png")):
        return False
    marker = frame_dir / ".source.json"
    marker.write_text(json.dumps({
        "source": video_path.name,
        "mtime_ns": source.st_mtime_ns,
        "size": source.st_size,
        "fps": int(fps),
        "target_size": [int(target_size[0]), int(target_size[1])],
    }))
    return True


from functools import lru_cache


@lru_cache(maxsize=256)
def _video_frame_count(path, modified, size, fps):
    import json
    from fractions import Fraction
    import subprocess

    for attempt in range(3):
        try:
            result = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=duration,nb_frames,avg_frame_rate", "-of", "json", path,
            ], capture_output=True, text=True, check=True, timeout=10)
            stream = json.loads(result.stdout)["streams"][0]
            # Extracted PNGs are resampled by ffmpeg to const.FPS. Match that
            # time base when measuring a video that has not been extracted yet.
            duration = stream.get("duration")
            if duration not in (None, "N/A") and float(duration) > 0:
                return round(Fraction(duration) * fps)

            # Some freshly written MP4s briefly report a zero stream duration
            # even though the muxed stream already declares its frame count.
            # Convert that count to the renderer's FPS while metadata settles.
            frame_count = stream.get("nb_frames")
            rate = stream.get("avg_frame_rate")
            if (frame_count not in (None, "N/A") and int(frame_count) > 0
                    and rate not in (None, "N/A", "0/0")):
                return round(Fraction(int(frame_count), 1) / Fraction(rate) * fps)
        except (OSError, ValueError, KeyError, IndexError, ZeroDivisionError, subprocess.SubprocessError):
            pass

        # SaveVideo may still be finalizing an MP4 when the workflow-complete
        # event reaches the renderer. Retry briefly before treating it as bad.
        if attempt < 2:
            time.sleep(0.5)
    return None

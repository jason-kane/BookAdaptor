import os

from artifact_editor import camera
import const
import logger
from artifact_editor import tools
from artifact_editor.phrases import phrases

log = logger.log(__name__)

def book_to_video(chapter, aspect=None, force=False, progress=None):
    """Render every XML phrase and image on one frame-aligned audio timeline."""
    from pathlib import Path
    from PIL import Image
    from cpu_queue import Batch
    from gpu_tasks import render_context
    from artifact_editor.library.storage import ensure_shelf_media_path

    aspect = aspect or chapter.get_aspect()
    chapter_media = Path('chapter') / f'{int(chapter.number):04}'
    frames_path = ensure_shelf_media_path(
        chapter.author.name, chapter.title, chapter_media / 'frames', directory=True,
    )
    framedir = frames_path / aspect
    framedir.mkdir(parents=True, exist_ok=True)
    ensure_shelf_media_path(
        chapter.author.name, chapter.title,
        chapter_media / f'{chapter.safe_title()}.mp4',
    )
    mplan = chapter.get_masterplan(force=True)
    if not mplan.words or mplan.words[-1].end_frame < 0:
        raise ValueError("The chapter has no narration to render")
    total_frames = mplan.words[-1].end_frame + 1
    # Validate and assemble narration before submitting expensive frame work.
    # Missing tracks must fail the render rather than silently omit phrases.
    audio_tracks = chapter.get_all_audio_tracks()
    audio_fn = str(Path(const.LIBRARY_DIR) / chapter.chapterdir / "audio.wav")
    phrases.assemble(audio_tracks, audio_fn)
    audio_duration = phrases.get_wav_duration(audio_fn)
    if abs(float(audio_duration) - total_frames / const.FPS) > 1 / const.FPS:
        raise ValueError("Audio duration does not match the complete chapter timeline")

    if progress:
        progress(5, "Preparing frame timeline")
    chapter.build_frame_to_camera(aspect=aspect, force=True)
    identity = {"author": chapter.author.name, "title": chapter.title,
                "chapter_number": chapter.number, "language": chapter.language, "aspect": aspect}
    parent_id = render_context.get().get("parent_job_id")
    fallback = framedir / "blank.png"
    Image.new("RGB", const.GEOMETRY[aspect]["SIZE"], "black").save(fallback)
    def frame_timeline():
        previous_image = None
        image_cursor = -1
        word_cursor = 0
        for frame_index in range(total_frames):
            while (image_cursor + 1 < len(mplan.images)
                   and mplan.images[image_cursor + 1].start_frame <= frame_index):
                image_cursor += 1
            while (word_cursor + 1 < len(mplan.words)
                   and mplan.words[word_cursor].end_frame < frame_index):
                word_cursor += 1
            word = mplan.words[word_cursor]
            if not word.start_frame <= frame_index <= word.end_frame:
                raise ValueError(f"Missing phrase timing for frame {frame_index}")
            paragraph_index = int(word.paragraph_index)
            if image_cursor >= 0:
                image_dict = mplan.images[image_cursor].as_dict()
                image_path = Path(image_dict["image"])
                if not image_path.is_absolute():
                    image_path = Path(const.LIBRARY_DIR) / image_dict["paragraph_dir"] / image_path
                image_dict["image_pfn"] = str(image_path)
            else:
                # Before the first XML image, show narration against a blank
                # background. Do not bring a later illustration forward in time.
                image_dict = {"index": -1, "start_frame": 0,
                              "end_frame": total_frames - 1, "image": str(fallback),
                              "image_pfn": str(fallback), "paragraph_dir": chapter.chapterdir,
                              "fullscreen": False, "mode": "scale"}
            image_dict["fullscreen"] = bool(image_dict.get("fullscreen") or word.fullscreen)
            image_dict["paragraph_tags"] = word.paragraph_tags
            yield {
                "chapterdir": chapter.chapterdir, "aspect": aspect,
                "frame_fn": str(framedir / f"frame_{frame_index:06}.png"),
                "scroll_lock": camera.frame_to_camera(frame_index),
                "first_frame_index": image_dict["start_frame"],
                "frame_index": frame_index, "max_frame_index": image_dict["end_frame"],
                "image_dict": image_dict, "previous_image_dict": previous_image,
                # The XML id is stable across phrase re-indexing and maps back
                # to the correct XML phrase; its numeric index drives rainbow
                # color selection after that phrase has been resolved.
                "paragraph_index": paragraph_index,
                "phrase_id": word.id if word.id is not None else word.index,
                "force": force,
            }
            previous_image = image_dict


    # Submit each phrase/vertical-range pair once, regardless of frame count.
    # Frames are not visible to CPU workers until every segment is ready.
    from artifact_editor.typography.page_segment import segment_tops
    segments = set()
    for arguments in frame_timeline():
        if arguments["image_dict"]["fullscreen"]:
            continue
        if arguments["scroll_lock"] is None:
            raise ValueError("No scroll position for a frame requiring text")
        for top in segment_tops(arguments["scroll_lock"], const.GEOMETRY[aspect]["TEXT_HEIGHT"]):
            segments.add((arguments["phrase_id"], top))
    preparation = Batch(parent_job_id=parent_id, chapter=identity)
    try:
        if progress:
            progress(5, f"Preparing text segments (0/{len(segments)})")
        for phrase_id, top in sorted(segments, key=lambda item: (str(item[0]), item[1])):
            preparation.submit("prepare_text_segment", {
                "phrase_id": phrase_id, "segment_top": top, "force": force,
            }, f"Text segment for phrase {phrase_id} at {top}px")
        preparation.wait(progress=(lambda completed: progress(
            5 + int(15 * completed / max(1, len(segments))),
            f"Preparing text segments ({completed}/{len(segments)})")) if progress else None)
    except Exception:
        preparation.client.hset(preparation.key, "cancelled", "1")
        raise
    finally:
        preparation.client.close()

    batch = Batch(parent_job_id=parent_id, chapter=identity)
    try:
        for frame_index, arguments in enumerate(frame_timeline()):
            batch.submit("draw_frame", arguments, f"Frame {frame_index + 1} of {total_frames}")

        batch.wait(progress=(lambda completed: progress(
            20 + int(55 * completed / total_frames),
            f"Rendering frames ({completed}/{total_frames})")) if progress else None)
    except Exception:
        batch.client.hset(batch.key, "cancelled", "1")
        raise
    finally:
        batch.client.close()

    missing = next((index for index in range(total_frames)
                    if not (framedir / f"frame_{index:06}.png").is_file()), None)
    if missing is not None:
        raise RuntimeError(f"Frame {missing} is missing; refusing to encode an incomplete video")
    if progress:
        progress(80, "Encoding complete chapter video")
    encoding = Batch(parent_job_id=parent_id, chapter=identity)
    try:
        encoding.submit("encode_video", {
            "fps": const.FPS, "framedir": str(framedir.resolve()), "wavfile": audio_fn,
            "videofile": chapter.get_video_filename(), "image_match": "frame_%06d.png",
            "start_number": 0, "max_frames": total_frames,
        }, "Encode chapter MP4")
        encoding.wait()
    finally:
        encoding.client.close()
    if progress:
        progress(99, "Chapter video ready")


def render_masterplan_widescreen(chapter, progress=None):
    """
    Render the masterplan for the book in bookdir, output is the mp4.
    Thin wrapper around Book().book_to_video()
    """       
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.bookdir,
        "frames"
    )
    os.makedirs(framedir, exist_ok=True)

    log.info('Triggering book_to_video()...')
    book_to_video(chapter, force=False, progress=progress)


def render_masterplan_portrait(chapter):
    """
    Render the masterplan for the book in bookdir, output is the mp4.
    Thin wrapper around Book().book_to_video()
    """
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.bookdir,
        "frames"
    )
    os.makedirs(framedir, exist_ok=True)

    log.info('Triggering book_to_video()...')
    book_to_video(chapter, force=False)

assemble_mp4 = tools.assemble_mp4

"""Relocate image slots while preserving their paragraph-local assets."""

from pathlib import Path
import shutil
import filecmp
from decimal import Decimal, InvalidOperation
from bs4 import BeautifulSoup

MAX_ANIMATION_DURATION_SECONDS = Decimal("8")


def delete_timeline_item(chapter, item_type, item_index):
    """Remove a timeline entry while preserving other entries' asset indices."""
    if item_type not in ("phrase", "image", "silence", "scene"):
        raise ValueError("Invalid timeline item")
    soup = chapter.get_xml()
    item = soup.find(item_type, index=str(item_index))
    if item is None:
        raise ValueError("Timeline item no longer exists; reload the chapter")
    item.decompose()
    update_image_frames(soup)
    chapter.save_timeline()


def recent_image_candidates(paths, current_path=None, limit=18):
    """Keep the latest slot images and the current selection if it is older."""
    candidates = {Path(path) for path in paths if Path(path).is_file()}
    current = Path(current_path) if current_path else None
    if current is not None and current.is_file():
        candidates.add(current)
    recent = sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, path.name), reverse=True)[:limit]
    if current in candidates and current not in recent:
        recent = recent[:max(0, limit - 1)] + [current]
    return [{"filename": path.name, "selected": path == current} for path in recent]


def phrase_timings(soup, item_type="phrase"):
    """Return chapter-relative audio times and inclusive, zero-based video frames."""
    seconds = Decimal(0)
    frame = 0
    timings = {}
    for phrase in soup.find_all(["phrase", "silence"]):
        duration = Decimal(str(phrase.get("duration", 0)))
        frames = int(phrase.get("frames", 0))
        if phrase.name == item_type and "duration" in phrase.attrs and "frames" in phrase.attrs:
            timings[str(phrase["index"])] = {
                "start": seconds,
                "end": seconds + duration,
                "duration": duration,
                "frame_start": frame,
                "frame_end": frame + frames - 1 if frames else None,
                "frames": frames,
            }
        seconds += duration
        frame += frames
    return timings


def image_timings(soup):
    """Aggregate phrases up to the next image, across paragraph boundaries."""
    phrases = phrase_timings(soup)
    seconds, frame = Decimal(0), 0
    timings = {}
    current = None
    for tag in soup.find_all(["image", "phrase", "silence"]):
        if tag.name == "image":
            current = str(tag["index"])
            timings[current] = {
                "start": seconds, "end": seconds, "duration": Decimal(0),
                "frame_start": frame, "frame_end": None, "frames": 0,
            }
        else:
            duration = Decimal(str(tag.get("duration", 0)))
            frames = int(tag.get("frames", 0))
            seconds += duration
            frame += frames
            if current is not None:
                timing = timings[current]
                timing["end"] = seconds
                timing["duration"] += duration
                timing["frames"] += frames
                timing["frame_end"] = frame - 1 if timing["frames"] else None
    for timing in timings.values():
        timing["exceeds_animation_duration"] = (
            timing["duration"] > MAX_ANIMATION_DURATION_SECONDS
        )
    return timings if phrases else {}


def add_image_below(chapter, phrase_index):
    soup = chapter.get_xml()
    phrase = soup.find("phrase", attrs={"index": str(phrase_index)})
    if phrase is None:
        raise ValueError("Phrase no longer exists; reload the chapter")
    image_index = max((int(image["index"]) for image in soup.find_all("image")), default=-1) + 1
    image = soup.new_tag("image", attrs={"index": str(image_index)})
    phrase.insert_after(image)
    update_image_frames(soup)
    chapter.save_timeline()
    return image


def move_image(chapter, image_index, target_type, target_index, position, library_dir="."):
    if target_type not in ("phrase", "image", "silence", "scene") or position not in ("before", "after"):
        raise ValueError("Invalid timeline destination")
    soup = chapter.get_xml()
    image = soup.find("image", attrs={"index": str(image_index)})
    target = soup.find(target_type, attrs={"index": str(target_index)})
    if image is None or target is None:
        raise ValueError("Timeline item no longer exists; reload the chapter")
    if image is target:
        return
    old_parent = image.find_parent("paragraph")
    new_parent = target.find_parent("paragraph")
    if old_parent is None or new_parent is None:
        raise ValueError("Timeline items must belong to a paragraph")
    if old_parent is not new_parent:
        old_dir = Path(library_dir) / chapter.get_paragraphdir(int(old_parent["index"]))
        new_dir = Path(library_dir) / chapter.get_paragraphdir(int(new_parent["index"]))
        stem = Path(image.get("src", "")).stem
        prefixes = [f"image_{int(image_index):06d}"]
        if stem:
            prefixes.append(stem)
        assets = [p for p in old_dir.iterdir() if any(
            p.name == prefix or p.name.startswith(prefix + ".") or p.name.startswith(prefix + "_")
            for prefix in prefixes
        )] if old_dir.exists() else []
        animation_dir = old_dir / "animation"
        if animation_dir.exists():
            assets.extend(animation_dir.glob(f"image_{int(image_index):06d}_*"))
        for source in assets:
            destination = new_dir / source.relative_to(old_dir)
            if destination.exists() and (source.is_dir() or not filecmp.cmp(source, destination, shallow=False)):
                raise ValueError("Image assets conflict with files in the destination paragraph")
        new_dir.mkdir(parents=True, exist_ok=True)
        for source in assets:
            destination = new_dir / source.relative_to(old_dir)
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_dir():
                    shutil.copytree(source, destination)
                else:
                    shutil.copy2(source, destination)
    image.extract()
    if position == "before":
        target.insert_before(image)
    else:
        target.insert_after(image)
    # Image indices identify workshop assets and remain stable after a move.
    update_image_frames(soup)
    chapter.save_timeline()


def update_image_frames(soup):
    last_image = None
    frames = 0
    for tag in soup.find_all(["image", "phrase", "silence"]):
        if tag.name == "image":
            if last_image is not None:
                last_image["frames"] = str(frames)
            last_image, frames = tag, 0
        else:
            frames += int(tag.get("frames", "0"))
    if last_image is not None:
        last_image["frames"] = str(frames)


def silence_duration(value):
    try:
        duration = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("Silence must be a number between 0 and 5 seconds")
    if not duration.is_finite() or not 0 <= duration <= 5:
        raise ValueError("Silence must be between 0 and 5 seconds")
    return duration.quantize(Decimal("0.001"))


def save_silence(chapter, silence, value, fps):
    duration = silence_duration(value)
    silence["duration"] = format(duration, ".3f")
    silence["frames"] = str(round(duration * fps))
    update_image_frames(chapter.get_xml())
    chapter.save_timing()


def add_silence(chapter, target_type, target_index, fps):
    if target_type not in ("phrase", "image", "silence", "scene"):
        raise ValueError("Invalid timeline item")
    soup = chapter.get_xml()
    target = soup.find(target_type, index=str(target_index))
    if target is None:
        raise ValueError("Timeline item no longer exists; reload the chapter")
    index = max((int(tag["index"]) for tag in soup.find_all("silence")), default=-1) + 1
    duration = silence_duration("0.500")
    silence = soup.new_tag("silence", attrs={
        "index": str(index), "duration": format(duration, ".3f"),
        "frames": str(round(duration * fps)),
    })
    target.insert_after(silence)
    update_image_frames(soup)
    # New entries need a structural save before timing-only updates can use them.
    chapter.save_timeline()
    return silence


def add_phrase_below(chapter, phrase_index):
    """Insert an empty phrase with its predecessor's speaker, then renumber."""
    soup = chapter.get_xml()
    target = soup.find("phrase", index=str(phrase_index))
    if target is None:
        raise ValueError("Phrase no longer exists; reload the chapter")
    phrase = soup.new_tag("phrase", attrs={"speaker": target.get("speaker", "Narrator")})
    target.insert_after(phrase)
    for index, tag in enumerate(soup.find_all("phrase")):
        tag["index"] = str(index)
    chapter.save_timeline()
    return phrase


def chapter_audio_tracks(chapter, library_dir, make_silence):
    """Collect narration and generated silence WAVs in XML document order."""
    import wave
    from contextlib import closing

    soup = chapter.get_xml()
    items = soup.find_all(["phrase", "silence"])

    def paragraph_dir(tag):
        paragraph = tag.find_parent("paragraph")
        return Path(library_dir) / chapter.get_paragraph_dir(int(paragraph["index"]))

    def phrase_path(tag):
        return paragraph_dir(tag) / Path(tag["src"]).name

    # Assembly copies raw samples, so silence must match the narration format.
    reference = next((phrase_path(tag) for tag in items
                      if tag.name == "phrase" and tag.get("src")
                      and phrase_path(tag).is_file()), None)
    params = (1, 2, 16000, 0, "NONE", "not compressed")
    if reference is not None:
        with closing(wave.open(str(reference), "rb")) as source:
            params = source.getparams()

    tracks = []
    for tag in items:
        if tag.name == "phrase":
            if not tag.get("src") or not phrase_path(tag).is_file():
                raise ValueError(f"Phrase {tag.get('index', '?')} is missing narration audio")
            tracks.append(str(phrase_path(tag)))
            continue
        duration = silence_duration(tag.get("duration", "0"))
        if duration == 0:
            continue
        filename = paragraph_dir(tag) / f"silence_{tag['index']}.wav"
        filename.parent.mkdir(parents=True, exist_ok=True)
        # Start empty each time: wav_append_delay appends to an existing WAV.
        with closing(wave.open(str(filename), "wb")) as target:
            target.setparams(params)
            target.writeframes(b"")
        make_silence(str(filename), float(duration), outfile=None)
        tracks.append(str(filename))
    return tracks


def add_import_silences(soup, fps):
    """Add paragraph pauses and a final pause to a freshly imported chapter."""
    import re

    phrases = [tag for tag in soup.find_all("phrase") if tag.get_text().strip()]
    silence_index = 0

    def append_pause(phrase, duration):
        nonlocal silence_index
        pause = soup.new_tag("silence", attrs={
            "index": str(silence_index), "duration": format(duration, ".3f"),
            "frames": str(round(duration * fps)),
        })
        phrase.insert_after(pause)
        silence_index += 1

    for previous, following in zip(phrases, phrases[1:]):
        before, after = previous.get_text(), following.get_text()
        whitespace = before[len(before.rstrip()):] + after[:len(after) - len(after.lstrip())]
        if re.search(r"\n[^\S\n]*\n", whitespace):
            append_pause(previous, 0.5)
    if phrases:
        append_pause(phrases[-1], 2.0)

    # Consecutive illustrations need screen time without inventing narration.
    # Document order also covers image runs that cross paragraph boundaries.
    items = soup.find_all(["image", "phrase", "silence"])
    for previous, following in zip(items, items[1:]):
        if previous.name == following.name == "image":
            append_pause(previous, 5.0)
    update_image_frames(soup)


def phrase_audio_missing(chapter, phrase, library_dir):
    """Check the paragraph-local WAV used by phrase audio generation."""
    source = phrase.get("src")
    if not source:
        return True
    paragraph = phrase.find_parent("paragraph")
    directory = Path(library_dir) / chapter.get_paragraph_dir(int(paragraph["index"]))
    return not (directory / Path(source).name).is_file()


def place_image_slots(chapter):
    """Place generated slots once narration timing is available."""
    soup = chapter.get_xml()
    if any("duration" not in phrase.attrs or "frames" not in phrase.attrs
           for phrase in soup.find_all("phrase")):
        raise ValueError("Add missing audio before placing image slots")
    add_import_images(soup)
    chapter.save_timeline()


def add_import_images(soup):
    """Place slots at phrase starts nearest eight-second chapter marks."""
    # Chapter.get_xml() can return either a document or its <chapter> tag.
    # Only BeautifulSoup documents implement new_tag, including for detached tags.
    document = soup if isinstance(soup, BeautifulSoup) else BeautifulSoup("", "xml")
    elapsed = Decimal(0)
    starts, anchors = [], []
    for tag in soup.find_all(["phrase", "silence", "illustration", "image"]):
        if tag.name in ("illustration", "image"):
            anchors.append((elapsed, tag))
        elif tag.name == "phrase":
            starts.append((elapsed, tag))
        if tag.name in ("phrase", "silence"):
            elapsed += Decimal(str(tag.get("duration", 0)))
    if not starts and not anchors:
        return

    if anchors:
        # Fixed source illustrations take precedence. Fill each interval evenly
        # so a generated slot cannot crowd the upcoming illustration.
        selected = []
        occupied = [time for time, _ in anchors]
        if starts and starts[0][0] not in occupied:
            selected.append(starts[0])
            occupied.append(starts[0][0])
        boundaries = sorted(set([*occupied, elapsed]))
        for left, right in zip(boundaries, boundaries[1:]):
            count = max(1, int(((right - left) / MAX_ANIMATION_DURATION_SECONDS).to_integral_value()))
            for step in range(1, count):
                target = left + (right - left) * step / count
                candidates = [(time, tag) for time, tag in starts
                              if left < time < right
                              and all(abs(time - other) >= 6 for other in occupied)]
                if candidates:
                    choice = min(candidates, key=lambda item: abs(item[0] - target))
                    selected.append(choice)
                    occupied.append(choice[0])
        entries = [(time, tag, False) for time, tag in selected]
        entries += [(time, tag, True) for time, tag in anchors]
        entries.sort(key=lambda item: item[0])
    else:
        selected = {0}
        mark = MAX_ANIMATION_DURATION_SECONDS
        while mark < elapsed:
            nearest = min(range(len(starts)), key=lambda i: abs(starts[i][0] - mark))
            selected.add(nearest)
            mark += MAX_ANIMATION_DURATION_SECONDS
        entries = [(starts[position][0], starts[position][1], False) for position in sorted(selected)]

    next_index = max((int(image["index"]) for image in soup.find_all("image")), default=-1) + 1
    for _, target, fixed in entries:
        if fixed and target.name == "image":
            continue  # Keep source assets and their persisted identities stable.
        image = document.new_tag("image", attrs={"index": str(next_index)})
        next_index += 1
        if fixed:
            image["source_image"] = target["source_image"]
            target.replace_with(image)
        else:
            target.insert_before(image)
    update_image_frames(soup)


def detach_all_images(chapter):
    """Remove generated still candidates and invalidate prompts for regeneration."""
    import const

    for image in chapter.get_xml().find_all("image"):
        paragraph = image.find_parent("paragraph")
        if paragraph is not None:
            image_index = int(image["index"])
            paragraph_dir = Path(const.LIBRARY_DIR) / chapter.get_paragraphdir(
                int(paragraph["index"])
            )
            for generated_image in paragraph_dir.glob(f"img_{image_index}_*.png"):
                if generated_image.is_file():
                    generated_image.unlink()

            comfy_output = Path(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"])
            for generated_image in comfy_output.glob(
                f"{chapter.nice}_img_{image_index}_*.png"
            ):
                if generated_image.is_file():
                    generated_image.unlink()

        image.attrs.pop("src", None)
        image.attrs.pop("prompt", None)
        image.attrs.pop("clip_prompt", None)
        image.attrs.pop("styled_prompt", None)
        for key in list(image.attrs):
            if key != "description" and key.endswith("_description"):
                image.attrs.pop(key, None)
    chapter.save_images()


def remove_image_animation(chapter, image):
    """Delete one image's generated videos and extracted animation frames."""
    import const

    paragraph = image.find_parent("paragraph")
    if paragraph is None:
        return
    paragraph_dir = Path(const.LIBRARY_DIR) / chapter.get_paragraphdir(
        int(paragraph["index"])
    )
    image_index = int(image["index"])
    animation_dir = paragraph_dir / "animation"
    for frame_path in animation_dir.glob(f"image_{image_index:06d}_*"):
        if frame_path.is_dir():
            shutil.rmtree(frame_path)
        elif frame_path.is_file():
            frame_path.unlink()
    legacy_frames = animation_dir / f"animation_{image_index:06d}"
    if legacy_frames.is_dir():
        shutil.rmtree(legacy_frames)

    library_root = Path(const.LIBRARY_DIR).resolve()
    explicit_video = image.get("animated_src")
    if explicit_video:
        explicit_path = Path(explicit_video)
        if not explicit_path.is_absolute():
            explicit_path = library_root / explicit_video.lstrip("/")
        try:
            explicit_path = explicit_path.resolve()
            if explicit_path.is_relative_to(library_root) and explicit_path.is_file():
                explicit_path.unlink()
        except (OSError, ValueError):
            pass
    image.attrs.pop("animated_src", None)
    for key in list(image.attrs):
        if key.startswith("animation_oom_"):
            image.attrs.pop(key, None)

    generated_videos = set(paragraph_dir.glob(f"img_{image_index}_*.mp4"))
    source = image.get("src")
    if source:
        source_path = Path(source)
        if source_path.suffix.lower() == ".png":
            generated_videos.add(paragraph_dir / f"{source_path.stem}.mp4")
            try:
                animation_count = max(1, int(image.get("animation_count", "1")))
            except ValueError:
                animation_count = 1
            generated_videos.update(
                paragraph_dir / f"{source_path.stem}_{video_index:02d}.mp4"
                for video_index in range(1, animation_count)
            )
    for video in generated_videos:
        if video.is_file():
            video.unlink()

    comfy_output = Path(const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"])
    for video in comfy_output.glob(f"{chapter.nice}_img_{image_index}_*.mp4"):
        if video.is_file():
            video.unlink()


def remove_all_animations(chapter):
    """Delete generated animation videos and extracted frames for this chapter."""
    for image in chapter.get_xml().find_all("image"):
        remove_image_animation(chapter, image)
    chapter.save_images()


def remove_all_image_metadata(chapter):
    """Clear generated scene details and prompts while preserving assets and timing."""
    fields = {
        "style", "setting", "tod", "camera", "scene_characters", "focus_character",
        "description", "animation_prompt", "prompt", "t5_prompt", "clip_prompt",
        "styled_prompt", "negative_prompt", "styled",
    }
    character_suffixes = ("_pose", "_location", "_action", "_motivation", "_description")
    for image in chapter.get_xml().find_all("image"):
        for key in list(image.attrs):
            if key in fields or key.startswith("lighting_") or key.endswith(character_suffixes):
                del image.attrs[key]
    chapter.save_images()

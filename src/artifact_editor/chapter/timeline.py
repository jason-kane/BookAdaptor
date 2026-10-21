"""Relocate image slots while preserving their paragraph-local assets."""

from pathlib import Path
import shutil
import filecmp
from decimal import Decimal, InvalidOperation
from bs4 import BeautifulSoup

MAX_ANIMATION_DURATION_SECONDS = Decimal("8")


def delete_timeline_item(chapter, item_type, item_index):
    """Remove a timeline entry while preserving other entries' asset indices."""
    if item_type not in ("phrase", "image", "silence"):
        raise ValueError("Invalid timeline item")
    soup = chapter.get_xml()
    item = soup.find(item_type, index=str(item_index))
    if item is None:
        raise ValueError("Timeline item no longer exists; reload the chapter")
    item.decompose()
    update_image_frames(soup)
    chapter.save_xml()


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
    chapter.save_xml()
    return image


def move_image(chapter, image_index, target_type, target_index, position, library_dir="."):
    if target_type not in ("phrase", "image", "silence") or position not in ("before", "after"):
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
    chapter.save_xml()


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
        raise ValueError("Silence must be a number between 0 and 3 seconds")
    if not duration.is_finite() or not 0 <= duration <= 3:
        raise ValueError("Silence must be between 0 and 3 seconds")
    return duration.quantize(Decimal("0.001"))


def save_silence(chapter, silence, value, fps):
    duration = silence_duration(value)
    silence["duration"] = format(duration, ".3f")
    silence["frames"] = str(round(duration * fps))
    update_image_frames(chapter.get_xml())
    chapter.save_xml()


def add_silence(chapter, target_type, target_index, fps):
    if target_type not in ("phrase", "image", "silence"):
        raise ValueError("Invalid timeline item")
    soup = chapter.get_xml()
    target = soup.find(target_type, index=str(target_index))
    if target is None:
        raise ValueError("Timeline item no longer exists; reload the chapter")
    index = max((int(tag["index"]) for tag in soup.find_all("silence")), default=-1) + 1
    silence = soup.new_tag("silence", attrs={"index": str(index)})
    target.insert_after(silence)
    save_silence(chapter, silence, "0.500", fps)
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
    chapter.save_xml()
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
        return paragraph_dir(tag) / tag["src"].lstrip("/")

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
            if tag.get("src"):
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


def phrase_audio_missing(chapter, phrase, library_dir):
    """Check the paragraph-local WAV used by phrase audio generation."""
    source = phrase.get("src")
    if not source:
        return True
    paragraph = phrase.find_parent("paragraph")
    directory = Path(library_dir) / chapter.get_paragraph_dir(int(paragraph["index"]))
    return not (directory / Path(source).name).is_file()


def add_import_images(soup):
    """Place slots at phrase starts nearest eight-second chapter marks."""
    # Chapter.get_xml() can return either a document or its <chapter> tag.
    # Only BeautifulSoup documents implement new_tag, including for detached tags.
    document = soup if isinstance(soup, BeautifulSoup) else BeautifulSoup("", "xml")
    elapsed = Decimal(0)
    starts = []
    for tag in soup.find_all(["phrase", "silence"]):
        if tag.name == "phrase":
            starts.append((elapsed, tag))
        elapsed += Decimal(str(tag.get("duration", 0)))
    if not starts:
        return

    selected = {0}
    mark = MAX_ANIMATION_DURATION_SECONDS
    while mark < elapsed:
        nearest = min(range(len(starts)), key=lambda i: abs(starts[i][0] - mark))
        selected.add(nearest)
        mark += MAX_ANIMATION_DURATION_SECONDS

    for index, position in enumerate(sorted(selected)):
        image = document.new_tag("image", attrs={"index": str(index)})
        starts[position][1].insert_before(image)
    update_image_frames(soup)

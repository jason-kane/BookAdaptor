"""Bridge Theater shot plans into the existing chapter timeline and renderer."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup

import const
from artifact_editor import config


def project_directory(project_id: str) -> Path:
    root = Path(const.THEATER_DIR).resolve()
    for candidate in root.iterdir():
        if not candidate.is_dir() or candidate.is_symlink():
            continue
        try:
            saved = json.loads((candidate / "project.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if saved.get("project_id") == project_id:
            return candidate
    raise FileNotFoundError(f"Theater project {project_id} was not found")


def update_project(project_id: str, status: str, **updates: Any) -> None:
    folder = project_directory(project_id)
    path = folder / "project.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["render_status"] = status
    data.update(updates)
    data["updated_at"] = __import__("datetime").datetime.now().astimezone().isoformat(timespec="seconds")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=folder, delete=False) as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary_path = handle.name
    Path(temporary_path).replace(path)


def _normalise_lines(value: Any, default_speaker: str) -> list[dict[str, str]]:
    if isinstance(value, str):
        entries: list[Any] = [value]
    elif isinstance(value, list):
        entries = value
    else:
        entries = []
    lines = []
    for entry in entries:
        if isinstance(entry, str):
            text, speaker, delivery = entry, default_speaker, ""
        elif isinstance(entry, dict):
            text = entry.get("text", entry.get("phrase", entry.get("line", "")))
            speaker = entry.get("speaker") or default_speaker
            delivery = entry.get("delivery", "")
        else:
            continue
        text = " ".join(str(text or "").split())
        if not text or text.lower() in {"silence", "none", "no dialogue"}:
            continue
        # Director scripts keep speaker labels as structured metadata, but some
        # scripts also repeat the label in the dialogue string. Do not send that
        # redundant prefix to TTS.
        label = re.match(r"^([^:\n]{1,80}):\s*", text)
        if label:
            normalise_name = lambda value: re.sub(
                r"[^a-z0-9]", "", re.sub(r"^the\s+", "", str(value).strip().casefold())
            )
            if normalise_name(label.group(1)) == normalise_name(speaker):
                text = text[label.end():].strip()
                if not text:
                    continue
        lines.append({"text": text, "speaker": str(speaker), "delivery": str(delivery)})
    return lines


def _shot_lines(shot: dict[str, Any]) -> list[dict[str, str]]:
    speaker = str(shot.get("speaker") or "Narrator")
    if speaker.strip().lower() in {"none", "silence", "sound design only"}:
        return []
    value = shot.get("spoken_phrases", shot.get("dialogue", shot.get("phrase", [])))
    lines = _normalise_lines(value, speaker)
    return [
        line for line in lines
        if not re.fullmatch(
            r"(?:[^:]{1,80}:\s*)?\(?\s*(?:silence|none)\s*\)?[.!…]*",
            line["text"], re.IGNORECASE,
        )
    ]


def _narration_chunks(text: str, count: int) -> list[str]:
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text.strip()) if part.strip()]
    if not sentences or count <= 0:
        return []
    chunks = ["" for _ in range(count)]
    for index, sentence in enumerate(sentences):
        slot = min(count - 1, index * count // len(sentences))
        chunks[slot] = f"{chunks[slot]} {sentence}".strip()
    return chunks


def prepare_chapter(project_id: str, data: dict[str, Any]):
    """Create a Textoviva-compatible chapter from the director's scenes and shots."""
    from artifact_editor.author.author import Author
    from artifact_editor.chapter.chapter import Chapter

    scenes = data.get("script", {}).get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("Create a director script with at least one scene before making a rough draft.")
    folder = project_directory(project_id)
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValueError("The director must name the film before making a rough draft.")
    chapter = Chapter(author=Author("Theater"), title=folder.name, number=1, language="english")
    # Rebuilding the rough draft regenerates chapter.xml from the current
    # script. Keep already-rendered stills attached to the same shot indices;
    # users have an explicit "Reset images" action when they want fresh art.
    reusable_stills: dict[tuple[str, str], str] = {}
    if Path(chapter.get_xml_fn()).is_file():
        chapter.load_xml(force=True)
        for existing_image in chapter.images():
            image_index = str(existing_image.get("index", ""))
            parent = existing_image.find_parent("paragraph")
            paragraph_index = str(parent.get("index", "")) if parent else ""
            existing_src = str(existing_image.get("src", "")).strip()
            existing_path = chapter.get_image_filename(existing_image)
            if paragraph_index and image_index and existing_src and existing_path and Path(existing_path).is_file():
                reusable_stills[(paragraph_index, image_index)] = existing_src
    chapter.mkdirs()
    # Chapter.mkdirs() creates paths relative to the process working directory,
    # while the chapter writers resolve them below const.LIBRARY_DIR. Theater
    # projects live under /active/Theater, so ensure the actual output folders
    # exist before save_xml()/writing chapter.txt.
    Path(chapter.get_xml_fn()).parent.mkdir(parents=True, exist_ok=True)
    Path(chapter.get_txt_fn()).parent.mkdir(parents=True, exist_ok=True)
    config.save_config(chapter.bookdir, {
        "author": "Theater", "title": title, "chapter_title": "Film timeline",
        "aspect_ratio": "widescreen", "default_style": "",
    })
    chapter.config.update({"author": "Theater", "title": title, "chapter_title": "Film timeline", "aspect_ratio": "widescreen"})
    shots: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for scene in scenes:
        for shot in scene.get("shots", []) if isinstance(scene, dict) else []:
            if isinstance(shot, dict):
                shots.append((scene, shot))
    if not shots:
        raise ValueError("The director's script has no shots to render.")
    narration = str(data.get("script", {}).get("story_rewrite") or "").strip()
    any_dialogue = any(_shot_lines(shot) for _, shot in shots)
    narration_chunks = _narration_chunks(narration, len(shots)) if not any_dialogue else []

    soup = BeautifulSoup("<chapter version='1.0'></chapter>", "xml")
    chapter_node = soup.find("chapter")
    phrase_index = 0
    image_index = 0
    silence_index = 0
    plain_text: list[str] = []
    for scene_index, scene in enumerate(scenes):
        if not isinstance(scene, dict):
            continue
        paragraph = soup.new_tag("paragraph", attrs={
            "index": str(scene_index), "dir": chapter.get_paragraph_dir(scene_index), "tags": "has-text=false,has-image=true",
        })
        chapter_node.append(paragraph)
        scene_marker = soup.new_tag("scene", attrs={
            "index": str(scene_index), "title": str(scene.get("title") or f"Scene {scene_index + 1}"),
            "description": str(scene.get("dramatic_purpose") or ""),
        })
        paragraph.append(scene_marker)
        for shot in scene.get("shots", []):
            if not isinstance(shot, dict):
                continue
            image_prompt = str(shot.get("image_prompt") or "").strip()
            if not image_prompt:
                image_prompt = ". ".join(str(value).strip() for value in (
                    scene.get("visual_note"), shot.get("visual_note"), shot.get("framing"),
                    shot.get("composition"), shot.get("action"), shot.get("lighting"),
                ) if value and str(value).strip())
            if not image_prompt:
                raise ValueError(f"Shot {image_index + 1} has no image prompt or visual description.")
            animation_prompt = str(shot.get("animation_prompt") or "").strip()
            if not animation_prompt:
                animation_prompt = str(shot.get("action") or shot.get("visual_note") or image_prompt)
            image = soup.new_tag("image", attrs={
                "index": str(image_index), "prompt": image_prompt,
                "animation_prompt": animation_prompt, "style": "",
                "fullscreen": "true", "mode": "scale",
                "render_profile": "theater",
                "scene_title": str(scene.get("title") or ""),
                "visual_note": str(shot.get("visual_note") or scene.get("visual_note") or ""),
            })
            still_key = (str(scene_index), str(image_index))
            if still_key in reusable_stills:
                image["src"] = reusable_stills[still_key]
            paragraph.append(image)
            lines = _shot_lines(shot)
            if not any_dialogue and narration_chunks:
                lines = [{"text": narration_chunks[image_index], "speaker": "Narrator", "delivery": ""}] if narration_chunks[image_index] else []
            if not lines:
                raise ValueError(
                    f"Shot {image_index + 1} has no spoken dialogue or narration. "
                    "Ask the director to refine the script with an audible story track before rendering."
                )
            for line in lines:
                phrase = soup.new_tag("phrase", attrs={
                    "index": str(phrase_index), "speaker": line["speaker"], "fullscreen": "true",
                    "delivery": line["delivery"], "id": f"{scene_index}_{phrase_index}",
                })
                phrase.string = line["text"]
                paragraph.append(phrase)
                plain_text.append(line["text"])
                phrase_index += 1
            if not lines:
                try:
                    duration = max(0.25, float(shot.get("silence_seconds", shot.get("duration_seconds", 2.5))))
                except (TypeError, ValueError):
                    duration = 2.5
                silence = soup.new_tag("silence", attrs={"index": str(silence_index), "duration": str(duration)})
                paragraph.append(silence)
                silence_index += 1
            try:
                pause = max(0.0, float(shot.get("pause_seconds", 0) or 0))
            except (TypeError, ValueError):
                pause = 0.0
            if pause:
                paragraph.append(soup.new_tag("silence", attrs={"index": str(silence_index), "duration": str(pause)}))
                silence_index += 1
            image_index += 1

    chapter.soup = soup
    chapter.save_xml()
    Path(chapter.get_txt_fn()).write_text("\n\n".join(plain_text), encoding="utf-8")
    return chapter


def prepare_audio_and_aspect(chapter, progress=None) -> tuple[float, str]:
    """Create speech tracks first, then choose the strict runtime-based format."""
    from artifact_editor.phrases import phrases
    from artifact_editor import llm
    import comfy

    total_seconds = 0.0
    all_phrases = list(chapter.phrases())
    # Speech audio is generated by ComfyUI, while director/script LLM tasks use
    # Llama. Mark every TTS workflow as priority so queued Llama work does not
    # get selected between phrases and trigger repeated slow provider handoffs.
    from gpu_tasks import prioritize_gpu_tasks

    pending = []
    with prioritize_gpu_tasks():
        for index, phrase in enumerate(all_phrases):
            phrase_xml = phrase.phrase_xml
            audio_filename = phrases.get_wavfile_filename(phrase_xml, chapter)
            if not Path(audio_filename).is_file():
                speaker = phrase_xml.attrs.get("speaker", "Narrator")
                if progress:
                    progress(5, f"Queueing voice for script line {index + 1}/{len(all_phrases)} · {speaker}")
                request = llm.text_2_audio(
                    chapter, phrase_xml, speaker, audio_filename, force=False,
                    queue_only=True,
                )
                if not request.get("ready"):
                    pending.append((index, phrase_xml, request))

        failures = []
        if pending:
            if progress:
                progress(5, f"Queued voice generation for {len(pending)} script lines")

            def collect_voice(position, result):
                index, phrase_xml, request = pending[position]
                if isinstance(result, Exception):
                    failures.append((index, result))
                    return
                llm.collect_text_2_audio(request)
                chapter.textoviva.speak(chapter, phrase_xml)
                if progress:
                    speaker = phrase_xml.attrs.get("speaker", "Narrator")
                    progress(5, f"Saved voice audio for line {index + 1}/{len(all_phrases)} · {speaker}")

            results = comfy.run_workflow_fn_batch(
                [
                    {
                        "workflow_fn": request["workflow_fn"],
                        "label": request["label"],
                        # Free cached VRAM once before this serial voice batch;
                        # the GPU queue runs its jobs one at a time.
                        "prepare_vram": position == 0,
                    }
                    for position, (_, _, request) in enumerate(pending)
                ],
                on_complete=collect_voice,
                return_exceptions=True,
            )
            failures.extend(
                (pending[index][0], result)
                for index, result in enumerate(results)
                if isinstance(result, Exception)
                and not any(failed_index == pending[index][0] for failed_index, _ in failures)
            )
        if failures:
            index, error = failures[0]
            raise RuntimeError(
                f"Could not create voice audio for script line {index + 1}: {error}"
            ) from error

        if progress:
            progress(5, "Aligning dialogue lines to their generated voice recordings")
        for index, phrase in enumerate(all_phrases):
            phrase_xml = phrase.phrase_xml
            audio_filename = phrases.get_wavfile_filename(phrase_xml, chapter)
            if not Path(audio_filename).is_file():
                raise ValueError(f"ComfyUI did not create audio for script phrase {index + 1}")
            phrases.wav_pad_to_frame(audio_filename)
            duration = float(phrases.get_wav_duration(audio_filename))
            if duration <= 0:
                raise ValueError(f"Script phrase {index + 1} has no audio duration")
            phrase_xml.attrs["duration"] = str(duration)
            phrase_xml.attrs["frames"] = str(round(duration * const.FPS))
            total_seconds += duration
    for silence in chapter.get_xml().find_all("silence"):
        duration = max(0.0, float(silence.get("duration", 0) or 0))
        silence["duration"] = str(duration)
        silence["frames"] = str(round(duration * const.FPS))
        total_seconds += duration
    if total_seconds <= 0:
        raise ValueError("The rough draft has no timed speech or silence")
    aspect = "portrait" if total_seconds < 300 else "widescreen"
    chapter.config["aspect_ratio"] = aspect
    chapter.aspect = aspect
    chapter.save_config()
    chapter.save_xml()
    return total_seconds, aspect


def rough_draft_progress(chapter, callback):
    """Number the real rough-render phases, omitting work this cut will skip."""
    if callback is None:
        return None

    images = chapter.get_xml().find_all("image")
    needs_images = any(
        not (chapter.get_image_filename(image) and Path(chapter.get_image_filename(image)).is_file())
        for image in images
    )
    has_animation = False
    if images:
        from artifact_editor.images.animation_source import animation_filename

        for image in images:
            paragraph = image.find_parent("paragraph")
            paragraph_dir = Path(const.LIBRARY_DIR) / chapter.get_paragraph_dir(
                int(paragraph["index"])
            )
            if animation_filename(image, paragraph_dir):
                has_animation = True
                break

    stages = ["voice", "assets", "narration"]
    if needs_images:
        stages.append("images")
    if has_animation:
        stages.append("animation")
    stages.extend(("camera", "timeline", "frames", "encode", "save"))
    labels = {
        "voice": "Creating voice audio for director-script dialogue",
        "assets": "Preparing chapter assets",
        "narration": "Aligning spoken-line durations with generated audio",
        "images": "Generating images",
        "animation": "Expanding animation frames",
        "camera": "Preparing camera movement",
        "timeline": "Building frame timeline",
        "frames": "Rendering frames",
        "encode": "Encoding video",
        "save": "Saving rough draft",
    }

    def stage_for(message):
        lowered = str(message).casefold()
        if any(term in lowered for term in (
            "director's script", "spoken line", "script line", "voice generation",
            "queueing voice", "saved voice audio", "speech audio for line", "rough voice track",
        )):
            return "voice"
        if "preparing chapter assets" in lowered:
            return "assets"
        if any(term in lowered for term in (
            "preparing narration audio", "aligning spoken-line timing",
            "aligning dialogue lines", "generating audio",
        )):
            return "narration"
        if any(term in lowered for term in (
            "generating image", "queued still image", "saving generated still",
        )):
            return "images"
        if "expanding animation" in lowered:
            return "animation"
        if "preparing camera movement" in lowered:
            return "camera"
        if "building frame timeline" in lowered or "preparing frame timeline" in lowered:
            return "timeline"
        if "rendering frames" in lowered:
            return "frames"
        if "encoding complete chapter video" in lowered or "chapter video ready" in lowered:
            return "encode"
        if "saving rough draft" in lowered:
            return "save"
        return None

    def report(percent, message):
        stage = stage_for(message)
        if stage not in stages:
            callback(percent, message)
            return
        detail = str(message)
        label = labels[stage]
        if stage == "voice" or stage in ("images", "animation", "frames"):
            label = detail
        elif detail.casefold() == label.casefold():
            pass
        numbered = f"[{stages.index(stage) + 1}/{len(stages)}] {label}"
        if label != detail and stage in ("images", "animation", "frames"):
            numbered += f" — {detail}"
        callback(percent, numbered)

    return report


def preserve_render(chapter, project_id: str, variant: str) -> Path:
    source = Path(chapter.get_video_filename())
    if not source.is_file():
        raise FileNotFoundError("The completed video file was not created")
    target = project_directory(project_id) / ("rough-draft.mp4" if variant == "rough" else "final.mp4")
    shutil.copy2(source, target)
    return target

from __future__ import annotations

import contextlib
import fcntl
import glob
import json
import os
import re
import shutil
from typing import TYPE_CHECKING

import redis
from filelock import FileLock
from flask import url_for

from bs4 import Tag
from worker_client import sentence_pipeline

from artifact_editor.phrases import phrases
import const
import logger
from artifact_editor.phrases.phrases import Phrase
from artifact_editor.phrases.timeline import timeline
from artifact_editor.images import images
from artifact_editor.video import video
from artifact_editor.images.selector import htmx as selector_htmx
from .workflow import animation_workflow_name
import comfy

log = logger.log(__name__)
from artifact_editor.cache import r

if TYPE_CHECKING:
    from artifact_editor.chapter.chapter import Chapter


class Textoviva:
    def __init__(self, chapter: Chapter):
        self.chapter = chapter
        self.videofile = chapter.get_video_filename()

    def create_video(self, force: bool = False, progress=None, regenerate_image_index=None, regenerate_image_indices=None, regenerate_insufficient=False):
        """
        Create a video from the chapter's phrases.
        """
        log.info("Creating textoviva video for chapter %s", self.chapter)
        # check if the video already exists
        if os.path.exists(self.videofile) and not force:
            log.info("Video file %s already exists, skipping creation", self.videofile)
            return

        def report(percent, message):
            if progress:
                progress(percent, message)

        report(1, "Preparing chapter assets")
        base_mode = "narrated_poem"

        # create the video.  The algorithm is fill in anything missing with best effort.
        # all the way from 'raw' chapter to video.

        # the presentation is then that video, with really good widget for
        # making it better, by correcting and refining the kinds of choices
        # we're about to make.

        # chapter() gives us a parsed xml <paragraph><phrase></paragraph> sort of presentation of the material.
        # Its really not a good way to represent objects like these.  I've grown to dislike it rather severely.
        
        # So what; database?  I've been avoiding it because I raced down that track
        # about a million iterations ago and it was a slow time sink of shit.
        r = redis.Redis(host="redis")
        # Red carpet for an exhaustive text-to-video pipeline.
        # We expect to have:
        #   a self.chapter() object, which is a chapter.Chapter() swiss army knife.
        #   an at least technically correct and complete chapter.get_xml().
        #   a self.chapter.phrases() generator, which yields the phrases in order.
        #
        #  we start with a full wide-screen idle-animated title and author page.
        # to build that, we start with the audio.
        # the Narrator voice a global.

        # create text_layer.png for display
        # create text_layer_rainbow.png for highlighting

        all_phrases = list(self.chapter.phrases())

        if not all_phrases:
            # you've got nothing.  Sad.  Build everything.
            # log.error('No phrases found in chapter %s.  This is broken.', self.chapter)
        
            # title_by_author = Phrase(
            #     chapter=self.chapter,
            #     phrase_id=0,
            #     speaker="narrator",
            # )
            # title_by_author.generate_image(
            #     "Elegant book cover for " 
            #     + self.chapter.title
            #     + " by "
            #     + self.chapter.author.pretty_name
            #     + " in an environment that represents the themes of the book."
            # )
            # # title_by_author.speak()
            # title_by_author.save()
            # all_phrases.append(title_by_author)

            # The new Phrase algorithm. 
            # 
            # try and divide the chapter by quotation
            # mark style dialog. if that works, ie: multiple speeches detected,
            # we're good. we will break the phrase up into the largest pieces by
            # a single speaker that will complete a phrase and still fit into
            # our TTS engine.
            
            # we're going to use spacy
            nlp = sentence_pipeline

            # we will run it on the raw chapter.txt
            chapter_text = self.chapter.load_txt()

            if base_mode in ["narrated_poem"]:
                # any work with a single narrator and one extra
                # newline between paragraphs.

                # since it's a "*_poem" on the latex side we will do everything
                # we can to preserve the physical presentation.
                paragraph_index = 0
                for paragraph in chapter_text.split("\n\n"):
                    if not paragraph.strip():
                        continue

                    paragraph_xml = self.chapter.soup.new_tag("paragraph")
                    paragraph_xml.attrs["index"] = paragraph_index
                    self.chapter.soup.append(paragraph_xml)
                    log.info("Paragraph: %s", paragraph_xml)
                
                    phrase_index = 0
                    for sentence_obj in nlp(paragraph).sents:
                        # process each sentence object here
                        log.info("Phrase: %s", sentence_obj.text)
                        phrase_xml = self.chapter.soup.new_tag("phrase")
                        phrase_xml.attrs["speaker"] = "Narrator"
                        phrase_xml.attrs["index"] = str(phrase_index)
                        paragraph_xml.append(phrase_xml)

                        phrase_xml.string = sentence_obj.text
                        phrase_index += 1

                    paragraph_index += 1

            self.chapter.save_xml()

            # but.. won't that break all the highlighting stuff?
            # yup.  but we will fix it, and it will be better.

            # 
            # Images will _no longer be directly tied to phrases.
            # For the sanity of the world, images are simply tied to frames.
            # they will not be stored in the chapter xml.

            # Backend is a simple pair, the index file, a list where the index
            # is the frame number, and we guarantee an entry up to whatever the
            # largest frame_index we've recieved is.

            # we're in the maybe hundreds of images, so using json for these
            # is an acceptable solution with good readability benefits.
            
            # the frame index:
            # [ image_unique, ... ]

            # the data file:
            # {
            #    image_unique: {all image metadata},
            # }
            #
            
            # they will be stored to the image API.  It's a black box with a file
            # in the chapter directory.

            # The API is _very_ easy.
            # 
            # GET /<frame_index>.png  Returns the PNG for a specific frame. 
            # POST /<frame_index>.png  Sets the PNG for a specific frame.
            # (the chapter identifier is already handled)

            # We start at frame_index 0 and the first ms of audio.
            # The default image for frame_index 0 is full black.
            # We advance one frame at a time.

            # If there is a new image, we use it.  If there is not, we continue
            # using the current image.  Easy.



        # phrases = [
        #     title_by_author
        # ]

        # TODO: pre-scan to determine names and aliases for every character
        # leads to better everything.

        # phrase the chapter.  I'm doing it algorithmicaly, but this isn't a bad
        # AI problem.  Each phrase is highlighted as a group in the read-a-long,
        # and image changes are possible at phrase boundaries.

        # once phrasing is complete, we can generate typography, both the
        # display version and the phrase map.  The phrase map is exactly the
        # same, but each phrase is highlighted in a different shade, which is
        # reversable.  This gives us easy access to exact per-phrase dimensions.

        # Seriously thinking about making it per-word.  We can do cool stuff
        # with per-word.  BUT, we need per-word audio tracking too, or it will
        # be shit.

        # but.. if we do that, we could use a human voice just as easily as a
        # generated one.
        # Start with Audio to establish our base timeline.
        report(5, "Preparing narration audio")
        total_duration = 0
        for phrase in self.chapter.phrases():
            # Chapter.phrases() yields Phrase wrappers; video generation needs
            # the underlying BeautifulSoup tag so it can read and update XML
            # attributes.
            phrase_xml = phrase.phrase_xml

            # smear some AI shit right here to choose the correct character.
            
            if not phrase_xml.attrs.get("speaker"):
                if base_mode in ["narrated_poem"]:
                    phrase_xml.attrs["speaker"] = "Narrator"
                else:
                    raise ValueError(f"{base_mode} is too fucking stupid to know who should be talking")

            if not phrase_xml.attrs.get("src") or not phrase_xml.attrs.get("pronunciation"):
                report(5, f"Phrase {phrase.index}: Generating Audio")
                phrase_xml = self.speak(
                    chapter=self.chapter,
                    phrase_xml=phrase_xml,
                )

            phrase_duration = float(phrase_xml.attrs.get('duration', '0') or 0)
            if phrase_duration <= 0:
                log.info('Measuring duration...')
                audio_filename = phrase_xml.attrs['src']
                phrase_duration = phrases.get_wav_duration(audio_filename)
                phrase_xml.attrs['duration'] = str(phrase_duration)
                log.info('Saving duration: %s', phrase_duration)

            total_duration += phrase_duration
            log.info('Total duration so far: %s', total_duration)

            self.chapter.save_xml()

        total_images = len(list(self.chapter.images()))

        # no images, or clearly too few.
        if total_images == 0 or total_duration / total_images > 16:
            # Add image slots every 8-ish seconds
            timeline.inject_image_slots(self.chapter)

        self.chapter.save_xml()

        # Derive each image slot's duration from the phrases and silence it covers.
        # Stored frame counts can be stale, so recalculate them unconditionally.
        for image_xml in self.chapter.images():
            duration_frames = 0
            next_image_xml = image_xml.find_next("image")
            for point in image_xml.next_elements:
                if point == next_image_xml:
                    break
                if not isinstance(point, Tag) or point.name not in ("phrase", "silence"):
                    continue

                duration = float(point.attrs.get("duration", "0") or 0)
                if duration <= 0 and point.name == "phrase":
                    if point.attrs.get("src"):
                        duration = phrases.get_wav_duration(point.attrs["src"])
                        point.attrs["duration"] = str(duration)
                    else:
                        self.speak(self.chapter, point)
                        duration = float(point.attrs.get("duration", "0") or 0)

                phrase_frames = round(duration * const.FPS)
                point.attrs["frames"] = str(phrase_frames)
                duration_frames += phrase_frames

            image_xml.attrs["frames"] = str(duration_frames)
            image_xml.attrs["duration"] = str(duration_frames / const.FPS)
            log.info(
                "Set image duration from covered phrases and silence",
                image_index=image_xml.attrs["index"],
                frames=duration_frames,
                seconds=duration_frames / const.FPS,
            )

        self.chapter.save_xml()

        # generate scene metadata and descriptions
        for image_xml in self.chapter.images():
            report(10, f"Image {image_xml.attrs['index']}: Preparing Metadata")
            images.generate_metadata_from_text(
                chapter=self.chapter,
                image_xml=image_xml,
                force_all=False,
            )
        self.chapter.save_xml()

        self.generate_missing_images(progress=progress)
        self.generate_missing_animations(
            progress=progress, force=force,
            regenerate_image_index=regenerate_image_index,
            regenerate_image_indices=regenerate_image_indices,
            regenerate_insufficient=regenerate_insufficient,
        )

        # Rebuild expansions before frame workers start, including animations
        # retained by "generate missing". Never reuse a partial or mixed-width
        # PNG sequence from an interrupted/older extraction.
        from artifact_editor import tools
        from artifact_editor.images.animation_source import animation_filename
        for image_xml in self.chapter.images():
            paragraph = image_xml.find_parent("paragraph")
            paragraph_dir = os.path.join(
                const.LIBRARY_DIR,
                self.chapter.get_paragraph_dir(int(paragraph["index"])),
            )
            filename = animation_filename(image_xml, paragraph_dir)
            if not filename:
                continue
            animation_root = os.path.join(paragraph_dir, "animation")
            prefix = f"image_{int(image_xml['index']):06d}_"
            for directory in glob.glob(os.path.join(animation_root, prefix + "*")):
                if os.path.isdir(directory):
                    shutil.rmtree(directory)
            report(65, f"Image {image_xml['index']}: Expanding Animation")
            tools.extract_frames(
                os.path.join(paragraph_dir, filename),
                os.path.join(animation_root, prefix + "00"),
            )

        # generate text scrolling rate
        report(65, "Preparing camera movement")
        self.chapter.build_frame_to_camera(aspect="widescreen")

        # rebuild masterplan
        self.chapter.get_masterplan(force=True)

        # if everything is where it belongs, this should assemble it into a
        # video.

        # TODO
        report(65, "Building frame timeline")
        video.render_masterplan_widescreen(
            self.chapter,
            progress=(lambda percent, message: report(65 + int(percent * 0.34), message))
            if progress else None,
        )


    def generate_missing_images(self, progress=None):
        """Generate still images only; preserve existing images."""
        report = progress or (lambda percent, message: None)
        # second pass is massively more efficent so we don't have to
        # toggle between models.
        for image_index, image_xml in enumerate(self.chapter.images()):
            # Keep the prompt tab's workflow selector populated, including for
            # images whose files already exist and therefore skip generation.
            if not image_xml.attrs.get("workflow_image_template"):
                workflow_template = const.DEFAULT_IMAGE_TEMPLATE
                if "." in workflow_template:
                    workflow_template = workflow_template.split(".")[-1]
                image_xml.attrs["workflow_image_template"] = workflow_template

            image_fn = self.chapter.get_image_filename(image_xml)
            if image_fn:
                if os.path.exists(image_fn):
                    log.info("[%s] Image %s already exists, skipping generation", image_index, image_fn)
                    continue
                else:
                    log.warning("[%s] Image %s does not exist. generating replacement...", image_index, image_xml)
            else:
                log.warning("[%s] Image %s has no filename...", image_index, image_xml)

            # a "paragraph" image has the entire paragraph available and highlighted to
            # the image generator.  The specific phrases unique to _this_ image
            # are also specified.  We're going to block, so if things aren't going well
            # it's easier to stop early.
            workflow_template = const.DEFAULT_IMAGE_TEMPLATE
            # mode.workflow_template
            if "." in workflow_template:
                workflow_template = workflow_template.split(".")[-1]

            image_xml.attrs["workflow_image_template"] = workflow_template

            workflow_fn = self.chapter.get_workflow(
                image_xml,
                workflow_name=workflow_template,
                video_index=None,
                template_environment={}
            )
            report(20, f"Image {image_xml.attrs['index']}: Generating Image")
            comfy.run_workflow_fn(workflow_fn)

            candidates = selector_htmx.gather_candidate_images(image_xml, self.chapter)
            image_xml.attrs['src'] = candidates[0] if candidates else ""

        self.chapter.save_xml()


    def generate_missing_animations(self, progress=None, force=False,
                                    regenerate_image_index=None, regenerate_image_indices=None,
                                    regenerate_insufficient=False):
        """Generate animations from existing stills without building a video."""
        report = progress or (lambda percent, message: None)
        missing = [image for image in self.chapter.images()
                   if not self.chapter.get_image_filename(image)
                   or not os.path.isfile(self.chapter.get_image_filename(image))]
        if missing:
            raise ValueError("Generate missing images before generating animations")
        for image_xml in self.chapter.images():
            if "animation_prompt" in image_xml.attrs and image_xml.attrs["animation_prompt"] not in ["", None]:
                continue  # already has an animation prompt

            # now we have images, but we really want an animation. ti->t qwen
            # for a description of what's happening in the image, in the context
            # of the story?  Use that as the animation prompt?
            report(30, f"Image {image_xml.attrs['index']}: Generating Animation Prompt")
            image_xml.attrs["animation_prompt"] = images.generate_animation_prompt(
                chapter=self.chapter,
                image_xml=image_xml,
            )
            self.chapter.save_xml()

        # Force only the selected animation when editing from the player.
        force_all = force
        # and finally draw the animations
        animation_images = list(self.chapter.images())
        for animation_position, image_xml in enumerate(animation_images):
            animation_percent = 35 + int(30 * animation_position / max(1, len(animation_images)))
            report(animation_percent, f"Image {image_xml.attrs['index']}: Preparing Animation")
            force = force_all or int(image_xml.attrs["index"]) in (regenerate_image_indices or ()) or (
                regenerate_image_index is not None
                and int(image_xml.attrs["index"]) == regenerate_image_index
            )
            
            if regenerate_insufficient:
                from artifact_editor.images.animation_source import animation_frame_count
                paragraph = image_xml.find_parent("paragraph")
                paragraph_dir = os.path.join(
                    const.LIBRARY_DIR,
                    self.chapter.get_paragraph_dir(int(paragraph["index"])),
                )
                count = animation_frame_count(image_xml, paragraph_dir, const.FPS)
                required = int(image_xml.attrs.get("frames", 0))
                if count is not None and count >= required:
                    continue
                if count is None or count < required:
                    force = True
                    # Start replacements at full duration, rather than reusing
                    # bounds learned while deliberately requesting shorter clips.
                    for attribute in ("animation_oom_audio_frames", "animation_oom_success_frames",
                                      "animation_oom_failure_frames"):
                        image_xml.attrs.pop(attribute, None)

            if "animated_src" in image_xml.attrs and image_xml.attrs["animated_src"] not in ["", None]:
                animated_src = image_xml.attrs["animated_src"]
            else:
                animated_src = self.chapter.get_image_filename(image_xml).replace('.png', '.mp4')

            # XML media paths are commonly stored relative to the library
            # (and may begin with "/active/" for URL generation). Resolve
            # those paths under LIBRARY_DIR before checking or writing them.
            animated_path = (
                animated_src
                if os.path.isabs(animated_src)
                else os.path.join(const.LIBRARY_DIR, animated_src.lstrip("/"))
            )

            # A previous ComfyUI run may have completed but left its MP4 in
            # ComfyUI output (or under its generated filename in the
            # paragraph directory). Reuse that indexed result before queuing
            # another animation workflow.
            if not force and not os.path.exists(animated_path):
                cached_videos = selector_htmx.gather_candidate_videos(
                    image_xml, self.chapter
                )
                if cached_videos:
                    os.makedirs(os.path.dirname(animated_path), exist_ok=True)
                    shutil.copy2(cached_videos[0], animated_path)
                    log.info(
                        "Reusing existing animation candidate",
                        image_index=image_xml.attrs["index"],
                        candidate=cached_videos[0],
                        destination=animated_path,
                    )

            # src="/active/Ed Nather/The Story of Mel/chapter/0001/paragraphs/000000/img_0_straight-zit-Straight_00_00001_.png"
            if force:
                if os.path.exists(animated_path):
                    log.info("Removing existing animation for forced re-render: %s", animated_path)
                    os.remove(animated_path)
                image_fn = self.chapter.get_image_filename(image_xml)
                if image_fn:
                    paragraph = image_xml.find_parent("paragraph")
                    image_index = int(image_xml.attrs["index"])
                    paragraph_dir = self.chapter.get_paragraph_dir(paragraph.attrs["index"])

                    # Remove cached ComfyUI outputs and chapter-side variants;
                    # otherwise gather_candidate_videos can select an old MP4
                    # and silently restore the animation we just invalidated.
                    comfy_prefix = f"{self.chapter.nice}_img_{image_index}"
                    comfy_output_dir = const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]
                    for stale_video in glob.glob(os.path.join(comfy_output_dir, comfy_prefix + "*.mp4")):
                        os.remove(stale_video)
                    for stale_video in images.all_videos_by_image_index(paragraph_dir, image_index):
                        os.remove(stale_video)

                    animation_dir = os.path.join(
                        const.LIBRARY_DIR,
                        self.chapter.get_paragraphdir(int(paragraph.attrs["index"])),
                        "animation",
                        f"image_{image_index:06d}_00",
                    )
                    shutil.rmtree(animation_dir, ignore_errors=True)

            if os.path.exists(animated_path):
                log.info("Animation %s already exists, skipping generation", animated_src)
                continue
            else:
                log.info('Animation %s does not exist, generating', animated_src)
                image_fn = self.chapter.get_image_filename(image_xml)
                extraction_video_fn = (
                    image_fn.replace(".png", ".mp4") if image_fn else None
                )
                if force and extraction_video_fn and os.path.isfile(extraction_video_fn):
                    log.info(
                        "Removing stale animation source video before forced rerender",
                        video_filename=extraction_video_fn,
                    )
                    os.remove(extraction_video_fn)
                log.info(
                    "Upcoming animation frame extraction source after cleanup",
                    video_filename=extraction_video_fn,
                    force=force,
                    exists=(
                        os.path.isfile(extraction_video_fn)
                        if extraction_video_fn
                        else False
                    ),
                )

            workflow_template = image_xml.attrs.get("workflow_i2v_template") or const.DEFAULT_I2V_TEMPLATE
            workflow_template = animation_workflow_name(workflow_template)

            image_xml.attrs["workflow_i2v_template"] = (
                image_xml.attrs.get("workflow_i2v_template") or const.DEFAULT_I2V_TEMPLATE
            )

            audio_frames = int(image_xml.attrs.get("frames", 0))
            workflow_video_index = 0

            def build_animation_workflow(frame_count: int):
                saved_frames = image_xml.attrs.get("frames")
                image_xml.attrs["frames"] = str(frame_count)
                try:
                    return self.chapter.get_workflow(
                        image_xml,
                        workflow_name=workflow_template,
                        video_index=workflow_video_index,
                        template_environment={},
                        # Keep animation prompt and frame inputs, but force the
                        # first frame to come from the selected still image.
                        use_image_source=force,
                    )
                finally:
                    if saved_frames is None:
                        image_xml.attrs.pop("frames", None)
                    else:
                        image_xml.attrs["frames"] = saved_frames

            bounds_audio_frames = int(
                image_xml.attrs.get("animation_oom_audio_frames", "0") or 0
            )
            if bounds_audio_frames != audio_frames:
                image_xml.attrs.pop("animation_oom_audio_frames", None)
                image_xml.attrs.pop("animation_oom_success_frames", None)
                image_xml.attrs.pop("animation_oom_failure_frames", None)

            lower_bound = int(
                image_xml.attrs.get("animation_oom_success_frames", "0") or 0
            )
            upper_bound = int(
                image_xml.attrs.get("animation_oom_failure_frames", "0") or 0
            )
            if force and 0 < lower_bound < upper_bound:
                attempted_frames = (lower_bound + upper_bound + 1) // 2
            else:
                # Cover the entire audio span, including silence. Shorter
                # requests are reserved for retries after an actual OOM.
                default_animation_frames = max(1, audio_frames)
                attempted_frames = default_animation_frames
            workflow_fn = build_animation_workflow(attempted_frames)
            if not workflow_fn:
                raise RuntimeError(
                    f"Could not build animation workflow {workflow_template} "
                    f"for image {image_xml.attrs['index']}"
                )
            attempted_duration = attempted_frames / const.FPS
            log.info(
                "Attempting animation generation",
                image_index=image_xml.attrs["index"],
                frames=attempted_frames,
                duration_seconds=attempted_duration,
            )
            report(animation_percent, f"Image {image_xml.attrs['index']}: Generating Animation")
            try:
                comfy.run_workflow_fn(
                    workflow_fn,
                    duration_seconds=attempted_duration,
                )
            except RuntimeError as error:
                error_text = str(error).lower()
                if "out of memory" not in error_text and "outofmemoryerror" not in error_text:
                    raise
                if regenerate_insufficient:
                    raise RuntimeError(
                        f"Image {image_xml.attrs['index']}: full-duration animation "
                        f"({audio_frames / const.FPS:g}s) ran out of memory. "
                        "Generate missing animations requires the full audio duration; "
                        "reduce workflow resolution or memory usage and retry. "
                        f"ComfyUI error: {error}"
                    ) from error

                # The first failed full-duration run follows a half-duration
                # trial; use their midpoint. Later trials bisect the saved
                # successful/failed frame bounds for this image.
                if force:
                    if not lower_bound and attempted_frames == default_animation_frames:
                        lower_bound = max(0, attempted_frames // 2)
                    upper_bound = min(
                        upper_bound or attempted_frames,
                        attempted_frames,
                    )
                    image_xml.attrs["animation_oom_audio_frames"] = str(audio_frames)
                    image_xml.attrs["animation_oom_success_frames"] = str(lower_bound)
                    image_xml.attrs["animation_oom_failure_frames"] = str(upper_bound)
                    self.chapter.save_xml()
                    retry_frames = (lower_bound + upper_bound + 1) // 2
                else:
                    retry_frames = max(1, attempted_frames // 2)
                if retry_frames >= attempted_frames:
                    raise

                log.warning(
                    "Retrying animation after CUDA out-of-memory at binary-search midpoint",
                    image_index=image_xml.attrs["index"],
                    failed_duration_seconds=attempted_duration,
                    lower_bound_duration_seconds=lower_bound / const.FPS,
                    retry_duration_seconds=retry_frames / const.FPS,
                    retry_frames=retry_frames,
                )
                comfy_output_dir = const.COMFY_DIRS["artifactserver"]["OUTPUT_DIR"]
                output_prefix = f"{self.chapter.nice}_img_{image_xml.attrs['index']}"
                for partial_output in glob.glob(
                    os.path.join(comfy_output_dir, output_prefix + "*.mp4")
                ):
                    os.remove(partial_output)

                workflow_fn = build_animation_workflow(retry_frames)
                if not workflow_fn:
                    raise RuntimeError(
                        f"Could not build half-duration animation workflow for image "
                        f"{image_xml.attrs['index']}"
                    ) from error
                attempted_frames = retry_frames
                attempted_duration = attempted_frames / const.FPS
                log.info(
                    "Attempting animation generation after OOM",
                    image_index=image_xml.attrs["index"],
                    frames=attempted_frames,
                    duration_seconds=attempted_duration,
                )
                report(animation_percent, f"Image {image_xml.attrs['index']}: Retrying Animation after out of memory")
                try:
                    comfy.run_workflow_fn(
                        workflow_fn,
                        duration_seconds=attempted_duration,
                    )
                except RuntimeError as retry_error:
                    retry_error_text = str(retry_error).lower()
                    if force and (
                        "out of memory" in retry_error_text
                        or "outofmemoryerror" in retry_error_text
                    ):
                        image_xml.attrs["animation_oom_failure_frames"] = str(retry_frames)
                        self.chapter.save_xml()
                    raise
            log.info(
                "Animation workflow completed; collecting generated videos",
                image_index=image_xml.attrs["index"],
                workflow=workflow_fn,
            )
            if force and image_xml.attrs.get("animation_oom_failure_frames"):
                previous_success = int(
                    image_xml.attrs.get("animation_oom_success_frames", "0") or 0
                )
                image_xml.attrs["animation_oom_audio_frames"] = str(audio_frames)
                image_xml.attrs["animation_oom_success_frames"] = str(
                    max(previous_success, attempted_frames)
                )
                self.chapter.save_xml()

            report(animation_percent, f"Image {image_xml.attrs['index']}: Collecting Animation")
            # find the video file
            # put it in os.path.exists(os.path.join(const.LIBRARY_DIR, animated_src))

            candidates = selector_htmx.gather_candidate_videos(image_xml, self.chapter)
            if candidates:
                log.info(
                    "Using newly generated animation candidate",
                    image_index=image_xml.attrs["index"],
                    candidate=candidates[0],
                )
                shutil.move(candidates[0], animated_path)
                image_xml['animated_src'] = animated_src
                self.chapter.save_xml()
                if regenerate_insufficient:
                    count = animation_frame_count(image_xml, paragraph_dir, const.FPS)
                    if count is None or count < audio_frames:
                        raise RuntimeError(
                            f"Image {image_xml.attrs['index']}: generated animation has "
                            f"{count if count is not None else 'unknown'} frames; "
                            f"the audio requires {audio_frames}. Check the workflow's "
                            "duration and output frame rate."
                        )
            else:
                raise RuntimeError(
                    f"Animation workflow completed for image {image_xml.attrs['index']} "
                    "but produced no matching MP4. Check the ComfyUI output directory "
                    f"{const.COMFY_DIRS['artifactserver']['OUTPUT_DIR']}"
                )

        self.chapter.save_xml()


    def speak(self, chapter: Chapter, phrase_xml:Tag) -> Tag:
        """
        Generate an audio file for the given speaker and IPA pronunciation.
        Returns the filename for the generated audio file.
        """
        wavfile = phrases.get_wavfile_filename(phrase_xml, chapter)

        _, pronunciation_str = phrases.speak(
            chapter=chapter,
            phrase_xml=phrase_xml,
            wavfile=wavfile,
            workdir=os.path.join(
                const.LIBRARY_DIR,
                os.path.dirname(wavfile).lstrip(
                    "/"
                ),
            ),
            delay=float(phrase_xml.attrs.get("delay", 0)),
        )

        phrases.wav_pad_to_frame(wavfile)
        phrase_xml.attrs["src"] = os.path.basename(wavfile)
        if pronunciation_str:
            phrase_xml.attrs["pronunciation"] = pronunciation_str

        duration = phrases.get_wav_duration(wavfile)
        phrase_xml.attrs["duration"] = str(duration)
        phrase_xml.attrs["frames"] = int(duration * const.FPS)

        return phrase_xml

    def get_audio_duration(self, audiofile: str) -> float:
        """
        Get the duration of the given audio file in seconds.
        These are fairly high precision.
        """
        return phrases.get_wav_duration(audiofile)

# def style_name_to_key(name):
#     """
#     Convert a style name to a key suitable for use in a JSON object.
#     """
#     return (
#         name.lower()
#         .replace(" ", "_")
#         .replace("-", "_")
#         .replace("'", "")
#         .replace('"', "")
#     )
def as_id(category: str):
    c = category.strip().lower()
    c = re.sub(r"[^\-a-z0-9_]", "_", c)
    # c = c.replace(" ", "_").replace(".", "_").replace(",", "_").replace(":", "_").replace(";", "_")
    return c


def add_tag_to_style(category_name, style_tag, new_tag):
    """
    Add a tag to a style in the given category.

    what the fuck is this?  this is shit.
    """
    key = as_id(f"{category_name}/{style_tag}")
    r.sadd(f"{key}/tags", new_tag)


def enable_style(style_tag):
    """
    OBSOLETE
    copy the indicated style from styles-all.json to styles.json
    """
    with open(os.path.join(os.path.dirname(__file__), "styles-all.json"), "r") as f:
        all_styles = json.loads(f.read())
        style_to_enable = None
        for s in all_styles:
            if as_id(s["name"]) == style_tag:
                style_to_enable = s
                break
        if not style_to_enable:
            raise ValueError(f"Style {style_tag} not found in styles-all.json")

    with open(os.path.join(os.path.dirname(__file__), "styles.json"), "r") as f:
        current_styles = json.loads(f.read())

    # check if the style is already enabled
    for s in current_styles:
        if as_id(s["name"]) == style_tag:
            return

    current_styles.append(style_to_enable)

    with open(os.path.join(os.path.dirname(__file__), "styles.json"), "w") as f:
        f.write(json.dumps(current_styles, indent=4))


def all_styles():
    """
    All _custom_ styles.  The "styler" app is intended to make
    it easy to find the syles you like and add them to "Custom"
    so you have a short list of favorites to choose from.

    We need to reach into:
        ComfyUI/custom_nodes/ComfyUI_MileHighStyler/data/Custom/custom.json

    Which looks like:
    [{
        "name": "Wizard of Oz",
        "prompt": "Wizard of Oz mode, {prompt}, \u00acA whimsical and fantastical page illustration style used in creating illustrations for the Wizard of Oz. The style uses strong black on paper strokes and simple outlines, with watercolor fill of major characters. The style emphasizes magical wonder characters, enchanted landscapes, and the imaginative world of Oz.",
        "negative_prompt": "Dark Fantasy Art, digital design"
    }
    ...
    ]

    Styles don't come with a good unique, and a list
    of dicts pretty much requires you to walk the
    friggin' list.  But -- the "Custom" list is small.
    No biggie.
    """
    custom_styles_fn = os.path.join(const.STYLES_DIR, "Custom", "custom.json")

    lock = FileLock(custom_styles_fn + ".lock")
    with lock:
        with open(custom_styles_fn, "r") as f:
            custom_styles = json.loads(f.read())

    return custom_styles


def all_favorite_styles():
    """
    All styles in the "Custom" category.  This is what we show in the UI.
    """
    return styles_in_group("Custom", "custom")


def all_loras():
    for fn in os.listdir(const.LORA_DIR):
        # this is what passes for rocket science around here.
        if fn.endswith(".safetensors"):
            if os.path.exists(os.path.join(const.LORA_DIR, fn + ".json")):
                with open(os.path.join(const.LORA_DIR, fn + ".json"), "r") as f:
                    lora_info = json.loads(f.read())
            else:
                lora_info = {"trigger_words": [], "weight": 1.0}

            if not lora_info.get("name"):
                lora_info["name"] = os.path.splitext(fn)[0].title().replace("_", " ")

            lora_info["filename"] = fn
            yield lora_info


def get_style_old(style_name, from_all=False):
    prompt_filter = "{prompt}"
    negative_prompt = ""
    found = False
    with open(
        os.path.join(
            os.path.dirname(__file__),
            "styles.json" if not from_all else "styles-all.json",
        ),
        "r",
    ) as f:
        all_styles = json.loads(f.read())
        for s in all_styles:
            if as_id(s["name"]) == style_name:
                prompt_filter = s["prompt"]
                negative_prompt = s.get("negative_prompt", b"")
                found = True
                break

    if not found:
        raise ValueError(f"Style {style_name} not found ({from_all=})")
    return prompt_filter, negative_prompt


VERSION = 1.37

def check_upgrade(category_name: str | None=None):
    version = r.get("styles_version")
    if version is None:
        log.info("No styles_version found in redis, importing all styles")

    try:
        upgrading = r.get("upgrading")
    except redis.exceptions.ResponseError as e:
        log.warning("Error checking upgrading flag in redis: %s", e)
        upgrading = None

    if upgrading:
        log.info("Styles upgrade in progress, skipping check_upgrade()")
        return

    r.set("upgrading", "1", ex=300)
    # set the flag to indicate upgrade in progress

    if version is None or float(version) < VERSION or category_name is not None:
        log.info("Upgrading styles in redis from version %s to %s", version, VERSION)

        # initial import into redis, or loaded whenever
        # redis is cleared.
        bulk_data = {}

        # wipe it clean
        r.flushdb()

        log.info("Importing styles into redis")

        for directory in os.listdir(const.STYLES_DIR):
            for fn in glob.glob(os.path.join(const.STYLES_DIR, directory, "*.json")):
                log.info("Found style file %s", fn)
                with open(fn, "r") as f:
                    try:
                        styles = json.loads(f.read())
                    except json.JSONDecodeError as e:
                        log.error("Error decoding JSON from %s: %s", fn, e)
                        raise

                    for style in styles:
                        # some reserved words
                        if style['name'] in ['tags', 'categories']:
                            continue

                        key = as_id(style['name'])

                        # add tags
                        dir_tag = os.path.basename(directory).lower()
                        json_tag = os.path.splitext(
                            os.path.basename(fn)
                        )[0].lower()

                        r.sadd(key + "/tags", dir_tag)
                        r.sadd(key + "/tags", json_tag)

                        # add tag indexes
                        r.sadd("tags", dir_tag)
                        r.sadd("tags", json_tag)

                        r.sadd(dir_tag + "/tags", key)
                        r.sadd(json_tag + "/tags", key)

                        # save the style itself
                        try:
                            r.hset(key, mapping={
                                "name": style.get("name", ""),
                                "prompt": style.get("prompt", "{prompt}"),
                                "negative_prompt": style.get("negative_prompt", ""),
                            })
                        except redis.exceptions.ResponseError as e:
                            log.error("Error saving style '%s' to redis: %s", key, e)
                            raise

                        # for tag in style["name"].split("_") + [ group ]:
                        #     r.sadd(key + "/tags", tag)
                        #     r.sadd("tags", tag)  # for reverse lookup of all tags



            # if category["tag"] not in ["era"]:
            #     # log.info('Skipping category "%s"', category["category"])
            #     continue

            #log.info('Scanning category "%s"', category["category"])
            # each json file in the directory is a group
            # for group in set(style_groups_in_category(category["category"])):
            #     log.info(
            #         'Importing styles in group "%s" of category "%s"', group, category
            #     )
            #     ticker = 100
            #     bulk_data = {}
            #     for style in styles_in_group(category["category"], group):
            #         key = as_id(f"{category['category']}/{style['name']}")

            #         #if ticker % 5000 == 0:
            #         if True:
            #             log.info(
            #                 "[%s] Importing style to hset(%s)",
            #                 ticker,
            #                 key,
            #                 style=style,
            #             )

            #         bulk_data[key] = style

            #         # style.setdefault("tags", [])
            #         # for tag in style["name"].split("_") + [ group ]:    
            #         #     if tag not in style["tags"]:
            #         #         style["tags"].append(tag)

            #         # r.hset(key, mapping=style)

            #         r.sadd(key + "/tags", category["category"])
            #         # log.info("category %s", category)
            #         r.sadd(key + "/tags", group)

            #         for tag in style["name"].split("_") + [ group ]:
            #             r.sadd(key + "/tags", tag)
            #             r.sadd("tags", tag)  # for reverse lookup of all tags

            #         ticker += 1

                # massive speedup
                # # log.info("Executing bulk import of %s styles into redis", len(bulk_data))
                # pipe = r.pipeline(transaction=False)
                # for key, style in bulk_data.items():
                #     # for fast reverse lookup
                #     # pipe.rpush(category["category"], key)
                #     # pipe.rpush(group, key)
                #     log.info(
                #         "Saving style",
                #         key=key,
                #         mapping=style
                #     )
                #     pipe.hset(key, mapping=style)

                # pipe.execute()
                # log.info("Bulk import complete")

        # if len(bulk_data) == 0:
        #     log.info("No styles imported")

        r.set("styles_version", VERSION)

    else:
        log.info("Styles version %s found in redis, no import needed", version)

    r.delete("upgrading")  # clear the flag after upgrade is done

class Style:
    def __init__(self, style_tag):
        self.style_tag = style_tag
        self.key = as_id(style_tag)

        self.style: dict[str, str] = {
            "name": style_tag,
            "tag": style_tag,
            "prompt": "{prompt}",
            "negative_prompt": "",
            "tier": "2",
            "chosen": "False",
            "cosmetic": style_tag,
        }
        self.tag_count = 0
        self.load_from_redis()        
        self.load_tags()

    def load_from_redis(self):
        redis_data = r.hgetall(self.key)
        for k in list(redis_data.keys()):
            v = redis_data.pop(k)
            if isinstance(k, bytes):
                k = k.decode("utf-8")
            if isinstance(v, bytes):
                v = v.decode("utf-8")
            redis_data[k] = v
        log.info('Merging in redis data', redis_data=redis_data)

        if not redis_data:
            log.warning("Style %s not found in redis, checking for upgrade", self.key)
            check_upgrade()
            redis_data = r.hgetall(self.key)

            if not redis_data:
                log.error("Style %s still not found in redis after upgrade", self.key)
                raise ValueError(f"Style {self.style_tag} not found.")

        # self.style is a dict, so we're merging
        # with redis writing over.
        self.style.update(redis_data)

    def save_to_redis(self):
        """
        In Redis:

        Hash Key [self.key] to a mapping of the style object with all string values.
        Set Key [self.key + "/tags"] to a set of tags for this style, the tags are strings.
        """
        log.info('Saving style to redis', key=self.key, mapping=self.style)

        # we can't include tags in the hash because
        # it is a list, unsupported unless we serialize.
        # but that is fine, we want them in a set anyway.
        if 'tags' in self.style:
            del self.style['tags']

        #r.delete(self.key + "/tags")
        #r.sadd(self.key + "/tags", *self.tags)
        #r.sadd("tags", *self.tags)
        
        r.hset(self.key, mapping=self.style)

    def load_tags(self) -> list[str]:
        try:
            # get the number of tags for this style from Redis
            self.tag_count = r.scard(self.key + "/tags")
        except redis.exceptions.ResponseError:
            self.tag_count = 0

        self._tags = r.smembers(self.key + "/tags")
        return self._tags
    
    @property
    def tier(self):
        return int(self.style.get("tier", "2"))

    @property
    def name(self):
        return self.style.get("name", self.style_tag)

    @property
    def prompt(self):
        return self.style.get("prompt", "{prompt}")

    @property
    def negative_prompt(self):
        return self.style.get("negative_prompt", "")

    @property
    def tags(self):
        return self.load_tags()

    def remove_tag(self, tag: str):
        """
        Remove a tag from this style.
        """
        if len(self.tags) <= 1:
            log.warning("Cannot remove all tags from style %s", self.style_tag)
            return

        # remove the tag from the style
        r.srem(self.key + "/tags", tag)

        # and remove the style from the tag
        r.srem(tag + "/tags", self.key)
        
        self.load_tags()
    
    @classmethod
    def from_image(cls, chapter, image_xml):
        """
        Construct a Style object from an image XML element, using the chapter's
        style settings.
        """
        style_tag = chapter.get_image_style(image_xml)

        return cls(
            style_tag=style_tag
        )

    def set_tier(self, tier: int):
        self.style["tier"] = tier
        self.save_to_redis()

        # and save the change to the file system.
        if "filename" in self.style and self.style["filename"]:
            fn = os.path.join(
                const.STYLES_DIR, 
                self.category_name, 
                self.style["filename"],
            )
            lock = FileLock(fn + ".lock")
            with lock:
                with open(fn, "r") as h:
                    try:
                        all_styles = json.loads(h.read())
                    except json.JSONDecodeError as e:
                        log.error("Error decoding JSON from %s: %s", fn, e)
                        raise

                for s in all_styles:
                    if "tag" not in s:
                        log.warning("Style in file %s missing 'tag': %s", fn, s)

                    if s.get("tag", "") == self.style_tag:
                        s["tier"] = tier
                        break

                with open(fn, "w") as f:
                    json.dump(all_styles, f, indent=4)

    def as_card(self, tag: str="", oob:bool=True):
        # what shows up on the un-expanded card:
        style_info = f"<p><b>Style:</b> {self.name}</p>"

        _, icon, variant, cosmetic = tier_cosmetics[self.tier]

        badge = f"""
        <wa-badge pill class="tier-badge" variant="{variant}">
            <wa-icon slot="start" name="{icon}"></wa-icon>
            {cosmetic}
        </wa-badge>
        """

        zit_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_zit_sampler.png",
        )

        krea_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_krea2_sampler.png",
        )
        flux_image_url = url_for(
            "styles.show_sample",
            filename=f"{self.style_tag}_flux_sampler.png",
        )
        return f"""
        <div 
            id="card_{self.style_tag}"
            {"hx-swap-oob='true'" if oob else ""}
            class="wa-stack card-overview history-item-{self.style_tag}"
        >
            <div class="wa-cluster">
                <div class="wa-frame:square stylesample">
                    <img
                        loading="lazy"
                        class="image_sample"
                        src="{zit_image_url}"
                        alt="Sample Image"
                    >
                        {badge}
                    </img>
                </div>
                <div class="wa-frame:square stylesample">
                    <img
                        loading="lazy"
                        class="image_sample"
                        src="{krea_image_url}"
                        alt="Sample Image"
                    >                
                        {badge}
                    </img>
                </div>
                <div class="wa-frame:square stylesample">
                    <img
                        loading="lazy"
                        class="image_sample"
                        src="{flux_image_url}"
                        alt="Sample Image"
                    >                
                        {badge}
                    </img>
                </div>
            </div>
            <wa-details
                hx-get="style_details"
                hx-vals='{{"style_tag": "{self.style_tag}", "tag": "{tag}"}}'
                hx-trigger="intersect once"
                hx-target="#style_details_{self.style_tag}"
                hx-on:htmx:after-request="tag_listeners({{style_tag: '{self.style_tag}', tag: '{tag}'}})"
                icon-placement="start">
                <span slot="summary">
                    {style_info}
                </span>
                
                <div id="style_details_{self.style_tag}"></div>
            </wa-details>
        </div>
        """

    def __repr__(self):
        return f"<Style {self.style_tag}>"

    def get_image_path(self, sampler: str):
        """
        Get the path to the sample image for this style and sampler.
        """
        filename = f"{self.style_tag}_{sampler}.png"

        sample_pfn = os.path.join(
            os.path.join(
                const.LIBRARY_DIR, 
                "style_exploration"
            ), 
            filename
        )

        return sample_pfn

class StyleLibrary:
    def __init__(self):
        self.styles: dict[str, Style] = {}

    def get_ranked_tags(self, force=True, **filters):
        check_upgrade()
        none = True
        if not force:
            for element in r.lrange("categories", 0, -1):
                none = False
                if isinstance(element, bytes):
                    element = element.decode("utf-8")
                yield element

        if none:
            ranking = []
            for tag in r.smembers("tags"):
                none = False

                try:
                    tag_member_count = r.scard(tag + "/tags")
                    if tag_member_count == 0:
                        log.warning(
                            "Tag %s has no associated styles, removing from redis", tag
                        )
                        r.srem("tags", tag)

                    ranking.append((tag_member_count, tag))

                except redis.exceptions.ResponseError:
                    if not r.exists(tag + "/tags"):
                        log.warning(
                            "Tag %s has no associated styles, removing from redis", tag
                        )
                        r.srem("tags", tag)

            if none:
                # iterate every hash in redis
                all_tags = set()
                for style_key in r.scan_iter(_type="HASH"):
                    tag = style_key.decode("utf-8")
                    for e in tag.split("_"):
                        all_tags.add(e)
                        r.sadd("tags", e)
                        r.sadd(e + "/tags", tag)
                    
                for tag_bytename in r.smembers("tags"):
                    tag = tag_bytename.decode("utf-8")
                    ranking.append((r.scard(tag + "/tags"), tag))
                    log.info("Counting tags: %s", ranking[-1])

            sorted_ranking = [
                tag for _, tag in sorted(
                    ranking,
                    key=lambda x: x[0],
                    reverse=True
                )
            ]
            log.info(f"{sorted_ranking=}")

            if sorted_ranking:
                r.lpush("categories", *sorted_ranking)

            for tag in sorted_ranking:
                yield tag
            
    def get_style(self, style_tag):
        key = as_id(style_tag)
        if key in self.styles:
            return self.styles[key]
        else:
            style = Style(style_tag)
            self.styles[key] = style
            return style

    def with_tag(self, tag):
        """
        Generator for Style objects that have the given tag.
        """
        check_upgrade()
        log.info('Retrieving set "%s/tags" from redis', tag)

        for style_tag in r.smembers(tag + "/tags"):
            yield Style(style_tag)

    def delete_tag(self, style_tag):
        """
        Delete a tag from the library and redis.
        """
        key = as_id(style_tag)
        log.info("Deleting tag %s", key)

        # remove the tag from all associated styles
        for style_key in r.smembers(key + "/tags"):
            r.srem(style_key + "/tags", key)

        r.srem("tags", key)  # remove the tag from the global tags set

        # delete the tag hash
        r.delete(key)

        # delete the tags set
        r.delete(key + "/tags")



# def get_style(category_name, style_tag):
#     """
#     Get the style dict for the given category and style tag.
#     """
#     log.info("Looking for style '%s' in category '%s'", style_tag, category_name)
#     check_upgrade()

#     key = as_id(f"{category_name}/{style_tag}")
#     log.info('Looking for style in redis with key "%s"', key)

#     style = {
#         "name": style_tag,
#         "tag": style_tag,
#         "category_name": category_name,
#         "group": "",
#         "prompt": "{prompt}",
#         "negative_prompt": "",
#         "tier": "2",
#         "chosen": "False",
#         "cosmetic": style_tag,
#     }
#     redis_data = r.hgetall(key)
#     if redis_data:
#         log.info("Found style %s in redis", key, redis_data=redis_data)
#     else:
#         log.warning("Style %s not found in redis, checking for upgrade", key)
#         check_upgrade()
#         redis_data = r.hgetall(key)
        
#         if not redis_data:
#             log.error("Style %s still not found in redis after upgrade", key)
#             raise ValueError(f"Style {style_tag} not found in category {category_name}")
    
#     for k in list(redis_data.keys()):
#         v = redis_data.pop(k)
#         if isinstance(k, bytes):
#             k = k.decode("utf-8")
#         if isinstance(v, bytes):
#             v = v.decode("utf-8")
#         redis_data[k] = v

#     style.update(redis_data)

#     try:
#         vcard = r.scard(key + "/tags")
#     except redis.exceptions.ResponseError:
#         vcard = 0

#     tags = []
#     if vcard:
#         vrange = r.smembers(key + "/tags")
#         for tag_element in vrange:
#             tag = (
#                 tag_element.decode("utf-8")
#                 if isinstance(tag_element, bytes)
#                 else tag_element
#             )
#             tags.append(tag)
#         style["tags"] = tags

#     if "prompt" not in style:
#         raise ValueError(f"Style {style_tag} import error: missing 'prompt'!")

#     return style

#     # # rpush("gpu_tasks", json.dumps(["llm_str_prompt", prompt, system_prompt, outputfile]))

#     #     if "tags" not in style:
#     #     style["tags"] = []

#     # log.info("Looking for style '%s' in category '%s'", style_tag, category_name)
#     # if style_tag in ["none", ]:
#     #     return (None, {
#     #         "name": "None",
#     #         "prompt": "{prompt}",
#     #         "negative_prompt": "",
#     #         "tag": "none"
#     #     })

#     # category_name = category_name.removeprefix("category_")
#     # for dirname in os.listdir(const.STYLES_DIR):
#     #     if dirname.lower() == category_name.lower():
#     #         category_name = dirname
#     #         break

#     # for style_file in glob.glob(os.path.join(
#     #     const.STYLES_DIR,
#     #     category_name,
#     #     "*.json"
#     # )):

#     #     pfn = os.path.join(
#     #         const.STYLES_DIR,
#     #         category_name,
#     #         style_file
#     #     )

#     #     log.info('Loading styles from %s', pfn)

#     #     lock = FileLock(pfn + ".lock")
#     #     with lock:
#     #         with open(pfn, "r") as f:
#     #             try:
#     #                 styles = json.loads(f.read())
#     #             except json.JSONDecodeError as e:
#     #                 log.error("Error decoding JSON from %s: %s", pfn, e)
#     #                 raise

#     #     change = False
#     #     for style in styles:
#     #         if 'tag' not in style:
#     #             # give tags to any styles that need them.
#     #             style['tag'] = as_id(
#     #                 style.get('cosmetic', style.get('name'))
#     #             )
#     #             change = True

#     #         if style['tag'] == style_tag:
#     #             if change:
#     #                 log.info('Saving updated styles to %s', pfn)
#     #                 lock = FileLock(pfn + ".lock")
#     #                 with lock:
#     #                     with open(pfn, "w") as f:
#     #                         json.dump(styles, f, indent=4)

#     #             return (style_file, style)

#     # return (None, None)


def style_groups_in_category(category_key, force=False):
    log.info('Looking for style groups in category "%s"', category_key)
    r = redis.Redis(host="redis")

    # which groups are in this category?
    # ie: which json style files were in this directory?
    key = as_id(category_key) + "/groups"
    groups = None
    if not force:
        log.info('Gathering members of set "%s" from redis', key)
        groups = r.smembers(key)
        log.info("Found %s groups in category %s", len(groups), key)

    if groups is None or len(groups) == 0:
        log.info("[Cache Miss] Rebuilding whole directory tree to catch %s...", key)
        for category_name in os.listdir(const.STYLES_DIR):
            log.info('Scanning category "%s"', category_name)
            category_dir = os.path.join(const.STYLES_DIR, category_name)
            if not os.path.isdir(category_dir):
                log.warning("Skipping non-directory %s in styles dir", category_dir)
                continue

            for fn in os.listdir(category_dir):
                # log.debug("Considering file %s in category %s", fn, category_name)
                if fn.endswith(".json"):
                    group_name = os.path.splitext(fn)[0]
                    scanned_category_key = as_id(category_name)
                    try:
                        log.info('Adding group "%s" to set "%s/groups" in redis', group_name, scanned_category_key)
                        r.sadd(scanned_category_key + "/groups", group_name)
                    except redis.exceptions.ResponseError as e:
                        log.warning(
                            "Error adding group %s to category %s in redis: %s",
                            group_name,
                            scanned_category_key,
                            e,
                        )
                        r.delete(scanned_category_key + "/groups")
                        r.sadd(scanned_category_key + "/groups", group_name)

        # try again
        groups = r.smembers(key)
        if not groups:
            log.warning("No groups found in category %s", key)
            return []
    else:
        log.info("[Cache Hit] Found %s groups in category %s", len(groups), key)

    return [g.decode("utf-8") if isinstance(g, bytes) else g for g in groups]

    # category_dir = os.path.join(
    #     const.STYLES_DIR,
    #     category,
    # )
    # for fn in os.listdir(category_dir):
    #     if fn.endswith(".json"):
    #         groups.append(os.path.splitext(fn)[0])
    # return groups


def styles_in_group(category_key: str, group: str, **filters):
    min_tier = int(filters.get("min_tier", 0))
    max_tier = int(filters.get("max_tier", 999))

    all_styles = []
    category_dir = None
    
    for dir in os.listdir(const.STYLES_DIR):
        dir_id = as_id(dir)
        if dir_id == category_key or dir == category_key:
            category_dir = dir
            break

    if category_dir is None:
        log.error("Category '%s' not found in styles dir", category_key)
        return []

    pfn = os.path.join(
        const.STYLES_DIR,
        category_dir,
        group + ".json",
    )

    try:
        with open(pfn, "r") as f:
            styles = json.loads(f.read())
            for s in styles:
                s["filename"] = group + ".json"
                s["category"] = category_dir
                if (
                    min_tier <= int(s.get("tier", "2")) <= max_tier
                ):  # default tier is 2 ('C' rank)
                    all_styles.append(s)
                else:
                    log.info(
                        "Filtering out style %s due to tier %s not in [%s, %s]",
                        s["name"],
                        s.get("tier", "2"),
                        min_tier,
                        max_tier,
                    )

                # one time type change
                if s.get("chosen", "False") is True:
                    s["chosen"] = "True"
                elif s.get("chosen", "False") is False:
                    s["chosen"] = "False"
    except FileNotFoundError:
        log.warning("Style group file not found: %s", pfn)
        # check_upgrade()
        raise

    return all_styles


category_index = None

tier_cosmetics = [
    (0, "face-sad-cry", "danger", "F"),
    (1, "face-frown", "danger", "D"),
    (2, "face-meh", "warning", "C"),
    (3, "face-smile", "warning", "B"),
    (4, "face-smile-beam", "success", "A"),
    (5, "face-grin", "success", "S"),
    (6, "face-grin-squint", "brand", "SS"),
    (7, "face-grin-stars", "brand", "SSS"),
]


def set_style_tier(category_name, style_tag, tier):
    """
    Obsolete, use Style.set_tier() instead.
    """
    style_file, style = get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["tier"] = tier
    fn = os.path.join(const.STYLES_DIR, category_name, style_file)

    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise

        for style in all_styles:
            if style["tag"] == style_tag:
                style["tier"] = tier
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    log.info("Set style %s in category %s to tier %s", style_tag, category_name, tier)


def id_to_category(style_tag: str):
    log.warning("Obsolete - this is not efficient.")

    r = redis.Redis(host="redis")
    style = None

    wildkey = f"*/{as_id(style_tag)}"
    log.info("Scanning redis for %s", wildkey)
    _, matches = r.scan(match=wildkey, count=1)
    log.info("matches=%s", matches)
    for m in matches:
        style = r.hgetall(m)

    if style:
        log.info("Found key %s", m, style=style)
        return style.get("category_name", None)
    else:
        log.error("No redis entry found", search=f"*/{style_tag}")
        check_upgrade(r)
        return None

    # global category_index
    # tag = as_id(tag)
    # if category_index is None or tag not in category_index:
    #     category_index = {}

    #     for category in all_categories():
    #         if category["tag"] == tag:
    #             category_index[category["tag"]] = category['category']
    #         else:
    #             log.warning("Category tag mismatch: %s != %s", category["tag"], tag)

    # if tag in category_index:
    #     return category_index[tag]

    # log.error("Category tag not found: %s", tag)
    # return None


def all_categories():
    categories = []
    for fn in os.listdir(
        os.path.join(
            const.STYLES_DIR,
        )
    ):
        if os.path.isdir(
            os.path.join(
                const.STYLES_DIR,
                fn,
            )
        ):
            categories.append(
                {
                    "tag": as_id(fn),
                    "category": fn,
                    "cosmetic": fn.title().replace("_", " "),
                }
            )
    return categories


# styles.mark_style_as_chosen(style_tag, category)
def mark_style_as_chosen(category_name, style_tag, chosen=True):
    style_file, style = get_style(category_name, style_tag)
    if not style:
        log.error("Style %s not found in category %s", style_tag, category_name)
        return

    style["chosen"] = "True" if chosen else "False"
    fn = os.path.join(const.STYLES_DIR, category_name, style_file)

    lock = FileLock(fn + ".lock")
    with lock:
        with open(fn, "r") as h:
            try:
                all_styles = json.loads(h.read())
            except json.JSONDecodeError as e:
                log.error("Error decoding JSON from %s: %s", fn, e)
                raise

        for s in all_styles:
            if s["tag"] == style_tag:
                s["chosen"] = "True" if chosen else "False"
                break

        with open(fn, "w") as f:
            json.dump(all_styles, f, indent=4)

    # and copy it into the "Custom" category if chosen, or remove it if not chosen
    custom_category = "Custom"
    if chosen:
        # copy the style into the Custom category
        custom_fn = os.path.join(const.STYLES_DIR, custom_category, "custom.json")
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if not os.path.exists(custom_fn):
                with open(custom_fn, "w") as f:
                    json.dump([style], f, indent=4)
            else:
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # check if the style is already in the custom styles
                if not any(s["tag"] == style_tag for s in custom_styles):
                    custom_styles.append(style)
                    with open(custom_fn, "w") as f:
                        json.dump(custom_styles, f, indent=4)

    else:
        # remove this style from the Custom category
        custom_fn = os.path.join(const.STYLES_DIR, custom_category, "custom.json")
        lock = FileLock(custom_fn + ".lock")
        with lock:
            if os.path.exists(custom_fn):
                with open(custom_fn, "r") as f:
                    custom_styles = json.loads(f.read())

                # filter out the selected style
                custom_styles = [s for s in custom_styles if s["tag"] != style_tag]

                with open(custom_fn, "w") as f:
                    json.dump(custom_styles, f, indent=4)

    log.info(
        "Marked style %s in category %s as chosen=%s", style_tag, category_name, chosen
    )

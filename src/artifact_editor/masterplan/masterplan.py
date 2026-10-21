import json
import math
import os
import decimal

from bs4 import Tag

from artifact_editor.tools import (
    as_int,
    as_str,
    as_float,
)
import logger
import const
from artifact_editor.tools import (
    tags_to_dict,
)
from artifact_editor.phrases import phrases

from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from artifact_editor.chapter.chapter import Chapter


log = logger.log(__name__)


class ImagePlan:
    def __init__(
        self, 
        index: int, 
        start_frame: int = 0, 
        frames: int = 0,
        image: str = '',
        paragraph_dir: str = '',
        clip_prompt: str = '',
        prompt: str = '',
        fullscreen: bool = True,
        tags: dict[str, bool] | None = None,
        mode: str = "scale", 
        **extra,
    ):
        self.index = index
        self.start_frame = start_frame
        self.frames = frames
        self.end_frame = start_frame + frames - 1
        self.image = image
        self.paragraph_dir = paragraph_dir
        self.clip_prompt = clip_prompt
        self.prompt = prompt
        self.fullscreen = fullscreen
        if tags is None:
            tags = {
                "has-text": False,
                "spoken-only": True
            }
        self.tags = tags
        self.mode = mode
        self.extra = extra

    def as_dict(self) -> dict[str, str|int|bool|None|dict[str, bool]]:
        return {
            "index": self.index,
            "start_frame": self.start_frame,
            "frames": self.frames,
            "end_frame": self.end_frame,
            "image": self.image,
            "paragraph_dir": self.paragraph_dir,
            "clip_prompt": self.clip_prompt,
            "prompt": self.prompt,
            "fullscreen": self.fullscreen,
            "tags": self.tags,
            "mode": self.mode,
            **self.extra,
        }

class WordPlan:
    """
        {
            "start_frame": 0,
            "frames": 54,
            "end_frame": 53,
            "text": "The Story of Mel\n\nby Ed Nather",
            "paragraph_tags": {
                "has-text": false,
                "spoken-only": true
            },
            "paragraph_dir": "Ed Nather/The Story of Mel/chapter/0001/paragraphs/000000",
            "paragraph_index": "0",
            "fullscreen": true,
            "index": 0,
            "id": null,
            "speaker": "narrator",
            "src": "ph_0_The_Story_of_Mel_by_Ed_f8793930_c3f0.wav"
        },    
    """
    def __init__(
        self,
        start_frame: int = 0,
        frames: int = 0,
        end_frame: int = 0,
        text: str = '',
        paragraph_tags: dict[str, bool] | None = None,
        paragraph_dir: str = '',
        paragraph_index: str = '',
        fullscreen: bool = True,
        index: int = 0,
        id: str | None = None,
        speaker: str = '',
        src: str = '',
        **extra,
    ):
        self.start_frame = start_frame
        self.frames = frames
        self.end_frame = end_frame
        self.text = text
        if paragraph_tags is None:
            paragraph_tags = {
                "has-text": False,
                "spoken-only": True
            }
        self.paragraph_tags = paragraph_tags
        self.paragraph_dir = paragraph_dir
        self.paragraph_index = paragraph_index
        self.fullscreen = fullscreen
        self.index = index
        self.id = id
        self.speaker = speaker
        self.src = src
        self.extra = extra

    def as_dict(self) -> dict[str, str|int|bool|None|dict[str, bool]]:
        return {
            "start_frame": self.start_frame,
            "frames": self.frames,
            "end_frame": self.end_frame,
            "text": self.text,
            "paragraph_tags": self.paragraph_tags,
            "paragraph_dir": self.paragraph_dir,
            "paragraph_index": self.paragraph_index,
            "fullscreen": self.fullscreen,
            "index": self.index,
            "id": self.id,
            "speaker": self.speaker,
            "src": self.src,
            **self.extra,
        }

class Plan:
    images: list[ImagePlan]
    words: list[WordPlan]

    def __init__(self):
        self.words: list[WordPlan] = []
        self.images: list[ImagePlan] = []

    def is_empty(self) -> bool:
        return not self.words and not self.images

class Masterplan:
    def __init__(self, chapter: Chapter):
        self.chapter = chapter
        self.plan = Plan()

        self.filename = os.path.join(
            const.LIBRARY_DIR,
            self.chapter.chapterdir,
            "masterplan.json"
        )

    def delete(self) -> None:
        if os.path.exists(self.filename):
            os.unlink(self.filename)       

    def load(self):
        self.plan = Plan()

        if os.path.exists(self.filename):
            try:
                with open(self.filename, "r") as h:
                    plan_data = json.load(h)
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                log.warning(
                    "Masterplan file is unreadable; treating it as empty",
                    filename=self.filename,
                    error=str(e),
                )
                return self.plan

            if not isinstance(plan_data, dict):
                log.warning(
                    "Masterplan file has an invalid structure; treating it as empty",
                    filename=self.filename,
                )
                return self.plan

            try:
                self.plan.words = [WordPlan(**word) for word in plan_data.get("words", [])]
                self.plan.images = [ImagePlan(**img) for img in plan_data.get("images", [])]
            except (TypeError, ValueError) as e:
                log.warning(
                    "Masterplan entries are invalid; treating it as empty",
                    filename=self.filename,
                    error=str(e),
                )
                self.plan = Plan()

        return self.plan

    def get(self, force:bool=False) -> Plan:
        self.plan = self.load()

        if force or self.plan.is_empty():
            self.plan = generate_masterplan(self.chapter, force=force)
            self.save()

        return self.plan

    def save(self):        
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)
        with open(self.filename, "w") as h:
            json.dump({
                "words": [word.as_dict() for word in self.plan.words],
                "images": [img.as_dict() for img in self.plan.images]
            }, h, indent=4)
    

def generate_masterplan(chapter: Chapter, force: bool=False) -> Plan:
    """
    no pressure.  we're generating video as a two step process.
    The first step is to turn everything we know about this book 
    into this JSON intermedatetary format.

    every image in xml needs to have a 'duration' for the number of frames
    it should persist.  The audio stage is responsible for this.

{
    words: [{
        start_frame: 0,
        duration: 130,
        text: "The first step is to create",
        paragraph_dir: "",
        text_pixelheight: 300,
        text_pixelrate: 1.0,
        speaker: "narrator"
    }, ... ]
    images: [{
        start_frame: 0,
        end_frame: 430,
        image: "https://example.com/image1.jpg",
        mode: "fit-to-width"
    }, ... ]
}

    Our output is one mp4 file with audio per chapter, suitable for being glued together.
    """
    words = []
    images = []
    image_index = 0
    
    masterplan = Masterplan(chapter)

    if force or masterplan.load().is_empty():
        log.info("Generating new masterplan for %s", chapter)
        
        frame_index = 0
        image_frame_index = 0
        frames = 0
        last_image = None
        # make sure the book.xml accumulated data (like durations) are properly calculated.
        for paragraph in chapter.paragraphs():
            # iterate contents once to measure image durations
            log.debug('Assigning frame durations to images in paragraph: %s', str(paragraph))
            for fragment in paragraph.contents:
                if isinstance(fragment, Tag):
                        
                    if fragment.name == "silence":
                        silence_frames = round(as_float(fragment.attrs.get("duration", 0)) * const.FPS)
                        fragment.attrs["frames"] = str(silence_frames)
                        frames += silence_frames

                    elif fragment.name == "phrase":
                        # accumulate phrase durations
                        try:
                            phrase_frames = as_int(fragment.attrs.get("frames", "0"))
                        except ValueError:
                            phrase_frames = 0

                        duration = as_float(fragment.attrs.get("duration", 0))

                        if 'src' in fragment.attrs and (phrase_frames == 0 or duration == 0):
                            # okay.. no problem.  this is based on the audio duration.
                            audio_filename = os.path.join(
                                const.LIBRARY_DIR,
                                chapter.get_paragraph_dir(as_int(paragraph.attrs.get("index"))),
                                as_str(fragment.attrs.get("src", ""))
                            )
                            duration = 0
                            if os.path.exists(audio_filename):
                                duration = phrases.get_wav_duration(audio_filename)

                            # the audio should be padded to fall on precise frame intervals
                            # but duration _is_ a float.  maybe it shouldn't be a float?
                            # lets have get_wav_duration return a decimal.  Clear up a lot
                            # of sources of slop.
                            phrase_frames = duration * const.FPS
                            
                            fragment.attrs["duration"] = str(float(duration))
                            fragment.attrs["frames"] = str(int(phrase_frames))
                                                    
                        log.debug('Accumulating frames=%s from phrase: %s', fragment.attrs.get("frames", 0), str(fragment))
                        frames += int(phrase_frames)

                    elif fragment.name in ["image", ]:
                        if last_image is not None:
                            log.debug('Assigning frames=%d to image: %s', frames, str(last_image))
                            last_image.attrs["frames"] = str(frames)
                        
                        last_image = fragment
                        frames = 0
            
        # there is a trailing image that needs to be capped off.
        if last_image is not None:
            log.debug('Assigning frames=%d to image: %s', frames, str(last_image))
            last_image.attrs["frames"] = str(frames)

        phrase_index = 0

        # second iteration, we can get down to business building the masterplan
        for paragraph in chapter.paragraphs():
        
            for fragment in paragraph.contents:
                if isinstance(fragment, Tag):

                    if fragment.name == "image":
                        # we're trusting the 'frames' attribute of the image
                        # to tell us how long it should persist.
                        src = as_str(fragment.attrs.get("src"))
                        
                        # _image_ frames, sure.
                        frame_duration = as_int(fragment.attrs.get("frames", "0"))
                        if frame_duration == 0:
                            # if there is one, there is probably more than one.
                            log.error("Image Framing Failed. Missing or invalid 'frames' attribute: %s", str(fragment))
                            frame_duration = 0
                        
                        # # cap off the previous image
                        # if frame_index > 0:
                        #     images[-1]['end_frame'] = frame_index - 1
                        if frame_duration > 0:

                            tags = tags_to_dict(paragraph.attrs.get('tags'))

                            image_config = {
                                # Keep the chapter XML image index. `image_index`
                                # counts only images with a valid duration, so it
                                # can drift when an earlier image is skipped.
                                "index": as_int(fragment.attrs.get("index", image_index)),
                                "start_frame": image_frame_index,
                                "frames": frame_duration,
                                "end_frame": (image_frame_index + frame_duration) - 1,
                                "image": src,
                                "paragraph_dir": chapter.get_paragraph_dir(
                                    as_int(paragraph.attrs.get("index"))
                                ),
                                "clip_prompt": as_str(fragment.attrs.get("clip_prompt", "")),
                                "prompt": as_str(fragment.attrs.get("prompt", "")),
                                "fullscreen": as_str(fragment.attrs.get("fullscreen", "false")).lower() == "true" or not tags.get('has-text', True),
                                "tags": tags,
                                "mode": "scale"
                            }

                            if 'recenter_x1' in fragment.attrs:
                                image_config['recenter'] = {
                                    "x1": as_int(fragment.attrs.get("recenter_x1", 0)),
                                    "y1": as_int(fragment.attrs.get("recenter_y1", 0))
                                }

                            if as_str(fragment.attrs.get("transition_type", "")):
                                transition_config = {}
                                for potential in fragment.attrs:
                                    if potential.startswith("transition_"):
                                        log.debug(f"Found transition config: {potential}={fragment.attrs[potential]}")

                                        key = potential.replace("transition_", "")
                                        if key:
                                            transition_config[str(key)] = as_str(fragment.attrs[potential])
                                
                                transition_config["type"] = as_str(fragment.attrs.get("transition_type", "cut"))
                                image_config["transition"] = transition_config

                            images.append(ImagePlan(**image_config))
                            image_index += 1
                        else:
                            log.warning(f"Invalid frame duration: {frame_duration}.  Image {src} will be skipped.")

                        image_frame_index += frame_duration

                    elif fragment.name == "silence":
                        # Audio assembly emits these pauses too. Keep the last
                        # highlight visible and advance the next phrase's start.
                        frame_duration = as_int(fragment.attrs.get("frames", 0))
                        frame_index += frame_duration
                        if words:
                            words[-1].frames += frame_duration
                            words[-1].end_frame += frame_duration

                    elif fragment.name == "phrase":
                        spoken_text = as_str(fragment.get_text())
                        frame_duration = as_int(fragment.attrs.get("frames", 0))
                        
                        # getting pixelheight here is expensive. how about.. its
                        # optional.  when set we obey it.  When _not_ set, we
                        # increment the previous pixelsheight by the most recent
                        # value of pixelrate and use that.

                        # text_pixelheight = int(), distance in pixels
                        # from the top of the text image to the horizontal line
                        # that should be in the exact center of the screen on
                        # the first frame when this text is spoken.

                        tags = tags_to_dict(paragraph.attrs.get('tags'))
                        our_words = {
                            # Cover any leading silence with the first phrase.
                            "start_frame": frame_index if words else 0,
                            "frames": frame_duration if words else frame_index + frame_duration,
                            "end_frame": (frame_index + frame_duration) - 1,
                            "text": spoken_text.strip(),
                            "paragraph_tags": tags,
                            "paragraph_dir": chapter.get_paragraph_dir(as_int(paragraph.attrs.get("index"))),
                            "paragraph_index": as_int(paragraph.attrs.get("index")),
                            "fullscreen": as_str(
                                fragment.attrs.get("fullscreen", "false")
                            ).lower() == "true" or not tags.get('has-text', True),
                            "index": as_int(fragment.attrs.get("index", phrase_index)),
                            # Phrase IDs are often composite strings (for
                            # example "35_1"), and older XML may omit them.
                            "id": as_str(
                                fragment.attrs.get("id")
                                or fragment.attrs.get("index", phrase_index)
                            ),
                            "speaker": "narrator",
                            "src": as_str(fragment.attrs.get("src")),
                        }

                        if "pixelheight" in fragment.attrs:
                            our_words['text_pixelheight'] = as_float(fragment.attrs.get('pixelheight'))

                        if "pixelrate" in fragment.attrs:
                            our_words['text_pixelrate'] = as_float(fragment.attrs.get('pixelrate'))
                                                            
                        words.append(WordPlan(**our_words))
                        frame_index += frame_duration 
                        phrase_index += 1

        chapter.save_xml()

        masterplan.plan = Plan()
        masterplan.plan.words = words
        masterplan.plan.images = images
        masterplan.save()
    
    return masterplan.plan


def frame_to_image(masterplan: Masterplan | Plan, frame_index: int) -> ImagePlan | None:

    plan = masterplan.plan if isinstance(masterplan, Masterplan) else masterplan

    for image in plan.images:
        if image.start_frame <= frame_index <= image.end_frame:
            log.debug('image: %s', image)
            # use a camera effect frame?
            frame_image = os.path.join(
                const.LIBRARY_DIR,
                image.paragraph_dir,
                "image_frames",
                f"image_{int(image.index):06d}",
                f"frame_{frame_index - image.start_frame:06d}.png",
            )

            if os.path.exists(frame_image):
                log.debug('Using frame image at %s', frame_image)
                image.image = os.path.join(
                    "image_frames",
                    f"image_{int(image.index):06d}",
                    os.path.basename(frame_image)
                )
            else:
                log.debug('No frame image found at %s', frame_image)

            return image

        if image.start_frame > frame_index:
            log.warning("masterplan images are non-contiguous: %s", frame_index)
            # images are ordered by start_frame, so if we hit one that starts after
            # the frame we're looking for, we can stop.
            return image

    # The final image persists through the end of the chapter timeline even
    # when its recorded duration ends before the final phrase does.
    return plan.images[-1] if plan.images else None


def frame_to_word(masterplan: Masterplan, frame_index: int) -> WordPlan| None:

    for word in masterplan.plan.words:
        if word.start_frame <= frame_index <= word.end_frame:
            return word

        if word.start_frame > frame_index:
            log.warning("masterplan words are non-contiguous: %s", frame_index)
            # words are ordered by start_frame, so if we hit one that starts after
            # the frame we're looking for, we can stop.
            return word

    return None



def from_frame(masterplan: Masterplan, frame_index:int) -> tuple[WordPlan| None, ImagePlan| None]:
    image_dict = frame_to_image(masterplan, frame_index)
    word_dict = frame_to_word(masterplan, frame_index)
    return word_dict, image_dict

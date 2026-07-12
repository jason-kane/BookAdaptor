import json
import math
import os
import decimal

from artifact_editor import tools

import logger
import const
from artifact_editor.tools import (
    tags_to_dict,
)
from artifact_editor.audio import audio

log = logger.log(__name__)

def get_masterplan_fn(chapter):
    return os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "masterplan.json"
    )
 
def delete_masterplan(chapter):
    masterplan_fn = get_masterplan_fn(chapter)

    if os.path.exists(masterplan_fn):
        os.unlink( masterplan_fn)

    return


def get_masterplan(chapter, force=False):
    if force:
        log.info("Generating new masterplan for %s", chapter)
        masterplan = generate_masterplan(chapter)
        save_masterplan(chapter, masterplan)

    else:
        masterplan_fn = get_masterplan_fn(chapter)
        
        try:
            with open(masterplan_fn, "r") as h:
                masterplan = json.load(h)
        except FileNotFoundError:
            log.warning("Master plan file not found: %s", masterplan_fn)
            masterplan = None
    
    if masterplan is None:
        log.info('Master plan file not found or invalid.')
    
    return masterplan


def save_masterplan(chapter, masterplan):
    masterplan_fn = get_masterplan_fn(chapter)
    
    log.info("Saving master plan to %s", masterplan_fn)
    os.makedirs(os.path.dirname(masterplan_fn), exist_ok=True)
    with open(masterplan_fn, "w") as h:
        json.dump(masterplan, h, indent=4)
    return masterplan


def generate_masterplan(chapter):
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
    
    masterplan = get_masterplan(chapter)

    if masterplan is None:
        log.info("Generating new masterplan for %s", chapter)
        
        frame_index = 0
        image_frame_index = 0
        frames = 0
        last_image = None
        # make sure the book.xml accumulated data (like durations) are properly calculated.
        for paragraph in chapter.get_xml().findAll("paragraph"):
            # iterate contents once to measure image durations
            log.debug('Assigning frame durations to images in paragraph: %s', str(paragraph))
            for fragment in paragraph.contents:
                if fragment.name == "phrase":
                    # accumulate phrase durations
                    try:
                        phrase_frames = int(fragment.attrs.get("frames", "0"))
                    except ValueError:
                        phrase_frames = 0

                    duration = decimal.Decimal(fragment.attrs.get("duration", 0))

                    if 'src' in fragment.attrs and (phrase_frames == 0 or duration == 0):
                        # okay.. no problem.  this is based on the audio duration.
                        audio_filename = os.path.join(
                            const.LIBRARY_DIR,
                            chapter.get_paragraph_dir(paragraph.attrs.get("index")),
                            fragment.attrs.get("src", "")
                        )
                        duration = 0
                        if os.path.exists(audio_filename):
                            duration = audio.get_wav_duration(audio_filename)

                        # the audio should be padded to fall on precise frame intervals
                        # but duration _is_ a float.  maybe it shouldn't be a float?
                        # lets have get_wav_duration return a decimal.  Clear up a lot
                        # of sources of slop.
                        phrase_frames = duration * const.FPS
                        
                        fragment.attrs["duration"] = duration
                        fragment.attrs["frames"] = int(phrase_frames)
                                                
                    log.debug('Accumulating frames=%s from phrase: %s', fragment.attrs.get("frames", 0), str(fragment))
                    frames += int(phrase_frames)

                elif fragment.name in ["image", ]:
                    if last_image is not None:
                        log.debug('Assigning frames=%d to image: %s', frames, str(last_image))
                        last_image.attrs["frames"] = frames
                    
                    last_image = fragment
                    frames = 0
        
        # there is a trailing image that needs to be capped off.
        if last_image is not None:
            log.debug('Assigning frames=%d to image: %s', frames, str(last_image))
            last_image.attrs["frames"] = frames

        phrase_index = 0

        # second iteration, we can get down to business building the masterplan
        for paragraph in chapter.get_xml().findAll("paragraph"):
        
            for fragment in paragraph.contents:
                if fragment is None:
                    continue

                if fragment.name == "image":
                    # we're trusting the 'frames' attribute of the image
                    # to tell us how long it should persist.
                    src = fragment.attrs.get("src")
                    
                    # _image_ frames, sure.
                    frame_duration = int(fragment.attrs.get("frames", "0"))
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
                            "index": image_index,
                            "start_frame": image_frame_index,
                            "frames": frame_duration,
                            "end_frame": (image_frame_index + frame_duration) - 1,
                            "image": src,
                            "paragraph_dir": chapter.get_paragraph_dir(paragraph.attrs.get("index")),
                            "clip_prompt": fragment.attrs.get("clip_prompt"),
                            "prompt": fragment.attrs.get("prompt"),
                            "fullscreen": fragment.attrs.get("fullscreen", "false").lower() == "true" or not tags.get('has-text', True),
                            "tags": tags,
                            "mode": "scale"
                        }

                        if 'recenter_x1' in fragment.attrs:
                            image_config['recenter'] = {
                                "x1": int(fragment.attrs.get("recenter_x1", 0)),
                                "y1": int(fragment.attrs.get("recenter_y1", 0))
                            }

                        if fragment.attrs.get("transition_type", ""):
                            transition_config = {}
                            for potential in fragment.attrs:
                                if potential.startswith("transition_"):
                                    log.debug(f"Found transition config: {potential}={fragment.attrs[potential]}")

                                    key = potential.replace("transition_", "")
                                    if key:
                                        transition_config[str(key)] = str(fragment.attrs[potential])
                            
                            transition_config["type"] = fragment.attrs.get("transition_type", "cut"),
                            image_config["transition"] = transition_config

                        images.append(image_config)
                        image_index += 1
                    else:
                        log.warning(f"Invalid frame duration: {frame_duration}.  Image {src} will be skipped.")

                    image_frame_index += frame_duration

                elif fragment.name == "phrase":
                    spoken_text = fragment.get_text()
                    frame_duration = int(fragment.attrs.get("frames", 0))
                    
                    # getting pixelheight here is expensive. how about.. its
                    # optional.  when set we obey it.  When _not_ set, we
                    # increment the previous pixelsheight by the most recent
                    # value of pixelrate and use that.

                    # text_pixelheight = int(), distance in pixels
                    # from the top of the text image to the horizontal line
                    # that should be in the exact center of the screen on
                    # the first frame when this text is spoken.

                    # text_pixelrate = float()
                    tags = tags_to_dict(paragraph.attrs.get('tags'))
                    our_words = {
                        "start_frame": frame_index,
                        "frames": frame_duration,  
                        "end_frame": (frame_index + frame_duration) - 1,
                        "text": spoken_text.strip(),
                        "paragraph_tags": tags,
                        "paragraph_dir": chapter.get_paragraph_dir(paragraph.attrs.get("index")),
                        "paragraph_index": paragraph.attrs.get("index"),
                        "fullscreen": fragment.attrs.get("fullscreen", "false").lower() == "true" or not tags.get('has-text', True),
                        "index": int(fragment.attrs.get("index", phrase_index)),
                        "id": fragment.attrs.get("id"),
                        "speaker": "narrator",
                        "src": fragment.attrs.get("src"),
                    }

                    if "pixelheight" in fragment.attrs:
                        our_words['text_pixelheight'] = float(fragment.attrs.get('pixelheight'))

                    if "pixelrate" in fragment.attrs:
                        our_words['text_pixelrate'] = float(fragment.attrs.get('pixelrate'))
                                                        
                    words.append(our_words)                   
                    frame_index += frame_duration 
                    phrase_index += 1

        chapter.save_xml()

        masterplan = {
            "words": words,
            "images": images
        }

        save_masterplan(chapter, masterplan)
    
    return masterplan                            


def frame_to_image(masterplan, frame_index):

    for image in masterplan["images"]:
        if 'image_pfn' not in image:
            image['image_pfn'] = os.path.join(
                const.LIBRARY_DIR,
                image['paragraph_dir'],
                image['image']
            )
        if image["start_frame"] <= frame_index <= image["end_frame"]:
            log.debug('image: %s', image)
            # use a camera effect frame?
            frame_image = os.path.join(
                const.LIBRARY_DIR,
                image['paragraph_dir'],
                "image_frames",
                f"image_{int(image['index']):06d}",
                f"frame_{frame_index - image['start_frame']:06d}.png",
            )

            if os.path.exists(frame_image):
                log.debug('Using frame image at %s', frame_image)
                image['image'] = os.path.join(
                    "image_frames",
                    f"image_{int(image['index']):06d}",
                    os.path.basename(frame_image)
                )
            else:
                log.debug('No frame image found at %s', frame_image)

            return image

        if image["start_frame"] > frame_index:
            log.warning("masterplan images are non-contiguous: %s", frame_index)
            # images are ordered by start_frame, so if we hit one that starts after
            # the frame we're looking for, we can stop.
            return image

    return None


def frame_to_word(masterplan, frame_index):

    for word in masterplan["words"]:
        if word["start_frame"] <= frame_index <= word["end_frame"]:
            return word

        if word["start_frame"] > frame_index:
            log.warning("masterplan words are non-contiguous: %s", frame_index)
            # words are ordered by start_frame, so if we hit one that starts after
            # the frame we're looking for, we can stop.
            return word

    return None



def from_frame(masterplan, frame_index:int) -> (dict, dict):
    image_dict = frame_to_image(masterplan, frame_index)
    word_dict = frame_to_word(masterplan, frame_index)
    return word_dict, image_dict
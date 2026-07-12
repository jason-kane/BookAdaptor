
import fcntl
import functools
from glob import glob
import itertools
import math
import os
import shutil
import traceback

import fnv_hash_fast
import redis
from PIL import Image

from artifact_editor.chapter.chapter import Chapter
import const
import logger
from artifact_editor import (
    images,
    tools,
    typography,
    video,
)
from text_to_image.registry import registry as t2i_registry

log = logger.log(__name__)


def wrap_with_full_traceback(func):
    """
    A decorator that wraps a function to capture and return the full traceback
    from a worker process.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            # Format the exception and traceback as a string
            tb_str = traceback.format_exc()
            # Raise a new exception in the parent with the full traceback
            raise Exception(f"Original exception in child process:\n{tb_str}") from e
    return wrapper


def ImageSafetyDanceOpen(image_pfn):
    """
    Open an image file with safety checks and logging.

    Bad images are removed.
    """
    imaginative_image = None

    with open(image_pfn + ".lock", "w") as lock_file:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX)

            imaginative_image = Image.open(image_pfn)
            imaginative_image.verify()
            
            imaginative_image = Image.open(image_pfn)
            imaginative_image.load()

        except OSError:
            log.error(
                "Failed to open imaginative image %s for frame",
                image_pfn,
            )
            os.unlink(image_pfn)
            return None
                    
        except SyntaxError as err:
            log.error(err)
            if os.path.exists(image_pfn):
                os.unlink(image_pfn)
            return None

        except AttributeError as err:
            log.error(err)
            if os.path.exists(image_pfn):
                os.unlink(image_pfn)
            return None

        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
    
    return imaginative_image


@wrap_with_full_traceback
def draw_frame(
    chapterdir,
    aspect,
    frame_fn,
    scroll_lock,
    first_frame_index,
    frame_index,
    max_frame_index,
    image_dict,
    previous_image_dict,
    animate_lock,
    paragraph_index,
    phrase_id,
    force=False,
):
    """
    Oh what a ridiculous clusterfuck this is.

    chapterdir: filesystem path to the chapter this frame belongs to
    aspect: "widescreen" or "portrait"
    frame_fn: filename to save this frame to, including path
    scroll_lock: the scroll position of the text for this frame
    first_frame_index: the index of the first frame in the sequence
    frame_index: the index of the current frame
    max_frame_index: the index of the last frame in the sequence, used to time panning effects
    image_dict: dictionary containing image metadata
    previous_image_dict: dictionary containing metadata of the previous image, because... feature creep?
    animate_lock: lock for synchronizing animation
    paragraph_index: index of the paragraph in the chapter
    phrase_id: identifier for the phrase within the paragraph
    force: boolean flag to force redraw of the frame, this should be a deep enough poke that it is serious.
    
    The reason for all this var-spew, is that we've probably just gotten a hand-off to our own
    process.  
    """

    log.debug(f"{chapterdir=}")
    log.debug(f"{aspect=}")
    log.debug(f"{frame_fn=}")
    log.info(f"{scroll_lock=}")
    log.debug(f"{first_frame_index=}")
    log.debug(f"{frame_index=}")
    log.debug(f"{max_frame_index=}")
    # log.info(f"{image_dict=}")
    log.debug(f"{previous_image_dict=}")
    log.debug(f"{animate_lock=}")
    log.debug(f"{paragraph_index=}")
    log.debug(f"{phrase_id=}")
    log.debug(f"{force=}")
    
    # real chapter object please and thank you.
    chapter = Chapter.from_chapterdir(chapterdir)

    # Failure is not an option.
    success = False
    paragraphdir = image_dict["paragraph_dir"]

    # everything needs to self correct so the next pass works.
    G = const.GEOMETRY[aspect]

    failcount = 0
    while success is False and failcount < 6:
        failcount += 1
        try:
            log.info(f'[{frame_index:08}] Top of frame persistence loop (attempt {failcount})')
            #chapter = typography.page_segment.Chapter(chapterdir, aspect=aspect)

            # image_dict is a grab-bag of metadata
            image_pfn = image_dict["image_pfn"]

            if not os.path.exists(image_pfn):
                log.info(f'{image_pfn=} does not exist, checking for adjusted version')
                if os.path.exists(image_pfn + ".adj.png"):
                    # a little sliver of  backwards compatibility
                    shutil.copyfile(
                        image_pfn + ".adj.png",
                        image_pfn
                    )
                else:
                    log.info(f'No adjusted version found for {image_pfn=}')

            if not os.path.exists(image_pfn):
                raise FileNotFoundError(f"Image file {image_pfn} does not exist")

            ii_pfn = image_pfn
            
            # I don't know how we want to scale 'viewer' images.
            animate = image_dict.get("animate", "").split(",")  # prompt for animation

            # how do we transition from the previous image into this image?

            # the best sequence of scale/animate depends on which mode is selected.
            # things that are static should be scaled first, then animated.  Active scaling, like
            # vertical pan, should be done after the animation so that the animation is not distorted.

            image_cycle = []
            image_list = []

            # turns an arbitrary length string into a 32-bit integer
            # with a good enough distribution to make collisions unlikely.
            if "prompt" in image_dict and image_dict["prompt"]:
                image_tag = fnv_hash_fast.fnv1a_32(image_dict.get("prompt").encode("utf-8"))
            else:
                image_tag = "static"

            camera_frame_dir = os.path.join(
                const.LIBRARY_DIR,
                paragraphdir,
                "image_frames",
                f"image_{int(image_dict['index']):06d}"
            )
           
            transition_frame_dir = os.path.join(
                const.LIBRARY_DIR,
                chapterdir,
                "transitions",
                f"transition_{int(image_dict['index']):06d}",
            )

            transition_done = os.path.join(transition_frame_dir, "done.flag")
            os.makedirs(transition_frame_dir, exist_ok=True)

            if not os.path.exists(transition_done):
                log.error('Transition frames defined but not found for frame', frame_index=frame_index, transition_frame_dir=transition_frame_dir)
                # not critical, but something is wrong with transitions.
            else:
                log.debug(f"Transition frame {transition_frame_dir} already exist...")

            # adjusted image list, preferred.
            if os.path.exists(camera_frame_dir):
                log.debug("Expanding camera frames from %s", camera_frame_dir)
                image_list = tools.expand_dir(
                    os.path.join(
                        camera_frame_dir,
                        "frame_*.png"
                    ),
                )

            if os.path.exists(transition_frame_dir):
                log.debug("Expanding transition frames from %s", transition_frame_dir)
                transition_frames = sorted(
                    tools.expand_dir(
                        transition_frame_dir + "/*.png",
                        reverse=True,
                    )
                )

                if transition_frames and "done.flag" in transition_frames:
                    transition_frames.remove("done.flag")

            animation_frames = []
            for animation_frame_directory_name in glob.glob(
                os.path.join(
                    const.LIBRARY_DIR,
                    paragraphdir,
                    "animation",
                    f"image_*_{int(image_dict['index']):06d}",
                )
            ):
                log.info(f"Found animation frame directory: {animation_frame_directory_name}")
            
                if os.path.exists(animation_frame_directory_name):
                    animation_frames += tools.expand_dir(
                        os.path.join(animation_frame_directory_name, "*.png"), 
                        reverse=False
                    )

                    log.info(
                        'Animation frames loaded:  %s', 
                        len(animation_frames)
                    )

            # # we ain't got nothin, but there is a video.. 
            base_video_fn = ii_pfn.replace('.png', '.mp4')
            if not animation_frames and os.path.exists(base_video_fn):
                # no frames.. but we have a video?  no problem.
                video_index = 0
                finding_frames = True

                while finding_frames:
                    animate_frame_dir = os.path.join(
                        const.LIBRARY_DIR,
                        paragraphdir,
                        "animation",
                        f"image_{int(image_dict['index']):06d}_{video_index:02d}",
                    )

                    if video_index == 0:
                        video_filename = base_video_fn
                    else:
                        video_filename = ii_pfn.replace('.png', f'_{video_index:02d}.mp4')

                    if os.path.exists(video_filename):
                        log.info(f"Animation video found for {video_filename}, extracting frames...")
                        os.makedirs(animate_frame_dir, exist_ok=True)
                        
                        tools.extract_frames(
                            video_filename,
                            animate_frame_dir
                        )
                        animation_frames = tools.expand_dir(
                            os.path.join(animate_frame_dir, "*.png"), 
                            reverse=False
                        )
                        log.info(
                            'Animation frames extracted from video:  %s', 
                            len(animation_frames)
                        )

                        video_index += 1
                    else:
                        finding_frames = False
            
            # if not animation_frames and animate:
            #     log.info('No animation frame directory found: %s', animate_frame_dir)
                
            # a finite number of images for the transition from the previous
            # image then an endless back and forth looping of the animation image
            # log.info(
            #     f"image_cycle=chain({transition_frames}, itertools.cycle({image_list}, {animation_frames}))"
            # )
            # if we've got nothing, just throw the static image up.
            if image_list in [None, []] and animation_frames in [None, []]:
                log.info(f"Static image: {ii_pfn}")
                image_list = itertools.cycle(
                    [
                        ii_pfn,
                    ]
                )
                
                if image_list:
                    log.debug('Cycling static image')

            # we're rebuilding this whole image_cycle thing for _every_frame_?!            
            image_cycle = itertools.chain(
                transition_frames,
                itertools.chain(
                    image_list,
                    animation_frames,
                    itertools.cycle(
                        [None]
                    )
                )
            )

            fullscreen = image_dict.get("fullscreen", False)

            if not fullscreen:
                if scroll_lock is None:
                    log.error(
                        '[%06i] No scroll_lock set for non-fullscreen frame!  Invalid camera at index %s', 
                        frame_index,
                        image_dict["index"]
                    )
                    raise ValueError('No scroll_lock set for non-fullscreen frame!')

                log.info('Finding text_image for phrase with id %s_%s', paragraph_index, phrase_id)
                # phrase_str = f"{paragraph_index}_{phrase_id}"
                phrase_xml = chapter.get_phrase(phrase_id)

                # text_image = typography.page_segment.from_offset(
                #     chapter=chapter,
                #     phrase_xml=phrase_xml,
                #     top_index=scroll_lock,
                #     force=force,
                # )
                text_image = chapter.get_highlighted_text_image(
                    phrase_xml=phrase_xml,
                    scroll_lock=scroll_lock,
                    force=force
                )

                if text_image is None:
                    log.error(f"! Failed to generate text image for phrase {phrase_id} !")
                    raise Exception(f"Failed to generate text image for phrase {phrase_id}")
            else:
                log.info('Fullscreen image, no text_image needed')

            if aspect == "widescreen":
                log.info('Creating widescreen canvas')
                # make an output dimensioned canvas
                canvas = Image.new("RGBA", size=(G["HSIZE"], G["VSIZE"]), color="black")

                # for the record, yes, this is a bit aggresively stupid but..
                # Q: how do you get the Nth entry in a generator?
                # A: by enumerating it
                log.info("Skip over the first %s of imaginative images", frame_index - first_frame_index, frame_index=frame_index, first_frame_index=first_frame_index)
                log.debug(
                    "transition_frames=%s, animation_frames=%s",
                    len(transition_frames),
                    len(animation_frames),
                )
                
                # its all about when you break.
                last_image_pfn = None
                i = 0
                for image_pfn in image_cycle:
                    # if static_only:
                    #     log.info("Static image, using it")
                    #     # static image, no need to skip anything
                    #     break
                    if image_pfn is None:
                        image_pfn = last_image_pfn

                    if i >= (frame_index - first_frame_index):
                        log.info(f"[{frame_index}] Choosing {os.path.basename(image_pfn)} for subframe {i}")
                        break

                    last_image_pfn = image_pfn
                    i += 1
                    
                    #else:
                    #    log.info(f"[{frame_index}] Skipping over {os.path.basename(image_pfn)}")

                mode = image_dict.get("mode", "fit-to-width")  # image scaling method

                log.debug(f"Using {image_pfn} as our imaginative image (Mode: {mode})")

                if not os.path.exists(image_pfn):
                    log.error(
                        "Imaginative Image %s does not exist, cannot draw frame %s",
                        image_pfn,
                        frame_index
                    )
                    continue

                try:
                    imaginative_image = Image.open(image_pfn)
                    imaginative_image.verify()
                    
                    imaginative_image = Image.open(image_pfn)
                    imaginative_image.load()
                except OSError as err:
                    log.error(
                        "Failed to open imaginative image for frame",
                        image_pfn=image_pfn,
                        frame_index=frame_index,
                        error=err,
                    )
                    # os.unlink(image_pfn)
                    continue

                except SyntaxError as err:
                    log.error(err)
                    if os.path.exists(image_pfn):
                        os.unlink(image_pfn)
                    continue
                
                changed = False
                try:
                    imaginative_image.load()
                except OSError:
                    log.error(
                        "Failed to load imaginative image %s for frame %s",
                        image_pfn,
                        frame_index
                    )
                    continue
                except SyntaxError as err:
                    log.error(err)
                    if os.path.exists(image_pfn):
                        os.unlink(image_pfn)
                    continue
                except AttributeError as err:
                    log.error(err)
                    if os.path.exists(image_pfn):
                        os.unlink(image_pfn)
                    continue

                if fullscreen:
                    x_offset = 0
                else:
                    # if we have text, put the image on the right
                    x_offset = G["HSIZE"] - const.IMG_TARGET_WIDTH

                if mode == "autopan":
                    mode = autopan(imaginative_image.size)

                if mode == "vpan":
                    changed = True
                    # vertical pan
                    # imaginative_image is too tall, so we are going to
                    # crop out a 1024x1024 section for an animated pan effect.
                    imaginative_image = vpan(
                        imaginative_image, 
                        first_frame=first_frame_index, 
                        this_frame=frame_index, 
                        last_frame=max_frame_index
                    )

                elif mode == "hpan":
                    changed = True
                    # horizontal pan imaginative_image is too short/wide, so we are going to
                    # resize to full screen height, then pan side-to-side across that image.
                    imaginative_image = hpan(
                        image_pfn,
                        imaginative_image, 
                        first_frame=first_frame_index, 
                        this_frame=frame_index, 
                        last_frame=max_frame_index
                    )

                # overwrite with the resized image

                if fullscreen:
                    # better have the right aspect ratio
                    if not changed and imaginative_image.size != G["SIZE"]:
                        log.info(f"Resizing imaginative image from {imaginative_image.size} to {G['SIZE']}")
                        imaginative_image = imaginative_image.resize(G["SIZE"])
                        imaginative_image.save(image_pfn)

                    canvas.paste(
                        imaginative_image,
                        (0, 0),
                    )
                else:
                    if not changed and imaginative_image.size != (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT):
                        animate_lock.acquire()

                        imaginative_image = Image.open(image_pfn)
                        if not changed and imaginative_image.size != (const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT):
                            log.info(f"Resizing imaginative image from {imaginative_image.size} to {(const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)}")
                            imaginative_image = imaginative_image.resize((const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT))
                            imaginative_image.save(image_pfn)

                        animate_lock.release()

                    # text on the left
                    canvas.paste(text_image, (0, 0))

                    # picture on the right
                    try:
                        canvas.paste(
                            imaginative_image,
                            (
                                x_offset, 
                                int((G["VSIZE"] - const.IMG_TARGET_HEIGHT) / 2)
                            ),
                        )
                    except OSError:
                        log.error(
                            "Failed to paste imaginative image onto canvas, "
                            "imaginative image size: %s, canvas size: %s",
                            imaginative_image.size,
                            canvas.size,
                        )
                        continue
                    except SyntaxError as err:
                        os.unlink(imaginative_image)
                        log.error(err)
                        continue

            elif aspect == "portrait":
                log.info('Creating portrait canvas for frame')
                # make an output dimensioned canvas
                # gasps from gramps, hi gramps
                canvas = Image.new(
                    "RGBA", 
                    size=(G["HSIZE"], G["VSIZE"]), 
                    color="black"
                )

                last_image_pfn = None
                for i, image_pfn in enumerate(image_cycle):
                    if image_pfn is None:
                        image_pfn = last_image_pfn

                    if i >= (frame_index - first_frame_index):
                        log.debug(f"[{frame_index}] Choosing {os.path.basename(image_pfn)} for subframe {i}")
                        break
                    
                    last_image_pfn = image_pfn

                mode = image_dict.get("mode", "fit-to-width")  # image scaling method

                imaginative_image = ImageSafetyDanceOpen(image_pfn)
                # imaginative_image = Image.open(image_pfn)
                # imaginative_image.load()
                
                if mode == "autopan":
                    mode = autopan(imaginative_image.size)

                if mode == "vpan":
                    # vertical pan
                    # imaginative_image is too tall, so we are going to
                    # crop out a section for an animated pan effect.
                    imaginative_image = vpan(
                        imaginative_image, 
                        first_frame=first_frame_index, 
                        this_frame=frame_index, 
                        last_frame=max_frame_index
                    )
                elif mode == "hpan":
                    imaginative_image = hpan(
                        image_pfn,
                        imaginative_image, 
                        first_frame=first_frame_index, 
                        this_frame=frame_index, 
                        last_frame=max_frame_index
                    )

                if fullscreen:
                    # grr, fullscreen == widescreen here
                    # and that is a tough nut.
                    canvas.paste(
                        imaginative_image,
                        (0, 0),
                    )
                else:
                    # place the imaginative image on the canvas.
                    # center it horizontally if there isn't alignment?
                    
                    # picture centered in the top image region
                    canvas.paste(
                        imaginative_image,
                        (
                            int((G["HSIZE"] - imaginative_image.width) / 2),
                            int((const.IMG_TARGET_HEIGHT - imaginative_image.height) / 2)
                        ),
                    )

                    # text on the bottom, centered
                    log.info(
                        'Pasting text_image (%s) on canvas @ %sx%s',
                        text_image.size,
                        (canvas.width - text_image.width) // 2,
                        imaginative_image.height
                    )
                    
                    canvas.paste(
                        text_image,
                        (
                            (canvas.width - text_image.width) // 2,
                            const.IMG_TARGET_HEIGHT
                        )
                    )

            canvas.save(frame_fn)
            log.info(f"[{frame_index:08}/{max_frame_index:08}] Saved {frame_fn}")
            success = True
        except Exception as err:
            log.error(f"Error drawing frame {frame_index}: {err}")
            log.error(traceback.format_exc())
            success = False

    log.info(f"[{frame_index:08}/{max_frame_index:08}] draw_frame() complete")


def hpan(image_pfn, imaginative_image, first_frame: int, this_frame:int, last_frame:int):
    vp_width, vp_height = imaginative_image.size

    if vp_height != const.IMG_TARGET_HEIGHT:
        if vp_height > const.IMG_TARGET_HEIGHT:
            # it's too big, thumbnail does the right thing
            imaginative_image.thumbnail((102400, const.IMG_TARGET_HEIGHT))
        else:        
            # the image is too small, we need to resize it up
            ratio = const.IMG_TARGET_HEIGHT / vp_height
            imaginative_image = imaginative_image.resize(
                (math.ceil(ratio * vp_width), math.ceil(ratio * vp_height))
            )
            vp_width, vp_height = imaginative_image.size

        # save it so we don't have to resize every frame.
        imaginative_image.save(image_pfn)

    pan_left = 0
    pan_distance = vp_width - const.IMG_TARGET_WIDTH
    per_frame_distance = pan_distance / (last_frame - first_frame)

    pan_left += per_frame_distance * (this_frame - first_frame)

    imaginative_image = imaginative_image.crop(
        (pan_left, 0, pan_left + const.IMG_TARGET_WIDTH, const.IMG_TARGET_HEIGHT)
    )
    return imaginative_image


def vpan(imaginative_image, first_frame: int, this_frame:int, last_frame:int):
    vp_width, vp_height = imaginative_image.size
    if vp_width != const.IMG_TARGET_WIDTH:
        # height is adjusted to maintain aspect ratio
        imaginative_image.thumbnail((const.IMG_TARGET_WIDTH, 102400))
        vp_width, vp_height = imaginative_image.size

    pan_top = 0
    pan_distance = vp_height - const.IMG_TARGET_HEIGHT
    per_frame_distance = pan_distance / (last_frame - first_frame)

    pan_top += per_frame_distance * (this_frame - first_frame)

    imaginative_image = imaginative_image.crop(
        (0, pan_top, const.IMG_TARGET_WIDTH, pan_top + const.IMG_TARGET_HEIGHT)
    )
    return imaginative_image


def autopan(imaginative_image_size):
    vp_width, vp_height = imaginative_image_size
    # given an image with these dimensions, should we pan vertically or horizontally?
    #
    # which dimension is closest (%) to correct?  We rescale in that direction and pan the other.
    if (vp_width / const.IMG_TARGET_WIDTH) > (
        vp_height / const.IMG_TARGET_HEIGHT
    ):
        # we want to scale the height, so we will pan horizontally
        return "hpan"
    else:
        return "vpan"
    

def clear_cache(chapter):
    # cache_key = f"segment:{chapter.key}:{phrase_xml.attrs['id']}:{segment_top}:{segment_top + SEGMENT_HEIGHT}"
    #for aspect in const.ALL_ASPECTS_LIST:
        # ps_chapter = typography.page_segment.Chapter(
        #     chapterdir=chapter.chapterdir,
        #     aspect=aspect,
        # )

    r = redis.Redis(host="redis")
    pattern = f"segment:{chapter.key}:*"
    for key in r.scan_iter(pattern):
        log.info(f'Deleting cache key {key.decode("utf-8")}')
        r.delete(key)

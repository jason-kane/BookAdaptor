import inspect
import json
import multiprocessing as mp
import os
import subprocess
import time

from artifact_editor import camera
import const
import logger
from artifact_editor import tools
from artifact_editor.audio import audio
from artifact_editor.frames import frames

log = logger.log(__name__)

def onErr(self, *args, **kwargs):
    stack = inspect.stack()
    for s in stack:
        log.error(s)

    log.error(args)
    raise args

def book_to_video(chapter, aspect="widescreen", force=False):
    """
    Turn the whole book into one mp4
    """
    global shared_text_image

    log.info(f"=== Book to {aspect.capitalize()} Video ===")
    framedir = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "frames",
        aspect
    )
    os.makedirs(framedir, exist_ok=True)

    # image timeline/table of content, populated by .frame_images()

    # annotate image tags with:
    #   frame_start
    #   frame_end
    #   text_height - bottom of previous highlighted block to bottom of current highlighted block in pixels
    #   hbottom - top of text_image to bottom of current highlighted block in pixels

    # what should the initial scroll lock be? approximate proper initial
    # scroll lock for title and author that don't wrap.
    # this is purely based on latex, TODO: we should calculate it from
    # the highlight block instead.
    scroll_lock = 0
    current_frame = 0
    previous_image = None
    audio_tracks = []

    log.info("Exhausting words and images...")
    paragraph_index = 0
    current_paragraph_index = -1

    mplan = chapter.get_masterplan()
    max_frame_index = mplan["words"][-1]["end_frame"]

    # first sync pass to link a specific camera location to every frame
    chapter.build_frame_to_camera(
        aspect=aspect,
        force=force
    )

    m = mp.Manager()
    animate_lock = m.Lock()

    if os.path.exists("scroll_to_page.pickle"):
        log.info("Clearing scroll_to_page.pickle")
        os.unlink("scroll_to_page.pickle")

    with mp.Pool(processes=const.PROCESSES) as pool:
        current_frame = 0
        jobs = []
        
        # throw a redraw task for every frame at the multiprocessing pool.
        while current_frame <= max_frame_index:
            log.info("drawing frame: [%s/%s] (%s)", current_frame, max_frame_index, aspect)

            # word is a word dict from masterplan:
            # {
            #     "start_frame": 0,
            #     "duration": 1.4,
            #     "text": "Meno",
            #     "paragraph_tags": {
            #         "has-text": False,
            #         "spoken-only": True
            #     },
            #     "phrasedir": "Plato/Meno/paragraphs/000000",
            #     "id": "0_1",
            #     "speaker": "narrator",
            #     "src": "/Plato/Meno/paragraphs/000000/ph_1_Meno_e6c5de6c.wav",
            #     "end_frame": 35
            # },

            image_dict = chapter.frame_to_image(mplan, current_frame)
            log.debug("image_dict: %s", image_dict)

            # # absolute path to the source image
            # # "image" can either be a simple filename relative to the paragraph_dir,
            # # or it can be relative to the library dir.
            # if "/" in image_dict['image']:
            #     image_dict["image_pfn"] = os.path.join(
            #         const.LIBRARY_DIR,
            #         image_dict['image'].lstrip("/")
            #     )
            # else:
            image_dict["image_pfn"] = os.path.join(
                const.LIBRARY_DIR,
                image_dict['paragraph_dir'],
                image_dict['image'].lstrip("/")
            )

            # when the image_dict changes, draw all the frames for that image_dict.
            if image_dict != previous_image:
                for current_frame in range(
                    image_dict["start_frame"], image_dict["end_frame"] + 1
                ):
                    word = chapter.frame_to_word(mplan, current_frame)
                    phrase_id = int(word['index'])
                    paragraph_index = int(word['paragraph_index'])
                    # paragraph_index, phrase_id = word["id"].split("_")

                    # paragraph_index = int(paragraph_index)
                    # phrase_id = int(phrase_id)

                    if paragraph_index != current_paragraph_index:
                        paragraph = chapter.get_xml().find(
                            "paragraph", index=str(paragraph_index)
                        )
                        # re.compile('^%s_.*$' % paragraph_index))

                        if paragraph is None:
                            log.error(
                                f"Paragraph {paragraph_index} not found in XML!!  Bad masterplan?"
                            )
                            break

                        paragraph_tags = tools.tags_to_dict(
                            paragraph.attrs.get("tags", "")
                        )
                        current_paragraph_index = paragraph_index

                    # if the frame already exists, skip it.
                    frame_fn = os.path.join(
                        framedir, f"frame_{current_frame:06}.png"
                    )
                    if os.path.exists(frame_fn):
                        log.debug("Frame %s already exists, skipping", frame_fn)
                        continue

                    image_dict["paragraph_tags"] = paragraph_tags

                    # check for a camera effect frame, if it exists that becomes
                    # our new base image.
                    camera_frame = os.path.join(
                        const.LIBRARY_DIR,
                        image_dict["paragraph_dir"],
                        "image_frames",
                        "image_%06d" % image_dict["index"],
                        f"frame_{current_frame:06}.png"
                    )

                    if os.path.exists(camera_frame):
                        log.info(f'[{current_frame:06}] Using camera image_frame')
                        image_dict["image"] = os.path.join(
                            image_dict["paragraph_dir"],
                            "image_frames",
                            "image_%06d" % image_dict["index"],
                            f"frame_{current_frame:06}.png"
                        )
                        image_dict["image_pfn"] = camera_frame

                    scroll_lock = camera.frame_to_camera(current_frame)
                    if scroll_lock is None:
                        log.warning(
                            f"No scroll_lock found for frame {current_frame}"
                        )

                    args = (
                        chapter.chapterdir,
                        aspect,
                        frame_fn,
                        scroll_lock,
                        image_dict["start_frame"],
                        current_frame,
                        image_dict["end_frame"],
                        image_dict,
                        previous_image,
                        animate_lock,
                        paragraph_index,
                        phrase_id
                    )

                    previous_image = image_dict

                    if const.MULTIPROCESS:
                        jobs.append(
                            pool.apply_async(
                                frames.draw_frame,
                                args=args,
                                error_callback=onErr,
                            )
                        )
                    else:
                        frames.draw_frame(*args)

                current_frame += 1

        if const.MULTIPROCESS:
            log.info("Closing pool...")
            if jobs:
                count = 0
                while True:
                    if count % 30 == 0:
                        log.info("Waiting for jobs to finish... (%s jobs)", len(jobs))

                    if count % 60 == 0:
                        log.info('Sample job: %s', jobs[0])

                    time.sleep(1)
                    passing = all([job.ready() for job in jobs])

                    if passing:
                        log.info("All jobs are ready, closing pool...")
                        break

                    count += 1

                for job in jobs:
                    try:
                        result = job.get(timeout=30)
                        log.debug(result)
                    except Exception as e:
                        log.error(f"Job {job} failed: {e}")


            pool.close()
            pool.join()
            log.info("Pool closed.")

    chapter.save_xml()

    audio_tracks = chapter.get_all_audio_tracks()

    # glue all the wav files in [audio_tracks] together.
    if not audio_tracks:
        log.warning("No audio tracks found, no video will be created.")

    else:
        audio_fn = os.path.join(
            const.LIBRARY_DIR, 
            chapter.chapterdir.lstrip("/"), 
            "audio.wav"
        )
        audio.assemble(audio_tracks=audio_tracks, outfile=audio_fn)

        # now make an mp4 with these frames set to this audio.
        video_pfn = chapter.get_video_filename()

        tools.assemble_mp4(
            fps=const.FPS,
            framedir=os.path.abspath(framedir),
            wavfile=audio_fn,
            videofile=video_pfn,
            image_match="frame_%06d.png",
        )



def render_masterplan_widescreen(chapter):
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
    book_to_video(chapter, "widescreen", force=False)


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
    book_to_video(chapter, "portrait", force=False)

assemble_mp5 = tools.assemble_mp4

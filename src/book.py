#!/usr/bin/env python3
import inspect
import json
import multiprocessing as mp
import os
import re
import time

import bs4

from PIL import Image

import logger
from artifact_editor import (
    characters,
    config,
    camera,
    video,
    audio,
    typography,
    tools,
)
from artifact_editor.images import images
from artifact_editor.frames import frames
import const

import roman

from artifact_editor.tools import (
    get_chapterurl,
)


log = logger.log(__name__)
log.info("book.py imports completed")

# global to use the text image in memory as we fork so we don't have to reload if from disk on every frame
shared_text_image = None


def ireplace(old, new, text):
    """
    replace only the _first_ instance of `old` in `text` with `new`.
    """
    index_l = text.lower().index(old.lower())
    out = text[:index_l] + new + text[index_l + len(old) :]
    log.info(f"{index_l} {text} -> {out}")
    return out


class PixelAdjustment(Exception):
    pass


class Book:
    def __init__(self, chapter):
        log.info(f"initializing new book({chapter})")
        self.chapter = chapter
        # L. Frank Baum/The Marvelous Land of Oz/chapter/0001/../..

        # cd_list = chapterdir.strip("/").replace('/..', '').split("/")
        # if len(cd_list) == 3:
        #     self.author, self.title, chapter_number = cd_list
        #     self.chapter_number = int(chapter_number)
        # elif len(cd_list) == 4:
        #     self.author, self.title, _, chapter_number = cd_list
        #     self.chapter_number = int(chapter_number)

        # self.chapterdir = f"{self.author}/{self.title}/chapter/{self.chapter_number:04}"
        # log.info('chapterdir: %s', self.chapterdir)

        self.raw = None
        self.chapters = []
        self.prelude_text = ""
        self.RESET = {}
        self.RESETS = []
        self.IMAGE_REPLACE = []
        self.RESET_ALL_AFTER_SEGMENT = None
        self.RESET_AUDIO = True
        self.RESET_TEXT = True

        self.FIRST = True

        self.masterplan = None

        self.audio_tracks = []
        # the alpha segment is the first segment with text in a chapter.  We can
        # give it some extra fancy beginning of the book flourish.
        self.alpha_segment = False

        self.load_config()

        self.soup = None
        self.raw = ""

        self.read_book()
        self.load_xml()

        self.frame_to_text_fn = os.path.join(self.chapterdir, "frame_to_text.json")
        self._max_image_index = None
        self._max_paragraph_index = None

    def get_previous_chapter(self):
        if self.chapter_number > 1:
            previous_chapter_number = self.chapter_number - 1
            previous_chapter_dir = f"{self.author}/{self.title}/chapter/{previous_chapter_number:04}"
            return Book(previous_chapter_dir)
        return None

    def get_chapterurl(self):

        return get_chapterurl(self.author, self.title, self.chapter_number)

    def save_config(self):
        """
        Save the config file for this book.
        """
        config.save_config(self.chapterdir, self.config)

    def load_config(self):
        """
        Reload the config file for this book.
        """
        self.config = config.get_config(self.chapterdir)

    def max_paragraph_index(self):
        if self._max_paragraph_index is None:
            for paragraph in self.soup.find("book").children:
                pass

                if hasattr(paragraph, "attrs"):
                    if "index" in paragraph.attrs:
                        self._max_paragraph_index = int(
                            paragraph.attrs["index"].split("_")[0]
                        )
                    else:
                        log.warning("Paragraph without index found: %s", paragraph)

        return self._max_paragraph_index

    def max_image_index(self) -> int:
        """
        what does max_image_index mean?  The maximum index of the images in the book?
        """
        if self._max_image_index:
            return self._max_image_index

        all_images = self.soup.findAll("image")
        try:
            last_index = all_images[-1].attrs['index']
        except KeyError:
            # re-index, then try again.
            images.assign_fragdex_and_index(self.soup)

            all_images = self.soup.findAll("image")
            last_index = all_images[-1].attrs['index']

        self._max_image_index = last_index
        return int(last_index)

    def safe_title(self):
        log.info(self.config)
        full_title = self.config["title"]
        safe_title = re.sub(r"[/\\?%*:|\"<>\x7F\x00-\x1F]", "-", full_title)
        return safe_title.replace(" ", "_")

    def read_book(self):
        """
        Read the text of the book in as self.raw
        """
        fn = "book.txt"

        with open(
            os.path.join(
                const.LIBRARY_DIR,
                self.chapterdir.lstrip('/'), 
                fn
            ), 
            "r"
        ) as h:
            self.raw = h.read()

        # some characters just cause chaos
        self.raw = self.raw.replace("—", "--")

    def load_xml(self):
        """
        Read the xml of the book into self.soup

        returns true if we got contents
        """
        self.soup = None

        xml_fn = os.path.join(const.LIBRARY_DIR, self.chapterdir, "book.xml")
        if os.path.exists(xml_fn):
            with open(xml_fn, "r") as h:
                self.soup = bs4.BeautifulSoup(h.read(), "xml")

            for image in self.soup.findAll("image"):
                if "frames" in image.attrs:
                    image.attrs["frames"] = int(float(image.attrs["frames"]))

            for phrase in self.soup.findAll("phrase"):
                if "frames" in phrase.attrs:
                    phrase.attrs["frames"] = int(float(phrase.attrs["frames"]))

            for index, paragraph in enumerate(self.soup.findAll("paragraph")):
                paragraph.attrs["index"] = index
                paragraph.attrs['dir'] = os.path.join(
                    self.chapterdir,
                    "paragraphs",
                    f"{index:0>6}"
                )
        else:
            self.soup = bs4.BeautifulSoup("<book></book>", "xml")

        return self.soup is not None

    def clean_xml(self):
        """
        Clean the xml of the book
        """
        change = False
        if self.soup is None:
            return

        # remove any empty tags
        for tag in self.soup.find_all():
            if not str(tag).strip():
                tag.decompose()
                change = True

        # remove any empty text nodes
        for textchunk in self.soup.find_all(
            string=lambda text: isinstance(text, bs4.element.NavigableString)
            and not text.strip()
        ):
            textchunk.extract()
            change = True

        for paragraph in self.soup.findAll("paragraph"):
            # remove obsolete attributes
            if "parsedir" in paragraph.attrs:
                del paragraph.attrs["parsedir"]
                change = True

        if change:
            log.info("Cleaning XML")
            self.save_xml()

    def save_raw(self):
        """
        Save the text of the book as a text file
        """
        fn = "book.txt"
        with open(os.path.join(const.LIBRARY_DIR, self.chapterdir, fn), "w") as h:
            h.write(self.raw)

    def save_xml(self):
        """
        Save a book as an xml file
        """
        book_fn = os.path.join(const.LIBRARY_DIR, self.chapterdir, "book.xml")

        # /home/jkane/books/active/Aesop/Fables/chapter/0025/book.xml
        # /home/jkane/books/active/Aesop/Fables/chapter/0025/book.xml
        log.info("Saving book as XML (%s)", book_fn)
        with open(book_fn, "w") as h:
            h.write(self.soup.prettify())
        
        log.info("Finished saving book as XML")
        with open(book_fn, "r") as h:
            log.info("book.xml contents:\n%s", h.read().splitlines()[:5])

    def get_characters(self):
        """
        Return a dict of all the characters in the book
        """
        return characters.get_all_characters(self.chapterdir)

    def get_character(self, name):
        """
        Return a dict of the character with the given name
        """
        return characters.get_character(self.chapterdir, name)

    def find_text_image(self, paragraph, frame_index):
        this_text_image = None
        if paragraph and paragraph.name in ["stanza", "paragraph"]:
            for bucket in self.frame_to_text:
                la, ae, ti = bucket
                if frame_index >= la and frame_index <= ae:
                    log.debug(f"Found {ti} for frame {frame_index}")
                    this_text_image = ti
                    break

            if this_text_image is None:
                log.warning(f"No text image found for frame {frame_index}")

        return this_text_image

    def get_first_phrase(self, paragraph):
        fragdex = 0
        for fragment in paragraph.contents:
            if fragment is None or not str(fragment).strip():
                continue
            fragdex += 1

            if fragment.name != "image":
                if "fragdex" not in fragment.attrs:
                    fragment.attrs["fragdex"] = fragdex

                return fragment
        return None

    def get_last_phrase(self, paragraph):
        for fragment in reversed(paragraph.contents):
            if fragment is None or not str(fragment).strip():
                continue

            if fragment.name != "image":
                return fragment
        return None

    def load_masterplan(self):
        if self.masterplan is None:
            
            masterplan_fn = os.path.join(
                const.LIBRARY_DIR, 
                self.chapterdir, 
                "masterplan.json",
            )
            log.info("Loading master plan %s", masterplan_fn)

            if os.path.exists(masterplan_fn):
                with open(masterplan_fn) as h:
                    log.info("Loading masterplan from %s", masterplan_fn)
                    self.masterplan = json.load(h)

        return self.masterplan

    def save_masterplan(self):
        log.info("Saving master plan...")
        masterplan_fn = os.path.join(
            const.LIBRARY_DIR, 
            self.chapterdir,
            "masterplan.json",
        )
        with open(masterplan_fn, "w") as h:
            json.dump(self.masterplan, h, indent=4)

    def get_all_audio_tracks(self):
        self.load_masterplan()
        audio_tracks = []
        for word in self.masterplan["words"]:
            audio_tracks.append(
                os.path.join(
                    const.LIBRARY_DIR, 
                    word["paragraph_dir"], 
                    word["src"].lstrip("/")
                )
            )

        return audio_tracks

    def frame_to_word(self, frame_index):
        '''
        Based on the 'words' section of masterplan, determine which 'word'
        object governs the given frame_index.
        '''
        self.load_masterplan()

        # quick access to get the last word.
        if frame_index == -1:
            return self.masterplan["words"][-1]

        for word in self.masterplan["words"]:
            if (word["start_frame"] <= frame_index) and (
                word["end_frame"] >= frame_index
            ):
                return word
        
        log.error('No word found for frame index %s', frame_index)
        return None

    def frame_to_image(self, frame_index):
        self.load_masterplan()

        for image in self.masterplan["images"]:
            if image["start_frame"] <= frame_index <= image["end_frame"]:

                # use a camera effect frame?
                frame_image = os.path.join(
                    const.LIBRARY_DIR,
                    image['paragraph_dir'],
                    "image_frames",
                    f"image_{image['index']:06d}",
                    f"frame_{frame_index - image['start_frame']:06}.png",
                )

                if os.path.exists(frame_image):
                    log.info('Using frame image at %s', frame_image)
                    image['image'] = os.path.join(
                        "image_frames",
                        f"image_{image['index']:06d}",
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

    def draw_frames(
        self,
        framedir,
    ):
        """
        Draw any missing frames
        """
        return

    def paragraph_scroll_rate(self, paragraph, paragraph_height):
        if paragraph["frames"] != 0:
            pixels_per_frame = paragraph_height / float(paragraph["frames"])
        return pixels_per_frame

    def scroll_lock_to_midpoint(self, scroll_lock):
        return (-1 * scroll_lock) + (const.VSIZE / 2)

    def midpoint_to_scroll_lock(self, midpoint: float) -> float:
        """
        It's difficult to believe these are both true.
        """
        # 398

        # seriously?  so input of 541 (top from first phrase),
        # give -541 + (1080 / 2) = -541 + 540 = -1
        # at least approximately correct
        #
        # 1437 (top of last phrase) -> -1437 + 540 = -897
        # plausible.  So yeah, this looks about right.
        return (-1 * midpoint) + (const.VSIZE / 2)

    def build_frame_to_camera(self, aspect='widescreen', force=False):
        """
        output is a camera.json

        given a frame index, return the scroll lock.
        What _matters_ is leaving things nice for the next person
        Calculate each inter-paragraph gap directly instead of assuming a constant.
        """
        log.info("=== Building frame to camera (%s) ===", aspect)
        self.load_masterplan()
        saved = camera.load_camera(self.chapterdir, aspect=aspect)
        if saved and not force:
            log.info("Camera loaded from disk, skipping build_frame_to_camera")
            return

        last_frame = self.masterplan["words"][-1]["end_frame"]
        # slots for every frame, we want the scroll_lock value for each frame.
        _frame_to_camera = [None] * (last_frame + 1)

        current_frame = 0

        self.scrolling_rate = None
        # set scrolling rate to the average required to place the bottom of the last
        # line in the center of the screen when the audio finishes.

        chapter = typography.page_segment.Chapter(
            chapterdir=self.chapterdir,
            aspect=aspect
        )
        
        # adjusted because we start half way down the screen
        # ^ ballpark, we hate ballpark.
        #height = chapter.get_text_height(force=force)

        G = const.GEOMETRY[aspect]
        # the text begins halfway down the screen.
        height = chapter.get_bottom_height(force=force) - (G['TEXT_HEIGHT'] // 2)

        scrolled_frames = 0
        for current_frame in range(last_frame + 1):
            word = self.frame_to_word(current_frame)
            # log.info(f'[{current_frame}] {word=}')
            if word['fullscreen']:
                continue
            scrolled_frames += 1

        self.scrolling_rate = height / float(scrolled_frames)
        log.info(
            f"In order to traverse {height} pixels of text height in {scrolled_frames} frames"
        )
        log.info(
            f"Setting initial scrolling rate to {self.scrolling_rate} pixels per frame"
        )

        scroll_lock = 0
        for current_frame in range(last_frame + 1):
            word = self.frame_to_word(current_frame)

            # skip over any fullscreen frames, they will fall-through to None
            # and we don't want to advance scroll lock.
            if word['fullscreen']:
                continue

            _frame_to_camera[current_frame] = scroll_lock
            scroll_lock += self.scrolling_rate
        
        log.info("Frame to camera built with %s frames", len(_frame_to_camera))
        camera.set_frame_to_camera(_frame_to_camera)
        camera.save_camera(chapterdir=self.chapterdir, aspect=aspect)
        return

    def get_scrolling_rate(self, frame):
        # at our current scrolling_rate, will we reach 'desired' in the next 10 frames?
        # adjust our trajectory gently.  log an error when we fail tolerance.
        if self.scrolling_rate is None:
            # set initial scroll rate to the (bottom - top) / frames
            # adjust from that baseline.
            # first_word = self.frame_to_word(0)
            last_word = self.frame_to_word(-1)

            total_frames = last_word.get("end_frame")

            if self.soup is None:
                self.load_xml()
                log.info("self.soup=%s", self.soup)

            all_paragraphs = self.soup.findAll("paragraph")

            paragraph_index = 0
            for paragraph_index, p in enumerate(all_paragraphs):
                if "has-text=false" in p.attrs.get("tags", ""):
                    continue
                break

            # first paragraph with words anyway
            first_paragraph = all_paragraphs[paragraph_index]
            first_phrase = first_paragraph.find("phrase")

            last_paragraph = all_paragraphs[-1]
            last_phrase = last_paragraph.findAll("phrase")[-1]

            try:
                height = int(last_phrase.attrs["bottom"]) - int(
                    first_phrase.attrs["top"]
                )
            except KeyError as err:
                log.error(
                    "%s: Phrases are missing geometry:\nlast_phase requires bottom: %s\nfirst phrase requires top: %s",
                    err,
                    last_phrase,
                    first_phrase,
                )

            self.scrolling_rate = height / float(total_frames)
            log.info(
                f"Setting initial scrolling rate to {self.scrolling_rate} pixels per frame"
            )

            # self.scrolling_rate = self.paragraph_scroll_rate(
            #     self.soup.find('paragraph', index=str(self.current_paragraph_index)),
            #     self.get_paragraph_height(self.soup.find('paragraph', index=str(self.current_paragraph_index))) + 43
            # )

        if self.desired is None:
            log.error("Desired is None, cannot adjust scrolling rate.")
            return self.scrolling_rate

        MAX_ACCELERATION = 0.25  # % per frame max acceleration
        MIN_SCROLLING_RATE = 0.125  # don't go too slow
        MAX_SCROLLING_RATE = 5.0  # don't go too fast

        if self.destination:
            if self.desired > frame["scroll_lock"]:
                # let things catch up.
                return MIN_SCROLLING_RATE

            distance = self.desired - frame["scroll_lock"]

            if self.scrolling_rate != 0:
                try:
                    frames_to_target = abs(distance / self.scrolling_rate)
                except TypeError:
                    log.error(f"{distance=}")
                    log.error(f"{self.scrolling_rate=}")
                    raise

            else:
                frames_to_target = float("inf")

            log.info(
                f"At scrolling rate {self.scrolling_rate}, we are {distance} pixels from desired {self.desired}, which will take {frames_to_target} frames to reach."
            )

            if frames_to_target < (0.9 * self.destination):
                # we're going to reach the target too soon, slow down
                old_scrolling_rate = self.scrolling_rate
                self.scrolling_rate = max(
                    MIN_SCROLLING_RATE, self.scrolling_rate * (1 - MAX_ACCELERATION)
                )
                log.info(
                    f"Slowing scrolling rate from {old_scrolling_rate} to {self.scrolling_rate}"
                )
            elif frames_to_target > (1.1 * self.destination):
                # we're going to reach the target too late, speed up
                old_scrolling_rate = self.scrolling_rate
                self.scrolling_rate = min(
                    MAX_SCROLLING_RATE, self.scrolling_rate * (1 + MAX_ACCELERATION)
                )
                log.info(
                    f"Speeding scrolling rate from {old_scrolling_rate} to {self.scrolling_rate}"
                )
            else:
                log.info(f"Scrolling rate {self.scrolling_rate} is good, no change.")

            self.destination -= 1
        return self.scrolling_rate

    def redimension_paragraph(self, last_paragraph_xml):
        """
        We are missing top/bottom attributes on the phrases in the book xml, so
        we need to calculate them.  This is indicative of:

        * a problem with the typography text generation failing to feedback the
          dimensions to the xml
        * manual clearing of data, in order to force exactly this recalculation
        * things running outside the expected sequence (ie: video before typography)
        """
        log.warning('OBSOLETE')
        paragraph_dir = last_paragraph_xml.attrs["dir"]

        xml_to_latex = typography.XmlConverter(
            chapterdir=self.chapterdir,
            xml=self.soup.find("book"),
        )

        for phrase in last_paragraph_xml.findAll("phrase"):
            fragdex = phrase.attrs["fragdex"]

            xml_to_latex.write_as_latex(
                paragraph_dir=paragraph_dir,
                highlight_phrase=phrase,
                highlight_paragraph=False
            )

            xml_to_latex.combine_text_layers(
                paragraph_dir=paragraph_dir,
                fragdex=fragdex
            )

            last_paragraph_xml.attrs["top"] = max(
                float(last_paragraph_xml.attrs.get("top", float("inf"))),
                float(phrase.attrs.get("top", float("inf")))
            )

            last_paragraph_xml.attrs["bottom"] = min(
                float(last_paragraph_xml.attrs.get("bottom", float("inf"))),
                float(phrase.attrs.get("bottom", float("inf")))
            )

        return last_paragraph_xml

    def max_frame_index(self):
        """
        Return the maximum frame index for this book.
        """
        self.load_masterplan()
        if not self.masterplan or "words" not in self.masterplan:
            log.error("Masterplan is empty or missing words.")
            return None

        return self.masterplan["words"][-1]["end_frame"]

    def book_to_video(self, aspect="widescreen", force=False):
        """
        Turn the whole book into one mp4
        """
        global shared_text_image

        log.info(f"=== Book to {aspect.capitalize()} Video ===")
        framedir = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
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

        max_frame_index = self.max_frame_index()
        # first sync pass to link a specific camera location to every frame
        self.build_frame_to_camera(
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

                image_dict = self.frame_to_image(current_frame)
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
                        word = self.frame_to_word(current_frame)
                        paragraph_index, phrase_id = word["id"].split("_")

                        paragraph_index = int(paragraph_index)
                        phrase_id = int(phrase_id)

                        if paragraph_index != current_paragraph_index:
                            paragraph = self.soup.find(
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
                            self.chapterdir,
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
                                    error_callback=self.onErr,
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

        self.save_xml()

        audio_tracks = self.get_all_audio_tracks()

        # glue all the wav files in [audio_tracks] together.
        if not audio_tracks:
            log.warning("No audio tracks found, no video will be created.")

        else:
            audio_fn = os.path.join(
                const.LIBRARY_DIR, self.chapterdir.lstrip("/"), "audio.wav"
            )
            audio.audio.assemble(audio_tracks=audio_tracks, outfile=audio_fn)

            # now make an mp4 with these frames set to this audio.
            video_pfn = self.get_video_filename()

            video.video.assemble_mp4(
                fps=const.FPS,
                framedir=os.path.abspath(framedir),
                wavfile=audio_fn,
                videofile=video_pfn,
                image_match="frame_%06d.png",
            )

    def get_aspect(self):
        """
        Return the aspect ratio of the book
        """
        # obsolete
        return self.config.get('aspect_ratio', "widescreen")

    def book_to_widescreen_video(self, force=False):
        """
        Turn the whole book into one mp4
        """
        return self.book_to_video(aspect="widescreen", force=force)

    def book_to_portrait_video(self, force=False):
        """
        Turn the whole book into one mp4
        """
        return self.book_to_video(aspect="portrait", force=force)

    def redraw_frame(self, frame_index, aspect='widescreen', force=False):
        """
        Redraw a single frame
        """
        global shared_text_image
        frame_index = int(frame_index)

        log.info(f"=== Redraw Frame {frame_index} ===")
        framedir = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            "frames",
            aspect
        )
        os.makedirs(framedir, exist_ok=True)

        scroll_lock = 0
        current_frame = 0
        previous_image = None

        log.info("Exhausting words and images...")
        paragraph_index = 0
        current_paragraph_index = -1

        max_frame_index = self.max_frame_index()
        # first sync pass to link a specific camera location to every frame
        self.build_frame_to_camera(
            aspect=aspect,
            force=True
        )

        m = mp.Manager()
        animate_lock = m.Lock()

        # I hate this
        if os.path.exists("scroll_to_page.pickle"):
            log.info("Clearing scroll_to_page.pickle")
            os.unlink("scroll_to_page.pickle")

        current_frame = 0
        while current_frame <= max_frame_index:
            log.info("Evaluating frame: [%s/%s]", current_frame, max_frame_index)

            image_dict = self.frame_to_image(current_frame)
            
            if current_frame > frame_index:
                # we're done
                break

            if current_frame == frame_index:
                log.info("image_dict: %s", image_dict)

            # absolute path to the source image
            image_dict["image_pfn"] = os.path.join(
                const.LIBRARY_DIR,
                image_dict['paragraph_dir'],
                image_dict['image']
            )

            # when the image_dict changes, draw all the frames for that image_dict.
            if image_dict != previous_image:
                log.info(f'Iterating frames {image_dict["start_frame"]} through {image_dict["end_frame"]}')
                for current_frame in range(
                    image_dict["start_frame"], 
                    image_dict["end_frame"] + 1
                ):
                    # by way of masterplan
                    word = self.frame_to_word(current_frame)
                    paragraph_index, fragdex = word["id"].split("_")

                    paragraph_index = int(paragraph_index)
                    fragdex = int(fragdex)

                    if paragraph_index != current_paragraph_index:
                        paragraph = self.soup.find(
                            "paragraph", index=str(paragraph_index)
                        )

                        if paragraph is None:
                            log.error(
                                f"Paragraph {paragraph_index} not found in XML!!  Bad masterplan?"
                            )
                            break

                        paragraph_tags = tools.tags_to_dict(
                            paragraph.attrs.get("tags", "")
                        )
                        current_paragraph_index = paragraph_index

                    # primary filter
                    if current_frame != frame_index:
                        continue

                    log.info('Frame %s found', current_frame)

                    frame_fn = os.path.join(
                        framedir, f"frame_{current_frame:06}.png"
                    )
                    if os.path.exists(frame_fn):
                        log.debug("Frame %s already exists, deleting..", frame_fn)
                        os.unlink(frame_fn)

                    image_dict["paragraph_tags"] = paragraph_tags

                    # camera_frame_adj = os.path.join(
                    #     const.LIBRARY_DIR,
                    #     image_dict["paragraph_dir"],
                    #     "image_frames",
                    #     "image_%06d" % image_dict["index"],
                    #     f"frame_{current_frame:06}.png.adj.png"
                    # )
                    # if os.path.exists(camera_frame_adj):
                    #     # there is an adjusted image frame, use it.
                    #     image_dict["image"] = os.path.join(
                    #         "image_frames",
                    #         "image_%06d" % image_dict["index"],
                    #         f"frame_{current_frame:06}.png.adj.png"
                    #     )
                    #     log.info('Using adjusted camera frame at %s', camera_frame_adj)
                    # else:
                    camera_frame = os.path.join(
                        const.LIBRARY_DIR,
                        image_dict["paragraph_dir"],
                        "image_frames",
                        "image_%06d" % image_dict["index"],
                        f"frame_{current_frame:06}.png"
                    )
                    if os.path.exists(camera_frame):
                        image_dict["image"] = os.path.join(
                            "image_frames",
                            "image_%06d" % image_dict["index"],
                            f"frame_{current_frame:06}.png"
                        )

                    scroll_lock = camera.frame_to_camera(current_frame)

                    args = (
                        self.chapterdir,
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
                        fragdex,
                    )

                    previous_image = image_dict
                    frames.draw_frame(
                        *args,
                        force=force
                    )
                current_frame += 1
            else:
                current_frame += 1

        self.save_xml()

    def get_video_filename(self):
        video_pfn = os.path.join(
            const.LIBRARY_DIR, self.chapterdir.lstrip("/"), self.safe_title() + ".mp4"
        )
        return video_pfn

    def onErr(self, *args, **kwargs):
        stack = inspect.stack()
        for s in stack:
            log.error(s)

        log.error(args[0])
        raise args[0]

    def parse_chapter_title(self, line):
        """
        we know this is a new chapter line, but we want to know the chapter title (if it has one)

        We already know it is whatever format the config has.  We want a pretty
        title string.

        What is the point of this?  It's a place to reach in and adjust.
        """
        clean_line = line.strip().strip("_").replace(".", "")
        if self.config["HAS_NUMBERED_CHAPTERS"]:
            clean_line = clean_line.strip("0123456789").strip("0123456789").strip()
            return clean_line

        elif self.config["HAS_ROMAN_NUMERAL_CHAPTERS"]:
            if "." in clean_line and self.config.get("CHAPTER_TITLE_ON_SAME_LINE", False):
                # CHAPTER XIII. How King Pellinore gat the lady and brought her to
                clean_line = clean_line[clean_line.find("."):]

            clean_line = clean_line.strip("ILVX").strip("ILVX").strip("ILVX").strip()
            return clean_line

        elif self.config["HAS_ALLCAPS_BREAKS"]:
            return clean_line

        elif self.config["HAS_UNPUNCTUATED_SINGLE_LINE"]:
            return clean_line

        return ""

    def indicates_a_new_chapter(self, index, book_lines):
        # line, previous_line, next_line):
        """
        Given config settings, does this line indicate we are starting a new chapter?
        """
        line = book_lines[index]
        previous_line = book_lines[index - 1] if index - 1 >= 0 else ""
        next_line = book_lines[index + 1] if index + 1 < len(book_lines) else ""

        clean_line = line.replace("*", "").strip().strip("_")
        as_list = clean_line.split()
        if not clean_line:
            return False
        
        if self.config.get("BIBLE_BREAKDOWN", False):
            if self.FIRST:
                log.info("Using BIBLE_BREAKDOWN chapter break method")
                self.FIRST = False

            # Each "book" of the bible is a chapter within
            # the system.  In our text file there are multiple
            # ways to differentiate them, I'm going simple.
            
            # two or more blank lines before
            # chapter title
            # two or more blank lines after

            # for the purpose of this function, we just need
            # to respond 'True' or 'False', we cam make it easy:
            if index < 2:
                # skip the first two lines, they are title/subtitle
                return False

            # the two previous lines are not blank
            if [l.strip() for l in book_lines[index - 2:index]] != ["", ""]:
                return False
            
            # the two next lines are not blank
            if [l.strip() for l in book_lines[index + 1:index + 3]] != ["", ""]:
                return False

            return True

        if self.config["HAS_NUMBERED_CHAPTERS"]:
            # 2
            if self.FIRST:
                log.debug("Using HAS_NUMBERED_CHAPTERS chapter break method")
                self.FIRST = False
            try:
                int(as_list[0])
                return True
            except ValueError:
                return False
            except IndexError:
                # empty line
                return False

        elif self.config["HAS_ROMAN_NUMERAL_CHAPTERS"]:
            if self.FIRST:
                log.info("Using HAS_ROMAN_NUMERAL_CHAPTERS chapter break method")
                self.FIRST = False

            # the whole line is a valid roman numeral
            if self.config.get("CALL_THEM_CHAPTERS", False):
                if "CHAPTER" in clean_line.upper():                    
                    clean_line = ireplace("CHAPTER", "", clean_line)
                    if "." in clean_line and self.config.get("CHAPTER_TITLE_ON_SAME_LINE", False):
                        # strip everything after the period
                        clean_line = clean_line[:clean_line.find(".")]

            elif self.config.get("CALL_THEM_BOOKS", False):
                if "BOOK" in clean_line.upper():
                    clean_line = ireplace("BOOK", "", clean_line)

            clean_line = clean_line.strip(".").strip()
            if clean_line:
                return roman.is_roman_numeral(clean_line)
            else:
                # log.info(f"NOT roman numeral: {clean_line}")
                return False

        elif self.config["HAS_ALLCAPS_BREAKS"]:
            if self.FIRST:
                log.debug("Using HAS_ALLCAPS_BREAKS chapter break method")
                self.FIRST = False

            if clean_line:
                return clean_line.upper() == clean_line
            return False

        elif self.config["HAS_UNPUNCTUATED_SINGLE_LINE"]:
            if self.FIRST:
                log.info("Using HAS_UNPUNCTUATED_SINGLE_LINE chapter break method")
                self.FIRST = False
            
            if (
                previous_line.strip() == ""  # preceded by a blank line
            ) and (
                next_line.strip() == ""  # followed by a blank line
            ) and (
                line[0] not in ['“']
            ) and (
                line[0] == line[0].upper()  # starts with a capital letter
            ) and (
                line[-1] not in ["]", ".", "!", "?", ";", ":"]  # does not end with punctuation
            ):
                return True
            
            return False
        
        log.error('Unknown chapter break method in config: "%s"', self.config)
        return False

    def append_chapter(self, chapter_text):
        chapter_dir = os.path.join(
            const.LIBRARY_DIR, self.chapterdir.lstrip("/"), "chapter"
        )

        os.makedirs(chapter_dir, exist_ok=True)
        chapter_dirs = os.listdir(chapter_dir)

        chapter_index = len(chapter_dirs) + 1

        my_chapter_dir = os.path.join(chapter_dir, f"{chapter_index:04}")
        os.makedirs(my_chapter_dir, exist_ok=True)
        mybook_fn = os.path.join(my_chapter_dir, "book.txt")

        with open(mybook_fn, "w", encoding="utf-8") as f:
            f.write(chapter_text)


MAX_IN_MEMORY = 5
PROCESS_IMAGE_CACHE = {}


def get_text_image(text_image_fn):
    if text_image_fn in PROCESS_IMAGE_CACHE:
        return PROCESS_IMAGE_CACHE[text_image_fn]
    else:
        log.info("in-memory text_image cache miss (loading from disk)")
        text_image = Image.open(text_image_fn)

        while (len(PROCESS_IMAGE_CACHE) + 1) >= MAX_IN_MEMORY:
            # remove the first key, which thanks to ordered dicts by default is
            # also the oldest key.
            first_key = list(PROCESS_IMAGE_CACHE.keys())[0]
            del PROCESS_IMAGE_CACHE[first_key]
            log.debug(f"Removed {first_key} from PROCESS_IMAGE_CACHE")

        PROCESS_IMAGE_CACHE[text_image_fn] = text_image
        return text_image


def main():
    log.info("Hello")
    log.info("Goodbye.")


if __name__ == "__main__":
    main()

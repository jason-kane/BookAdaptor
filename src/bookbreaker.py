#!/usr/bin/env python3

import argparse

import neobreaker.const as const
import neobreaker.logger as logger
import book
import neobreaker.resets as resets

print("Initializing BookBreaker...")

DEBUG = const.DEBUG

# the most chapters a book might have
MAX_CHAPTERS = 200

# the most paragraphs a chapter might have
MAX_SEGMENTS = 200

log = logger.log(__name__)

# slow but generally zero cost full rebuild, it will re-use the images and wav.
# this is not what you want unless you're fixing a core engine problem
REGENERATE_ALL_NOT_AI = {
    "verify": True,  # make sure each png we generate is valid
    "redraw_frames": True,  # redraw every frame (slow)
    "replace_missing_frames": True,
    "sweep_old_frames": True,
    "rebuild_preface_mp4": False,  # regenerate preface mp4 files
    "rebuild_prelude_mp4": False,  # regenerate prelude mp4 files
    "rebuild_afterword_mp4": False,  # regenerate afterword mp4 files
    "rebuild_phrase_mp4": True,  # regenerate phrase mp4 files
    "rebuild_chapter_mp4": True,  # regenerate chapter mp4 files
    "rebuild_audio": False,  # get fresh audio files
    "rebuild_text": True,  # regenerate chapter.png
    "disable_voice_generation": False,
}

NEW_AUDIO = REGENERATE_ALL_NOT_AI.copy()
NEW_AUDIO["rebuild_audio"] = True


POLISH = {
    "verify": False,  # make sure each png we generate is valid
    "redraw_frames": False,  # redraw every frame (slow)
    "replace_missing_frames": True,
    "sweep_old_frames": False,
    "rebuild_preface_mp4": False,  # regenerate preface mp4 files
    "rebuild_prelude_mp4": False,  # regenerate prelude mp4 files
    "rebuild_afterword_mp4": False,  # regenerate afterword mp4 files
    "rebuild_phrase_mp4": False,  # regenerate phrase mp4 files
    "rebuild_chapter_mp4": False,  # regenerate chapter mp4 files
    "rebuild_audio": False,  # get fresh audio files
    "disable_voice_generation": False,
    # 'STOP_AFTER_SEGMENT': 4,
}


def bookbreaker(bookdir):
    log.info('bookbreaker("{}", {})'.format(bookdir, const.DEBUG))
    # We have a book, we want an audio/video/reading experience.

    # We will be word-for-word true to the book and the structure created by the author.

    # How each book is broken down depends on the book.  The hinters to treat a
    # book a particular way are in the config.json which is crafted to suit each
    # book.  Regardless of what they actually are, the content feeding a
    # particular video is called a chapter
    #
    #
    #
    # Current Issues:
    #  - you have to run with regenerate twice or you'll get jumpy text
    #
    # Fnord is the future.  Finish generating wizard of oz, then we move to
    # fnord. what book should we use for fnord?  Alice?  Quixote?
    #
    # is there a fnord shortcut?  can I use it to place images and text in the
    # right places, then use bookbreaker/iterator to build the thing?

    # does it make sense outside debug to cache both the frames and the video?
    # Can't we discard the frames when the video is complete?  Why?  GB.  Many.
    # bold.

    # regenerate preface mp4 files, this is title, chapter and translator
    POLISH["rebuild_preface_mp4"] = False

    # regenerate prelude mp4 files, any pre-first chapter introductory material that is short and interesting.
    POLISH["rebuild_prelude_mp4"] = False

    # Trailing messages, chapter x of y, like and subscribe, etc..
    POLISH["rebuild_afterword_mp4"] = False

    log.info(f"Checking {bookdir}")
    b = book.Book(bookdir)

    b.PICKDIR = False

    # when reset is true we:
    #     re-assemble the video mp4 if it doesn't exist
    #     rebuilt & render latex side-text if it is missing
    #     create image.png if it is missing
    #     re-render all the frames based on cached base image.png

    # when reset is NOT true:
    #     assemble new mp4 if there isn't one
    #     re-render if image.png does not exist.

    ONE_CHAPTER = None
    START_CHAPTER = None
    END_CHAPTER = None
    # 20

    ONE_SEGMENT = None
    START_SEGMENT = None
    END_SEGMENT = None

    # inclusive
    # START_CHAPTER = 16
    # END_CHAPTER = 24

    one_video_per = "chapter"
    # one_video_per = "book"

    #
    #
    #
    #
    #
    #
    # high level, we make a Book(), then call chapter_to_video on it to get a
    # video for each chapter..

    # Triggers a deep instead of shallow regen of any cached objects.
    # typically this means going back to an image or tts AI service.

    # Costs some time; regenerate the latex files, pdf and reassembled png for
    # the scrolling text side of the screen
    b.RESET_TEXT = False

    # $$  Regenerate text-to-speech instead of using whatever we have cached.
    b.RESET_AUDIO = False

    # $$$  Regenerate images in each section instead of using cached originals.
    b.RESET_PREFACE_IMAGES = False
    b.RESET_SCROLLING_IMAGES = False
    b.RESET_POSTFIX_IMAGES = False

    # using POLISH and phrase RESETS to only build specific pieces of the video.
    if resets.any:
        log.info("Determining resets...")
        if ONE_CHAPTER:
            log.info(f"Single chapter: {ONE_CHAPTER}")
            START_CHAPTER = ONE_CHAPTER
            END_CHAPTER = ONE_CHAPTER

        if START_CHAPTER is None:
            START_CHAPTER = 1

        if END_CHAPTER is None:
            END_CHAPTER = MAX_CHAPTERS

        log.info(f"Resetting chapters {START_CHAPTER} through {END_CHAPTER}")
        for chapter_index in range(START_CHAPTER, END_CHAPTER + 1):
            if ONE_SEGMENT:
                log.info(f"Single segment: {ONE_SEGMENT}")
                START_SEGMENT = ONE_SEGMENT
                END_SEGMENT = ONE_SEGMENT

            if START_SEGMENT is None:
                START_SEGMENT = 0

            if END_SEGMENT is None:
                END_SEGMENT = MAX_SEGMENTS

            if START_SEGMENT is not None and END_SEGMENT is not None:
                log.info(f"Resetting segments {START_SEGMENT} through {END_SEGMENT}")
                for segment_index in range(START_SEGMENT, END_SEGMENT + 1):
                    b.RESETS.append(f"{chapter_index:02}_{segment_index:02}")

    log.info(f"Resets: {b.RESETS}")

    b.IMAGE_REPLACE = []

    # pickdir is easier
    # b.IMAGE_REPLACE = [
    # ]

    # b.RESETS += [
    #    '01_03',
    # ]

    # obsolete
    b.RESET_ALL_AFTER_SEGMENT = 999
    # None

    # TODO: progress bars should be per-core (ie: based on the multiprocessing
    # worker) and thread, organized nicely that way.  Can we use RICH to split
    # screen the logging and the progress bars?
    #
    #
    # We need to break this book up chapters. Each chapter gets turned into its
    # own video. what that really _means_ depends on the book and the type of
    # content.  I like having one poem per video for example, so I make each
    # poem its own chapter.  Because it is all in one video you want it to be
    # coherent as an isolated segment.

    # make a video for each chapter
    # starting at one for human reasons
    count = 1
    # skip = [ ]
    # one_video_per = book.config.get("ONE_VIDEO_PER", "chapter")

    if one_video_per == "book":
        # we want one video for the whole book.
        log.info("Building a book video")
        # this sorta works now.
        b.book_to_video()
        log.info("book_to_video complete")

    elif one_video_per == "chapter":
        log.info("Building one video for each chapter")
        all_chapters = list(b.as_chapters())
        log.info(f"Chapters: {all_chapters}")
        for chapter in all_chapters:
            log.info(f"Working on chapter {chapter}")
            if ONE_CHAPTER and count != ONE_CHAPTER:  # the human reason
                log.info(f"A Skipping chapter {chapter} ({count}!={ONE_CHAPTER})")
                count += 1
                continue
            else:
                log.info(f"{count=} == {ONE_CHAPTER=}")

            if START_CHAPTER and count < START_CHAPTER:
                count += 1
                log.info(f"B Skipping chapter {chapter}")
                continue
            else:
                log.info(f"{count=} >= {START_CHAPTER=}")

            if END_CHAPTER and count > END_CHAPTER:
                count += 1
                log.info(f"C Skipping chapter {chapter}")
                continue
            else:
                log.info(f"{count=} < {END_CHAPTER=}")

            log.info(f"Working on chapter {chapter}")
            b.chapter_to_video(chapter)

            chapter.save_xml()

            count += 1

        # this processes all the rest of the chapters, which triggers shit.
        # b.save_xml()
        log.info(f"{count-1} chapters processed")

    b.save_xml()

def main():
    parser = argparse.ArgumentParser(
        prog="BookBreaker",
        description="Breaks a book into chunks suitable for AI presentation",
        epilog="Copyright 2024  Jason Kane  All Rights Reserved",
    )
    parser.add_argument(
        "bookdir", help="Directory (rel or abs) on the shelf for this book"
    )
    args = parser.parse_args()

    bookbreaker(args.bookdir)


main()

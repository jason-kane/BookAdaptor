import fcntl
import io
import numpy as np
import os
from ascii_magic import AsciiArt
import redis
from PIL import Image

import const
import logger
from artifact_editor.chapter import chapter as chapter_lib
from artifact_editor.tools import tags_to_dict
from artifact_editor.typography import typography

log = logger.log(__name__)


SEGMENT_HEIGHT = 2048  # pixels

# class Chapter:
#     def __init__(self, chapterdir, aspect):
#         self.chapterdir = chapterdir
#         self.book = booklib.get_book(chapterdir)
#         self.soup = self.book.soup
#         self.aspect = aspect

#         self.config = self.get_config()
#         self.key = self.config.get('title') + "_" + self.config.get('chapter_title') + "_" + self.aspect
#         self.structure = self.config.get('TEXT_STRUCTURE', 'novel')

#     def get_config(self):
#         return config.get_config(self.chapterdir)

#     def phrases(self):
#         return self.soup.find_all('phrase')

#     def get_bottom_height(self, force=False):
#         all_phrases = self.phrases()
#         last_phrase = Phrase(all_phrases[-1], self.aspect)
#         last_phrase_dimensions = last_phrase.get_highlight_dimensions(force=force)
#         return last_phrase_dimensions['bottom']

#     def get_text_height(self, force=False):
#         all_phrases = self.phrases()
        
#         # first_phrase = Phrase(all_phrases[0])
#         last_phrase = Phrase(all_phrases[-1])

#         total_height = 0

#         # find the top of the first phrase _with_ text.
#         for phrase in all_phrases:
#             paragraph = phrase.find_parent('paragraph')
#             paragraph_tags = tags_to_dict(paragraph.attrs.get("tags", ""))
#             if not paragraph_tags.get("has-text", True):
#                 log.info('Skipping paragraph with no text highlight...')
#                 continue

#             first_phrase = Phrase(phrase)
#             break

#         first_phrase_dimensions = first_phrase.get_highlight_dimensions(force=force)
#         last_phrase_dimensions = last_phrase.get_highlight_dimensions(force=force)

#         bottom = last_phrase_dimensions['bottom']
#         top = first_phrase_dimensions['top']

#         total_height = bottom - top
#         log.info(f'Chapter {self.key} text height is {bottom} - {top} = {total_height}')

#         self.book.save_xml()  # save any cached dimensions
#         return total_height


def draw_text_layers(
    chapter, 
    phrase_xml,
    force=False,
    rainbow=False,
):
    """
    Xml -> Latex -> PNG for each highlighted phrase in the chapter
    
    output are text_layer*.png files each representing the entire chapter
    """
    log.info(f"draw_text_layers({chapter=}, {phrase_xml=}, {force=})")
    parser_class = {
        'novel': typography.NovelToLatex,
        'verse': typography.VerseToLatex,
        'play': typography.PlayToLatex,
        'scripture': typography.ScriptureToLatex
    }[chapter.structure]

    log.debug('Using parser class %s', parser_class)
    parser = parser_class(chapter=chapter)

    # write_to_latex gives us one or more image files in paragraph_dir.  They
    # will be named either "text_layer.png" or "text_layer_0.png", etc.
    
    # we're relying on caching inside write_to_latex to keep this from being
    # pathologically slow.
    if phrase_xml:
        paragraph = phrase_xml.find_parent('paragraph')
    
        paragraph_tags = tags_to_dict(
            paragraph.attrs.get("tags", "")
        )
        if paragraph_tags.get("has-text", True):
            # So award for shittiest function name goes to.. me!
            # actual function output is either:
            # 
            # one "text_layer.png"
            # OR
            # text_layer-0.png, text_layer-1.png, etc..
            # force here is _rough_, so maybe only force the first
            # iteration or you're worst-caseing the cache.
            parser.write_as_latex(
                paragraph_dir=chapter.get_paragraph_dir(paragraph.attrs['index']),
                highlight_phrase=phrase_xml,
                highlight_paragraph=False,
                force=force
            )
        else:
            log.info(f"Skipping text layer generation for paragraph {paragraph.attrs.get('id', 'unknown')} because it is not displayed.")
    elif rainbow:
        # one with nothing highlighted
        parser.write_as_latex(
            paragraph_dir=None,
            highlight_phrase=False,
            force=force
        )

        # one with every phrase highlighted
        parser.write_as_latex(
            paragraph_dir=None,
            highlight_phrase=None,
            force=force,
            rainbow=True
        )


def SixtyKCrop(fn: str) -> Image.Image:
    im = Image.open(fn)
    try:
        im = im.convert("RGBA")
    except SyntaxError as e:
        log.error('Error converting image to RGBA', error=e, filename=fn)
        raise

    width, height = im.size
    log.info(f'Opened image size {width}x{height}')
    if height == 60000:
        log.info('Image height is 60000, cropping to fit the text...')
        # this image hasn't been vertically cropped to fit the text yet.
        with open(fn + ".lock", "w") as lockf:

            # acquire the lock, otherwise we get races and corrupted images.
            fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
            
            # make sure it's still the wrong size
            im = Image.open(fn)
            width, height = im.size
            if height == 60000:
                # before we do that.. we need to crop vertically.
                crop_box = im.getbbox()

                if crop_box:
                    im = im.crop((0, crop_box[1], im.width, crop_box[3]))
                    width, height = im.size
                    if height == 60000:
                        log.error("You've screwed it up now.  Expect weird whitespace gaps at 60000 pixel intervals.  Moron.")    
                    log.info('Saving cropped image: %s', fn)
                    im.save(fn)
                else:
                    log.error('Image has no text, good luck.')
            
            # release the lock
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)

    return im


def retrieve_segment_from_book_old(
        chapter,
        phrase_xml, 
        top_index: int, 
        bottom_index: int,
        force: bool = False
    ) -> bytes:
    """
    returns a raw binary string of a PNG image, it's a black text on transparent
    for a specific segment of the book.  This is cached in redis so I don't care
    if it is slow (It will be)

    phrase is a BeautifulSoup object for the phrase we want to highlight.
    phrase must have an index attribute

    It must have a parent, paragraph, which also has an index attribute.
    """
    log.info(
        "retrieve_segment_from_book", 
        chapter=chapter, 
        phrase_xml=phrase_xml, 
        top_index=top_index, 
        bottom_index=bottom_index, 
        force=force
    )

    G = const.GEOMETRY[chapter.aspect]

    paragraph = phrase_xml.find_parent('paragraph')
    
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs['index'])

    phrase_id = int(phrase_xml.attrs['index'])

    # if any of these images exist they all ought to exist.
    # but they could be corrupted, this is lazy.
    # if not glob.glob(os.path.join(
    #     paragraph_dir, 
    #     chapter.aspect, 
    #     f"text_layer_{phrase_id}-*.png")
    # ):
    
    # No dumbass, we just make sure inside draw_text_layers has a fast path for
    # already existing images.
    
    # It's stupid, but this is responsible for making sure the text image files exist
    # for this content, generating them if necessary.
    draw_text_layers(chapter, phrase_xml, force=force)

    # we used to use parser.combine_text_layers(), but that doesn't quite do
    # what we want.
    
    # go throught the images on disk to find the segment that 
    # contains our top_index to bottom_index range.  The heights of these
    # are not predictable, so we add them up until we find
    # the right one.
    images = list(chapter_lib.phrase_images(
        paragraph_dir, chapter.aspect, phrase_id
    ))
    log.info('images: %s', images)
    
    canvas = Image.new(
        'RGBA',
        (
            G["TEXT_WIDTH"],
            bottom_index - top_index
        ),
        (0, 0, 0, 0)
    )
    canvas_consumed = 0

    # each image is a vertical stack of text.  The heights vary because we
    # try very hard to make sure the pages do not split paragraphs.  that
    # makes the vertical alignment much easier to handle since page breaks
    # are also paragraph breaks.

    if len(images) == 1:
        # there is only one image, this is easy.
        # extract the requested rectangle and return it.
        im = SixtyKCrop(images[0])  # make sure it's cropped properly
        cropped = im.crop((
            0,
            top_index,
            G["TEXT_WIDTH"],
            bottom_index
        ))
        canvas.paste(cropped, (0, 0))
        # canvas_consumed
        images = []

        # try:
        #     fn = images.pop(0)
        # except IndexError:
        #     log.info('No more images to process.')
        #     break

    total_height = 0
    for fn in images:
        # load the image, do any preliminary prep.
        im = SixtyKCrop(fn)

        if total_height + im.height < top_index:
            # this entire image is before our range, skip it.
            log.info(f'Skipping image {fn}, total_height {total_height} + im.height {im.height} < top_index {top_index}')
            total_height += im.height
            continue

        # okay, we have an image at or after the top of our range.
        # Three possibilities.  This is the beginning, middle, or end of our
        # range.

        # Beginning
        if total_height < top_index:
            # that total_height + im.height is >= top_index is already
            # guaranteed by the outer if statement.  So when total_height needs
            # the pixels from im.height to reach top_index, we know we're
            # containing the beginning of the range.
            
            # crop it out and paste it into canvas at 0,0
            log.info(f'Image {fn} contains the beginning of our range.')
            cropped = im.crop(
                (
                    0, 
                    top_index - total_height, 
                    G["TEXT_WIDTH"],
                    min(im.height, bottom_index - total_height)
                )
            )
            canvas.paste(cropped, (0, 0))
            canvas_consumed += cropped.height
            total_height += im.height
        
        # Middle
        elif total_height + im.height <= bottom_index:
            # the entire image is inside our range, we want all of it.
            # this is also something that doesn't happen with current
            # settings.
            log.info(f'Image {fn} is entirely inside our range.')
            canvas.paste(im, (0, canvas_consumed))
            canvas_consumed += im.height
            total_height += im.height

        # End
        else:
            # this image contains the end of our range.  If it also contained
            # the beginning we would have already handled it, so we get the
            # simple case.
            log.info(f'Image {fn} contains the end of our range.')
            cropped = im.crop(
                (
                    0, 
                    0, 
                    G["TEXT_WIDTH"],
                    bottom_index - total_height
                )
            )
            canvas.paste(cropped, (0, canvas_consumed))
            canvas_consumed += cropped.height
            total_height += im.height
            break

        assert canvas_consumed == canvas.height, f'Canvas consumption mismatch: {canvas_consumed} != {canvas.height}'

        log.info('Examining image %s...', fn)
        im = Image.open(fn)
        # this algorithm is close but wrong 
        
        # total_height is the accumulator for how many pixels we've already
        # stacked up.

        # im.height is the height of this latest image we're adding to the stack.
        
        # top_index is the beginning of the segment we actually want.
        if total_height + im.height >= top_index:
            # there are some pixels in im that we want.  But which ones?
            if total_height + im.height <= bottom_index:
                # all of it.  Both the top _and_ bottom of this text image is
                # inside our range.  This doesn't actually happen.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        im.height
                    )
                )
                canvas.paste(cropped, (0, 0))
                break

            while total_height + im.height > bottom_index:
                # we need to only take part of this image.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        min(im.height, bottom_index - total_height)
                    )
                )
                canvas.paste(
                    cropped, (0, 0)
                )

            else:
                # only part of it.  The top is inside our range, but the bottom
                # extends past our range.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        im.height
                    )
                )
                canvas.paste(cropped, (0, 0))
                
                # we need the next image to complete the segment.
                total_height += im.height

            # bottom = bottom_index - total_height
            # if bottom > im.height:
            #     bottom = im.height

            # cropped = im.crop(
            #     (
            #         0, top_index - total_height, 
            #         const.TEXT_WIDTH, bottom
            #     )
            # )
            
            PADDING = 65
            
            if total_height + im.height < bottom_index:
                # we need to pad the bottom with transparency
                try:
                    bottom_image_fn = images.pop(0)
                    bottom_image = Image.open(bottom_image_fn)
                except IndexError:
                    log.info('No more images to process.')
                    break

                canvas.paste(cropped, (0, 0))
                log.info('Pasting bottom_image at %s', PADDING + (im.height - (top_index - total_height)))
                canvas.paste(
                    bottom_image,
                    (
                        0,
                        PADDING + (
                            im.height - (top_index - total_height)
                        )
                    )
                )
            
            byte_io = io.BytesIO()
            canvas.save(byte_io, 'PNG')
            byte_io.seek(0)
            return byte_io.read()
        else:
            log.info(f'We are at {total_height}, we need to reach {top_index=}')
            log.info(f'Adding {im.height=} to total_height {total_height=}')
            total_height += im.height

    # okay, lets talk about what just happened. things were great, we were
    # cruising through, but I guess total_height + im.height never quite made it
    # to top_index.  I'm not judging, but this is unexpected.
    #
    # I'll log some hard numbers, that should make it easier to see.
    #
    # top_index = 61500
    # bottom_index = 61600

    # if total_height + im.height >= top_index:
    log.warning(
        'Cache miss: Failed to find segment from %s to %s, total_height only reached %s',
        top_index, bottom_index, total_height
    )

    # duh, it's just falling off the bottom, give them
    # as many blank pixels as they want.
    # im = Image.new(
    #     'RGBA',
    #     (
    #         const.TEXT_WIDTH,
    #         bottom_index - top_index
    #     ),
    #     (0, 0, 0, 0)
    # )
    byte_io = io.BytesIO()
    canvas.save(byte_io, 'PNG')
    byte_io.seek(0)
    return byte_io.read()



def retrieve_segment_from_book(
        chapter,
        phrase_xml, 
        top_index: int, 
        bottom_index: int,
        force: bool = False
    ) -> bytes:
    """
    returns a raw binary string of a PNG image, it's a black text on transparent
    for a specific segment of the book.  This is cached in redis so I don't care
    if it is slow (It will be)

    phrase is a BeautifulSoup object for the phrase we want to highlight.
    phrase must have an index attribute

    It must have a parent, paragraph, which also has an index attribute.
    """
    log.info(
        "retrieve_segment_from_book", 
        chapter=chapter, 
        phrase_xml=phrase_xml, 
        top_index=top_index, 
        bottom_index=bottom_index, 
        force=force
    )

    G = const.GEOMETRY[chapter.aspect]

    paragraph = phrase_xml.find_parent('paragraph')
    paragraph_dir = chapter.get_paragraph_dir(paragraph.attrs['index'])
    phrase_index = int(phrase_xml.attrs['index'])

    # this is fun.
    r, g, b = chapter.index_to_highlight_color(phrase_index)
    text_layer_rainbow_fn = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "text_layer_rainbow.png")

    # Load the rainbow file
    with open(text_layer_rainbow_fn, "rb") as h:
        rainbow_img = Image.open(h)
        rainbow_img = rainbow_img.convert("RGBA")

    # (the clever bit)
    # filter out all the parts of the image that aren't
    # this phrase.  This gives us a mask for the exact
    # geometry of _this_ phrase.
    data = np.array(rainbow_img)
    highlight_mask = (data[:,:,0] == r) & (data[:,:,1] == g) & (data[:,:,2] == b)

    # get the bounding box of the highlight mask
    coordinates = np.argwhere(highlight_mask)
    if coordinates.size == 0:
        log.warning("No highlight region found for phrase with color ({r}, {g}, {b})", phrase_index=phrase_xml.attrs['index'], r=r, g=g, b=b)
    
    # now load up the _real_ text layer image.
    with open(
        os.path.join(
            const.LIBRARY_DIR, 
            chapter.chapterdir, 
            "text_layer_plain.png"
        ), "rb"
    ) as h:
        img = Image.open(h)
        img = img.convert("RGBA")

    # white canvas
    # NO - infinity roll canvas.
    # white_bg = Image.new("RGBA", img.size, "WHITE")
    paper_background = typography.infinitePaperRoll(
        width=img.size[0],
        min_height=G['TEXT_HEIGHT'],
        offset=top_index,
        aspect=chapter.aspect
    )
    log.info(f'Generated paper background of size {paper_background.size} for image of size {img.size} at offset {top_index}')

    # create the correct paper _background_ in the
    # right region of img_copy
    img_copy = img.copy()
    img_copy.paste(
        paper_background, 
        (0, top_index)
    )

    # # crop to same height as highlight mask
    # paper_background = paper_background.crop(
    #     (
    #         0, 0, 
    #         img.size[0], img.size[1]
    #     )
    # ).convert("RGBA")

    # every pixel in highlight_mask that is (r,g,b) should be a yellow pixel in img.
    img_data = np.array(img_copy)
    img_data[highlight_mask] = [255, 255, 0, 255]
    img_copy = Image.fromarray(img_data)

    # re-draw the dark text on the paper background       
    img = Image.alpha_composite(img_copy, img)

    text_height = 1080 * 5

    # crop, based on top_index.
    img.save('/output/debug_image.png')

    img = img.crop(
        (
            0, top_index, 
            img.size[0], top_index + text_height
        )
    )

    log.info(
        'Cropping text image', 
        top_index=top_index, 
        bottom_index=top_index + text_height
    )

    # what, returning a PIL image is too street trash for you?
    byte_io = io.BytesIO()
    img.save(byte_io, 'PNG')
    byte_io.seek(0)
    return byte_io.read()

    # canvas = Image.new(
    #     'RGBA',
    #     (
    #         G["TEXT_WIDTH"],
    #         bottom_index - top_index
    #     ),
    #     (0, 0, 0, 0)
    # )
    # canvas_consumed = 0

    # each image is a vertical stack of text.  The heights vary because we
    # try very hard to make sure the pages do not split paragraphs.  that
    # makes the vertical alignment much easier to handle since page breaks
    # are also paragraph breaks.

    if len(images) == 1:
        # there is only one image, this is easy.
        # extract the requested rectangle and return it.
        im = SixtyKCrop(images[0])  # make sure it's cropped properly
        cropped = im.crop((
            0,
            top_index,
            G["TEXT_WIDTH"],
            bottom_index
        ))
        canvas.paste(cropped, (0, 0))
        # canvas_consumed
        images = []

        # try:
        #     fn = images.pop(0)
        # except IndexError:
        #     log.info('No more images to process.')
        #     break

    total_height = 0
    for fn in images:
        # load the image, do any preliminary prep.
        im = SixtyKCrop(fn)

        if total_height + im.height < top_index:
            # this entire image is before our range, skip it.
            log.info(f'Skipping image {fn}, total_height {total_height} + im.height {im.height} < top_index {top_index}')
            total_height += im.height
            continue

        # okay, we have an image at or after the top of our range.
        # Three possibilities.  This is the beginning, middle, or end of our
        # range.

        # Beginning
        if total_height < top_index:
            # that total_height + im.height is >= top_index is already
            # guaranteed by the outer if statement.  So when total_height needs
            # the pixels from im.height to reach top_index, we know we're
            # containing the beginning of the range.
            
            # crop it out and paste it into canvas at 0,0
            log.info(f'Image {fn} contains the beginning of our range.')
            cropped = im.crop(
                (
                    0, 
                    top_index - total_height, 
                    G["TEXT_WIDTH"],
                    min(im.height, bottom_index - total_height)
                )
            )
            canvas.paste(cropped, (0, 0))
            canvas_consumed += cropped.height
            total_height += im.height
        
        # Middle
        elif total_height + im.height <= bottom_index:
            # the entire image is inside our range, we want all of it.
            # this is also something that doesn't happen with current
            # settings.
            log.info(f'Image {fn} is entirely inside our range.')
            canvas.paste(im, (0, canvas_consumed))
            canvas_consumed += im.height
            total_height += im.height

        # End
        else:
            # this image contains the end of our range.  If it also contained
            # the beginning we would have already handled it, so we get the
            # simple case.
            log.info(f'Image {fn} contains the end of our range.')
            cropped = im.crop(
                (
                    0, 
                    0, 
                    G["TEXT_WIDTH"],
                    bottom_index - total_height
                )
            )
            canvas.paste(cropped, (0, canvas_consumed))
            canvas_consumed += cropped.height
            total_height += im.height
            break

        assert canvas_consumed == canvas.height, f'Canvas consumption mismatch: {canvas_consumed} != {canvas.height}'

        log.info('Examining image %s...', fn)
        im = Image.open(fn)
        # this algorithm is close but wrong 
        
        # total_height is the accumulator for how many pixels we've already
        # stacked up.

        # im.height is the height of this latest image we're adding to the stack.
        
        # top_index is the beginning of the segment we actually want.
        if total_height + im.height >= top_index:
            # there are some pixels in im that we want.  But which ones?
            if total_height + im.height <= bottom_index:
                # all of it.  Both the top _and_ bottom of this text image is
                # inside our range.  This doesn't actually happen.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        im.height
                    )
                )
                canvas.paste(cropped, (0, 0))
                break

            while total_height + im.height > bottom_index:
                # we need to only take part of this image.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        min(im.height, bottom_index - total_height)
                    )
                )
                canvas.paste(
                    cropped, (0, 0)
                )

            else:
                # only part of it.  The top is inside our range, but the bottom
                # extends past our range.
                cropped = im.crop(
                    (
                        0, 
                        top_index - total_height, 
                        G["TEXT_WIDTH"],
                        im.height
                    )
                )
                canvas.paste(cropped, (0, 0))
                
                # we need the next image to complete the segment.
                total_height += im.height

            # bottom = bottom_index - total_height
            # if bottom > im.height:
            #     bottom = im.height

            # cropped = im.crop(
            #     (
            #         0, top_index - total_height, 
            #         const.TEXT_WIDTH, bottom
            #     )
            # )
            
            PADDING = 65
            
            if total_height + im.height < bottom_index:
                # we need to pad the bottom with transparency
                try:
                    bottom_image_fn = images.pop(0)
                    bottom_image = Image.open(bottom_image_fn)
                except IndexError:
                    log.info('No more images to process.')
                    break

                canvas.paste(cropped, (0, 0))
                log.info('Pasting bottom_image at %s', PADDING + (im.height - (top_index - total_height)))
                canvas.paste(
                    bottom_image,
                    (
                        0,
                        PADDING + (
                            im.height - (top_index - total_height)
                        )
                    )
                )
            
            byte_io = io.BytesIO()
            canvas.save(byte_io, 'PNG')
            byte_io.seek(0)
            return byte_io.read()
        else:
            log.info(f'We are at {total_height}, we need to reach {top_index=}')
            log.info(f'Adding {im.height=} to total_height {total_height=}')
            total_height += im.height

    # okay, lets talk about what just happened. things were great, we were
    # cruising through, but I guess total_height + im.height never quite made it
    # to top_index.  I'm not judging, but this is unexpected.
    #
    # I'll log some hard numbers, that should make it easier to see.
    #
    # top_index = 61500
    # bottom_index = 61600

    # if total_height + im.height >= top_index:
    log.warning(
        'Cache miss: Failed to find segment from %s to %s, total_height only reached %s',
        top_index, bottom_index, total_height
    )

    # duh, it's just falling off the bottom, give them
    # as many blank pixels as they want.
    # im = Image.new(
    #     'RGBA',
    #     (
    #         const.TEXT_WIDTH,
    #         bottom_index - top_index
    #     ),
    #     (0, 0, 0, 0)
    # )
    byte_io = io.BytesIO()
    canvas.save(byte_io, 'PNG')
    byte_io.seek(0)
    return byte_io.read()




def from_offset(
    chapter,
    phrase_xml,
    top_index: int,   # scroll_lock
    force=False,
    no_background=False,
) -> Image.Image:
    """
    retrieve a finished page segment.  A page segment is a black on transparent
    PNG reflecting one TEXT_HEIGHT of text starting at (absolute) top_index laid
    over a paper textured background.

    The width is based on aspect.

    return value is a PIL Image object.
    """
    log.info(f'from_offset({chapter=}, {phrase_xml=}, {top_index=}, {chapter.aspect=})')

    # Connect to local Redis.  It holds SEGMENT_HEIGHT tall PNG image segments for the 
    # text side of the screen.  get_segment abstracts that and lets you
    # get a TEXT_HEIGHT tall image at any pixel offset (top_index).
    redis_cache = redis.Redis(host="redis")

    # The cache holds SEGMENT_HEIGHT tall segments.
    # so we're going to get more than we need, then use
    # PIL to construct the exact image we want.
    top_index = int(top_index)
    G = const.GEOMETRY[chapter.aspect]
    bottom_index = top_index + G['TEXT_HEIGHT']

    adjusted_top = (top_index // SEGMENT_HEIGHT) * SEGMENT_HEIGHT
    adjusted_bottom = (bottom_index // SEGMENT_HEIGHT + 1) * SEGMENT_HEIGHT
    log.info('from_offset alignment', adjusted_top=adjusted_top, adjusted_bottom=adjusted_bottom)

    # Blank slate canvas to work up on.
    text_image = Image.new(
        'RGBA', 
        (
            G['TEXT_WIDTH'],
            bottom_index - top_index
        ), 
        (0, 0, 0, 0)
    )

    # log.info(f'To build image at offset {top_index} we are pasting segments from {adjusted_top} to {adjusted_bottom}')

    for segment_top in range(adjusted_top, adjusted_bottom, SEGMENT_HEIGHT):
        cache_key = f"segment:{chapter.key}:{phrase_xml.attrs['index']}:{segment_top}:{segment_top + SEGMENT_HEIGHT}"

        image_segment = redis_cache.get(cache_key)
        if force:
            #bypass cache
            image_segment = None

        if image_segment is None:
            log.info('Cache miss. Segment not found in cache with key', cache_key=cache_key, force=force)

            try:
                image_segment = retrieve_segment_from_book(
                    chapter,
                    phrase_xml,
                    segment_top,
                    segment_top + SEGMENT_HEIGHT,
                    force=force
                )
            except OSError as e:
                log.error(f'Error retrieving segment from book: {e}')
                # say please.
                image_segment = retrieve_segment_from_book(
                    chapter,
                    phrase_xml,
                    segment_top,
                    segment_top + SEGMENT_HEIGHT,
                    force=True
                )

            try:
                log.info('Saving segment %s to cache', cache_key)
                redis_cache.set(cache_key, image_segment)
            except Exception as e:
                log.error(f'Error saving segment {image_segment} to cache: %s', e)
                log.error('Segment key was: %s', cache_key)
        
                try:
                    pil_segment = Image.open(io.BytesIO(image_segment))
                    pil_segment.verify()
                except Exception as verif_error:
                    log.error('Segment image verification failed: %s', verif_error)

                redis_cache.delete(cache_key)  # don't keep bad data
                raise
        else:
            log.info('Cache hit. Found segment in cache with key', cache_key=cache_key, force=force)

        pil_segment = Image.open(io.BytesIO(image_segment))

        log.info('Cropping segment to %s thru %s',
            max(0, top_index - segment_top),
            min(SEGMENT_HEIGHT, bottom_index - segment_top)
        )

        cropped = pil_segment.crop((
            0, max(0, top_index - segment_top),
            G['TEXT_WIDTH'],
            min(SEGMENT_HEIGHT, bottom_index - segment_top)
        ))

        log.debug('Pasting cropped segment %s into text image at %s',
            cropped.size,
            max(0, segment_top - top_index)
        )
        # cropped.save(f'/tmp/segment_{segment_top}.png')

        log.info('pasting segment from %s to %s into text_image at offset %s',
            max(0, top_index - segment_top),
            min(SEGMENT_HEIGHT, bottom_index - segment_top),
            max(0, segment_top - top_index)
        )
        text_image.paste(
            cropped,
            (0, max(0, segment_top - top_index))
        )
    
    log.info('Finished pasting segments into text image.')
    if no_background:
        return text_image
    
    log.info('Applying background to text image...')
    finished_image = typography.apply_background(
        text_image,
        top_index,
        aspect=chapter.aspect
    )

    log.info(f'Returning {finished_image=}')

    # this is gonna be.. stupid.    
    # my_art = AsciiArt.from_pillow_image(finished_image)
    # my_art.to_terminal(columns=40, monochrome=False)

    #     chapter,
    #     phrase_xml,
    #     top_index: int,   # scroll_lock
    #     force=False,
    #     no_background=False,
    # ) -> Image.Image:
    return finished_image


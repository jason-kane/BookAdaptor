import glob
import json
import os
import binascii
import re
import shutil
import subprocess
from functools import lru_cache

from PIL import (
    Image,
    ImageChops,
)

import const
import logger
from artifact_editor import (
    config,
)
from artifact_editor.tools import (
    get_chapterdir,
    tags_to_dict,
)

log = logger.log(__name__)

Image.MAX_IMAGE_PIXELS = 933120000
FIRST_PAGE_TOP_MARGIN = 150
PAPER_HEIGHT = 200  # inches
HIGHLIGHT_COMMAND = r"\highLight"


def get_chapter_tex(chapter, aspect):
    # where it should be
    override_latex_path = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        f"chapter_override.tex"
    )

    if os.path.exists(override_latex_path):
        return override_latex_path

    latex_path = os.path.join(
        const.LIBRARY_DIR,
        chapter.chapterdir,
        "text_highlighted_plain.tex"
    )

    if not os.path.exists(latex_path):
        log.info('%s does not exist, looking for highlighted_*.tex files...', latex_path)
        # where it might be
        paragraphs_dir = os.path.join(const.LIBRARY_DIR, chapter.chapterdir, "paragraphs")
        # You can get here without paragraphs directories existing.
        os.makedirs(paragraphs_dir, exist_ok=True)

        for paragraph_name in sorted(os.listdir(paragraphs_dir)):
            paragraph_dir = os.path.join(paragraphs_dir, paragraph_name)
            log.info('Checking paragraph dir %s...', paragraph_dir)
            
            aspect_dir = os.path.join(paragraph_dir, aspect)
            if not os.path.exists(aspect_dir):
                continue

            for sample in sorted(glob.glob(os.path.join(aspect_dir, "text_highlighted_*.tex"))):
                log.info('Choosing %s as source for %s', sample, latex_path)
                shutil.copy(
                    os.path.join(aspect_dir, sample),
                    latex_path
                )
                return latex_path
            
    return latex_path


def _cached_get_filename(paragraph_dir, fragdex, name, template="{name}_{fragdex}{ext}"):
    name, ext = os.path.splitext(name)
    if ext in [".png", ".tex"]:
        # binary treatment
        pass
    else:
        ext = ".json"

    return os.path.join(
        const.LIBRARY_DIR, 
        paragraph_dir, 
        template.format(
            name=name,
            fragdex=fragdex,
            ext=ext
        )
    ), ext


def cached(paragraph_dir, fragdex, name, template="{name}_{fragdex}{ext}"):
    """
    very basic json file cache
    """
    log.debug('START cached(paragraph_dir=%s, fragdex=%s, name=%s)', paragraph_dir, fragdex, name)
    
    if fragdex is None:
        fragdex = ""

    cache_fn, ext = _cached_get_filename(paragraph_dir, fragdex, name, template=template)

    if os.path.exists(cache_fn):
        log.debug(f'{cache_fn} already exists')
                            
        if ext == ".png":
            r = Image.open(cache_fn)

        elif ext in [".tex", ]:
            with open(cache_fn, "r") as h:
                r = h.read()
        elif ext in ['', ".json"]:
            # no extension == .json
            try:
                with open(cache_fn, "r") as h:
                    r = json.load(h)
            except json.decoder.JSONDecodeError:
                log.error(f'Failed to load {cache_fn}')
                os.unlink(cache_fn)
                return None

        log.debug('FINISH cached(paragraph_dir=%s, fragdex=%s, name=%s%s) from %s', paragraph_dir, fragdex, name, ext, cache_fn)
        return r
    else:
        log.debug(f'{cache_fn} does not exist')
        return None


def cached_save(paragraph_dir, fragdex, name, obj, template="{name}_{fragdex}{ext}"):
    log.info('START cached_save(paragraph_dir=%s, fragdex=%s, name=%s)', paragraph_dir, fragdex, name)
    if fragdex is None:
        fragdex = ""

    cache_fn, ext = _cached_get_filename(paragraph_dir, fragdex, name, template=template)
    
    if ext in [".json", ]:
        with open(cache_fn, "w") as h:           
            json.dump(obj, h, indent=4)
    elif ext in [".png"]:
        try:
            obj.save(cache_fn)
        except:
            log.info('obj:\bn%s', obj)
            raise
    elif ext in [".tex"]:
        # basic binary
        with open(cache_fn, "w") as h:
            h.write(obj)

    log.info('FINISH cached_save(paragraph_dir=%s, fragdex=%s, name=%s%s) -> %s', paragraph_dir, fragdex, name, ext, cache_fn)
    return True


def cached_clear(paragraph_dir, fragdex, name, template="{name}_{fragdex}{ext}"):
    """
    clear the cache for this phrase
    """
    log.debug('START cached_clear(paragraph_dir=%s, fragdex=%s, name=%s)', paragraph_dir, fragdex, name)
    if fragdex is None:
        fragdex = ""

    cache_fn = _cached_get_filename(paragraph_dir, fragdex, name, template=template)

    if os.path.exists(cache_fn):
        os.unlink(cache_fn)

    log.debug('FINISH cached_clear(paragraph_dir=%s, fragdex=%s, name=%s)', paragraph_dir, fragdex, name)
    return True


def apply_background(text_layer, offset=0, aspect='widescreen'):
    """
    Apply a paper background to the text layer.
    
    text_layer: a PIL Image, black on transparent.

    offset: how many pixels to pre-roll the paper background so the grain
    matches perfectly.
    """
    log.info(f'apply_background({text_layer=}, {offset=})')
    if text_layer is None:
        return None
    
    G = const.GEOMETRY[aspect]
    
    width, height = text_layer.size
    # image = Image.new(
    #     'RGBA', 
    #     size=(
    #         const.HSIZE - const.IMG_TARGET_WIDTH,  # the width of the text-side
    #         total_height + const.VSIZE  # doesn't have to be perfact, it just can't be too small.
    #     ), 
    #     color="black"
    # )

    # paste a region of background.jpg as the background of image
    # throw enough extra whitespace so we can scroll all the way up if we want.
    log.info('Creating paper background (%s)', aspect)
    if aspect == "widescreen":
        width = G["HSIZE"] - const.IMG_TARGET_WIDTH
    elif aspect == "portrait":
        width = G["HSIZE"]

    paper_background = infinitePaperRoll(
        width, 
        min_height=height + G["VSIZE"] + FIRST_PAGE_TOP_MARGIN,
        offset=offset
    ).copy()

    log.info('Background image is %s', paper_background.size)
    # image.paste(
    #     paper_background,
    #     (0, 0)
    # )
    # image = paper_background

    log.debug('Pasting text layer (%s, %s)', *text_layer.size)
    # text layer is black on transparent, so we're pasting it into image and using
    # it as its own mask.
    # text_layer_width, text_layer_height = text_layer.size
    # FIRST_PAGE_TOP_MARGIN),

    paper_background.paste(
        text_layer,
        (0, 0),
        text_layer
    )
    
    # this is going to be taller than necessary in most cases,
    # text_layer is much more refined than this brutish beast.
    return paper_background


class XmlToLatex:
    structure = "Unknown"
    
    def __init__(self, chapter):
        # we have the filesystem
        self.chapter = chapter
        # which gives us the config
        self.config = chapter.config
        # and we have the xml
        if chapter.soup is None:
            chapter.load_xml()
        
        self.soup = chapter.soup
        self.all_characters = {}
        self.aspect = chapter.aspect
        self.G = const.GEOMETRY[self.aspect]

    def clear_characters(self):
        """
        Clear the list of characters, so we can start fresh.
        """
        self.all_characters = {}

    def add_character(self, speaker_name, thin_name=None):
        """
        Some formats (like plays) need a list of characters for proper
        formatting.  The way this is  getting formatted directly instead of
        calling name_to_tag or similar is concerning.
        """        
        if thin_name is None:
            thin_name = speaker_name.lower().replace(" ", "_")

        self.all_characters[speaker_name] = thin_name

    def write_as_latex(
        self,
        paragraph_dir=None,
        highlight_phrase=None,
        highlight_paragraph=False,
        force=False,
        rainbow=False
    ):
        """ 
        convert custom xml books to latex, subclasses for each type of book.

        expects some functions to exist and be customized for the material.
        Exactly what XML should be depends on those functions. 

        output is either:
        one "text_layer.png"
        OR 
        text_layer-0.png, text_layer-1.png, etc..

        based on the image height, in paragraph_dir/aspect for highlighted phrases,
        chapterdir for the global stuff.
        """
        # if we exceed 195 inches of page height (latex and pdf have a 200 inch
        # limit) it _will_ generate multiple "pages" and then paste them
        # together. the cosmetics of the page break are NOT really handled so
        # there will almost certainly be some artifacts at the glue joint, most
        # likely extra/missing vertical whitespace.

        # TODO: this base class needs to hand off the particulars to the
        # subclass.  

        # One for parsing the xml
        # one for building the latex
        # one for highlighting a phrase
        # we leave the actual tex-=>pdf processing here, 
        # we leave assembling the pngs into a strip here.
        log.info(f"write_as_latex({paragraph_dir=}, {highlight_phrase=}, {highlight_paragraph=}, {force=})")

        if highlight_phrase:
            phrase_index = int(highlight_phrase.attrs["index"])
            phid = "_" + str(phrase_index)
        else:
            phrase_index = ""
            highlight_phrase = None
            if rainbow:
                phid = "_rainbow"
            else:
                phid = "_plain"

        if paragraph_dir:
            out_dir = os.path.join(const.LIBRARY_DIR, paragraph_dir, self.aspect)
        else:
            out_dir = os.path.join(const.LIBRARY_DIR, self.chapter.chapterdir)

        tex_fn = os.path.join(
            out_dir,
            f"text_highlighted{phid}.tex"
        )
        highlighted_pdf_fn = os.path.join(
            out_dir,
            f"text_highlighted{phid}.pdf"
        )
        single_page = os.path.join(
            out_dir,
            f"text_layer{phid}.png"
        )                   
        first_page = os.path.join(
            out_dir,
            f"text_layer{phid}-0.png"
        )

        if os.path.exists(single_page):
            # ease off buddy, we have what we really want, who cares how it got there?
            if not force:
                # cache hit
                log.debug(f"{single_page} already exists")
                return None
            else:
                log.info('Cache Hit - but force is True.')
        else:
            log.info('Cache miss: %s does not exist', single_page)
                
        if "text_layer_1.png" in single_page:
            raise Exception("Debugging")

        if not os.path.exists(tex_fn) or force:
            log.info(f"Cache Fail: Generating Latex to create {tex_fn} ({phrase_index})")
            self.save_as_latex(
                highlight_phrase=highlight_phrase,
                tex_fn=tex_fn,
                highlight_paragraph=highlight_paragraph,
                rainbow=rainbow
            )

        if not os.path.exists(single_page) or force:
            # tex to pdf
            log.debug(f'Looking for {highlighted_pdf_fn}...')
            if not os.path.exists(highlighted_pdf_fn) or force:
                log.info(f"Rendering Latex to create highlighted.pdf ({phrase_index})")            
                subprocess.run(["latexmk", f"-output-directory={out_dir}", tex_fn])
                            
        if os.path.exists(single_page) and not force:
            log.debug(f"{single_page} already exists")
        elif os.path.exists(first_page) and not force:
            log.debug(f"[good] {single_page} does not exist, but {first_page} does!")
        else:
            # Expected:
            #   text_layer_{phrase_index}.png
            #   or 
            #   text_layer_{phrase_index}-0.png
            #
            # Convert pdf to png(s)
            if force:
                log.info("Force flag set, regenerating png images from pdf...")
            else:
                log.info("Neither expected text_layer png file exists: \n%s\n%s", 
                single_page,
                first_page
            )
            log.info('Regenerating png images from pdf...')

            cmd = [
                "convert",
                "-define",
                "png:color-type=6",
                "-density",
                "300",
                "-depth",
                "8",
                highlighted_pdf_fn,
                "-quality",
                "90",
                single_page,
            ]
            log.info(" ".join(cmd))
            completed_process = subprocess.run(cmd)

            if completed_process.returncode != 0:
                log.error(f"Command failed: {cmd}")
                # we're going to delete the pdf.
                if os.path.exists(highlighted_pdf_fn):
                    os.unlink(highlighted_pdf_fn)
                return

        # this will create text_layer.png
        # OR text_layer-0.png, text_layer-1.png, etc..

        # clean as we go, no need to balloon and pop
        self.tidy(out_dir, phid)
        # our "output" is the existence of text_layer_{phrase_id}-0.png
        return None
   
    def combine_text_layers(self, paragraph_dir, fragdex):
        """
        Input is a directory with text_layer_{fragdex}-*.png images.

        Output are:
        text_layer: a single image with all the text layers combined.  
        Okay, so this is what we don't want.  It's slow as shit.
        How about we give you the number of pages,
        and a function you can call that will return the index for a page.

        That is better for everyone.

        ct_layers: a dictionary of global coordinates about the geometry 
        of the highlighted portion of the text.  It isn't complete, but 
        it is suitable for most purposes.
        Sample:

        not highlighted, not highlighted, HERE IS HIGHLIGHTED HERE IS
        HIGHLIGHTED, not highlighted, not highlighted

        (0,0)

        |                                    V--- Top ---v
        |-----------------------Left --->|                           
                    |<--- Right
           ^--- Bottom ---^
        
        {
            "left": left,
            "top": htop,
            "right": right,
            "bottom": hbottom,
            "height": total_height,
            "fragdex": real_fragdex,
            "paragraph_dir": paragraph_dir
        }
        
        #Output is a single image with all the text layers combined, and the
        height of the combined image, the top and bottom of the highlighted
        segment, and the filename of the combined image.
        """
        log.error('OBSOLETE?')
        log.info('START combine_text_layers(paragraph_dir=%s, fragdex=%s)', paragraph_dir, fragdex)
        aspect = "widescreen"
        real_fragdex = fragdex
        
        log.debug(f'Looking for multiple text_layer_{real_fragdex}-*.png images...')

        if fragdex is None:
            fragdex = ""
        
        index = 0
        cached_ct_layers = None
        # Try and return values from .json cache next to the text_layer image
        while True:
            log.info(f'Looking for {paragraph_dir} text_layer_{fragdex}-{index} in filesystem cache')
            cached_ct_layers = cached(
                paragraph_dir,
                fragdex,
                name="text_layer", 
                template="{name}_{fragdex}-" + str(index) + "_{ext}" )

            if cached_ct_layers and 'top' in cached_ct_layers:
                return index, cached_ct_layers
            
            if cached_ct_layers is None:
                break

            index += 1
        
        ct_layers = {}

        index = 0
        done = False
        page_images = []
        bare_images = []
        while not done:            
            single_pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}.png")
            # pfn is the output from converting a pdf to png
            multiple_pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}-{index}.png")
            
            # pfn is black on transparent, we want a nice paper background with a little texture.
            finished_pfn = os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
                f"text_layer_{fragdex}-{index}-done.png"
            )
            
            log.info('Looking for %s', multiple_pfn)

            if os.path.exists(single_pfn):
                # got it in one, this is a very short piece.
                #pfn = single_pfn
                shutil.copyfile(single_pfn, multiple_pfn)
                done = True

            elif os.path.exists(multiple_pfn):
                log.info('Measuring highlighted segment of image %s', multiple_pfn)
                page_images.append(multiple_pfn)

                log.info('Opening image %s', multiple_pfn)
                # TODO: add in-memory cache here
                img = Image.open(multiple_pfn)
                try:
                    img.verify()
                except SyntaxError as se:
                    log.error(f"Image {multiple_pfn} is corrupted: {se}")
                    done = True
                    index += 1
                    continue

                img = Image.open(multiple_pfn)

                width, height = img.size
                
                if height == 60000:
                    # before we do that.. we need to crop vertically.
                    crop_box = img.getbbox()
                    img=img.crop((0, crop_box[1], img.width, crop_box[3]))
                    width, height = img.size
                    if height == 60000:
                        log.error("You've screwed it up now.  Expect weird whitespace gaps at 60000 pixel intervals.  Moron.")    
                    log.info('Saving cropped image: %s', multiple_pfn)
                    img.save(multiple_pfn)

                # turn the transparent pixels black, that lets us focus on the red channel.
                black_bg = Image.new("RGBA", img.size, "BLACK")
                img = Image.alpha_composite(black_bg, img)

                ct_layers = {
                    "index": index,
                    "height": height,
                    "paragraph_dir": paragraph_dir,
                    "fragdex": real_fragdex                
                }
                
                # the highlighted segment
                bbox = img.getchannel('R').getbbox()
                if bbox is None:
                    log.info(f'{img} has no highlighted segments')
                    bare_images.append((index, multiple_pfn, finished_pfn))
                else:
                    # this is right for htop, but wrong for hbottom.  We need the top of the _next_
                    # highlighted region to be our hbottom for height calculation purposes.  Or maybe half
                    # the bottom, and half the top.  If we use this hbottom we don't include the stanza-stanza vertical gaps.
                    # in our scroll_speed calculations.
                    #
                    #  Maybe that is good?  Maybe we calculate the speed it will take to get hbottom to the middle o the screen?
                    # I'll try that, making this use the "next" htop adds a whole pass to do it right.
                    hleft, htop, hright, hbottom = bbox
                    log.info(f'{hleft=}, {htop=}, {hright=}, {hbottom=}')

                    # how much of an indent is there on the left side of the first line?
                    # cut a strip
                    topstrip = img.crop((hleft, htop, hright, htop + 1))
                    # okay, so this is shit.  The yellow highlight is #ff f2 00
                    left, _, _, _ = topstrip.getchannel('R').getbbox()

                    # similarly a pixel strip from the bottom gives us 'right'
                    _, _, right, _ = img.crop((hleft, htop, hright, hbottom - 1)).getchannel('R').getbbox()

                    ct_layers.update({
                        "left": left,
                        "top": htop,
                        "right": right,
                        "bottom": hbottom,
                    })
            
                    log.info('ct_layers: %s', ct_layers)

                log.info(f"Saving ct_layers cache to text_layer_{real_fragdex}-page_.json")
                # extra template hoop so they alphasort right next to the png they describe
                cached_save(
                    paragraph_dir, 
                    real_fragdex, 
                    "text_layer", 
                    ct_layers, 
                    template="{name}_{fragdex}-" + str(index) + "_{ext}"
                )
                
                log.info('Applying background to text layer %s', multiple_pfn)
                
                text_layer = self.get_text_layer(paragraph_dir, fragdex, index)
                
                if text_layer is not None:
                    image = apply_background(text_layer, 0, aspect=aspect)
                    if image is not None:
                        image.save(finished_pfn, "PNG")
                        log.info('Saved finished text layer to %s', finished_pfn)
                    else:
                        log.error('Failed to apply background to text layer %s', multiple_pfn)
                else:
                    log.error('No text layer found for %s', multiple_pfn)

                log.info('FINISH combine_text_layers(paragraph_dir=%s, fragdex=%s)', paragraph_dir, fragdex)
            else:
                log.error(f'{multiple_pfn} does not exist')
                done = True

            index += 1

        # remove text_layer images that do not include a highlighted portion
        log.info('There are %s bare images', len(bare_images))
        for bare_index, finished_pfn, image_filename in bare_images:
            log.info(f'Cleaning up bare image {image_filename}')

            page_image_dir = os.path.join(
                const.LIBRARY_DIR, self.chapterdir, 'page_images',
            )
            os.makedirs(page_image_dir, exist_ok=True)

            bare_filename = os.path.join(
                page_image_dir,
                f"page_{bare_index}-done.png"
            )

            if os.path.exists(finished_pfn):
                if not os.path.exists(bare_filename):
                    text_layer = Image.open(finished_pfn)
                    # No, more.  we need to layer on the _right_ background
                    # texture, then we can use these directly.
                    apply_background(
                        text_layer=text_layer,
                        offset=0
                    ).save(bare_filename)

                    #shutil.copyfile(finished_pfn, bare_filename)

                os.unlink(finished_pfn)
                os.unlink(image_filename)

        # return the number of pages, and the ct_layers for the last page
        return index, ct_layers
    
    def get_text_layer(self, paragraph_dir, fragdex, page_index=None):
        """return the requested black-on-transparent text layer"""
        # single_pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}.png")
        # pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}-{index}.png")        
        if page_index is None:
            pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}.png")
        else:
            pfn = os.path.join(const.LIBRARY_DIR, paragraph_dir, f"text_layer_{fragdex}-{page_index}.png")

        text_image = Image.open(pfn)

        if page_index:
            # there are multiple pages, we can't only include "this" page.
            # we need the next page glue on or we get a trailing blank
            previous_pfn = os.path.join(
                const.LIBRARY_DIR, 
                self.chapterdir, 
                "page_images",
                f"page_{page_index - 1}-done.png"
            )
           
            next_pfn = os.path.join(
                const.LIBRARY_DIR, 
                self.chapterdir, 
                "page_images",
                f"page_{page_index + 1}-done.png"
            )
            
            height = 0
            previous_image = None
            if os.path.exists(previous_pfn):
                previous_image = Image.open(previous_pfn)
                previous_image.load()
                height += previous_image.height

            height += text_image.height

            next_image = None
            if os.path.exists(next_pfn):
                next_image = Image.open(next_pfn)
                next_image.load()
                height += next_image.height

            canvas = Image.new(
                "RGBA",
                (text_image.width, height),
                (0, 0, 0, 0)  # transparent background
            )

            offset = 0
            if previous_image:
                canvas.paste(previous_image, (0, 0))
                offset += previous_image.height

            canvas.paste(text_image, (0, offset))
            offset += text_image.height

            if next_image:
                canvas.paste(next_image, (0, offset))
                offset += next_image.height
            
            text_image = canvas

        return text_image

    def tidy(self, workdir, phid):
        for ext in ["aux", "log", "fls", "fdb_latexmk"]:
            fn = os.path.join(workdir, f"text_highlighted{phid}.{ext}")
            if os.path.exists(fn):
                os.unlink(fn)

        # obsolete files
        for fn in glob.glob(os.path.join(workdir, f"highlighted_*.pdf")):
            if os.path.exists(fn):
                os.unlink(fn)

        for fn in glob.glob(os.path.join(workdir, f"highlighted_*.tex")):
            if os.path.exists(fn):
                os.unlink(fn)

        for fn in glob.glob(os.path.join(workdir, f"text_layers_merged{phid}.png")):
            if os.path.exists(fn):
                os.unlink(fn)

    def wipe(self, workdir, phid):
        self.tidy(workdir, phid)
        for ext in ["tex", "pdf"]:
            fn = os.path.join(workdir, f"text_highlighted{phid}.{ext}")
            if os.path.exists(fn):
                os.unlink(fn)

        for fn in glob.glob(os.path.join(workdir, f"text_layer{phid}.png")):
            if os.path.exists(fn):
                os.unlink(fn)

        for fn in glob.glob(os.path.join(workdir, f"text_layer{phid}-*.png")):
            if os.path.exists(fn):
                os.unlink(fn)

        for fn in glob.glob(os.path.join(workdir, f"text_layer_{phid}-*.json")):
            if os.path.exists(fn):
                os.unlink(fn)

    def verify_text_images(self):
        # verify every text_layer png is valid
        log.info('Verifying text_layer png images...')

        for paragraph in self.soup.find_all("paragraph", recursive=True):
            paragraph_dir = paragraph.attrs["dir"]
            
            for fn in glob.glob(os.path.join(
                const.LIBRARY_DIR,
                paragraph_dir,
                "text_layer_*.png"
            )):
                pfn = os.path.join(
                    const.LIBRARY_DIR,
                    paragraph_dir,
                    self.aspect,
                    fn
                )
                
                log.debug(f'Verifying {pfn}...')
                img = Image.open(pfn)
                try:
                    img.verify()
                    log.debug(f'{pfn} is valid')
                except SyntaxError as se:
                    log.error(f"Image {pfn} is corrupted: {se}")
                    os.unlink(pfn)
            
    def title_spread(self, title):
        """
        If the title is too long, we need to split it into two lines.
        """
        if len(title) > 29:
            # find the space nearest but not exceeding the halfway point
            middle = len(title) // 2
            found = title[middle]
            
            while found != " " and middle > 0:
                middle -= 1
                found = title[middle]
            
            if found != " ":
                raise ValueError(f'Failed to find a good place to break title: {title}')

            left = title[:middle].strip()
            right = title[middle:].strip()
            return [left, right]
        else:
            return [title, ]


class NovelToLatex(XmlToLatex):
    # one of these should be True
    justified = False
    ragged = True

    def save_as_latex(self, highlight_phrase, tex_fn, highlight_paragraph=False, rainbow=False):
        log.info(f"save_as_latex({highlight_phrase=}, {tex_fn=}, {highlight_paragraph=}, {rainbow=})")
        chapter_contents = ""
        first_chapter = True

        rainbow_series = []

        phrase_index = 0
        for paragraph_xml in self.soup.find_all("paragraph", recursive=True):
            for phrase_xml in paragraph_xml.find_all("phrase", recursive=False):               
                if phrase_xml.attrs.get("index") is None:
                    phrase_index += 1
                    phrase_xml.attrs["index"] = phrase_index
                else:
                    phrase_index = int(phrase_xml.attrs.get("index"))

                if highlight_paragraph is False:
                    highlight = False
                else:
                    highlight = True

                plain_phrase = phrase_xml.get_text(
                    separator=" ",
                    strip=True
                )


                phrase = ""

                paragraph = phrase_xml.find_parent("paragraph")
                paragraph_tags = tags_to_dict(
                    paragraph.attrs.get("tags", "")
                )
                
                if paragraph_tags.get('has-text', True) is False:
                    # this paragraph has no text, skip it.
                    log.info('Paragraph %s has has_text=False tag, skipping...', paragraph)
                    continue

                elif phrase_xml.attrs.get("type", "") == "dinkus":
                    # https://ctan.math.illinois.edu/macros/latex/contrib/froufrou/froufrou.pdf
                    log.info('Dinkus found...')
                    dinkus_style = phrase_xml.attrs.get("style", "dinkus")
                    phrase += "\n" + f"\\froufrou[{dinkus_style}]" + "\n"
                    highlight = False

                elif phrase_xml.attrs.get("type", "") == "chapter_title":
                    log.info('Typesetting chapter title...')
                    # f strings and latex don't mix very well.
                    # phrase = f"\\centerline{{\\textbf{{\\large {phrase}}}}}\n"
                    if first_chapter:
                        phrase += "\n" + r"\vspace{0.25cm}" + "\n"
                        first_chapter = False
                    else:
                        phrase += "\n" + r"\vspace{1.5cm}" + "\n"

                    # title_spread because sometimes chapter titles are too long
                    # and latex will NOT make reasonable decisions about where
                    # to break.
                    for chapter_title_segment in self.title_spread(plain_phrase):
                        phrase += (r"\centerline{\textbf{\large %s}}" % chapter_title_segment) + "\n"

                    phrase += r"\vspace{0.25cm}" + "\n"

                elif phrase_xml.attrs.get("type", "") == "section_header":
                    log.info('Typesetting section header...')
                    phrase += "\n" + r"\vspace{0.5cm}" + "\n"
                    for segment in self.title_spread(plain_phrase):
                        phrase += (r"\centerline{\textbf{\normalsize %s}}" % segment) + "\n"
                    phrase += r"\vspace{0.25cm}" + "\n"

                else:
                    phrase = plain_phrase

                if "_" in phrase:
                    log.debug("Adding italics...")
                    phrase = phrase.replace(r"&", r"\&")
                    phrase = re.sub(
                        # r"_([-\' \’\?a-zA-Z \.!]+)_", 
                        r"_([^_]+)_", 
                        r"\\textit{\g<1>}", 
                        phrase
                    )

                # for typograph purposes, we have a little fixing to do
                if "œ" in plain_phrase:
                    log.debug(r"Replacing œ with \oe...")
                    plain_phrase = plain_phrase.replace("œ", r"\oe")
                
                if "—" in plain_phrase:
                    log.debug(r"Replacing — with --...")
                    plain_phrase = plain_phrase.replace("—", "--")

                # doing a string match here instead of phrase_xml and getting
                # highlight_phrase as the soup obj
                if rainbow:
                    # convert the phrase_id unique to hex pairs
                    index = 10 + int(phrase_xml.attrs.get('index', '0'))
                    rainbow_int = index % 16777216  # limit to 24 bits

                    log.info('index: %s', index)
                    log.info('rainbow_int: %s', rainbow_int)
                    rainbow_bytes = rainbow_int.to_bytes(3, 'big')
                    r, g, b = rainbow_bytes
                    log.info('rainbow_bytes: %s', rainbow_bytes)

                    rainbow_series.append(f"\\definecolor{{B{rainbow_int:X}}}{{RGB}}{{{r},{g},{b}}}")

                    if phrase.endswith(r"\nobreak\\"):
                        # put the nobreak _outside_ the highlight.
                        phrase = f"\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{phrase[:-len(r'\nobreak\\')]}}}" + r"\nobreak\\" + "\n"
                    else:
                        phrase = f"\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{phrase}}}\n"

                elif highlight:
                    if phrase_xml == highlight_phrase or highlight_phrase is None:
                        # this _phrase_ should be highlighted
                        if phrase.endswith(r"\nobreak\\"):
                            phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}" + r"\nobreak\\" + "\n"
                        else:
                            phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                    elif highlight_paragraph:
                        if phrase_xml.parent == highlight_phrase.parent:
                            phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                        else:
                            phrase += "\n"
                    else:
                        # log.info(f'{phrase} != {highlight_phrase}')
                        phrase += "\n"
                else:
                    phrase += "\n"
                
                if phrase_xml.attrs.get("last"):
                    # this is the last phrase in the stanza, we need to add an extra newline
                    # to separate stanzas.
                    phrase += "\n"

                chapter_contents += phrase
            
            chapter_contents += "\n"
        
        if self.justified:
            # classic book look, but with a narrow page it screws up a lot.
            with open(tex_fn, "w") as h:
                h.write(
                    r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=%sin,paperwidth=2.825in,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{froufrou}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}

\usepackage[osf]{libertinus-otf}

%s

\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\setlength{\leftmargini}{0.125em}

\begin{document}
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}

%s

\end{document}
""" % (
            PAPER_HEIGHT,
            "\n".join(rainbow_series),
            self.title_spread(self.config["title"]),
            self.config["author"], 
            chapter_contents,
        )
            )

        elif self.ragged:
            # more of a modern look, much better for narrow pages how close
            # 2.825in is to to our pixel width will be a major factor in how
            # blurry this is.
            
            # we _want_ pixel width = const.HSIZE - const.IMG_TARGET_WIDTH
            # 2.8in =  (1920 - 1080) / 300
            
            # 300 is our pixels->inches conversion factor
            paperwidth = self.G['TEXT_WIDTH'] / 300
            log.info(f'paperwidth: {paperwidth}')
            os.makedirs(os.path.dirname(tex_fn), exist_ok=True)

            with open(tex_fn, "w") as h:
                h.write(
                    r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=%sin,paperwidth=%sin,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{froufrou}
\usepackage[osf]{libertinus-otf}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}
\usepackage{microtype}

%s

\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\raggedright
\hyphenpenalty=500
\setlength{\leftmargini}{0.125em}

\begin{document}
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}

%s

\end{document}
""" % (
            PAPER_HEIGHT,
            paperwidth,
            "\n".join(rainbow_series),
            self.config["title"],
            self.config["author"],
            chapter_contents,
        )
            )


    def save_as_latex_old(self, highlight_phrase, tex_fn, highlight_paragraph=False):
        log.info(f"save_as_latex({highlight_phrase=}, {tex_fn=}, {highlight_paragraph=})")
        chapter_contents = ""
        first_chapter = True
        
        for paragraph_xml in self.soup.find_all("paragraph", recursive=True):
            for phrase_xml in paragraph_xml.find_all("phrase", recursive=False):

                highlight = True
                plain_phrase = phrase_xml.get_text(separator=" ", strip=True)
                phrase = ""

                paragraph = phrase_xml.find_parent("paragraph")
                paragraph_tags = tags_to_dict(
                    paragraph.attrs.get("tags", "")
                )
                
                if paragraph_tags.get('has-text', True) is False:
                    # this paragraph has no text, skip it.
                    log.info('Paragraph %s has has_text=False tag, skipping...', paragraph)
                    continue

                elif phrase_xml.attrs.get("type", "") == "dinkus":
                    # https://ctan.math.illinois.edu/macros/latex/contrib/froufrou/froufrou.pdf
                    log.info('Dinkus found...')
                    phrase += "\n" + r"\froufrou[dinkus]" + "\n"
                    highlight = False
                elif phrase_xml.attrs.get("type", "") == "chapter_title":
                    log.info('Typesetting chapter title...')
                    # f strings and latex don't mix very well.
                    # phrase = f"\\centerline{{\\textbf{{\\large {phrase}}}}}\n"
                    if first_chapter:
                        phrase += "\n" + r"\vspace{0.25cm}" + "\n"
                        first_chapter = False
                    else:
                        phrase += "\n" + r"\vspace{1.5cm}" + "\n"

                    # title_spread because sometimes chapter titles are too long
                    # and latex will NOT make reasonable decisions about where
                    # to break.
                    for chapter_title_segment in self.title_spread(plain_phrase):
                        phrase += (r"\centerline{\textbf{\large %s}}" % chapter_title_segment) + "\n"

                    phrase += r"\vspace{0.25cm}" + "\n"
                elif phrase_xml.attrs.get("type", "") == "section_header":
                    log.info('Typesetting section header...')
                    phrase += "\n" + r"\vspace{0.5cm}" + "\n"
                    for segment in self.title_spread(plain_phrase):
                        phrase += (r"\centerline{\textbf{\normalsize %s}}" % segment) + "\n"
                    phrase += r"\vspace{0.25cm}" + "\n"
                else:
                    phrase = plain_phrase

                if "_" in phrase:
                    log.debug("Adding italics...")
                    phrase = phrase.replace(r"&", r"\&")
                    phrase = re.sub(
                        r"_([-\' \’\?a-zA-Z \.!]+)_", 
                        r"\\textit{\g<1>}", 
                        phrase
                    )

                # doing a string match here instead of phrase_xml and getting
                # highlight_phrase as the soup obj
                # TODO: try this
                if highlight:
                    if phrase_xml == highlight_phrase or highlight_phrase is None:
                        # this _phrase_ should be highlighted
                        phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                    elif highlight_paragraph:
                        if phrase_xml.parent == highlight_phrase.parent:
                            phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                        else:
                            phrase += "\n"
                    else:
                        # log.info(f'{phrase} != {highlight_phrase}')
                        phrase += "\n"
                else:
                    phrase += "\n"
                
                if phrase_xml.attrs.get("last"):
                    # this is the last phrase in the stanza, we need to add an extra newline
                    # to separate stanzas.
                    phrase += "\n"

                chapter_contents += phrase
            
            chapter_contents += "\n"
        
        if self.justified:
            # classic book look, but with a narrow page it screws up a lot.
            with open(tex_fn, "w") as h:
                h.write(
                    r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=%sin,paperwidth=2.825in,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{froufrou}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}

\usepackage[osf]{libertinus-otf}

\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\setlength{\leftmargini}{0.125em}

\begin{document}
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}

%s

\end{document}
""" % (
            PAPER_HEIGHT,
            self.title_spread(self.config["title"]),
            self.config["author"], 
            chapter_contents,
        )
            )

        elif self.ragged:
            # more of a modern look, much better for narrow pages how close
            # 2.825in is to to our pixel width will be a major factor in how
            # blurry this is.
            
            # we _want_ pixel width = const.HSIZE - const.IMG_TARGET_WIDTH
            # 2.8in =  (1920 - 1080) / 300
            
            # 300 is our pixels->inches conversion factor
            paperwidth = self.G['TEXT_WIDTH'] / 300
            log.info(f'paperwidth: {paperwidth}')
            os.makedirs(os.path.dirname(tex_fn), exist_ok=True)

            with open(tex_fn, "w") as h:
                h.write(
                    r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=%sin,paperwidth=%sin,top=0.5in,bottom=4in,left=0.1in,right=0.1in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[utf8]{inputenc}
\usepackage[T1]{fontenc}
\usepackage{froufrou}
\usepackage[osf]{libertinus-otf}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}
\usepackage{microtype}
                
\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\raggedright
\hyphenpenalty=500
\setlength{\leftmargini}{0.125em}

\begin{document}
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}

%s

\end{document}
""" % (
            PAPER_HEIGHT,
            paperwidth,
            self.config["title"], 
            self.config["author"],
            chapter_contents,
        )
            )


class VerseToLatex(XmlToLatex):

    def save_as_latex(self, highlight_phrase, tex_fn, highlight_paragraph=False, rainbow=False):
        log.info(f"Verse: save_as_latex({highlight_phrase=}, {tex_fn=}, {highlight_paragraph=}, {rainbow=})")
        chapter_contents = ""

        first_chapter = True
        rainbow_series = []

        if highlight_phrase:
            highlight_phrase_index = int(highlight_phrase.attrs.get("index", -1))
            log.info(f'highlight_phrase_index: {highlight_phrase_index}')
        else:
            highlight_phrase_index = -1

        for paragraph_xml in self.soup.find_all("paragraph", recursive=True):
            paragraph_tags = tags_to_dict(
                paragraph_xml.attrs.get("tags", "")
            )
            
            if paragraph_tags.get('has-text', True) is False:
                # this paragraph has no text, skip it.
                log.info('Paragraph %s has has_text=False tag, skipping...', paragraph_xml)
                continue
                        
            for phrase_xml in paragraph_xml.find_all("phrase", recursive=False):
                phrase_index = int(phrase_xml.attrs.get("index"))
                # current design has no children, so this is not doing as much as it looks like.
                plain_phrase = phrase_xml.get_text(separator=" ", strip=True)
                phrase = []

                if phrase_xml.attrs.get("type", "") == "dinkus":
                    # https://ctan.math.illinois.edu/macros/latex/contrib/froufrou/froufrou.pdf
                    log.info('Dinkus found...')
                    dinkus_style = phrase_xml.attrs.get("style", "dinkus")
                    phrase.append("\n" + f"\\froufrou[{dinkus_style}]" + "\n")
                
                elif phrase_xml.attrs.get("type", "") == "chapter_title":
                    log.info('Typesetting chapter title...')
                    # f strings and latex don't mix very well.
                    # phrase = f"\\centerline{{\\textbf{{\\large {phrase}}}}}\n"
                    if first_chapter:
                        phrase.append("\n" + r"\vspace{0.25cm}" + "\n")
                        first_chapter = False
                    else:
                        phrase.append("\n" + r"\vspace{1.5cm}" + "\n")

                    # title_spread because sometimes chapter titles are too long
                    # and latex will NOT make reasonable decisions about where
                    # to break.
                    for chapter_title_segment in self.title_spread(plain_phrase):
                        phrase.append((r"\centerline{\textbf{\large %s}}" % chapter_title_segment) + "\n")

                    phrase.append(r"\vspace{0.25cm}" + "\n")

                elif phrase_xml.attrs.get("type", "") == "section_header":
                    log.info('Typesetting section header...')
                    phrase.append("\n" + r"\vspace{0.5cm}" + "\n")
                    for segment in self.title_spread(plain_phrase):
                        phrase.append((r"\centerline{\textbf{\normalsize %s}}" % segment) + "\n")
                    phrase.append(r"\vspace{0.25cm}" + "\n")
                else:
                    if "\n" in plain_phrase:
                        # this is a multi-line phrase, we need to add a linebreak
                        # after each line.
                        lines = plain_phrase.splitlines()
                        for line in lines[:-1]:
                            phrase.append(line.strip() + r"\nobreak\\")
                        # let the last line fall through for the no_linebreak handler.
                        plain_phrase = lines[-1].strip()

                    if phrase_xml.attrs.get("no_linebreak", "false").lower() == "true":
                        phrase.append(plain_phrase)
                    else:
                        phrase.append(plain_phrase + r"\nobreak\\")

                phrase_str = "".join(phrase)
                if "_" in phrase_str:
                    log.debug("Adding italics...")
                    phrase_str = phrase_str.replace(r"&", r"\&")
                    phrase_str = re.sub(
                        #r"_([-\' \’\?a-zA-Z \.!]+)_", 
                        r"_([^_]+)_", 
                        r"\\textit{\g<1>}", 
                        phrase_str
                    )

                # for typograph purposes, we have a little fixing to do
                if "œ" in phrase_str:
                    log.debug(r"Replacing œ with \oe...")
                    phrase_str = phrase_str.replace("œ", r"\oe")
                
                if "—" in phrase_str:
                    log.debug("Replacing — with --...")
                    phrase_str = phrase_str.replace("—", "--")                    

                if rainbow:
                    # convert the phrase_id unique to hex pairs
                    index = 10 + int(phrase_xml.attrs.get('index', '0'))
                    rainbow_int = index % 16777216  # limit to 24 bits

                    log.info('index: %s', index)
                    log.info('rainbow_int: %s', rainbow_int)
                    rainbow_bytes = rainbow_int.to_bytes(3, 'big')
                    r, g, b = rainbow_bytes
                    log.info('rainbow_bytes: %s', rainbow_bytes)
                    
                    rainbow_series.append(f"\\definecolor{{B{rainbow_int:X}}}{{RGB}}{{{r},{g},{b}}}")
                    if phrase_str.endswith(r"\nobreak\\"):
                        # move the nobreak so it is _outside_ the highlight.
                        phrase_str = f"\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{phrase_str[:-len(r'\nobreak\\')]}}}" + r"\nobreak\\" + "\n"
                    else:
                        phrase_str = f"\\color{{B{rainbow_int:X}}}\\highLight[B{rainbow_int:X}]{{{phrase_str}}}\n"
                elif highlight_paragraph:
                    phrase_str = HIGHLIGHT_COMMAND + "{" + phrase_str + "}\n"
                elif highlight_phrase_index == phrase_index:
                    # this _phrase_ should be highlighted
                    phrase_str = HIGHLIGHT_COMMAND + "{" + phrase_str + "}\n"
                else:
                    phrase_str += "\n"

                chapter_contents += phrase_str
            
            # when the last line of the stanza ends with \\ it messes up the spacing.
            if rainbow or highlight_phrase_index == phrase_index:
                eol = r"}\nobreak\\" + "\n"
                if chapter_contents.endswith(eol):
                    chapter_contents = chapter_contents[:-1 * len(eol)] + "}\n"
            else:
                eol = "\\nobreak\\\\\n"               
                if chapter_contents.endswith(eol):
                    chapter_contents = chapter_contents[:-1 * len(eol)] + "\n"
            
            chapter_contents += "\n"

            # phrase = phrase_xml.get_text(separator=" ")
            # phrase = phrase.strip(" ") # spaces, but not newlines.

            # if phrase_xml == highlight_phrase or highlight_phrase is None:
            #     # this _phrase_ should be highlighted
            #     phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
            # elif highlight_paragraph is not None:
            #     if phrase_xml.parent == highlight_paragraph:
            #         phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
            #     else:
            #         phrase += "\n"                
            # else:
            #     # log.info(f'{phrase_xml} != {highlight_phrase}')
            #     phrase += "\n"
            
            # if phrase_xml.attrs.get("last"):
            #     # this is the last phrase in the stanza, we need to add an extra newline
            #     # to separate stanzas.
            #     phrase += "\n"

            # chapter_contents += phrase

        # quotes
        if '"' in chapter_contents:
            log.debug("Adding smart quotes...")
            for quote in re.findall(r'"([^"]*)"', chapter_contents):
                chapter_contents = chapter_contents.replace(
                    f'"{quote}"', 
                    f'\\enquote{{{quote}}}'
                )

        #  If you start a line with [ immediately after \\, LaTeX may interpret
        #  it as an optional vertical spacing argument (e.g., \\[2cm]). 
        clean_contents = []
        for line in chapter_contents.splitlines():
            if line.startswith("["):
                clean_contents.append(r"{}[" + line[1:])
            else:
                clean_contents.append(line)

        chapter_contents = "\n".join(clean_contents)
                

        # mass-market paperbacks are 4.25x6.87
        # "trade" paperbacks are 5-5.5 x 8-8.5

        # widescreen has 1920 - 1080 = 840 pixels for our text, which at 300 pixels
        # per inch is only 2.8 inches.

        # if we down-res to 200 ppi, we get 4.2 inches, which is much better,
        # but blurrier.  It's a harsh trade-off.

        # There is _another_ problem.  With the highlighting, we're easing the
        # burder on the reader to track the text.  That makes a slightly faster
        # scrolling speed more than tolerable.  A wider page, with more words and
        # slower scrolling feels stagnant.
        width = "2.825in"
        if self.chapter.get_aspect() == "portrait":
            # portrait has 1080 pixels for text, which at 300 pixels per inch is
            # 3.6 inches.  Better than widescreen, but still very tight.  Any
            # margins are handled in Latex, which presumably knows what it is
            # doing.  At 200ppi we get 5.4 inches, trade-paperback size.
            width = "3.6in"
        # \usepackage{luaquotes}
        with open(tex_fn, "w") as h:
            # ,10pt
            h.write(
                r"""\documentclass[parskip=full]{scrartcl}
\usepackage[paperheight=%sin,paperwidth=%s,top=0.5in,bottom=4in,left=0.0625in,right=0.0625in,heightrounded]{geometry}
\addtokomafont{title}{\centering}
\addtokomafont{author}{\centering}
\usepackage[english]{babel}
\usepackage[T1]{fontenc}
\usepackage{froufrou}
\usepackage[osf]{libertinus-otf}

\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}
\usepackage{microtype}

\usepackage{verse,anyfontsize}

%s

\pagenumbering{gobble}
\widowpenalties 1 10000
\raggedbottom
\setlength{\leftmargini}{0.125em}
\setlength{\vindent}{0.75em}

\begin{document}
\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{%s}
\date{}
\maketitle
\vspace{-0.5in}

\begin{verse}
%s
\end{verse}                                        
\end{document}
"""
            % (
                PAPER_HEIGHT,
                width,
                "\n".join(rainbow_series),
                self.config["title"], 
                self.config["author"], 
                chapter_contents,
            )
        )


class PlayToLatex(XmlToLatex):

    def save_as_latex(self, highlight_phrase, tex_fn, highlight_paragraph=None, rainbow=False):

        chapter_contents = ""
        old_speaker = None

        for phrase_xml in self.soup.find_all("phrase", recursive=True):
            phrase = phrase_xml.get_text(separator=" ", strip=True)
            new_speaker = phrase_xml.attrs.get("speaker", None)

            if phrase_xml == highlight_phrase or highlight_phrase is None:
                # this _phrase_ should be highlighted
                phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}"
            elif highlight_paragraph is not None:
                if phrase_xml.parent == highlight_paragraph:
                    phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}"
                # else:
                #     phrase += "\n"                
            # else:
            #     # log.info(f'{phrase_xml} != {highlight_phrase}')
            #     phrase += "\n"

            # do we need to identify the speaker?  Add a prefix.
            if new_speaker and new_speaker != old_speaker:
                phrase = f"\n\\{new_speaker}speaks {phrase}"
                old_speaker = new_speaker

            if phrase_xml.attrs.get("last"):
                # this is the last phrase in the stanza, we need to add an extra newline
                # to separate stanzas.
                phrase += "\n\n"
            
            if chapter_contents:
                if chapter_contents[-1] in [":", ".", "!", "?"]:
                    chapter_contents += "  " + phrase
                else:
                    chapter_contents += " " + phrase
            else:
                chapter_contents = phrase
       
        character_list = ""
        longest = ""
        log.info('all_characters: %s', self.all_characters)
        for character in self.all_characters:
            log.info('Adding character: %s', character)
            # The introduction of a new character is made by the command
            # \Character. It takes three arguments: the first, optional, is
            # the entry for the list of Dramatis Personæ, the second is the name
            # appearing in the text and the third is the base for the
            # construction of the commands typesetting the occurrence of that
            # name in the stage direction and as a speaker. Shortly, if (name)
            # is given as third argument, the macro will return the following
            # commands: \(name) is used in stage direction, \(name)speaks is
            # used as speaker.
            character_list += (
                f"    \\Character[]{{{character}}}{{{character}}}\n"
            )
            if len(character) > len(longest):
                longest = character
        
        with open(tex_fn, "w") as h:
            h.write(
                r"""\documentclass[parskip=full]{scrartcl}
    \usepackage[paperheight=%sin,paperwidth=2.8in,top=0.5in,bottom=4in,left=0.125in,right=0.125in,heightrounded]{geometry}
    \addtokomafont{title}{\centering}
    \addtokomafont{author}{\centering}
    \usepackage [english]{babel}
    \usepackage[utf8]{inputenc}
    \usepackage[T1]{fontenc}

    \usepackage{luaquotes}
    \usepackage{luacolor}
                
    \usepackage{lua-ul}
    
    \usepackage{titling}
    \usepackage{dramatist}
    \renewcommand{\speaksfont}{\bfseries}

    \usepackage{calc}
                    
    \pagenumbering{gobble}
    \widowpenalties 1 10000
    \raggedbottom

    \begin{document}
    \setlength{\droptitle}{-45pt}
    \posttitle{\par\end{center}}
    \title{%s}
    \author{%s}
    \date{}
    \maketitle
    \vspace{-0.5in}

    \begin{flushleft}
%s
    \addtolength{\speakswidth}{\Dlabelsep}
    \addtolength{\speaksindent}{\speakswidth}
    
    \begin{drama}
    %s
    \end{drama}
    \end{flushleft}

    \end{document}    
    """ % (
        PAPER_HEIGHT,
        self.config["title"], 
        self.config["author"], 
        character_list,
        chapter_contents,
        )
    )        


class ScriptureToLatex(XmlToLatex):

    def save_as_latex(self, highlight_phrase, tex_fn, highlight_paragraph=None, rainbow=False):
        log.info('Highlighting the phrase %s', highlight_phrase)

        with open(tex_fn, "w") as h:
            h.write(
                r"""\documentclass{article}
\usepackage[paperheight=%sin,paperwidth=2.825in,top=0.5in,bottom=4in,left=0.0625in,right=0.0625in,heightrounded]{geometry}
\usepackage[english]{babel}
\usepackage[letterspace=150]{microtype}
\usepackage{luaquotes}
\usepackage{luacolor}
\usepackage{lua-ul}
\usepackage{titling}

\usepackage{scripture}
\scripturesetup{
  poetry/leftmargin=2em,
  poetry/verse/sep=1em
}
\setlength{\parfillskip}{30pt plus 1fil}
\pagenumbering{gobble}
\widowpenalties 1 10000

\begin{document}

\setlength{\droptitle}{-45pt}
\posttitle{\par\end{center}}
\title{%s}
\author{}
\date{}
\maketitle
\vspace{-0.5in}

\begin{scripture}
""" % (
    PAPER_HEIGHT,
    self.config["title"]
    ))

        out = []
        first = True
        # log.info('Processing %s...', self.soup)
        abs_index = 0
        for chapter in self.soup.find_all("paragraph", recursive=True):
            if "chapter" not in chapter.attrs:
                # Non-chapter preface stuff
                continue

            log.info('Chapter %s', chapter.attrs["chapter"])
            if not first:
                out.append("\n" + r"\extraskip" + "\n\n")
            first = False
            out.append(r"\ch{" + chapter.attrs["chapter"] + "}")

            for verse in chapter.find_all("phrase", recursive=False):
                abs_index += 1
                log.info('Verse %s', verse.attrs["verse"])
                verse_number = int(verse.attrs["verse"])
                
                out.append(f"\\vs{{{verse_number}}}")
                phrase = verse.get_text(separator=" ", strip=True)
                
                if rainbow:
                    # convert the verse number unique to hex pairs
                    index = abs_index
                    rainbow_int = index % 16777216  # limit to 24 bits

                    log.info('index: %s', index)
                    log.info('rainbow_int: %s', rainbow_int)
                    rainbow_bytes = rainbow_int.to_bytes(3, 'big')
                    r, g, b = rainbow_bytes
                    log.info('rainbow_bytes: %s', rainbow_bytes)

                    out.append(f"\\definecolor{{V{rainbow_int:X}}}{{RGB}}{{{r},{g},{b}}}")
                    phrase = f"\\color{{V{rainbow_int:X}}}\\highLight[V{rainbow_int:X}]{{{phrase}}}\n"
                else:
                    if verse == highlight_phrase or highlight_phrase is None:
                        phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                    elif highlight_paragraph is not None:
                        if verse.parent == highlight_paragraph:
                            phrase = HIGHLIGHT_COMMAND + "{" + phrase + "}\n"
                        else:
                            phrase += "\n"
                    else:
                        phrase += "\n"   
           
                out.append(phrase)

            # extra linefeed at the end of each chapter
            out.append("\n")

        out.append(r"\end{scripture}" + "\n")
        out.append(r"\end{document}" + "\n") 

        with open(tex_fn, "a") as h:
            h.write("".join(out))


def XmlConverter(chapter, xml, aspect='widescreen'):
    # my_config = config.get_config(chapterdir)
    structure = chapter.config.get('TEXT_STRUCTURE', 'novel')

    if structure == "novel":
        return NovelToLatex(chapter)
    elif structure == "verse":
        return VerseToLatex(chapter)
    elif structure == "play":
        return PlayToLatex(chapter)
    elif structure == "scripture":
        return ScriptureToLatex(chapter)

    log.error('Unknown/unsupported structure: %s', structure)
    return None


@lru_cache(maxsize=2)
def load_paper():
    paper_fn = os.path.join(
        const.ASSETS_DIR,
        'paper-background.jpg'
    )
    paper = Image.open(paper_fn)
    paper.load()
    return paper


def infinitePaperRoll(width, min_height, offset=0, aspect='widescreen'):
    """
    offset is trash
    """
    log.info(f'infinitePaperRoll(width={width}, min_height={min_height}, offset={offset})')
    single_height = 2448
    #
    # paper-background.jpg is 2448 pixels tall, we want an image this is width
    # pixels wide, at least min_height pixels tall, where the top edge is offset pixels
    # through the background image.
    #

    # how far through _this_ background image are we starting?
    offset = offset % single_height
    log.info(f'{offset=} mod {single_height=} = {offset=}')

    G = const.GEOMETRY[aspect]

    #  does this fit on a single background page?
    if (offset + min_height) < single_height:
        # it fits
        base = load_paper().crop((
            0, offset, 
            width,
            single_height
        ))
        base.load()

        log.info(
            'Creating single',
            offset=str(offset), 
            min_height=str(min_height), 
            single_height=str(single_height)
        )
        return base
    else:
        # the requested image is taller than a single background page
        # when measured from offset.  So we paste as many additional
        # background images as needed to fill min_height.
        log.info('paper surgery required', offset=offset, min_height=min_height, single_height=single_height)

        # double height canvas, this is a very lazy approach.
        # canvas = Image.new('RGBA', (width, min_height), (255,255,255,0))

        # one full copy of the paper background trimmed to width
        background = load_paper().crop((
            0, 0, 
            width, 
            single_height
        ))
        background.load()

        #if offset:

        return ImageChops.offset(
            background,
            xoffset=0,
            yoffset=-offset
        )
        


        # return background

        # canvas.paste(
        #     background.crop(
        #         (
        #             0, offset, 
        #             const.HSIZE - const.IMG_TARGET_WIDTH, 
        #             single_height
        #         )
        #     ),
        #     (0, 0)
        # )

        # canvas.paste(
        #     background.crop(
        #         (
        #             0, 0,
        #             const.HSIZE - const.IMG_TARGET_WIDTH, 
        #             single_height - offset
        #         )
        #     ),
        #     (0, single_height - offset)
        # )

        # image = canvas.crop((
        #     0, offset, 
        #     const.HSIZE - const.IMG_TARGET_WIDTH, 
        #     offset + min_height
        # ))

        # return background
        # canvas

        # total_height = 0
        # while total_height < min_height:
        #     canvas.paste(
        #         background,
        #         (0, total_height)
        #     )
        #     total_height += single_height


        # previous_height = 0
        # for count in range(1 + int(min_height / single_height)):
        #     log.debug(f'Pasting at (0, offset + {count * single_height})')
        #     if count == 1:
        #         # the first paste doesn't use the full background, we need to crop
        #         # background to the offset
        #         first_background = background.crop((
        #             0, offset, 
        #             const.HSIZE - const.IMG_TARGET_WIDTH, single_height
        #         ))
        #         image.paste(
        #             first_background,
        #             (0, 0)
        #         )
        #         previous_height = single_height - offset
        #     else:
        #         # where should we paste?
        #         top_edge = previous_height + single_height
        #         image.paste(
        #             background,
        #             (0, top_edge)
        #         )

        # return image

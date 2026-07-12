import fcntl
import json
import os
import pickle

from PIL import Image

from artifact_editor.tools import (
    tags_to_dict,
)

from artifact_editor.typography import typography

import logger
import const

log = logger.log(__name__)

# are we in a thread where this cache won't matter?
scroll_to_page = {}
# starts_only = []

class Page:
    starting_scroll_lock = 0
    IMAGE_FILENAME = ""

    def __init__(self, chapterdir):
        """
        Initialize a Page object.
        """
        self.chapterdir = chapterdir
        self.paragraph_index = 0
        self.page_index = 0
        self.starts_only = []
        self.images = {}

    def localized_scroll_lock(self, scroll_lock):
        """
        Convert a scroll lock to a localized scroll lock.

        scroll_lock: distance in pixel between the top edge of the visible
        screen and the top edge of the chapter, if all the page images are laid
        out top to bottom with appropriate spacing between them.

        Every frame advances the global scroll lock to advance the text image.
        """
        # which page is this?

        if scroll_lock == 0:
            return 0

        # at what value of scroll lock are we no longer able to respond with scroll lock?
        # for which 

        return scroll_lock + self.starting_scroll_lock

    def as_image(self, paragraph_index, fragdex):
        """
        Return the Pillow Image() of this page with the fragdex phrase highlighted.
        """
        if (paragraph_index, fragdex) not in self.images:
            imagefn = self.get_imagefn(paragraph_index, fragdex)
            
            if os.path.exists(imagefn):
                self.images[(paragraph_index, fragdex)] = Image.open(imagefn)
            else:
                # there isn't a version of the page for this paragraph that has this phrase highlighted.
                log.debug(f"Image file {imagefn} does not exist!")
                raise FileNotFoundError(f"Image file {imagefn} does not exist!")

        return self.images[(paragraph_index, fragdex)]

    @classmethod
    def first_page(cls, chapterdir):
        """
        Return the first page in the chapter.
        """
        cls.chapterdir = chapterdir
        cls.paragraph_index = 0
        return cls

    def get_metadatafn(self, paragraph_index, fragdex, page):
        """
        Return the metadata filename for _this_ page.
        """
        self.METADATA_FILENAME = os.path.join(
            const.LIBRARY_DIR,
            self.chapterdir,
            'paragraphs',
            f'{paragraph_index:06}',
            f'text_layer_{fragdex}-{page}_.json'
        )
        return self.METADATA_FILENAME

    def get_metadata(self, paragraph_index, fragdex, page):
        """
        Return the metadata for the given paragraph and fragdex.
        """
        self.METADATA_FILENAME = self.get_metadatafn(paragraph_index, fragdex, page)
        
        if not os.path.exists(self.METADATA_FILENAME):
            log.error(f"Metadata file {self.METADATA_FILENAME} does not exist!")
            return None
        
        with open(self.METADATA_FILENAME, 'r', encoding='utf-8') as f:
            self.METADATA = json.load(f)
        
        return self.METADATA

    def get_imagefn(self, paragraph_index, fragdex):
        #
        # returns the version of the image that has the paper background texture 
        #
        log.info(f'get_imagefn({paragraph_index}, {fragdex})')
        
        if fragdex:
            self.IMAGE_FILENAME = os.path.join(
                # /home/jkane/books/active/Aesop/Fables/chapter/0018/paragraphs/000003/text_layer_1.png
                const.LIBRARY_DIR, 
                self.chapterdir,
                'paragraphs',
                f'{paragraph_index:06}',
                f'text_layer_{fragdex}-{self.page_index}-done.png'
            )
            return self.IMAGE_FILENAME
        else:
            # but.. done here does NOT mean we have a good properly aligned
            # paper background. it only means we are cropped to the text, black
            # on transparent.  no, lets make our dreams come true.

            self.IMAGE_FILENAME = os.path.join(
                # /home/jkane/books/active/Aesop/Fables/chapter/0018/paragraphs/000003/text_layer_1.png
                const.LIBRARY_DIR,
                self.chapterdir,
                'page_images',
                f'page_{self.page_index}-done.png'
            )
            return self.IMAGE_FILENAME            

    def next_page(self):
        """
        Return the next page in the chapter.
        """
        if not hasattr(self, 'paragraph_index'):
            log.error("Page has no paragraph_index attribute, cannot get next page.")
            return None
        
        next_page = self.__class__(self.chapterdir)
        next_page.page_index = self.page_index + 1

        # every page starts with a paragraph?
        next_page.paragraph_index = self.paragraph_index + 1
        return next_page

    @classmethod
    def from_scroll_lock(cls, chapterdir, scroll_lock):
        """
        Convert a scroll lock to a Page object.
        """
        
        global scroll_to_page

        # it's a distance.
        try:
            scroll_lock = abs(scroll_lock)
        except TypeError:
            log.error(f"scroll_lock {scroll_lock} is not a number!")
            raise

        # and finally, which page is for scroll_lock?
        if False:
            if not scroll_to_page:
                if os.path.exists('scroll_to_page.pickle'):
                    log.info("Loading scroll_to_page pickle from disk")
                    with open('scroll_to_page.pickle', 'rb') as f:
                        scroll_to_page = pickle.load(f)
                        #starts_only = [start for (start, _, _) in scroll_to_page]

        if scroll_to_page:
            for (starting, ending) in scroll_to_page.keys():
                if starting <= scroll_lock < ending:
                    page = scroll_to_page[(starting, ending)]
                    log.info(f"[HYPER] Found page for scroll_lock {scroll_lock}->{scroll_lock}: {page}")
                    return page
        
        cls.build_scroll_to_page_cache(chapterdir)

        # and finally, which page is for scroll_lock?
        for (starting, ending) in scroll_to_page.keys():
            if starting <= scroll_lock < ending:
                page = scroll_to_page[(starting, ending)]
                log.info(f"[SLOW] Found page for scroll_lock {scroll_lock}->{scroll_lock}: {page} [{starting} - {ending}]")

                with open('scroll_to_page.pickle', 'wb') as f:
                    pickle.dump(scroll_to_page, f)

                page.starting_scroll_lock = starting
                page.ending_scroll_lock = ending
                return page

        log.error(f"Failed to find page for scroll_lock {scroll_lock}")

    @classmethod
    def build_scroll_to_page_cache(cls, chapterdir):
        """
        Build a cache for scroll lock to page mappings.
        """
        global scroll_to_page
        scroll_to_page = {}
        # acquire filesystem lock
        try:
            with open('from_scroll_lock.lock', 'w') as f:
                # the first process that gets this lock sets up the cache,
                # everyone else gets the fast version.
                fcntl.flock(f, fcntl.LOCK_EX)
                log.info("Acquired from_scroll_lock.lock")

                # double check, who knows how long that lock took to acquire
                #if not scroll_to_page:
                    # if os.path.exists('scroll_to_page.pickle'):
                    #     log.info("Loading scroll_to_page pickle from disk")
                    #     with open('scroll_to_page.pickle', 'rb') as f:
                    #         stp = pickle.load(f)
                    #         for k in stp:
                    #             scroll_to_page[k] = stp[k]                

                first_page = cls(chapterdir)

                # this is actually >= the correct value for max_paragraph
                # but that happens to be exactly what we need.
                max_paragraph =  len(os.listdir(
                    os.path.join(const.LIBRARY_DIR, first_page.chapterdir, "paragraphs")
                ))

                paragraph_index = 0
                page_index = 0
                if hasattr(first_page, 'METADATA'):
                    # If we have metadata, we can use it
                    if first_page.METADATA in [None, ""]:
                        first_page.METADATA = {}
                else:
                    # default to empty dict
                    first_page.METADATA = {}

                # keep going until we find the first paragraph with a text layer.
                while paragraph_index <= max_paragraph + 3:
                    #log.info(f'{paragraph_index=}, {max_paragraph=}, {page_index=}')
                    fragdex = 1
                    
                    # fragdex and page_index are fixed, we're just checking each
                    # paragraph for text_layer_1-0.json
                    METADATA_FILENAME = first_page.get_metadatafn(
                        paragraph_index=paragraph_index,
                        fragdex=fragdex,
                        page=page_index,
                    )

                    if not os.path.exists(METADATA_FILENAME):
                        # keep looking
                        log.debug('Metadata file %s does not exist, trying next paragraph.', METADATA_FILENAME)
                        # increment the index
                        paragraph_index += 1
                        continue

                    # Found it, the first paragraph with a text layer.
                    first_page.paragraph_index = paragraph_index
                    first_page.METADATA_FILENAME = METADATA_FILENAME
                    log.info('Loading first page metadata from %s', first_page.METADATA_FILENAME)
                    first_page.METADATA = json.load(open(first_page.METADATA_FILENAME, 'r', encoding='utf-8'))
                    paragraph_index += 1
                    break

                # scroll_lock (begin, end) to page object
                if not first_page.METADATA:
                    return None

                page_first_scroll_lock = first_page.METADATA['height'] 
                scroll_to_page[(0, page_first_scroll_lock)] = first_page

                next_page = first_page.next_page()

                while next_page:
                    # gather the metadata, since we care here about the total height not the
                    # particulars of the highlight we can use any fragdex
                    METADATA_FILENAME = next_page.get_metadatafn(
                        paragraph_index=next_page.paragraph_index,
                        fragdex=1,
                        page=next_page.page_index,
                    )
                    log.info(f'Gathering metadata from {METADATA_FILENAME}')

                    if os.path.exists(METADATA_FILENAME):
                        next_page.METADATA = json.load(
                            open(next_page.METADATA_FILENAME, 'r', encoding='utf-8')
                        )
                    else:
                        log.debug(f"Metadata file {METADATA_FILENAME} does not exist, pages exhausted.")
                        next_page = None
                        break

                    # what is the last scrool lock on this page?
                    log.info('Next page metadata: %s', next_page.METADATA)
                    page_final_scroll_lock = page_first_scroll_lock + next_page.METADATA['height']
                    # scroll_to_page.append((page_first_scroll_lock, page_final_scroll_lock, next_page))
                    scroll_to_page[(page_first_scroll_lock, page_final_scroll_lock)] = next_page
                    
                    # starts_only.append(page_first_scroll_lock)
                    # next page starts where we leave off
                    page_first_scroll_lock = page_final_scroll_lock
                    
                    # get the next page                   
                    next_page = next_page.next_page()

                return None
        except IOError:
            log.error("Could not acquire from_scroll_lock.lock")
            return None
  
    def __str__(self):
        return f"Page(page_index={self.page_index})"


def calculate_page_offsets(chapter):
    """
    Calculate the page offsets for all pages in the chapter.
    page XML will be mutated, unless you want subtle hell you should
    save before this call and load after.
    """
    # Get the list of all pages in the chapter
    Page.build_scroll_to_page_cache(chapter.chapterdir)

    # gives us: scroll_to_page[(0, page_first_scroll_lock)] = first_page
    page_to_offset = {}
    for key, value in scroll_to_page.items():
        # key is (starting_scroll_lock, ending_scroll_lock)
        # value is the Page object
        page_to_offset[value.page_index] = key
      
    for paragraph in chapter.get_xml().find_all("paragraph"):
        # Check if the paragraph has a page_offset attribute
        paragraph_tags = tags_to_dict(paragraph.attrs.get('tags', ""))

        if not paragraph_tags.get("has-text", True):
            log.info('Paragraph %s has no text, skipping.', paragraph.attrs.get('id', 'unknown'))
            continue

        if 'page_index' not in paragraph.attrs:
            for page_index in page_to_offset.keys():
                # dumbass.. we won't have top and bottom on paragraphs
                # we have to derive this differently.  We _do_ have ct_layers cache files.
                first_phrase = paragraph.find("phrase")
                first_phrase_ct_layers = typography.cached(
                    paragraph.attrs['dir'],
                    first_phrase.attrs['fragdex'],
                    name="text_layer", 
                    template="{name}_{fragdex}-" + str(page_index) + "_{ext}" )
            
                if first_phrase_ct_layers and 'top' in first_phrase_ct_layers:
                    # we found the page that has highlighted text for this phrase.
                    paragraph.attrs['page_index'] = page_index
                    break
                else:
                    log.info('Phrase %s is _not_ on page %s', first_phrase, page_index)

        if 'page_index' not in paragraph.attrs:
            # log.info('Paragraph %s has no page index, its funny business, skipping it.', paragraph)
            continue

        elif 'page_offset' not in paragraph.attrs:
            # Calculate the page offset based on the page index
            starting_scroll_lock, _ = page_to_offset[paragraph.attrs['page_index']]
            paragraph.attrs['page_offset'] = starting_scroll_lock

    chapter.save_xml()
  

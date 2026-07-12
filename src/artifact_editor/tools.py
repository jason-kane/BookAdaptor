# does NOT have any artifact_editor imports
import glob
import json
import os
import copy
import random
import re
import subprocess
import time

import fnv_hash_fast

import const
import logger
from urllib.parse import unquote, urlparse
log = logger.log(__name__)

mybook = None

PUNCTUATION = [".", "!", "?", ",", '"', "'", ":", ";", "“", "’", "”"]


def chapterkey_to_chapterargs(chapter_key):
    # yeah, I do sometimes want to punch myself in the face.
    return json.loads(chapter_key)


def tightstring(s):
    return " ".join(s.split()).strip()


def cast_bestsort(char_dict):
    """
    Sort characters by their total appearances (visual + audio).
    weighted to favor visible characters heavily.
    """
    return dict(
        sorted(
            char_dict.items(),
            key=lambda item: item[1]["visual_appearances"]
            + item[1]["audio_appearances"] * 0.1,
            reverse=True,
        )
    )    


def get_author_title_chapter_from_url(chapterurl):
    """
    Extracts author, title, and chapter from a chapter URL.
    Example URL: /author/title/chapter/
    """
    parts = chapterurl.strip('/').split('/')

    author = parts[0]
    title = "/".join(parts[1:-1])
    chapter = parts[-1]
    
    return author, title, chapter


def get_paragraph_dir(chapterdir, paragraph):
    """
    Get the directory for a paragraph, creating it if it doesn't exist.
    """
    if 'dir' not in paragraph.attrs:
        paragraph_index = int(paragraph.attrs['index'])

        paragraph_dir = os.path.join(
            chapterdir, 
            "paragraphs", 
            f"{paragraph_index:06}"
        )
        log.warning('Assigning paragraph_dir: %s', paragraph_dir)
        # why stored instead of calculated?  
        paragraph.attrs['dir'] = paragraph_dir.lstrip('/')
    else:
        paragraph_dir = paragraph.attrs['dir']

    return paragraph_dir


def get_text_to_next(image_xml, next_image_xml):
    """
    we can commit and do commit typographical crimes here.  We are driving the
    AI prompting for images.  We're given two image_xmls, we want all the raw
    text that occurs between them.  we're smashing it down to a simple series of
    words/punctuation.
    """
    log.debug('get_text_to_next called with %s and %s', image_xml, next_image_xml)
    this_image_xml = image_xml
    paragraph = image_xml.find_parent("paragraph")
    # all the text between this image_xml and the next image_xml.

    # the string we're building up to return.
    text_to_next = ""

    # the last image is a special case.
    if next_image_xml:
        # there is a next image_xml, march move toward the end of this paragraph
        while this_image_xml != next_image_xml:
            # the previous entry ended in a punctuation mark.
            if text_to_next and text_to_next[-1] in PUNCTUATION:
                text_to_next += " "

            
            new_text_segment =  " ".join(this_image_xml.get_text().replace(' *', '').strip().split())

            # we begin with a punctuation mark, so we need a space.
            if new_text_segment and new_text_segment[0] in PUNCTUATION:
                new_text_segment = " " + new_text_segment

            text_to_next += new_text_segment

            this_image_xml = this_image_xml.next_sibling

            if this_image_xml is None:
                # we've reach the end of the paragraph, keep looking in the next paragraph.
                paragraph = paragraph.next_sibling

                while paragraph and not hasattr(paragraph, "contents"):
                    # skip any text at the paragraph level
                    paragraph = paragraph.next_sibling

                if paragraph and paragraph.contents:
                    this_image_xml = paragraph.contents[0]
                else:
                    break
    else:
        # there isn't a next image_xml.
        # We just want the rest of the document.
        while this_image_xml:
            if text_to_next and text_to_next[-1] in PUNCTUATION:
                text_to_next += " "

            text_to_next += " ".join(this_image_xml.get_text().replace(' *', '').strip().split())
            this_image_xml = this_image_xml.next_sibling

            if this_image_xml is None:
                # we've reach the end of the paragraph, keep looking in the next paragraph.
                paragraph = paragraph.next_sibling
                while paragraph and not hasattr(paragraph, "contents"):
                    # skip any text at the paragraph level
                    paragraph = paragraph.next_sibling

                if paragraph and paragraph.contents:
                    this_image_xml = paragraph.contents[0]
                else:
                    break

    return text_to_next.strip()



def get_surrounding_paragraphs(paragraph, context_min=200, context_before=None, context_after=None, with_tags=False):   
    trim_paragraph = copy.deepcopy(paragraph)

    previous_paragraph = paragraph
    next_paragraph = paragraph

    if with_tags:
        allow = ["index"]

        # list of strings, minimal excess whitespace
        log.info('get_surrounding_paragraphs called with context_min=%s, context_before=%s, context_after=%s', context_min, context_before, context_after)
        for attr in list(trim_paragraph.attrs.keys()):
            if attr not in allow:
                del trim_paragraph.attrs[attr]

        for child in trim_paragraph.descendants:
            # also clear all children
            if hasattr(child, "attrs"):
                for attr in list(child.attrs.keys()):
                    if attr not in allow:
                        del child.attrs[attr]

        bundle = [
            #tightstring(
                trim_paragraph.prettify()
            #)
        ]
    
    else:
        bundle = [
            tightstring(paragraph.get_text())
        ]

    if context_before is not None and context_after is not None:
        # we want a specific amount of context before and after.
        
        while len(json.dumps(bundle)) < context_before and previous_paragraph:
            # find the first previous sibling
            previous_paragraph = previous_paragraph.previous_sibling
            while previous_paragraph is not None and not previous_paragraph.get_text().strip():
                previous_paragraph = previous_paragraph.previous_sibling

            if previous_paragraph:
                bundle.insert(0, tightstring(previous_paragraph.get_text()))
    
        while len(json.dumps(bundle)) < context_after and next_paragraph:
            next_paragraph = next_paragraph.next_sibling
            while next_paragraph is not None and not next_paragraph.get_text().strip():
                next_paragraph = next_paragraph.next_sibling

            if next_paragraph:
                bundle.append(tightstring(next_paragraph.get_text()))

    elif context_min:
        # we want as much context as we can reasonably get, add paragraphs to the back
        # and front until we reach context_min.
        while len(json.dumps(bundle)) < context_min and (previous_paragraph or next_paragraph):
            # find the first previous sibling
            if previous_paragraph:
                previous_paragraph = previous_paragraph.previous_sibling
                while (
                    previous_paragraph is not None 
                    and 
                    (
                        not previous_paragraph.get_text().strip() 
                        or 
                        previous_paragraph.attrs.get("fullscreen", "false") == "true"
                    )
                ):
                    previous_paragraph = previous_paragraph.previous_sibling

            if next_paragraph:
                next_paragraph = next_paragraph.next_sibling
                while next_paragraph is not None and not next_paragraph.get_text().strip():
                    next_paragraph = next_paragraph.next_sibling

            if previous_paragraph:
                bundle.insert(0, tightstring(previous_paragraph.get_text()))
            
            if next_paragraph:
                bundle.append(tightstring(next_paragraph.get_text()))
    
    return "\n".join(bundle)


def friendly_location(in_location):
    """
    Locations are stage-direction abbreviations.  We don't expect text-to-image to comprehend them.
    """
    return {
        "UR": "left background",
        "UC": "center background",
        "UL": "right background",

        "SR": "left",
        "CS": "middle",
        "SL": "right",

        "DR": "left front",
        "DC": "middle front",
        "DL": "right front"
    }.get(in_location, in_location)


def get_bookurl(author, title):
    return f"{author}/{title}"

import inspect


def requestToBookdir(request_url):
    # This is a placeholder implementation; adjust as needed for your routing
    # http://localhost:5000/Aesop/Fables/0024/images/0
    parsed = urlparse(request_url)
    p = parsed.path.split('/')
    author = p[0]
   
    if "chapter" in parsed:
        chapter_index = parsed.index("chapter")
        title = '/'.join(p[1:chapter_index])
        chapter_number = p[chapter_index + 1]
        chapterurl = f"{author}/{title}/chapter/{chapter_number}"
        return chapterurl
    
    else:
        # look for a four digit chapter number.
        title_parts = []
        for part in p[1:]:
            if part.isdigit() and len(part) == 4:
                chapter_number = part
                break
            title_parts.append(part)
        title = '/'.join(title_parts)    

    bookdir = f"{author}/{title}/"
    return bookdir


def chapterurl_to_chapterdir(chapterurl):
    return "/".join(
        chapterurl.split("/")[:-1] + ["chapter", chapterurl.split("/")[-1]]
    ).lstrip("/")


def requestToChapterKwargs(request_url):
    parsed = urlparse(request_url)
    p = parsed.path.split('/')
    
    log.info('parsed.path: %s', parsed.path)
    log.info('p: %s', p)

    # leading /
    if p[0] == '':
        p = p[1:]

    # unescape %20 and similar
    p = [unquote(part) for part in p]

    # library = p[0]
    author = p[1]
    title = p[2]
    chapter_number = int(p[3])
    language = p[4]
   
    return {
        "author": author,
        "title": title,
        "number": chapter_number,
        "language": language
    }


def requestToChapterURL(request_url):
    # Extract the chapter URL from the request URL
    # This is a placeholder implementation; adjust as needed for your routing
    # http://localhost:5000/Aesop/Fables/0024/images/0
    parsed = urlparse(request_url)
    p = parsed.path.split('/')
    
    log.info('parsed.path: %s', parsed.path)
    log.info('p: %s', p)

    if p[0] == '':
        log.info('leading slash detected in request_url path')
        i = 1
    else:
        i = 0

    author = p[i]
   
    log.info('|| requestToChapterURL called with %s', request_url)
    chapter_number = None
    if "chapter" in p:
        log.info('parsed: %s', parsed)
        chapter_index = p.index("chapter")
        title = '/'.join(p[i + 1:chapter_index])
        chapter_number = p[chapter_index + 1]
        chapterurl = f"/{author}/{title}/chapter/{chapter_number}"
    
    else:
        log.info('no "chapter" in parsed: %s', p)
        # look for a four digit chapter number.
        title_parts = []
        for part in p[i + 1:]:
            if part.isdigit() and len(part) == 4:
                chapter_number = part
                break
            title_parts.append(part)
        title = '/'.join(title_parts)    

        if chapter_number is None:
            log.error('No chapter number found in URL: %s', request_url)
            return None

        log.info(f"/{author=}/{title=}/chapter/{chapter_number=}")
        chapterurl = f"/{author}/{title}/chapter/{chapter_number}"    

    log.info('|| requestToChapterURL responding with %s', chapterurl)
    return chapterurl


def get_chapterurl(author, title=None, chapter_number: int | None=None):
    #stack = inspect.stack()
    #caller = stack[1]
    #log.info(f"{caller.function} -> get_chapterurl({author}, {title}, {chapter_number})")

    if "/chapter/" in author and title is None and chapter_number is None:
        # it's really a chapterdir
        # /<author>/<title>/chapter/<chapter>/
        return author.replace("chapter/", "", 1)
    
    if "chapter/" in title and chapter_number is None:
        return title.replace("chapter/", "", 1)
    
    return f"{author}/{title}/{chapter_number}"


def get_chapterdir(author_name, title=None, chapter_number=None):
    #stack = inspect.stack()
    #caller = stack[1]
    #log.info(f"{caller}{caller.function} -> get_chapterdir({author_name}, {title}, {chapter_number})")

    if "/" in author_name and title is None and chapter_number is None:
        if "/chapter/" in author_name:
            # it's really a chapterdir
            # /<author>/<title>/chapter/<chapter>/
            author, title, chapter_number = author_name.lstrip('/').replace("chapter/", "", 1).split('/')
        else:
            # it's really a chapterurl
            # <author>/<title>/<chapter>/
            author, title, chapter_number = author_name.lstrip('/').split('/')

    if '/chapter' in title:
        log.error ('get_chapterdir: title should not contain "/chapter"')
        title = title.replace('/chapter', '', 1)
    
    return os.path.join(
        author_name,
        title,
        'chapter',
        f'{int(chapter_number):04}',
    )


def get_bookdir(author, title):
    log.debug(f"get_bookdir({author}, {title})")
    return os.path.join(
        const.LIBRARY_DIR,
        author,
        title
    )


def script(script_body):
    """
    Generate a script tag with the given body.
    """
    return f"""<script>{script_body}</script>"""


def generic_button(
        chapterurl,
        category="audio",
        method="POST",
        tag="actions_url",
        cosmetic="Cosmetic Button Text",
        include=None,
        variant=None,
        target=None,
        vals=None,
        url=None,
        id=None,
        tooltip=None
    ):
    # 
    if id is None:
        id = f"{category}_{tag}_btn_{os.urandom(4).hex()[:6]}"

    #if tooltip:
    #    out = f'<wa-tooltip for="{id}">{tooltip}</wa-tooltip> <wa-button id="{id}" '
    #else:
    out = f'<wa-button id="{id}" '

    if url is None:
        if category:
            url = f"/{chapterurl}/{category}/actions/{tag}"
        else:
            url = f"/{chapterurl}/actions/{tag}"

    if method == "POST":
        out += f'hx-post="{url}" '
    elif method == "DELETE":
        out += f'hx-delete="{url}" '
            
    out += 'hx-on::before-request="beforeRequest(this,event)" '
    out += 'hx-on::after-request="afterRequest(this,event)" '
    if target:
        out += f"""
            hx-target="{target}" """

    if vals:
        # hx-vals='js:{{fragdex: {fragdex}, selectedImage: getSelectedImageIndex()}}'
        # vals="fragdex: {fragdex}, selectedImage: getSelectedImageIndex()"
        out += f"""
            hx-vals='js:{{{vals}}}' """

    out += '               hx-swap="outerHTML" '
    
    if include:
        out += f'\n            hx-include="{include}"'
    
    if variant:
        out += f'\n            variant="{variant}"'

    out += f""">{cosmetic}</wa-button>
    """
    log.info('generic_button: %s', out)
    return out


def hidden(key, value):
    return f"""<input type="hidden" name="{key}" value="{value}"></input>"""


def wait_for(wf, timeout=None):
    count = 0
    start = time.time()
    while not os.path.exists(wf):
        time.sleep(1)
        count += 1
        if timeout:
            if time.time() > (start + timeout):
                log.error(f"Timeout waiting for {wf} after {timeout} seconds.")
                return False
          
        if count % 60:
            log.info(f"(still waiting for {wf})")

    return True


def get_tag(in_text, loras=[], randomized=True):
    """
    in_text is a beautifulsoup tag, we want a short string that is safe and brief for a filename.
    
    liberal in what we accept, etc..
    """
    if hasattr(in_text, "attrs"):
        text = in_text.attrs.get("prompt")

        if not text:
            text = in_text.get_text(separator=" ", strip=True)
    else:
        text = in_text
        
    text = "_".join(text.split()[:6])[:25]
    tag = re.sub(r"[^a-z_A-Z0-9]", "_", text)

    # loras are a list of strings.  include them in the hash.
    log.info('loras: %s', loras)
    in_text += "".join(loras)

    #
    # If I'm going this far.. might as do it right.  make a short hash of the text.
    hash = fnv_hash_fast.fnv1a_32(
        in_text.encode('utf-8')
    )
    tag += f"_{hash:04x}"
    
    if randomized:
        # followed by 4 characters of random hex
        tag += f"_{random.randint(0, 0xFFFF):04x}"

    log.info('in_text: %s', in_text)
    log.info('Tag: %s', tag)
    return tag 


def tags_to_dict(tags):
    """
    Paragraph Tags.  ie:

    "has-text=false,has-image=true"

    becomes
    {
        "has-text": "false",
        "has-image": "true"
    }
    """
    if tags is None:
        return {}
    
    # could be smart oneliner with nested comprehensions.. but hello?  readability.
    # always.  obviously correct.
    #
    tags = tags.split(",")
    out = {}
    for tag in tags:
        if not tag:
            # it has to be a string, the only falsy string is an empty string
            # so this is shorthand for 'if tag == "":'
            continue

        # the maxsplit=1 is so we can do dumb things like a=b=c and get {"a": "b=c"}
        k, v = tag.split('=', maxsplit=1)
        if v.lower() in ("true", "false"):
            # normalize to lowercase boolean strings
            v = v.lower() == "true"

        out[k] = v

    return out


def dict_to_tags(tag_dict) -> str:
    """
    Paragraph Tags serialization.
    {
        "has-text": "false",
        "has-image": "true"
    }

    becomes:

    "has-text=false,has-image=true"
    """
    if tag_dict is None:
        return ""

    # could be smart oneliner with nested comprehensions.. but hello?  readability.
    # always.  obviously correct.
    #
    tag_list = []
    for key in tag_dict:
        if tag_dict[key] is True:
            tag_list.append(f"{key}=true")
        elif tag_dict[key] is False:
            tag_list.append(f"{key}=false")
        else:
            tag_list.append(f"{key}={tag_dict[key]}")
    tag_str = ",".join(tag_list)
    return tag_str
    

def expand_dir(directory_name, skip_endswith=None, reverse=False):
    image_list = []
   
    for image_fn in sorted(
        glob.glob(directory_name), reverse=reverse
    ):
        if skip_endswith and any(
            [image_fn.endswith(skipstr) for skipstr in skip_endswith]
        ):
            continue

        image_list.append(image_fn)

    return image_list


def extract_frames(videofile, frame_dir):
    """
    Extract frames from videofile into frame_dir.
    """
    log.info(f"Extracting frames from {videofile} to {frame_dir}...")
    os.makedirs(frame_dir, exist_ok=True)
    cmd = [
        "/usr/bin/ffmpeg",
        "-i", videofile,
        os.path.join(frame_dir, "frame_%06d.png")
    ]
    # log.info(json.dumps(cmd, indent=2))
    subprocess.run(cmd, capture_output=False)
    log.info(f"DONE extracting frames from {videofile} to {frame_dir}.")
    return frame_dir


def assemble_mp4(fps, framedir, wavfile, videofile, image_match="image_%06d.png", start_number=None, max_frames=None):
    """
    Assemble all the images in framedir with the audio in wavfile at FPS.
    Output is videofile.
    """
    log.info(f"START assemble_mp4({fps=}, {framedir=}, {wavfile=}, {videofile=})")
    # tie it all together
    # 
    if not os.path.exists(framedir):
        log.error(f'Framedir {framedir} does not exist!')
        return

    elif len(os.listdir(framedir)) <= 1:
        log.error(f'{framedir=} exists but is empty!')
        return
    
    log.info(f'Assembling video from {len(os.listdir(framedir))} frames in {framedir}')

    cmd = [
        "/usr/bin/ffmpeg",
        "-nostdin",
        "-y",
        "-thread_queue_size", str(10000),
        "-framerate", str(fps),
    ]
    
    if start_number:
        cmd.append("-start_number %s" % str(start_number))
    
    if max_frames:
        cmd.append("-frames:v %s" % str(max_frames))
   
    cmd += ["-i", os.path.join(framedir, image_match)]
    
    if wavfile:
        cmd += ["-i", wavfile]
    
    cmd += [
        "-c:a", "aac",
        "-shortest",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        videofile
    ]
    # #"-c:v h264_amf",   # AMD hardware encoding
    log.info(json.dumps(cmd, indent=2))
    subprocess.run(cmd, capture_output=False)
    # subprocess.run(cmd)
    log.info(f"DONE assemble_mp4({fps=}, {framedir=}, {wavfile=}, {videofile=})")
    return videofile


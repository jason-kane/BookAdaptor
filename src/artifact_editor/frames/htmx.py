import logging
import os
import time
from artifact_editor.author.views import author_base
import const
from artifact_editor.tools import (
    generic_button,
)
from flask import url_for

log = logging.getLogger(__name__)


def frame_pfn(chapter, aspect, frame_index):
    # <div id="image-pfn"><h4>{{frame_pfn}}</h4></div>
    framedir = os.path.join(chapter.chapterdir, "frames", aspect)
    frame_pfn = os.path.join(
        framedir, f"frame_{frame_index:06}.png"
    )
    return frame_pfn


def frame_to_phrase(chapter, frame_index: int):
    frame_count = 0
    for phrase in chapter.get_xml().find_all("phrase"):
        frames = int(phrase.attrs.get("frames", "0"))
        if frame_index >= frame_count and frame_index < frame_count + frames:
            return phrase
        frame_count += frames
    return None


def frame_to_phrase_old(mybook, chapterdir, frame_index: int):
    """
    return the phrase_xml for a specific frame number
    """
    phrase_index = None
    mp = mybook.load_masterplan()

    regenerate = mp is None

    while phrase_index is None:
        if regenerate:
            masterplan.delete_masterplan(chapterdir)
            log.info("Regenerating master plan for %s", chapterdir)
            plan = masterplan.generate_masterplan(chapterdir)
            masterplan.save_masterplan(chapterdir, plan)
            mp = mybook.load_masterplan()
            regenerate = False

        for mp_phrase in mp.get("words", []):
            paragraph_index = None
            start_frame = mp_phrase.get("start_frame", 0)
            end_frame = mp_phrase.get("end_frame", 0)

            if frame_index >= start_frame and frame_index <= end_frame:
                paragraph_index = int(mp_phrase.get("id").split("_")[0])
                log.info(f"Found paragraph {paragraph_index} for frame {frame_index}")
                assert paragraph_index == int(
                    mp_phrase.get("paragraph_dir").split("/")[-1]
                )

                try:
                    phrase_index = int(mp_phrase["index"])
                except KeyError:
                    regenerate = True

                break


def frame_navigator(chapter, frame_index: int):
    frameurl = os.path.join(chapter.url, chapter.language, "frames")
    change = False

    aspect = chapter.get_aspect()

    out = []
    # last_image = None
    frame_index = 0
    for phrase_index, phrase in enumerate(chapter.get_xml().find_all("phrase")):
        paragraph_xml = phrase.find_parent("paragraph")
        image_xml = phrase.find_previous("image")

        if "index" not in phrase.attrs:
            change = True
            phrase.attrs["index"] = str(phrase_index)

        if phrase.attrs.get("type") == "dinkus":
            # no image for dinkus
            continue

        img_src = url_for(
            "library.book.chapter.images.show_image_by_index",
            **chapter.kwargs,
            height=0,
            image_index=image_xml.attrs["index"],
        )

        frame_url = url_for(
            "library.book.chapter.frames.base",
            **chapter.kwargs,
            aspect=aspect,
            frame_index=frame_index,
        )

        out.append(f"""<a href="{frame_url}">
            <div 
                hx-get="{frame_url}.hx"
                hx-swap="none"
                class="wa-stack frame-nav">
                <img src="{img_src}"></img>
                <p>{paragraph_xml.attrs["index"]}-{image_xml.attrs["index"]}-{phrase.attrs['index']}</p>
            </div></a>
        """)

        frame_index += int(phrase.attrs.get("frames", "0"))

    if change:
        chapter.save_xml()

    if aspect == "widescreen":
        height = 228
    elif aspect == "portrait":
        height = 250

    return (
        f"""
    <div>paragraph-image-phrase</div>
    <div 
        style='display: flex; overflow-x: auto; overflow-y: hidden;height: {height}px; white-space: nowrap;'
    >"""
        + "".join(out)
        + "</div>"
    )


def frame_display(chapter, frame_index: int):
    book = chapter.get_xml().find("book")

    aspect = chapter.get_aspect()

    # frameurl = os.path.join(
    #     chapter.url,
    #     chapter.language,
    #     "frames",
    #     aspect,
    # )

    total_frames = 0
    # int(book.attrs.get("total_frames", 0))
    if total_frames == 0:
        for phrase in book.find_all("phrase"):
            total_frames += int(phrase.attrs.get("frames", "0"))

        book.attrs["total_frames"] = total_frames
        chapter.save_xml()

    # the currently selected phrase
    phrase_xml = frame_to_phrase(chapter, frame_index)
    image_xml = phrase_xml.find_previous("image")
    paragraph_xml = phrase_xml.find_parent("paragraph")

    def get_frame_url(offset, size):
        idx = frame_index + offset

        # for right click-open in new tab
        href = url_for(
            "library.book.chapter.frames.base",
            **chapter.kwargs,
            aspect=aspect,
            frame_index=idx,
        )

        actual_href = url_for(
            "library.book.chapter.frames.htmx_frame",
            **chapter.kwargs,
            aspect=aspect,
            frame_index=idx,
        )

        if offset < 0:
            return f'<a hx-get="{actual_href}" hx-swap="none" href="{href}"><wa-icon library="system" style="width: 40px; height: 480px; font-size: {size}px" name="chevron-left"></wa-icon></a>'
        else:
            # right side
            return f'<a hx-get="{actual_href}" hx-swap="none" href="{href}"><wa-icon library="system" style="width: 40px; height: 480px; font-size: {size}px" name="chevron-right"></wa-icon></a>'

    # left side chevrons
    left_chevrons = []
    if frame_index - 100 >= 0:
        left_chevrons.append(get_frame_url(-100, 48))
    if frame_index - 10 >= 0:
        left_chevrons.append(get_frame_url(-10, 36))
    if frame_index - 1 >= 0:
        left_chevrons.append(get_frame_url(-1, 24))

    # right side chevrons
    right_chevrons = []
    if frame_index + 1 <= total_frames:
        right_chevrons.append(get_frame_url(1, 24))
    if frame_index + 10 <= total_frames:
        right_chevrons.append(get_frame_url(10, 36))
    if frame_index + 100 <= total_frames:
        right_chevrons.append(get_frame_url(100, 48))

    redraw_url = url_for(
        "library.book.chapter.frames.redraw_frame",
        **chapter.kwargs,
        aspect=aspect,
        frame_index=frame_index,
    )

    redraw_button = f"""
        <wa-button
            id="redraw_button"
            hx-post="{redraw_url}"
            hx-vals='{{"frame_index": "{str(frame_index)}"}}'
            hx-swap='none'
            hx-trigger="click"
            hx-on::before-request="beforeFrameRequest(event)"
            hx-on::after-request="afterFrameRequest(event)"
            appearance="filled"
            variant="success">Redraw</wa-button>"""

    G = const.GEOMETRY[aspect]
    img_width = G["HSIZE"]
    # img_height = G["VSIZE"]
    # height: {img_height}px;

    img_src = url_for(
        "library.book.chapter.frames.frame_image",
        author=chapter.author.name,
        title=chapter.title,
        chapter_number=chapter.number,
        language=chapter.language,
        aspect=aspect,
        frame_index=frame_index,
    )

    pfn = frame_pfn(chapter, aspect, frame_index)

    return f"""
<div class="wa-cluster" style="align-items: center; justify-content: center;">
    <div class="wa-cluster" style="width: 20%; align-items: right; justify-content: flex-end;">
        {"".join(left_chevrons)}
    </div>

    <div 
        id="frame_image"
        class="wa-stack"
        style="width: 40%; align-items: center; justify-content: center;">
        <img
            border="1px solid white"
            style="width: {img_width}px;"
            src="{img_src}?{time.time()}"
            alt="Frame {frame_index}">
        </img>
    </div>

    <div class="wa-cluster" style="width: 20%; align-items: left; justify-content: flex-start;">
        {"".join(right_chevrons)}        
    </div>
</div>

<div class="wa-cluster" style="align-items: center; justify-content: center;">
    <h2>{frame_index}</h2>
    {redraw_button}
    <div id="frame-pfn">
        <wa-button
            hx-get="open_directory?fn={pfn}"
            hx-swap="none"
        >Show in File Manager</wa-button>
    </div>
    <wa-button href="/{chapter.url}/audio?page={int(paragraph_xml.attrs['index']) + 1}">Audio Workshop</wa-button>
    <wa-button href="/{chapter.url}/images/{image_xml.attrs['index']}">Image Workshop</wa-button>
    <wa-button 
        hx-post="/{chapter.url}/frames/regenerate_w_tmi"
        hx-vals='{{"frame_index": "{frame_index}"}}'
        hx-trigger="click"
        variant="warning">Regenerate Image w/TMI</wa-button>
</div>"""


def image_durations_button(chapter):
    return generic_button(
        chapter.url,
        category="frames",
        tag="image_durations",
        cosmetic="Recalculate Image Durations",
    )


def set_fragment_id_button(bookurl):
    return f"""<wa-button 
            id="set_fragment_id"
            hx-post="/{bookurl}/frames/actions/set_fragment_id"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)"
            hx-target="#set_fragment_id"
            hx-swap="outerHTML">Set Fragment IDs</wa-button>
    """


def clear_broken_frames(chapter):
    """
    Sometimes we get broken frame images, usually when the service crashes
    mid-write.  We need to identify and remove them so they can be replaced
    without regenerating everything.
    """
    return f"""<wa-button 
            id="clear_broken_frames"
            hx-post="/{chapter.url}/frames/actions/clear_broken_frames"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)"
            hx-target="#clear_broken_frames"
            hx-swap="outerHTML">Clear Broken Frames</wa-button>
    """


def clear_cache(chapter):
    """
    Clear the frame cache for this chapter.
    """
    return f"""<wa-button 
            id="clear_cache"
            hx-post="/{chapter.url}/frames/actions/clear_cache"
            hx-on::before-request="beforeRequest(this,event)"
            hx-on::after-request="afterRequest(this,event)"
            hx-target="#clear_cache"
            variant="danger"
            hx-swap="outerHTML">Clear Text Cache</wa-button>
    """

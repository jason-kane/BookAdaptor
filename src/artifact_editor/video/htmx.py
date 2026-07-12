from artifact_editor.tools import (
    generic_button,
)
from flask import url_for


def render_masterplan_widescreen_button(chapter):
    render_url = url_for(
        'library.book.chapter.video.render_masterplan_widescreen_handler',
        **chapter.kwargs,
    )

    return f"""
    <wa-button
        hx-post="{render_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="brand" 
        appearance="accent" 
        size="medium" 
        class=""
        >Render Master Plan (Widescreen)</wa-button>
    """


def render_masterplan_portrait_button(chapter):
    render_url = url_for(
        'library.book.chapter.video.render_masterplan_portrait_handler',
        **chapter.kwargs,
    )

    return f"""
    <wa-button
        hx-post="{render_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="brand" 
        appearance="accent" 
        size="medium" 
        class=""
        >Render Master Plan (Portrait)</wa-button>
    """


def calculate_paragraph_durations_button(chapter):
    return generic_button(
        chapter.url,
        category="video",
        tag="calculate_paragraph_durations",
        cosmetic="Recalculate Paragraph Durations"
    )  


def clear_adjusted_images_button(chapter):
    return generic_button(
        chapter.url,
        category="video",
        tag="clear_adjusted_images",
        cosmetic="Clear Adjusted Images"
    )  


def clear_all_frames_button(chapter):
    render_url = url_for(
        'library.book.chapter.video.clear_all_frames',
        **chapter.kwargs,
    )

    return f"""
    <wa-button
        hx-post="{render_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="danger" 
        appearance="accent" 
        size="medium" 
        class=""
        >Clear All Frames</wa-button>
    """    
    return generic_button(
        chapter.url,
        category="frames",
        tag="clear_all_frames",
        cosmetic="Clear All Frames",
        variant="danger",
    )


def clear_all_transitions_button(chapter):
    return generic_button(
        chapter.url,
        category="frames",
        tag="clear_all_transitions",
        cosmetic="Clear All Transitions",
        variant="danger",
    )
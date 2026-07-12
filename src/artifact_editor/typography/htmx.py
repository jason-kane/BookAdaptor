from flask import url_for
from artifact_editor.chapter import Chapter
from artifact_editor.tools import (
    generic_button,
)


def clear_all_text_button(chapter):
    clear_text_url = url_for(
        'library.book.chapter.typography.clear_all_text',
        **chapter.kwargs,
    )
    return f"""
    <wa-button 
        hx-post="{clear_text_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="danger" 
        appearance="accent" 
        size="medium" 
        class="">Clear All Text</wa-button>"""    


def draw_missing_text_button_widescreen(chapterurl):
    return generic_button(
        chapterurl,
        category="typography",
        tag="draw_missing_text_widescreen",
        cosmetic="Draw Missing Text (widescreen)",
    )


def draw_missing_text_button_portrait(chapterurl):
    return generic_button(
        chapterurl,
        category="typography",
        tag="draw_missing_text_portrait",
        cosmetic="Draw Missing Text (portrait)",
    )


def redraw_all_text_button_widescreen(chapterurl):
    return generic_button(
        chapterurl,
        category="typography",
        tag="redraw_all_text_widescreen",
        cosmetic="Redraw All Text (widescreen)",
    )

def hyper_redraw_all_text_button(chapter, aspect):
    redraw_url = url_for(
        'library.book.chapter.typography.hyper_redraw_all_text',
        aspect=aspect,
        **chapter.kwargs,
    )
    return f"""
    <wa-button 
        hx-post="{redraw_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class="">Hyper Redraw All Text ({aspect})</wa-button>"""    


def redraw_all_text_button_portrait(chapterurl):
    return generic_button(
        chapterurl,
        category="typography",
        tag="redraw_all_text_portrait",
        cosmetic="Redraw All Text (portrait)",
    )

def clear_highlight_dimensions_button(chapterurl):
    return generic_button(
        chapterurl,
        category="typography",
        tag="clear_highlight_dimensions",
        cosmetic="Clear Highlight Dimensions",
    )


def refresh_examples(chapter):
    refresh_url = url_for(
        'library.book.chapter.typography.refresh_examples',
        **chapter.kwargs,
    )
    return f"""
    <wa-button 
        hx-post="{refresh_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class="">Refresh Examples</wa-button>"""    


def reset_camera_button(chapter):
    reset_camera_url = url_for(
        'library.book.chapter.typography.reset_camera',
        **chapter.kwargs,
    )
    return f"""
    <wa-button 
        hx-post="{reset_camera_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class="">Reset Camera</wa-button>"""    


def evaluate_camera_rate_portrait_button(chapter):
    evaluate_url = url_for(
        'library.book.chapter.typography.evaluate_camera_rate',
        aspect="portrait",
        **chapter.kwargs,
    )

    return f"""
    <wa-button 
        hx-post="{evaluate_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class="">Evaluate Camera Rate (portrait)</wa-button>""" 


def evaluate_camera_rate_widescreen_button(chapter):
    evaluate_url = url_for(
        'library.book.chapter.typography.evaluate_camera_rate',
        aspect="widescreen",
        **chapter.kwargs,
    )

    return f"""
    <wa-button 
        hx-post="{evaluate_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class="">Evaluate Camera Rate (widescreen)</wa-button>""" 



def build_missing_highlight_geometry_button(chapter):
    return generic_button(
        chapter.url,
        category="typography",
        tag="build_missing_highlight_geometry",
        cosmetic="Build Missing Highlight Geometry",
    )


def verify_text_images_button(chapter):
    return generic_button(
        chapter.url,
        category="typography",
        tag="verify_text_images",
        cosmetic="Verify Text Images"
    ) 
import html

from artifact_editor.tools import (
    generic_button,
)

from flask import url_for

def save_book_button(chapterurl):
    return generic_button(
        chapterurl,
        category="text",
        tag="save_book",
        cosmetic="Save Book",
    )


def convert_to_xml_button(chapter):
    return f"""
    <wa-button 
        id="convert_to_xml_btn" 
        hx-post="{ url_for('library.book.chapter.text.convert_to_xml', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language) }"
        hx-on::before-request="beforeRequest(this,event)"
        hx-on::after-request="afterRequest(this,event)"
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        size="medium" 
        class=""
    >Convert to XML</wa-button>"""
   

def book_text(book_text: str, chapter) -> str:
    save_chapter_text_url = url_for(
        'library.book.chapter.text.save_chapter_text',
        **chapter.kwargs,
    )
    delete_chapter_url = url_for(
        'library.book.chapter.text.delete_chapter',
        **chapter.kwargs,
    )
    
    return f"""
    <div id="book_text_wrapper">
        <div
            id="text_editor"
            label="Raw Text"
            name="book_text">
        </div>
        
        <wa-button
            hx-post="{save_chapter_text_url}"
            hx-target="#book_text"
            hx-include="#book_text"
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="afterRequest(this,event)" 
            hx-swap="outerHTML"
            id="save_txt_button"
            value="save"
            name="save">Save Chapter Text</wa-button>
        
        <wa-button
            hx-delete="{delete_chapter_url}"
            hx-target="#book_text"
            hx-include="#book_text"
            hx-on::before-request="beforeRequest(this,event)" 
            hx-on::after-request="afterRequest(this,event)" 
            hx-swap="outerHTML"
            id="delete_txt_button"
            variant="danger"
            value="delete"
            name="delete">Delete Chapter</wa-button>
    </div>
    """
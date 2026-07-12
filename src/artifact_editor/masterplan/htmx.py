from artifact_editor import (
    tools,
)
from flask import url_for

def regenerate_masterplan_button(chapter):
    regenerate_url = url_for(
        'library.book.chapter.masterplan.regenerate',
        **chapter.kwargs
    )
    return f"""
    <wa-button 
        hx-post="{regenerate_url}" 
        hx-on::before-request="beforeRequest(this,event)" 
        hx-on::after-request="afterRequest(this,event)" 
        hx-swap="outerHTML" 
        variant="neutral" 
        appearance="accent" 
        class="">Regenerate Master Plan</wa-button>
      """
    # return tools.generic_button(
    #     chapter.url,
    #     category="masterplan",
    #     tag="regenerate_masterplan",
    #     cosmetic="Regenerate Master Plan"
    # )

from flask import render_template, url_for

from artifact_editor.audio.sound import sound
import os
import wave
import const
import logging

log = logging.getLogger(__name__)

def local_sound_selector(chapter, sound_xml, page):
    # web awesome to select a sound from the local library.  
    # I'm thinking a classic upload widget file selector, hinted to start in the sound directory.  let the browser provide the UI.  I'm not sure if that is clever or stupid.
    # Ahh, teh file system access API?
    # https://web.dev/file-system-access/
    # no asshole.

    out = """  
<wa-dialog label="Local Sounds" id="dialog-dismiss">
  <div class="wa-grid" style="--min-column-size: 15ch;">"""

    for sound_file in sound.list_sounds():
        out += f"""
        <wa-card with-footer>
            <div class="wa-flank:end">
                <div class="wa-stack wa-gap-xs">
                    <div class="wa-cluster wa-gap-xs">
                        <h3 class="wa-heading-m">{sound_file}</h3>
                    </div>

                    <span class="wa-caption-s">description of this sound file</span>
                </div>
            </div>
            <div slot="footer" class="wa-grid wa-gap-xs" style="--min-column-size: 10ch;">
                <div class="wa-stack">
                    <div>
                        <audio controls>
                            <source src="{ url_for('library.sounds_src', src=sound_file) }" type="audio/wav">
                        </audio>
                        
                    </div>
                    <div class="wa-cluster wa-gap-xs">
                        <wa-button 
                            hx-post="{ url_for('library.book.chapter.audio.sound.select_local_sound', author=chapter.author.name, title=chapter.title, chapter_number=chapter.number, language=chapter.language, sound_index=sound_xml['index'], page=page) }"
                            hx-vals='{{ "index": "{sound_xml["index"]}", "sound_file": "{sound_file}" }}'
                            data-dialog="close"
                            appearance="outlined">
                            Choose
                        </wa-button>
                    </div>
                </div>
            </div>            
        </wa-card>
    """

    out += """
</div>

  <wa-button slot="footer" variant="brand" data-dialog="close">Close</wa-button>
</wa-dialog>

<wa-button appearance="filled" data-dialog="open dialog-dismiss">Choose Sound File</wa-button>
    """
    return out

            # <div slot="footer" class="wa-grid wa-gap-xs" style="--min-column-size: 10ch;">
            #     <div class="wa-stack">
            #         <div>
            #             <audio controls>
            #                 <source src="{sound_file}" type="audio/wav">
            #             </audio>
            #         </div>
            #         <div class="wa-cluster wa-gap-xs">
            #             <wa-button appearance="outlined">
            #                 <wa-icon slot="start" name="phone"></wa-icon>
            #                 Choose
            #             </wa-button>
            #         </div>
            #     </div>
            # </div>
    
def sound_editor(chapter, sound_xml, page):
    """
    We are an editor with sensible defaults.  The goal is to require no input.  
    
    The path to deliver that is to make the development pipeline feel like a customizable character editor in the sense you can just use defaults.  Click 'random' then 'choose' over and over.  You can do that but each step can be meaningfully improved with even minimal human editorial intervention.  Right now, there is a massive gulf in quality based on the level of human intervention.  I expect that to narrow over time.
    """
    paragraph = sound_xml.find_parent("paragraph")
    src = sound_xml.attrs.get("src", "")
    log.info('sound_xml: %s', sound_xml)
    
    if sound_xml:
        duration = float(sound_xml.attrs.get("duration", 0))
        if duration is None:
            fn = os.path.join(
                const.SOUND_DIR,
                os.path.basename(src)
            )
            # we want the duration fo this audio file
            if os.path.exists(fn):
                with wave.open(fn, 'r') as f:
                    frames = f.getnframes()
                    rate = f.getframerate()
                    duration = frames / float(rate)
                
                sound_xml.attrs["duration"] = duration
                chapter.save_xml()
            else:
                log.error(f"Sound file not found for duration calculation: {fn}")

    return render_template(
        "sound.html",
        chapter=chapter,
        sound_xml=sound_xml,        
        src=src,
        **chapter.kwargs,
        local_sound_selector=local_sound_selector,
        duration=duration
    )
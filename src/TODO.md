# Jul 3

## Scene
Generating lighting doesn't self-refresh
All the "Generate" should have comfy buttons
Prompt->Comfy does not include the style
default t2i should be workflow


 FIXED: animate first/last frame is using the previous image as the first frame, not the current image


DONE: Image -> Selector
When the previous image has an animation, the last frame of the animation should always be an option in the chooser.



# ComfyUI Worflow Loader is still shitty

# You moron.
Progress bars.  Fucking Everywhere. and reloading the page doesn't break shit.



# Unsorted
FIXED Chapter titles don't get a sensible image prompt
FIXED chapter titles aren't being identified in the input stream and properly decorated.  but the decoration does work now.

Rename Chapter Directory()

convert to xml uses llm, but is running in the wrong process.

transitions live 100% on the receiver side of the timeline

Sound Effects.  Think knocking on the door at the end of Monkeys Paw.

# Global
    move gpu specific imports to only be in gpu import tree

# Library
    Ugly as shit
    Widget to upload a new story

# Text Workshop
    Ugly as shit
    OBSOLETE - Disable convert to xml until characters.json exists?

    hint for hints showing expected format
    rendered video text scrolling
    * Extract Characters is broken
    BUG - Convert to XML doesn't populate xml text on completion

# Typography
    BUG background paper texture isn't scrolling
    BUG dinkus isn't identified and typeset

# Character Workshop

## DONE
    FIXED ability to merge characters
    NO NEED highlight on hover
    Voice Effects Add feature
    read-only field for 'tag'
    Fields for character gender, age (got lost with voices)
    Character name changes don't save
    Description changes don't save
    TTS Engine does not save    
    - Generate Description
    - "Random" button to randomize voice mixer settings
    - (Re)Generate Image

## BUGS

## FEATURES
    - Global "Default" for Narrator voice
    - "Enable Voice" toggle (like enable animation)  

    Max width for mixer? (generous?) w/horizontal scrollbar
    Layout with multiple effects will be awkward (future problem)
    
    voice selector option - "All", only when all the other category filters are in place.   

    New character 
        has wrong target (doubles up a bunch of the page)
        Uses name instead of tag as the characters.json dict key
    
## RESEARCH    
    can I train a flux-dev LORA with 12GB?  what exactly is the process?   
    img-to-img to build up a large enough sample for LORA training
    + manual culling of images that don't fit the desired outcome

    LORA character training

    Upload orignal images of the character from PD sources.
        Also for generating wardrobe suggestions.

    Character goal:  
        Whenever a character enters a scene they were not in previously, they begin with a psychological state and a motivation.  These can only be determined by the DIRECTOR.

        Each character has a finetune based on the material about that character in the book.  
        
        The DIRECTOR is responsible for interogating and prompting the characters while faithfully interpreting the book into a different medium.
       
        The goal is to determine the emotional state of the character to inform whatever sort of emote our TTS can provide for the lines, how the character should be described at selected moments in the text.  IE: Character pose and description of motion, "a picture was taken of CHARACTER in the scene at this point in the story.  Describe that picture."

        The potential output mediums for a DIRECTOR is a fascinating question.  One option is a sort of scene from a stage-play, "presented" through blender.

        Basic emote library w/skeletal motion, character models choose when the emote changes.

        models are skinned, dressed, thick AI decision making.

        Lots of little scenes, but they are all placed in a common world.  Procgen seed sort of world.  Every scene is placed precisely, its generation is what initializes that portion of the generated world, changing settings to match whatever the book wants.  any subsequest scene in the same place will already have those decisions made.  Prod-gen, but with an AI making intentional choices based on the story instead of randomly.

        we want a moldable real-world size 
        

# Audio Workshop
    DONE Populate pronunciation field, prefer it if it exists for audio generation
    DONE page load magic is too much, fast load, button to trigger the generate-all-missing action
    DONE "Merge with previous"
    DONE Add Image Slot

    Add advanced button to detect unpronouncable words and add stubs to the global pronounciation dict for them

    TMI images still need a *lot* of help


    Add Sound Slot 

    "Regenerate" doesn't busy-spin
    "Regenerate" doesnt' re-configure the audio player
    "Regenerate" doesn't include freshly changed pronunciation string

    Feature Rebalance - finds any image-to-image duration less than a threshold, and combines it with the previous or next phrase -- whichever is shorter.

    Finds any duration greater than a threshold, AI split it into two portions.

    New Character (stubbed but not implimented)

    Destroy Image Slot requires a manual refresh

    Split/Delete workflow is buggy, leads to weird navigational problems.

    Split should remove the pronunciation

    Split/Merge need to invalidate typography


# Image Workshop
    DONE [Save] for Meta Prompt (autosave)
    DONE Ability to select image from all previously generated
    DONE BUG copy from previous should carry over the meta prompt
        its copy from previous just broken entirely?  Nothing seems to work.
    DONE BUG Pose doesn't save
    DONE Pose/Action should be together
    DONE [Save] for Pose should htmx
    DONE Ability to delete image from previously generated
    DONE !! Separate T5 and CLIP prompts !!
        -----------------
        CLIP Prompt:
            Think of this as a list of tags, like "red roses, vibrant, close-up, photorealistic". It's ideal for specifying visual elements and artistic styles. 

        T5 Prompt:
            This is a more elaborate description, like "A vase of red roses sits on a wooden table near a window, bathed in warm afternoon light, photorealistic style". It helps define the scene's context and overall composition. 
        ----------------
    DONE Draw All Missing Images (untested)
    DONE copy from previous (untested)

    
    TODO:
        Apply Style is broken        
        Re-Prompt and Redraw All Images (untested)
        rebuild prompt from Metadata only updates the T5 prompt
        enhance prompt (untested)

    
    Front cover has no clip
    TMI destroys front cover

    Ditto for author page

    You can't choose a Mode then apply it without refreshing (choosing likely breaks the "apply mode" button)


    Per-image engine selection
    [Set Default] and [Paste Default] to go with [Copy Previous]
    [Save] for Action should htmx
    [Copy Unset]
    Meta prompt help listing available variables
        
    [PARTIAL] choose autopan/scale/crop for non-square images
    
    Next Image should be a link, maybe even a clean link
    BUG Location clickers are broken

    BUG Only characters listed in scene characters (ie: 1:1 with characters.json tags) should appear
    BUG rebuild prompt from meta - should use the cosmetic character names, not the tags

    BUG Draw This Prompt doesn't refresh the forex panel

    Feature - Clear Metadata for current image

    MAYBE FIXED BUG - removing a character from a scene requires a page refresh to remove the detail section for them.
    MAYBE FIXED BUG - adding a character to the scene requires a page refresh to be visible.

    BUG - removing a scene character doesn't remove them from focus_character

    DONE (differently) Settings should be bookmark-ble

    Bulk transition application

    # DONE (for Settings)
    not just copy previous, copy from [x] with stills of the most recent N images

    BUG "Use Image" doesn't work
    IMPROVEMENT Use Image and Delete Image should be right next to the image

    "Build Transition" should automatically delete, the "cut" transition type is effectively the same as "delete"

## Transition

DONE present the previous image, an empty frame, and the current image.
DONE the empty frame in the middle makes it contextually clear that is our concern.
DONE button to render the animation frames
NOT NEEDED button to make a video of the animation (throwaway)
DONE the middle window becomes the animation, with play button.

Choose a transition plugin, configure any parameters with suitable widgets.
save choices to transition namespaced tags on the image in the book xml

default is no transition, complete replacement in one frame.

^^ All re-usable tech for the animation interface

## Animate
    Twist
    Zoom to
    
    TODO

# Frames
    DONE Meta version of video.
    DONE Frame navigation
    DONE Single frame Re-Render
    First and last completed frame for each (phrase or image)?

# Video 
    DONE Full re-render option
    regenerate master plan sets duration=0 on all paragraphs!

    CRITICAL: The timing gap between audio segments should vary based on the punctuation used.

---------------------------



favicon

"Translated By" is coming in when it doesn't belong (example!)


# Transitions

[ ] zoom out transition, from pixelated zoomed in, simple, half second sort of flavor.  able to choose the initial x/y because that will become a visual focus.

# Latex

[X] There is a spacing problem in the Raven, there doesn't seem to be consistent vertical space only between stanzas.  

# Animations
[ ] The images are there, but they aren't being used?

# Audio
[ ] Slowly getting better?  This still sucks.  Can I use aeneas to help clean up the noice/halucinations?


# Development Flow
[ ] The new flow
This isn't bulk fire and forget.
You are a twat for thinking that could work.
There are phases.

(1) raw text to basic xml
(2) thematic style choices for the image rendering
(3) characterizations
    a) name, gender, age, accent and strength for each speaking character.
(4) first render
(5) adjust generated prompts for images
(6) adjust pronunciations with helpers
(7) choose transitions and animations.



move raw text to segments to automation
move segment text to phrases to automation
generate helpers for each phrase (phi3?)
AI determind who is speaking and add the tags
add any new characters to characters.json for the book


(DONE) Move TTS to the GPU worker path

what if I do a before and after prompt prefix, then treat those as two frames of a video and apply the up-framer to try and create some frames to put in between?  Maybe can blur frame to frame to gap fill; then we video-to-video with cogx, and maybe we come away with something that looks clean?

It's wild, but I can actually try it.


Should be fixed.
[ ] The image (tex and convert) is really painfully slow on big chapters (like Oedipus Rex).  Kicking the can on that until after the problem is resolved.  We don't really need the _whole_ chapter as an image, just the visible portion.  Some kind of chunking should be do-able.

Fixed
[ ] the seperate segment and phrase audio generation thing is stupid now that multi-voice works.  Doubles the length of a long pole.

Fixed
Each phrase gets a phrase.wav and a phrase.adjusted.wav that adds padding?

[ ] refactor 'phrase' to 'visual' because that is what it really is.

Obsolete
[ ] refactor 'segment' to 'sync frame' (ugg) What it really defines are the edges at which average scroll rates are calculated, the delta-v can be visible.  Between paragraphs feels less intrusive, even when the change is dramatic.

We want one at least every visible screen height to restrain the size of segments.

[ ] phrase get a file=filename attribute that is populated when it gets a value.

[ ] fancy named chapters, segments and phrases.

[ ] verse mode, per-segment?

[ ] move preface/prelude/afterword knobs inside book.xml





text clickthrough, 
character, manual de-dupe
audio go through whole thing, make sure the right character is attributed to each spoken phrase
audio 'generate all missing audio'
images, draw all missing images
typography, draw missing text FAILS BECAUSE THERE IS NO MASTERPLAN (TODO)
video, regenerate master plan
_then_ typography -> draw missing text
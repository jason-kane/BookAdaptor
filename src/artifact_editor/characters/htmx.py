import json
from os import name

import logger
from artifact_editor.tools import (
    generic_button,
)
from flask import url_for
import audio_effects
from .voices import KOKORO_VOICES
from .characters import (
    get_global_character,
    get_all_characters,
    condense_description,
    save_character,
)

log = logger.log(__name__)


def age_selection(chapter, character_dict, tag):
    update_age_url = url_for(
        "library.book.chapter.characters.set_age", **chapter.kwargs, character_tag=tag
    )
    return f"""
        <wa-select
            label="Age"
            name="age"
            value="{character_dict.get('age', '')}"
            hx-post="{update_age_url}"
            hx-target="#character_{tag}"
            hx-swap="outerHTML transition:true"
            hx-trigger="change delay:500ms"
        >
            <wa-option value="child">Child</wa-option>
            <wa-option value="young">Young Adult</wa-option>
            <wa-option value="middle_aged">Middle Aged</wa-option>
            <wa-option value="old">Old</wa-option>
        </wa-select>
"""


def gender_selection(chapter, character_dict, tag):
    update_gender_url = url_for(
        "library.book.chapter.characters.set_gender",
        **chapter.kwargs,
        character_tag=tag,
    )
    return f"""
    <wa-select
        label="Gender"
        name="gender"
        value="{character_dict.get('gender', '')}"
        hx-post="{update_gender_url}"
        hx-target="#character_{tag}"
        hx-swap="outerHTML transition:true"
        hx-trigger="change delay:500ms"
    >
        <wa-option value="male">Male</wa-option>
        <wa-option value="female">Female</wa-option>
        <wa-option value="other">Other/Unknown</wa-option>
    </wa-select>
    """


def headshot(character_dict, chapter, tag):
    OUT = f"""<div class="wa-stack" id="headshot_{tag}">"""
    cosmetic_name = character_dict.get("name", tag)
    author = chapter.author
    title = chapter.title
    chapter_number = chapter.number

    regenerate_url = url_for(
        "library.book.chapter.characters.regenerate_headshot",
        **chapter.kwargs,
        character_tag=tag,
    )

    headshot_src_url = url_for(
        "library.book.chapter.characters.headshot", **chapter.kwargs, character_tag=tag
    )

    if character_dict.get("headshot", False):
        # they DO have a headshot.
        return (
            OUT
            + f"""
        <div class="wa-frame:square" slot="header">
            <div class="wa-stack wa-align-items-center wa-gap-xs wa-caption-m">
                <img height="600px" width="600px" src="{headshot_src_url}" alt="{cosmetic_name}'s headshot">
            </div>
        </div>

        <wa-button
            hx-get="{ regenerate_url }"
            hx-target="#headshot_{tag}">Regenerate Image</wa-button>
    </div>
        """
        )
    else:
        # they do NOT have a headshot.
        return (
            OUT
            + f"""
        <div class="wa-frame:landscape" slot="header">
            <div class="wa-stack wa-align-items-center wa-gap-xs wa-caption-m">
                <wa-icon src="/static/fontawesome7/svgs/regular/image.svg"></wa-icon>
                <span>No Image</span>
            </div>
        </div>
    
        <wa-button
            hx-get="{regenerate_url}"
            hx-on::before-request="beforeRequest(this, event)"
            hx-on::after-request="afterRequest(this, event)"
            hx-target="#headshot_{tag}">Generate Image</wa-button>
    </div>
    """
        )


# Tip is a young boy from the country of the Gillikins in the Land of Oz. He was raised by an old woman named Mombi, who was rumored to have magical abilities. Despite being brought up by Mombi, Tip does not remember anything
def character_description_response(chapter, character_name, description):
    html_escaped_description = description.replace('"', "&quot;").replace("'", "&#39;")

    update_description_url = url_for(
        "library.book.chapter.characters.update_character_description",
        **chapter.kwargs,
        character_tag=character_name,
    )

    return f"""
    <wa-textarea 
        label="Description"
        name="description"
        id="{character_name}_description"
        value="{html_escaped_description}"
        hx-post="{update_description_url}"
        hx-trigger="change delay:500ms"
        rows="{len(description) // 100}"
        size="large"
        style="width: 100%"
    ></wa-textarea>
    """


def voice_selector(chapter, character, character_name):
    out = f"""<wa-select
        id="{character_name}_voice_selector"
        label="Voice"
        name="voice">"""

    character_voice_ids = [c["id"] for c in character.get("voices", [])]

    gender = character.get("voice_gender", "all")
    language = character.get("voice_language", "all")
    accent = character.get("voice_accent", "all")

    for voice_name in KOKORO_VOICES:
        # we already have this voice in the blend
        if voice_name in character_voice_ids:
            continue

        voice = KOKORO_VOICES[voice_name]
        if gender == "all" or gender == voice["gender"]:
            if language == "all" or language == voice["language"]:
                if (
                    accent == "all"
                    or accent == voice["accent"]
                    or voice["accent"] is None
                ):
                    out += (
                        f'<wa-option value="{voice["id"]}">{voice["name"]}</wa-option>'
                    )
                else:
                    log.info("Accent mismatch: %s != %s", accent, voice["accent"])
            else:
                log.info("Language mismatch: %s != %s", language, voice["language"])
        else:
            log.info("Gender mismatch: %s != %s", gender, voice["gender"])

    out += "</wa-select>"
    return out


def accent_selector(
    chapter, character, character_tag, gender="all", language="all", accent="all"
):
    gender = character.get("voice_gender", gender)
    language = character.get("voice_language", language)
    accent = character.get("voice_accent", accent)

    set_accent_url = url_for(
        "library.book.chapter.characters.voice_set_accent",
        **chapter.kwargs,
        character_tag=character_tag,
    )

    out = f"""<wa-select 
        label="Accent" 
        name="accent"
        value="{accent}"
        hx-target="#{character_tag}_engine" 
        hx-post="{set_accent_url}"
        hx-trigger="change">"""

    out += '<wa-option value="all">All</wa-option>'

    # only include accents that comply with the current gender and language filters
    accents = set()
    for voice_name in KOKORO_VOICES:
        voice = KOKORO_VOICES[voice_name]
        if gender == "all" or gender == voice["gender"]:
            if language == "all" or language == voice["language"]:
                accents.add(voice["accent"])

    if accent and accent not in accents:
        accent = "all"

    for accent in accents:
        out += f'<wa-option value="{accent}">{accent}</wa-option>'

    out += "</wa-select>"
    return out


def language_selector(chapter, character, character_tag, gender="all", language="all"):
    # only include languages that comply with these filters
    set_language_url = url_for(
        "library.book.chapter.characters.voice_set_language",
        **chapter.kwargs,
        character_tag=character_tag,
    )

    gender = character.get("voice_gender", gender)
    language = character.get("voice_language", language)

    out = f"""<wa-select 
        label="Language"
        value="{language}"
        name="language"
        hx-target="#{character_tag}_engine" 
        hx-post="{set_language_url}"
        hx-trigger="change">"""

    out += '<wa-option value="all">All</wa-option>'

    languages = set()
    for voice_name in KOKORO_VOICES:
        voice = KOKORO_VOICES[voice_name]
        if gender == "all" or gender == voice["gender"]:
            languages.add(voice["language"])

    for language in languages:
        out += f'<wa-option value="{language}">{language}</wa-option>'

    out += "</wa-select>"
    return out


def gender_selector(chapter, character, character_tag):
    set_voice_gender_url = url_for(
        "library.book.chapter.characters.voice_set_gender",
        **chapter.kwargs,
        character_tag=character_tag,
    )

    gender = character.get("voice_gender", "all")

    return f"""<wa-select 
        label="Gender" 
        value="{gender}" 
        name="gender" 
        hx-target="#{character_tag}_engine" 
        hx-post="{set_voice_gender_url}"
        hx-trigger="change">
        <wa-option value="all">All</wa-option>
        <wa-option value="male">Male</wa-option>
        <wa-option value="female">Female</wa-option>                        
    </wa-select>"""


def get_character_effects(chapterurl, character_name, character: dict | None = None):
    # we don't want to allow adding the same effect twice.
    effects_selector = audio_effects.registry.selector(
        skip=character.get("effects", {}).keys(), ns=f"{character_name}"
    )

    effects_configuration = ""
    log.info("character effects: %s", character.get("effects", {}))
    for effect_key, effect_config in character.get("effects", {}).items():
        effect = audio_effects.registry.get_effect(effect_key)
        if effect is None:
            log.error("Effect %s not found in registry", effect_key)
            continue

        log.info("Rendering configuration for effect %s", effect_key)
        widget = effect().get_configuration_widgets(
            chapterurl=chapterurl,
            character_name=character_name,
            effect_config_dict=effect_config,
        )
        effects_configuration += widget

    return f"""
    <div class="wa-stack" id="{character_name}_effects">
        <div class="wa-cluster wa-align-items-end">
            {effects_selector}

            <wa-button 
                hx-post="/{chapterurl}/characters/{character_name}/add_effect"
                hx-target="#{character_name}_effects"
                hx-include="#{character_name}_effect_selector"
                hx-swap="outerHTML">
                <wa-icon src="/static/fontawesome7/svgs/solid/plus.svg" label="Add Effect"></wa-icon>
            </wa-button>
        </div>
        {effects_configuration}
    </div>
    """


def single_voice_mixer(chapter, character_tag, voice_id, voice_name, voice_strength):
    set_voice_strength_url = url_for(
        "library.book.chapter.characters.voice_set_strength",
        **chapter.kwargs,
        character_tag=character_tag,
        voice_id=voice_id,
    )

    mixer = f"""
        <wa-slider {"class='inactive'" if voice_strength in [0, '0', ''] else ''}
            id="{character_tag}_{voice_id}_mixer"
            name="{voice_id}"
            orientation="vertical"
            value="{voice_strength}"
            label="{voice_name}" 
            hx-put="{set_voice_strength_url}"
            hx-trigger="change"
            hx-target="#{character_tag}_{voice_id}_mixer"
            hx-swap="outerHTML"
            hx-on::after-request="voice_weights_have_changed('{character_tag}')"
            min="0"
            max="100"
            step="1"
            with-tooltip
            with-markers>
        </wa-slider> 
    """
    return mixer


def voice_blender(
    chapter,
    character: dict,
    character_tag: str,
    filename=None,
):
    if not character_tag:
        log.error("voice_blender invoked with no character name!")

    gender = character.get("voice_gender", "all")
    language = character.get("voice_language", "all")
    accent = character.get("voice_accent", "all")

    all_voices = character.get("voices", [])

    if all_voices:
        mixer = f"""<div 
            id='{character_tag}_voice_selection_panel' 
            class='wa-cluster'
        >"""

        for voice in all_voices:
            voice_name = voice.get("name", "")
            voice_id = voice.get("id", "")
            voice_strength = voice.get("strength", 50)
            mixer += single_voice_mixer(
                chapter, character_tag, voice_id, voice_name, voice_strength
            )

            # don't delete.  just let the bottom be zero.  good enough.
            # <wa-button
            #     hx-post="/{chapterurl}/characters/{character_name}/actions/remove_voice"
            #     hx-target="#{character_name}_mixer"
            #     hx-swap="outerHTML transition:true"
            #     hx-include="true"
            #     name="voice_id" value="{voice_id}">-</wa-button>
        mixer += "</div>"

        log.info("character: %s", character)
        log.info("gender: %s, language: %s, accent: %s", gender, language, accent)

    effects = get_character_effects(chapter, character_tag, character)

    if filename is None:
        filename = "sample_audio.wav"

    else:
        log.info("No voices for character %s", character_tag)

    log.info(
        "Generating audio sample source with %s",
        json.dumps(
            {
                "character_tag": character_tag,
                "filename": filename,
                "**": chapter.kwargs,
            }
        ),
    )

    audio_src = url_for(
        "library.book.chapter.characters.sample_audio",
        character_tag=character_tag,
        filename=filename,
        **chapter.kwargs,
    )

    audio_sample_source = f"""<source
        src="{audio_src}"
        type="audio/wav"></source>"""

    create_audio_src = url_for(
        "library.book.chapter.characters.create_audio_sample",
        character_tag=character_tag,
        **chapter.kwargs,
    )

    playsample = f"""
    <form>
        <div class="wa-stack">
            <wa-textarea 
                id="{character_tag}_sample"
                name="sample_sentence"
                value="It makes me uncomfortable when you look at me like that." 
                placeholder="Type a sample sentence to test the voice."></wa-textarea>

            <div class="wa-cluster">
                <wa-button
                    hx-post="{create_audio_src}"
                    hx-include="#{character_tag}_voice_selector"
                    hx-target="#{character_tag}_audio_player">
                    <wa-icon src="/static/fontawesome7/svgs/solid/refresh.svg" label="Generate"></wa-icon>
                </wa-button>

                <audio 
                    id="{character_tag}_audio_player"
                    hx-on::after-swap="console.log('loading...'); this.load(); this.play()"
                    controls
                >
                {audio_sample_source}
                </audio> 
            </div>
        </div>
    </form>"""

    add_voice_url = url_for(
        "library.book.chapter.characters.add_voice_to_blend",
        **chapter.kwargs,
        character_tag=character_tag,
    )

    out = f"""
            <div id="{character_tag}_engine" class="wa-stack">
                <!-- Kokoro Voice Configuration --> 
                <div class="wa-cluster wa-align-items-end">
                    {gender_selector(chapter, character, character_tag)} 
                    {language_selector(chapter, character, character_tag)} 
                    {accent_selector(chapter, character, character_tag)}
                    {voice_selector(chapter, character, character_tag)}

                    <wa-button 
                        hx-post="{add_voice_url}"
                        hx-include="#{character_tag}_voice_selector"
                        hx-target="#{character_tag}_engine"
                        hx-on::after-request="if (event.detail.successful) character_has_voice('{character_tag}')"
                        hx-swap="outerHTML">
                        <wa-icon src="/static/fontawesome7/svgs/solid/plus.svg" label="Add"></wa-icon>
                    </wa-button>
                </div>"""

    if all_voices:
        out += f"""
                <div id="{character_tag}_mixer" class="wa-cluster wa-align-items-start">
                    {mixer}
                    {effects}
                    {playsample}
                </div>
                """
    out += "</div>"

    return out


def all_character_cards(chapter, characters):
    out = ""
    for character_tag, character in characters.items():
        one_character = character_data(chapter, character)
        if one_character is not None:
            out += one_character
    return out


def character_pronunciation_widget(chapterurl, tag, pronunciation):
    return f"""
    <wa-slider
        label="Pronunciation Filter"
        hint="Enunciate as if part of this era"
        name="pronunciation_filter"
        value="{pronunciation}"
        min="700"
        max="2000"
        step="50"
        hx-put="/{chapterurl}/characters/{tag}/pronunciation_filter"
        hx-trigger="change delay:500ms"
        hx-target="#phrase-{tag}-pronunciation-filter"
        id="phrase-{tag}-pronunciation-filter"
        hx-swap="outerHTML"
        indicator-offset="0"
        with-markers
        with-tooltip
    >
        <span slot="reference">Old</span>
        <span slot="reference">Middle</span>
        <span slot="reference">Early</span>
        <span slot="reference">Late</span>
        <span slot="reference">Now</span>
    </wa-slider>  
    """


def character_data(chapter, character_dict):
    """
    Full edit block for a single character.
    None for an invalid character
    """
    log.info("Rendering character data for %s", character_dict)
    # things called multiple times
    tag = character_dict["tag"]
    name = character_dict["name"]

    global_character = character_dict.get("is_global", False)

    # fields with defaults
    if global_character:
        # no buddy, this is a _global_ character.
        # override the character dict on every
        # load with the global character data.
        character_dict = get_global_character(chapter, character_dict["name"])
        tag = character_dict["tag"]

    if "(" in tag:
        # invalid
        return None

    description = character_dict.get("description", "")
    if isinstance(description, list):
        description = condense_description(description)
        character_dict["description"] = description
        save_character(chapter, character_dict["tag"], character_dict)

    tts_engine = character_dict.get("tts_engine", "kokoro")
    visual_appearances = character_dict.get("visual_appearances", 0)
    audio_appearances = character_dict.get("audio_appearances", 0)

    blender = voice_blender(
        chapter=chapter,
        character=character_dict,
        character_tag=tag,
    )

    pronunciation_filter = character_dict.get("pronunciation_filter", 2000)

    character_pronunciation_widget_html = character_pronunciation_widget(
        chapter.url, tag, pronunciation_filter
    )

    has_positive_voice_weights = any(
        voice["strength"] > 0 for voice in character_dict.get("voices", [])
    )
    has_voice_weights = "voice_selection_panel" in blender

    return f"""
    <div 
        style="border: 1px solid #e0e0e0; padding: 16px; border-radius: var(--wa-border-radius-m); position: relative;" 
        id="character_{tag}"
        class="wa-stack"
    >
        <div id="{tag}_scope" class="wa-split wa-gap-0">    
            <div class="wa-stack" id="{tag}_left_column" style="width: 50%">
                <div class="wa-cluster">
                    <h2>{name}</h2>
                    <h4>({tag})</h4>
                    [{visual_appearances}/{audio_appearances} visual/audio]
                </div>

                <wa-switch
                    hx-post="{ url_for('library.book.chapter.characters.toggle_global_character', **chapter.kwargs, character_name=tag) }"
                    hx-target="#character_{tag}"
                    {"checked" if global_character else ""}
                >Global Character</wa-switch>

                <wa-input 
                    label="Character Name" 
                    name="name" 
                    value="{name}"
                    hx-post="{ url_for('library.book.chapter.characters.set_name', **chapter.kwargs, character_tag=tag) }"
                    hx-trigger="change delay:500ms"
                    hx-target="#{tag}_scope">
                </wa-input>

                {character_description_response(chapter, tag, description)}
                <div class="wa-cluster">
                    <wa-button 
                        hx-post="{ url_for('library.book.chapter.characters.generate_description', **chapter.kwargs, character_tag=tag) }"
                        hx-target="#{tag}_description"
                        hx-swap="outerHTML transition:true">Extract Text Description</wa-button>

                    <wa-button 
                        hx-post="{ url_for('library.book.chapter.characters.fanciful_description', **chapter.kwargs, character_tag=tag) }"
                        hx-target="#{tag}_description"
                        hx-swap="outerHTML transition:true">Fanciful Description</wa-button>                        

                    <wa-button 
                        hx-post="{ url_for('library.book.chapter.characters.condense_description', **chapter.kwargs, character_name=tag) }"
                        hx-target="#{tag}_description"
                        {"disabled" if not description else ""}
                        hx-swap="outerHTML transition:true">Condense Description</wa-button>
                </div>

                <div class="wa-cluster">
                    {gender_selection(chapter, character_dict, tag)}
                    {age_selection(chapter, character_dict, tag)}
                </div>

                <wa-select
                    label="TTS Engine" 
                    value="{tts_engine}" 
                    hx-post="{ url_for('library.book.chapter.characters.update_tts_engine', **chapter.kwargs, character_tag=tag) }"
                    hx-trigger="change delay:500ms"
                    name="tts_engine">
                    <wa-option value="kokoro">Kokoro</wa-option>
                </wa-select>                

                {blender}
                {character_pronunciation_widget_html}                

            </div>
            
            <div class="wa-stack" id="{tag}_right_column">
                {headshot(character_dict, chapter, tag)}
            </div>
        </div>

        <div class="wa-stack" id="{tag}_after_split">

            <div class="wa-cluster">

                <wa-button {'' if has_positive_voice_weights else 'disabled'}
                    id="{tag}_remove_voices"
                    hx-post="{ url_for('library.book.chapter.characters.remove_voice_weights', **chapter.kwargs, character_name=tag) }"
                    hx-target="#{tag}_engine" 
                    hx-swap="outerHTML transition:true">Remove Voice</wa-button>        

                <wa-button 
                    id="{tag}_randomize_voices"
                    hx-post="{ url_for('library.book.chapter.characters.randomize_voice_weights', **chapter.kwargs, character_name=tag) }"
                    hx-target="#{tag}_engine"
                    {'disabled' if not has_voice_weights else ''}
                    hx-swap="outerHTML transition:true">Randomize Voice Weights</wa-button>
                
                <wa-button 
                    hx-delete="{ url_for('library.book.chapter.characters.delete_character', **chapter.kwargs, character_tag=tag) }"
                    hx-target="#character_cards"
                    hx-include="#{tag}_scope"
                    variant="danger"
                    hx-swap="outerHTML transition:true">Delete</wa-button>
            </div>
        </div>
    </div>
    """


def drift_resolution_widget(chapter):
    out = f"""
    <div id="character_drift_resolution">
    <h2>Character Drift Resolution</h2>

    <p>Unattached Speakers - these are speakers referenced in the xml but we do not have a character with that name.</p>
    <p>We want to either create a character using the new character form (above), or rename references from this unattached speaker to another, valid speaker.</p>
    <p>These should be so obvious as to make this feel stupid.</p>
    <div class="wa-stack">
    """
    all_characters = get_all_characters(chapter)
    unattached_speakers = set()
    for phrase in chapter.get_xml().find_all("phrase"):
        character_name = phrase.get("speaker")
        if character_name and character_name not in all_characters:
            unattached_speakers.add(character_name)

    for speaker in unattached_speakers:
        set_name_url = url_for(
            "library.book.chapter.characters.set_name",
            **chapter.kwargs,
            character_tag=speaker,
        )
        out += f"""
            <h3>"{ speaker }"</h3>
            <form>
                <div class="wa-cluster">
                    <wa-select
                        name="rename_to"
                        hx-post="{ set_name_url }"
                        hx-target="#character_drift_resolution"
                        hx-swap="outerHTML transition:true">
                        <wa-option value="">Select Character</wa-option>"""

        for character in all_characters:
            c = all_characters[character]
            out += f'<wa-option value="{ character }">{ c['name'] }</wa-option>'

        out += f"""  </wa-select>
               
                  <wa-button
                    hx-post="{ url_for('library.book.chapter.characters.drift_resolution', **chapter.kwargs, character_name=speaker) }"
                    hx-target="#character_drift_resolution"
                    hx-swap="outerHTML transition:true">Rename</wa-button>
                
                </div>               
            </form>
            """

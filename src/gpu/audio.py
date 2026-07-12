import contextlib
import math
import os
import shutil
import wave
from bs4 import BeautifulSoup
import re

import audio_effects
import const
import logger

from artifact_editor.characters import characters
from artifact_editor.author.author import Author
from artifact_editor.chapter.chapter import Chapter
from artifact_editor.audio.pronunciation import pronunciation

import numpy as np
import random

from artifact_editor.audio import utterances

# KOKORO_VOICES
from artifact_editor.characters import voices


log = logger.log(__name__)


def _create_audio_kokoro(
        character_name, 
        character, 
        text, 
        pronunciation_ipa, 
        wavfile, 
        chapter_args,
    ):
    """
    no cloning, but we can blend voices and adjust IPA. 
    )
    [Kokoro](/kˈOkəɹO/)
    """
    import soundfile as sf
    from kokoro_onnx import Kokoro
    from misaki import en

    log.info(f'create_audio_kokoro({character_name=}, {text=}, {pronunciation_ipa=}, {wavfile=}, {chapter_args=})')
    chapter_args[0] = Author(chapter_args[0])
    chapter = Chapter(*chapter_args)

    kokoro = Kokoro("kokoro-v1.0.fp16-gpu.onnx", "voices-v1.0.bin")

    if character.get('voices') is None:
        log.warning('Using random voice for character %s', character_name)
        #Use the character attributes "accent" and "gender" to reduce the pool
        #of voices we will otherwise randomly choose from.
        all_voices = voices.KOKORO_VOICES.copy()
        voice_names = list(all_voices.keys())
        if character['accent'] == 'british':
            for voice_name in voice_names:
                if voice_name[0] != 'b':
                    del all_voices[voice_name]

        elif character['accent'] == "american":
            for voice_name in voice_names:
                if voice_name[0] != 'a':
                    del all_voices[voice_name]

        voice_names = list(all_voices.keys())
        if character['gender'] == 'male':
            for voice_name in voice_names:
                if voice_name[1] != 'm':
                    del all_voices[voice_name]

        elif character['gender'] == 'female':
            for voice_name in voice_names:
                if voice_name[1] != 'f':
                    del all_voices[voice_name]

        voice_names = list(all_voices.keys())
        log.info('Selecting from remaining %s voices', len(voice_names))

        if character.get('kokoro_voice') is not None:
            # use it if we got it, and it's legit.
            if character['kokoro_voice'] in voice_names:
                log.debug('Using existing kokoro voice %s', character['kokoro_voice'])
                voice_name = character['kokoro_voice']

        if voice_name is None:
            voice_name = random.choice(voice_names)

        # [{'id': 'af_nova', 'name': 'Nova', 'strength': 50}, 
        #  {'id': 'af_kore', 'name': 'Kore', 'strength': 50}, 
        #  {'id': 'am_onyx', 'name': 'Onyx', 'strength': 50}] 

        if voice_name not in all_voices:
            log.error(f'Voice {voice_name} not found in KOKORO_VOICES')
            raise ValueError(f'Voice {voice_name} not found in KOKORO_VOICES')

        voice_list = [
            {
                'id': voice_name,
                'name': all_voices[voice_name]['name'],
                'strength': 50
            }
        ]
        character['voices'] = voice_list
        all_characters = characters.get_all_characters(chapter)
        all_characters[characters.name_to_tag(character_name)] = character
        # and save it.
        characters.save_characters(chapter, all_characters)
    
    weight_list = [ v['strength'] for v in character['voices'] ]
    total = sum(weight_list)
    
    if total != 100:
        log.warning('Voice weights do not sum to 100%, normalizing')
        weight_list = [w * (100 / total) for w in weight_list]

    for index, v in enumerate(character['voices']):
        log.info(f'{v["id"]}: {weight_list[index]}')

    blended_voice = np.average(
        [ kokoro.get_voice_style(v['id']) for v in character['voices'] ],
        axis=0,
        weights=weight_list
    ).astype(np.float32)

    log.info('Using blended voice %s', blended_voice)

    # log.info('Using kokoro voice %s', character['kokoro_voice'])
    if not pronunciation_ipa:
        if 'voice_accent' not in character and 'accent' in character:
            character['voice_accent'] = character['accent']
            del character['accent']

        g2p = en.G2P(trf=False, british=character['voice_accent'] == 'british')
        pronunciation_ipa, _ = g2p(text)
        log.info(f'Generated pronunciation: "{pronunciation_ipa}"')

    try:
        samples, sample_rate = kokoro.create(
            pronunciation_ipa,
            voice=blended_voice,
            is_phonemes=True,
            speed=1,
        )
    except ValueError as e:
        log.error(f'Error generating audio with Kokoro: {e}')
        return None, pronunciation_ipa

    sf.write(
        wavfile,
        samples,  # audio_cpu,
        sample_rate # 24000
    )

    return wavfile, pronunciation_ipa


def xml_to_list_of_wav(chapter, workdir, xml_text):
    """
    xml_text is a single stringified <phrase/> tag.

    return a sorted list of wav filenames, the caller will glue them together.

    we are an audio object.  our children are tags which are the names of
    characters.  content that is not tagged defaults to the narrator.

    We need a sequence of audio files.  each audio file only includes one
    speaker.  The more back and forth between characters, the more audio files.

    The files are also split up above us with multiple <audio> tags when one
    character is speaking.  This lets us decide where breaks go between
    generated fragments.  We aren't letting xtts break sentences so we must do it.

    These breaks are also the chunks where the text display highlights text.
    """
    log.info("xml_to_list_of_wav() xml_text: %s", xml_text)
    bookdir = chapter.bookdir

    audiodir = os.path.join(const.LIBRARY_DIR, workdir, "audio")
    os.makedirs(audiodir, exist_ok=True)

    phrase_xml = BeautifulSoup(xml_text, "xml").find('phrase')

    if phrase_xml is None:
        log.error("No <phrase> tag found in xml_text")
        return []
    out = []
    index = 0
       
    # at this point speaker is required.
    log.info(f'phrase_xml.attrs: {phrase_xml.attrs}')
    speaker_name = phrase_xml.attrs['speaker']
    
    character = characters.get_character(
        chapter,
        speaker_name
    )
    log.info(f'Character for speaker {speaker_name}: {character}')

    text_to_speak = phrase_xml.get_text()
    
    # for text_to_speak in phrase_xml.children:
    if True:
        # clear problematic latex bits that are prone to leaking through.
        for ltx, rpl in ((r"\\", ""), (r"\nobreak", ""), ("—", " "), ("_", "")):
            text_to_speak = text_to_speak.replace(ltx, rpl)

        # this is tricky, because it's really a bug we're working around.
        # the colon is sliding through into the IPA and causing errors.
        # a period will give us a pause, which is more or less what we want.
        text_to_speak = text_to_speak.replace(": ", ". ")

        # and the damnable []s
        text_to_speak = text_to_speak.replace("[", "").replace("]", "")
        
        text_to_speak = pronunciation.apply_global_pronunciations(
            chapter,
            text_to_speak
        )
        # text_to_speak is now decorated with [word](pronunciation) strings from
        # the global pronunciations.
        
        # regularize spacing.
        text_to_speak = " ".join(text_to_speak.split()).strip()
        if text_to_speak and character:
            # we're good to go.

            log.info(f'Speaking "{text_to_speak}" as {speaker_name}')
                
            if text_to_speak == "***":
                # dinkus, say nothing.
                log.info('Dinkus found, generating silence')
                duration = float(phrase_xml.attrs.get('duration', '1.0'))
                # a duration length wav file of silence.
                sample_rate = 24000
                nframes = int(sample_rate * duration)
                wavfile = os.path.join(
                    audiodir, f"page_{index}_{duration}_dinkus.wav"
                )

                with wave.open(wavfile, 'w') as h:
                    h.setnchannels(1)
                    h.setsampwidth(2)  # 16 bits
                    h.setframerate(sample_rate)
                    h.setnframes(nframes)
                    h.setcomptype('NONE', 'not compressed')
                    h.writeframes(b'\0' * nframes * 2)

                pronunciation_ipa = ""
            
            else:                
                # string or None
                pronunciation_ipa = phrase_xml.attrs.get('pronunciation')

                log.info(f'Pronunciation: {pronunciation_ipa}')
                log.info(f'phrase.attrs: {phrase_xml.attrs}')

                # can't pronounce underscores.
                text_to_speak = text_to_speak.replace("_", "").strip()

                # there are some.. quirks.
                if "[[" in text_to_speak:
                    # we got [[soul](/soʊl/)]  
                    #
                    # the outer brackets break kokoro, they shouldn't but they do.
                    # we want to keep the inner pronunciation, though, and any text outside the inner brackets.
                    if re.match(r"\[\[.*\]\(.*\)\]", text_to_speak):
                        log.info('Detected [[word](pronunciation)] problem pattern, extracting word and pronunciation')
                        match = re.match(r"(.*)\[\[(.*)\]\((.*)\)\](.*)", text_to_speak)
                        if match:
                            text_to_speak = f"{match.group(1)}[{match.group(2)}]({match.group(3)}){match.group(4)}"
                            log.info(f'Extracted text_to_speak: "{text_to_speak}", pronunciation_ipa: "{pronunciation_ipa}"')
                        else:
                            log.warning('Failed to parse [[word](pronunciation)] pattern, leaving text unchanged')

                # before, for the more common case where we've already pronounced
                # at least once.  Reprocessing the accent is not a big deal, and
                # I want to keep that code exercised right now.
                #
                # later we will believe filtered_pronounciation if it exists.
                if pronunciation_ipa and character.get('pronunciation_filter'):
                    filter_value = int(character.get('pronunciation_filter'))
                    log.info(f'Filtering pronunciation for: {filter_value}')
                    # filtered_pronunciation
                    # wait, seriously?  Hell yes.
                    pronunciation_ipa = utterances.accentByYear(year=filter_value).apply(pronunciation_ipa)
                    phrase_xml.attrs['filtered_pronunciation'] = pronunciation_ipa
                else:
                    log.info('No one shot pronunciation filter applied')
                    log.info(f'{phrase_xml.attrs=}')

                log.info("Submitting '%s' to Kokoro as %s", text_to_speak, character)
                
                wavfile, pronunciation_ipa = _create_audio_kokoro(
                    character_name=speaker_name,
                    character=character,
                    text=text_to_speak,
                    pronunciation_ipa=pronunciation_ipa,
                    # this is an absolute path 
                    wavfile=os.path.join(audiodir, f"page_{index}.wav"),
                    chapter_args=chapter.args
                )

                if pronunciation_ipa and character.get('pronunciation_filter'):
                    if phrase_xml.attrs.get('filtered_pronunciation') is None:
                        # first time, we now have generated modern IPA.

                        filter_value = int(character.get('pronunciation_filter'))
                        log.info(f'Filtering pronunciation for: {filter_value}')
                        log.info(f'Before: {pronunciation_ipa}')
                        # wait, seriously?  Hell yes.
                        filtered_pronunciation = utterances.accentByYear(year=filter_value).apply(pronunciation_ipa)
                        log.info(f' After: {filtered_pronunciation}')

                        wavfile, filtered_pronunciation = _create_audio_kokoro(
                            character_name=speaker_name,
                            character=character,
                            text=text_to_speak,
                            pronunciation_ipa=filtered_pronunciation,
                            # this is an absolute path 
                            wavfile=os.path.join(audiodir, f"page_{index}.wav"),
                            chapter_args=chapter.args
                        )

                        phrase_xml.attrs['filtered_pronunciation'] = filtered_pronunciation
                        pronunciation_ipa = filtered_pronunciation
                else:
                    log.info('No one shot pronunciation filter applied')
                    log.info(f'{phrase_xml.attrs=}')

                if wavfile and character.get('effects', ''):
                    for key, config_dict in character.get('effects', {}).items():
                        log.info('Applying effect %s %s', key, config_dict)
                        effect = audio_effects.registry.get_effect(key)
                
                        wavpath = os.path.dirname(wavfile)
                        wavname = os.path.basename(wavfile)
                        wavbase = os.path.splitext(wavname)[0]

                        out_wavfile = os.path.join(
                            wavpath,
                            wavbase + f".{key}.wav"
                        )

                        effect().apply(
                            config_dict,
                            input_wav_filename=wavfile,
                            output_wav_filename=out_wavfile
                        )

                        wavfile = out_wavfile

            if wavfile:
                index += 1
                out.append((
                    os.path.join(
                        bookdir, 
                        wavfile
                    ),
                    pronunciation_ipa
                ))

    # tuples of ('wavfile', 'pronunciation')
    # in many cases there will only be one entry.  Do not assume.
    log.info("Returning", out=out)
    return out




def _speak(chapter_spec, xml_text, wavfile, workdir, done_flag_fn):
    """
    called from the GPU task fifo

    We're going to make the source text carry the weight by moving from plain
    text to a very limited XML-ish format

    xml_text is a stringified <PHRASE/> 
    """
    log.info('_speak(%s, %s, %s, %s, %s)', chapter_spec, xml_text, wavfile, workdir, done_flag_fn)
    gap = 0.2  # in seconds, between wavs
    log.info('workdir', workdir=workdir)
    os.makedirs(workdir, exist_ok=True)
    chapter_spec[0] = Author(chapter_spec[0])
    chapter = Chapter(*chapter_spec)

    wavlist = xml_to_list_of_wav(chapter, workdir, xml_text)

    pronunciation_file = wavfile + ".pronunciation"

    first = True
    log.info("Processing wav list", wavlist=wavlist)
    full_pronunciation = []
    for wav, wav_pronunciation in wavlist:
        log.info("Processing wav file", wav=wav)
        if wav is None:
            continue

        full_pronunciation.append(wav_pronunciation)

        if first:
            # the first one is easy.
            os.makedirs(os.path.dirname(wavfile), exist_ok=True)

            log.info("Copying wav file", src=wav, dest=wavfile)
            shutil.copyfile(
                wav, 
                wavfile
            )
            log.info("Copy Complete")
            # if pre_padding:
            #     wav_prepend_delay(wavfile, pre_padding)
            first = False
        else:
            # append the next wav to the current wave file with a gap of silence between them
            log.info(f'Loading audio frames from {wavfile}...')
            with contextlib.closing(wave.open(wavfile, "rb")) as input:
                nchannels, sampwidth, framerate, nframes, comptype, compname = (
                    input.getparams()
                )
                all_frames = input.readframes(nframes)

            if gap > 0:
                # add 'gap' (float) seconds of silence between wavs
                log.info('Saving frames with trailing gap...', gap=gap, wavfile=wavfile)
                with contextlib.closing(wave.open(wavfile, "wb")) as out:
                    out.setparams(
                        (
                            nchannels,
                            sampwidth,
                            framerate,
                            nframes,
                            comptype,
                            compname,
                        )
                    )
                    # gap seconds of silence
                    all_frames += b"\0" * int(math.ceil(framerate * gap))
                    out.writeframes(all_frames)

            # read the 'new' wave file and append it
            log.info('Reading audio frames...', wav=wav)
            with contextlib.closing(wave.open(wav, "rb")) as input:
                nchannels, sampwidth, framerate, nframes, comptype, compname = (
                    input.getparams()
                )
                all_frames += input.readframes(nframes)

            # save the new all_frames to disk
            log.info('Saving...', wavfile=wavfile)
            with contextlib.closing(wave.open(wavfile, "wb")) as out:
                out.setparams(
                    (nchannels, sampwidth, framerate, nframes, comptype, compname)
                )
                out.writeframes(all_frames)

        os.makedirs(
            os.path.dirname(pronunciation_file),
            exist_ok=True,
        )
        with open(pronunciation_file, "w") as h:
            # save the pronunciation as a string
            h.write(" ".join(full_pronunciation))

    # if not first and extra > 0:
    #     # add 'extra' (float) seconds of silence
    #     wav_append_delay(wavfile, extra)

    # generate audio timings for this page
    # if not os.path.exists(wavfile + ".json"):
    #     # generate audio timings w/whisper
    #     npaudio = whisper.load_audio(os.path.abspath(wavfile))
    #     model = whisper.load_model("tiny")
    #     result = whisper.transcribe(model, npaudio)
    #     with open(wavfile + ".json", "w") as h:
    #         json.dump(result, h, indent=2)

    log.info("Setting done flag", done_flag_fn=done_flag_fn)
    with open(done_flag_fn, 'w') as h:
        h.write('done')

    log.info("Returning", wavfile=wavfile)
    return wavfile


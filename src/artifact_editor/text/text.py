import itertools
import json
import re
from collections import Counter

import nltk
import torch

import logger
from artifact_editor.characters.characters import (
    add_character,
    get_all_characters,
    name_to_tag,
)
from artifact_editor import llm

log = logger.log(__name__)
device = "cuda" if torch.cuda.is_available() else "cpu"


def detect_paragraph_technique(paragraph_text):
    """
    Detect the technique we should use to identify speakers in the paragraph
    text.

    We support two options right now.  The first is a socratic dialog.  The
    format is very simple. For example:

    SOCRATES: Yes, I have.

    MENO: And did you not think that he knew?

    Right, very simple.

    The second option is much more clever.  It's based on the clean dialog in
    "The Monkeys Paw".  We use quote marks to identify when someone is speaking,
    then throw AI at determining exactly which character is doing that speaking.
    It mostly works, which is kind of amazing.

    This is a simple heuristic to determine the technique we should use to
    identify speakers in the paragraph text.

    Returns:
        str: The technique to use. Options are "socratic" or "dialog".
    """
    # Simple heuristic: if there are quote marks in the text, assume it's dialog.
    if '"' in paragraph_text or "“" in paragraph_text or "”" in paragraph_text:
        log.info("Quotation marks found, choosing dialog technique")
        return "dialog"

    # If there are no quotes but there is a pattern of character names followed by colons,
    # we can assume it's a socratic dialog.
    if re.search(r"^\s*[A-Z ]+:\s", paragraph_text):
        log.info("Simple character names found, choosing socratic technique")
        return "socratic"

    # Default to dialog if nothing else matches
    log.info("Unable to detect technique, defaulting to dialog")
    log.info("basis: %s", paragraph_text)
    return "dialog"


MAX_PHRASE = 10


def chunk_string(line):
    if len(line.split()) > MAX_PHRASE:
        linelist = line.split()

        while linelist:
            buildup = []
            while len(buildup) < MAX_PHRASE:
                l = ""
                while ";" not in l and "," not in l and "." not in l:
                    if linelist:
                        l = linelist.pop(0)
                        buildup.append(l)
                    else:
                        break

                if not linelist:
                    break

            yield " ".join(buildup)
    else:
        yield line


previous_speaker = None


def socratic_to_paragraph(
    soup,
    chapterdir,
    paragraph_text,
    paragraph,
    hints,
    characters_dict,
):
    """
    Process a paragraph of text using the socratic dialog technique.

    This assumes that the paragraph text is in a format like:

    SOCRATES: Yes, I have.
    MENO: And did you not think that he knew?

    We will split the text on new lines and identify the speakers based on
    the pattern of capitalized names followed by a colon.

    Args:
        paragraph_text (str): The raw text of one paragraph.
        paragraph (ET.Element): The XML element to populate with the processed data.
        hints (str): Additional context or hints for identifying speakers.
        characters_dict (dict): A dictionary of known characters.

    Returns:
        None
    """
    global previous_speaker

    if paragraph_text.strip()[0] == "(":
        if paragraph_text.strip()[-1] == ")":
            paragraph_text = paragraph_text.strip()[1:-1]

    pre_colon = paragraph_text.split("\n")[0].split(":")[0]
    if pre_colon == pre_colon.upper():
        speaker_name = pre_colon
        spoken_text = paragraph_text.split(":", maxsplit=1)[1]
    else:
        # the same speaker is still talking
        speaker_name = previous_speaker
        spoken_text = paragraph_text

    previous_speaker = speaker_name

    character_names = characters_dict.keys()

    # Normalize speaker name to tag
    speaker = name_to_tag(speaker_name)

    if speaker not in character_names:
        add_character(
            chapterdir=chapterdir, character_name=name_to_tag(speaker), chardict={}
        )
        character_names = get_all_characters(chapterdir).keys()

    paragraph.add_element("image")

    for partial_line in chunk_string(spoken_text):
        phrase = paragraph.add_element("phrase")
        phrase.attrs["speaker"] = speaker
        # character = ET.SubElement(phrase, speaker)
        phrase.text = partial_line.replace("\n", " ")


def sentence_NER(text):
    from transformers import AutoModelForTokenClassification, AutoTokenizer, pipeline
    # https://huggingface.co/dslim/bert-base-NER

    tokenizer = AutoTokenizer.from_pretrained("dslim/bert-base-NER")
    model = AutoModelForTokenClassification.from_pretrained("dslim/bert-base-NER")

    nlp = pipeline(
        "ner",
        model=model,
        tokenizer=tokenizer,
        aggregation_strategy="simple",
        device=device,
    )

    ner_results = nlp(text)
    return ner_results


def llm_query(prompt: str) -> str:
    """
    Query the LLM with the given prompt and return the response.

    Args:
        prompt (str): The prompt to send to the LLM.

    Returns:
        str: The response from the LLM.
    """
    return llm.str_prompt(prompt)


def llm_query_old(prompt):
    # Load model directly
    from llama_cpp import Llama

    llm = Llama.from_pretrained(
        repo_id="bartowski/Meta-Llama-3.1-8B-Instruct-GGUF",
        filename="Meta-Llama-3.1-8B-Instruct-IQ2_M.gguf",
        verbose=False,
        n_gpu_layers=10,
        n_ctx=1024 + 512,
    )
    out = llm.create_chat_completion(messages=[{"role": "user", "content": prompt}])
    log.info(f"{out=}")
    full = out["choices"][0]["message"]["content"]
    return full, full


def identify_speaker_old(segment, paragraph_text, hints, characters_dict):
    """
    This does not work very well.

    Go through this text from a conventionally written paragraph and identify
    the speaker based on the context and the characters present in the scene.
    """
    characters_dict = characters_dict.copy()
    if "Narrator" in characters_dict:
        del characters_dict["Narrator"]

    speaker = None
    log.info(f"{segment=}")
    log.info(f"{paragraph_text=}")

    paragraph_characters = sentence_NER(paragraph_text)
    paragraph_characters = []
    log.info("NER Response", paragraph_characters=paragraph_characters)
    ner_people_names = []
    for entity in paragraph_characters:
        if entity["entity_group"] == "LOC":
            log.info(f"!! Found location in paragraph: {entity['word']} !!")
            hints += f" There is a location called {entity['word']} in the story. "

        elif entity["entity_group"] == "PER":
            log.info(f"!! Found person in paragraph: {entity['word']} !!")
            hints += f"There is a person named {entity['word']} in the story. "
            ner_people_names.append(name_to_tag(entity["word"]))

    local_characters = {}
    for name in ner_people_names:
        for character_name, character in characters_dict.items():
            if name in character_name:
                log.info(f"!! Adding {character_name} to local characters !!")
                local_characters[character_name] = character

            elif name in " ".join(character.get("alias", [])):
                log.info(f"!! Alias Adding {character_name} to local characters !!")
                local_characters[character_name] = character

    if not local_characters:
        log.info("Unable to reduce potential speakers with NER")
        local_characters = characters_dict

    def parse_llm_response(in_str):
        """
        All this for the range of responses we get from:
            "Using a single quoted string identify the speaker for one
            utterance."

        That is the LLM piece, to identify, given one string in a piece of
        literaterary writing that occurs in a larger context of surrounding
        text, identify the speaker of each phrase enclosed in quotation marks
        (parsed out mechanically). Almost any LLM can try and perform this task.
        I'm trying Phi 3.1 mini 128k instruct gguf right now.
        I shoudl try Phi-4-mini 3.8B

        in_str is the plain string response from Phi.  There are a pretty wide
        variety of responses that can become valid with a little cleanup.
        Prompt better, use a better LLM, and maybe this part won't suck so much.
        """
        log.info("parse_llm_response", in_str=in_str)

        speaker = None
        if name_to_tag(in_str) in characters_dict.keys():
            speaker = name_to_tag(in_str)
        elif "```json" in in_str:
            # strip out any leading text
            in_str = in_str[in_str.find("```json") :]
            # remote the markdown code block wrapper
            in_str = in_str.strip().removeprefix("```json").removesuffix("```")
            log.info(f"Attempting json.loads() on:\n{in_str}")
            try:
                as_json = json.loads(in_str)
            except json.JSONDecodeError as e:
                as_json = []

            for key in as_json:
                if key.strip().lower() in [
                    "speaker",
                    "narrator",
                ]:
                    value = as_json[key]
                    if type(value) is bool:
                        # not kidding.
                        for key in as_json:
                            if as_json[key] is True:
                                speaker = key
                                break
                    else:
                        speaker = as_json[key]

        # not elif, because if the json stuff doesn't work out we want
        # to fall through into this stuff.
        if speaker is None:
            if "**" in in_str:
                # The speaker in the paragraph is **Mr. White**.
                speaker = in_str.split("**")[1]

            elif in_str[0] == '"' and in_str[-1] == '"':
                # "Mr. White"
                speaker = in_str[1:-1]

            elif in_str[0] == "'" and in_str[-1] == "'":
                #'Mr. White'
                speaker = in_str[1:-1]
            elif in_str.startswith("dict_keys(['") and in_str.endswith("'])"):
                # dict_keys(['Mr. White'])
                speaker = in_str[len("dict_keys(['") : -len("'])")]
            else:
                for regex in [
                    r"^([A-Z]*)$",  # we got a one word reply, use it
                    # r"IS \"(.*)\"",  # is "Mr. White"
                    r"DICT_KEYS\(['(.*)']\)",  # dict_keys(['Mr. White'])
                    # r"IS (?!:NOT )(.*)[\.\,\!\?\:\;]",
                    r"ANSWER: \"?(.*)\"?",
                    r"\*\*ANSWER:\*\* \"?(.*)\"?",
                ]:
                    log.info(f"{regex=}")
                    if match := re.search(regex, in_str.upper()):
                        log.info(f"{match=}")
                        speaker = match.group(1)
                        speaker = speaker
                        break  # no need to keep evaluating

        if speaker is None:
            most = 0
            in_str = name_to_tag(in_str)

            for name in characters_dict.keys():
                # the most frequently referenced name in the in_str
                count = name_to_tag(in_str).count(name)
                if count > most:
                    speaker = name
                    most = count
                log.info(f"{name=}, +{count=}, {most=}")

            for character_name, character in characters_dict.items():
                if in_str in character.get("alias", []):
                    speaker = character_name
                    break

        if speaker is None:
            log.info(f"!! Unable to identify speaker in LLM response: {in_str} !!")
            return None

        speaker = name_to_tag(speaker)
        log.info(f"{speaker=}")
        return speaker

    # long paragraphs can be a thing, but it will blast through our context window.
    answers = []

    while paragraph_text:
        if len(paragraph_text) > 640:
            evaluate_text = paragraph_text[:640]
            paragraph_text = paragraph_text[640:]
        else:
            evaluate_text = paragraph_text
            paragraph_text = ""

        alpha_response = llm.str_prompt(
            prompt=f"In this paragraph:\n\n{evaluate_text}\n\nUsing a single quoted string identify the speaker for one utterance. {hints}. Only respond with one of these possible answers: {local_characters.keys()}\n\nThe utterance is:\n\n{segment}",
            system_prompt="You are a helpful assistant that identifies the speaker of a given utterance in a paragraph of text. You will be provided with a paragraph and an utterance, and you must identify the speaker of that utterance based on the context of the paragraph. You will only respond with the name of the speaker in a single quoted string. If you cannot identify the speaker, respond with 'Unknown'.",
            force=True,
        )

        alpha_answer = parse_llm_response(alpha_response)
        answers.append(alpha_answer)

        # # and verify
        # beta_response = llm_query(
        #     f"I'm trying to identify the speaker of \n\n{segment}\n\n in the paragraph \n\n{paragraph_text}\n\nThe only possible answers are {characters_dict.keys()}.  I know these facts: {hints}. In one quoted string tell me who is the speaker?"
        # )
        # beta_answer = parse_llm_response(beta_response)
        # answers.append(beta_answer)

        # if alpha_answer not in local_characters.keys():
        #     for name, ch in local_characters.items():
        #         if alpha_answer in ch.get("alias", []):
        #             answers.append(name)
        #             break

        # if beta_answer not in local_characters.keys():
        #     for name, ch in local_characters.items():
        #         if beta_answer in ch.get("alias", []):
        #             answers.append(name)
        #             break

    log.info(f"{answers=}")

    better_answers = []
    for answer in answers:
        if name_to_tag(answer) in local_characters.keys():
            better_answers.append(name_to_tag(answer))

    speaker = None
    if better_answers:
        log.info(f"{better_answers=}")
        speaker = Counter(better_answers).most_common(1)[0][0]

    if not speaker:
        speaker = Counter(answers).most_common(1)[0][0]

    # # winner winner chicken dinner
    # if alpha_answer == beta_answer:
    #     speaker = alpha_answer
    # elif (
    #     name_to_tag(alpha_answer) in local_characters.keys() and name_to_tag(beta_answer) in local_characters.keys()
    # ) or (
    #     name_to_tag(alpha_answer) not in local_characters.keys() and name_to_tag(beta_answer) not in local_characters.keys()
    # ):
    #     if alpha_answer != beta_answer:
    #         print(f"Speaker identified as both {alpha_answer} and {beta_answer}.")
    #         # tiebreaker
    #         full, gamma_response = llm_query(
    #             f"In this story {hints}. I am trying to identify the speaker of \n\n{segment}\n\n but it must be super confusing because some people say {alpha_answer} but other people say {beta_answer}.  This is in the context of \n\n{paragraph_text}\n\n  In a single quoted string what is the name of the speaker?"
    #         )
    #         gamma_answer = parse_llm_response(gamma_response)

    #         if gamma_answer not in local_characters.keys():
    #             for name, ch in local_characters.items():
    #                 if gamma_answer in ch.get("alias", []):
    #                     gamma_answer = name
    #                     break

    #         print(f"Speaker identified as {alpha_answer}, {beta_answer}, and {gamma_answer}.")
    #         if gamma_answer == alpha_answer:
    #             speaker = alpha_answer

    #         elif gamma_answer == beta_answer:
    #             speaker = beta_answer

    # elif name_to_tag(alpha_answer) in characters_dict.keys() and name_to_tag(beta_answer) not in characters_dict.keys():
    #     # alpha is a clearly more compliant response
    #     speaker = alpha_answer

    # elif name_to_tag(alpha_answer) not in characters_dict.keys() and name_to_tag(beta_answer) in characters_dict.keys():
    #     # beta is a clearly more compliant response
    #     speaker = beta_answer

    log.info(f"===> {speaker=}")
    return speaker


def identify_speaker(segment, paragraph_text, hints, characters_dict):
    """
    Go through this text from a conventionally written paragraph and identify
    the speaker based on the context and the characters present in the scene.
    """
    characters_dict = characters_dict.copy()
    if "Narrator" in characters_dict:
        del characters_dict["Narrator"]

    speaker = None
    log.info(
        "identify_speaker()",
        segment=segment,
        paragraph_text=paragraph_text,
        hints=hints,
        characters_dict=characters_dict,
    )

    def parse_llm_response(in_str):
        """
        All this for the range of responses we get from:
            "Using a single quoted string identify the speaker for one
            utterance."

        That is the LLM piece, to identify, given one string in a piece of
        literaterary writing that occurs in a larger context of surrounding
        text, identify the speaker of each phrase enclosed in quotation marks
        (parsed out mechanically). Almost any LLM can try and perform this task.
        I'm trying Phi 3.1 mini 128k instruct gguf right now.
        I shoudl try Phi-4-mini 3.8B

        in_str is the plain string response from Phi.  There are a pretty wide
        variety of responses that can become valid with a little cleanup.
        Prompt better, use a better LLM, and maybe this part won't suck so much.
        """
        log.info("parse_llm_response", in_str=in_str)

        speaker = None
        if name_to_tag(in_str) in characters_dict.keys():
            speaker = name_to_tag(in_str)
        elif "```json" in in_str:
            # strip out any leading text
            in_str = in_str[in_str.find("```json") :]
            # remote the markdown code block wrapper
            in_str = in_str.strip().removeprefix("```json").removesuffix("```")
            log.info(f"Attempting json.loads() on:\n{in_str}")
            try:
                as_json = json.loads(in_str)
            except json.JSONDecodeError as e:
                as_json = []

            for key in as_json:
                if key.strip().lower() in [
                    "speaker",
                    "narrator",
                ]:
                    value = as_json[key]
                    if type(value) is bool:
                        # not kidding.
                        for key in as_json:
                            if as_json[key] is True:
                                speaker = key
                                break
                    else:
                        speaker = as_json[key]

        # not elif, because if the json stuff doesn't work out we want
        # to fall through into this stuff.
        if speaker is None:
            if "**" in in_str:
                # The speaker in the paragraph is **Mr. White**.
                speaker = in_str.split("**")[1]

            elif in_str[0] == '"' and in_str[-1] == '"':
                # "Mr. White"
                speaker = in_str[1:-1]

            elif in_str[0] == "'" and in_str[-1] == "'":
                #'Mr. White'
                speaker = in_str[1:-1]
            elif in_str.startswith("dict_keys(['") and in_str.endswith("'])"):
                # dict_keys(['Mr. White'])
                speaker = in_str[len("dict_keys(['") : -len("'])")]
            else:
                for regex in [
                    r"^([A-Z]*)$",  # we got a one word reply, use it
                    # r"IS \"(.*)\"",  # is "Mr. White"
                    r"DICT_KEYS\(['(.*)']\)",  # dict_keys(['Mr. White'])
                    # r"IS (?!:NOT )(.*)[\.\,\!\?\:\;]",
                    r"ANSWER: \"?(.*)\"?",
                    r"\*\*ANSWER:\*\* \"?(.*)\"?",
                ]:
                    log.info(f"{regex=}")
                    if match := re.search(regex, in_str.upper()):
                        log.info(f"{match=}")
                        speaker = match.group(1)
                        speaker = speaker
                        break  # no need to keep evaluating

        if speaker is None:
            most = 0
            in_str = name_to_tag(in_str)

            for name in characters_dict.keys():
                # the most frequently referenced name in the in_str
                count = name_to_tag(in_str).count(name)
                if count > most:
                    speaker = name
                    most = count
                log.info(f"{name=}, +{count=}, {most=}")

            for character_name, character in characters_dict.items():
                if in_str in character.get("alias", []):
                    speaker = character_name
                    break

        if speaker is None:
            log.info(f"!! Unable to identify speaker in LLM response: {in_str} !!")
            return None

        speaker = name_to_tag(speaker)
        log.info(f"{speaker=}")
        return speaker

    # long paragraphs can be a thing, but it will blast through our context window.
    answers = []

    while paragraph_text:
        # this 640 is stupid and arbitrary.  We want a sentence break.
        if len(paragraph_text) > 640:
            evaluate_text = paragraph_text[:640]
            paragraph_text = paragraph_text[640:]
        else:
            evaluate_text = paragraph_text
            paragraph_text = ""

        alpha_response = llm.str_prompt(
            prompt=f"""Paragraph:
            
{evaluate_text}
                       
The specific utterance is:
            
{segment}""",
            system_prompt="You are a helpful assistant that "
            "identifies the speaker of a specific utterance "
            "in a paragraph of text. You will be provided "
            "with a paragraph and an utterance, and you must "
            "identify the speaker of that utterance based on "
            "the context of the paragraph. You will only "
            "respond with the name of the speaker in a single "
            "quoted string. If you cannot identify the speaker, "
            "respond with 'Unknown'.",
            force=True,
        )
        log.info(f"{alpha_response=}")

        alpha_answer = parse_llm_response(alpha_response)
        answers.append(alpha_answer)

        log.error(
            "answers",
            answers=answers,
            alpha_answer=alpha_answer,
            segment=segment,
            paragraph_text=paragraph_text,
            hints=hints,
            characters_dict=characters_dict,
        )
        # raise ValueError()

        # and verify
        # beta_response = llm_query(
        #     f"I'm trying to identify the speaker of \n\n{segment}\n\n in the paragraph \n\n{paragraph_text}\n\nThe only possible answers are {characters_dict.keys()}.  I know these facts: {hints}. In one quoted string tell me who is the speaker?"
        # )
        # beta_answer = parse_llm_response(beta_response)
        # answers.append(beta_answer)

        if alpha_answer not in characters_dict.keys():
            for name, ch in characters_dict.items():
                if alpha_answer in ch.get("alias", []):
                    answers.append(name)
                    break

        # if beta_answer not in characters_dict.keys():
        #     for name, ch in characters_dict.items():
        #         if beta_answer in ch.get("alias", []):
        #             answers.append(name)
        #             break

    log.info(f"{answers=}")

    better_answers = []
    for answer in answers:
        if name_to_tag(answer) in characters_dict.keys():
            better_answers.append(name_to_tag(answer))

    speaker = None
    if better_answers:
        log.info(f"{better_answers=}")
        speaker = Counter(better_answers).most_common(1)[0][0]

    if not speaker:
        speaker = Counter(answers).most_common(1)[0][0]

    log.info(f"===> {speaker=}")
    return speaker


def dialog_to_paragraph(
    chapter,
    paragraph_text,
    paragraph,
    all_paragraphs,
    paragraph_index,
    hints,
    characters_dict,
):
    # we're split on a quote mark, so all the even indexed segments are outside
    # quote marks.  All the odd segments are _inside_ quote marks. this
    # 'quote_cycle' will alternate the same way.  Which is cool and fancy and
    # shit but isn't it a single bit comparison to get is_odd()?
    log.info(
        "dialog_to_paragraph()",
        chapter=chapter,
        paragraph_text=paragraph_text,
        paragraph=paragraph,
        all_paragraphs=all_paragraphs,
        paragraph_index=paragraph_index,
        hints=hints,
        characters_dict=characters_dict,
    )
    quote_cycle = itertools.cycle([False, True])
    character_names = characters_dict.keys()

    speaker = None
    # ‘’
    for segment in re.split('["“”]', paragraph_text):
        log.info(segment)

        in_quote = next(quote_cycle)

        # we have a segment of text, but we need to split it into
        # phrases that are short enough to be spoken by the TTS
        # engine.

        if in_quote:
            # who is speaking?
            tries = 3
            speaker = None
            while name_to_tag(speaker) not in character_names and tries > 0:
                # give it the paragraph before and after as additional context
                speaker = identify_speaker(
                    segment=segment,
                    paragraph_text="\n\n".join(
                        all_paragraphs[paragraph_index - 1 : paragraph_index + 1]
                    ),
                    hints=hints,
                    characters_dict=characters_dict,
                )
                if speaker and name_to_tag(speaker) not in character_names:
                    print(
                        'WARNING: Identified speaker as "%s" but that character name is not in characters.json'
                        % speaker
                    )
                    add_character(chapter, character_name=speaker, chardict={})
                    character_names = get_all_characters(chapter).keys()

                tries -= 1

        if speaker is None:
            speaker = "Narrator"

        if in_quote:
            segment = "“" + segment + "”"

        for full_line in nltk.sent_tokenize(segment):
            # create an <image/> inside paragraph
            image = chapter.soup.new_tag("image")
            paragraph.append(image)

            for partial_line in chunk_string(full_line):
                phrase = chapter.soup.new_tag("phrase")
                phrase.attrs["speaker"] = speaker

                # character = ET.Element(speaker)
                phrase.string = partial_line.replace("\n", " ")
                paragraph.append(phrase)
                # phrase.append(character)

    return paragraph


def narrator_to_paragraph(chapter, paragraph_text, paragraph):
    """
    Only the narrator ever speaks, so this is easy.
    """
    speaker = "Narrator"
    soup = chapter.soup

    try:
        tokenized = nltk.sent_tokenize(paragraph_text)
    except LookupError:
        nltk.download("punkt_tab")
        tokenized = nltk.sent_tokenize(paragraph_text)

    for full_line in tokenized:
        # create an <image/> inside paragraph
        image = soup.new_tag("image")
        paragraph.append(image)

        for partial_line in chunk_string(full_line):
            phrase = soup.new_tag("phrase")
            phrase.attrs["speaker"] = speaker

            # character = ET.Element(speaker)
            phrase.string = partial_line.replace("\n", " ")
            # phrase.append(character)

            paragraph.append(phrase)

    return paragraph


def poetry_to_paragraph(chapter, paragraph_text, paragraph):
    """
    Single narrator, but we want to preserve line breaks
    and spacing.
    """
    log.info("poetry_to_paragraph(%s, %s, %s)", chapter, paragraph_text, paragraph)
    speaker = "Narrator"
    # one image per line is a bit.. aggressive. we want one image per 8 seconds
    # of audio, but we also want complete sentences, which can be troublesome to
    # detect with poetry.
    #
    for full_line in paragraph_text.split("\n"):
        # step one, let the user decide where to put images.
        # # create an <image/> inside paragraph
        # image = chapter.soup.new_tag("image")
        # paragraph.append(image)

        phrase = chapter.soup.new_tag("phrase")
        phrase.attrs["speaker"] = speaker
        phrase.string = full_line.strip()
        paragraph.append(phrase)

    log.info("%s", chapter.soup.prettify())
    return paragraph


def biblical_to_paragraph(verse_string, chapter_xml):
    """
    Above us, the book has already been split on "blank" newlines.

    paragraph_text is something like:

    ```
    1:1 In the beginning God created the heaven and the earth.
    1:2 And the earth was without form, and void; and darkness...
    ```

    paragraph is an ElementTree object that we will populate with the
    appropriate XML elements.
    """
    log.info(f"biblical_to_paragraph({verse_string=}, {chapter_xml=})")
    chapter = None

    for verse in verse_string.split("\n"):
        m = re.match(r"^([0-9]*:[0-9]*) (.*)$", verse)
        if not m:
            log.info(f"Unable to parse verse line: {verse}")
            continue

        chapter_verse = m.group(1)
        verse_text = m.group(2)
        # chapter_verse, verse_text = verse.split(maxsplit=1)
        chapter, verse = chapter_verse.split(":")

        # initially there are no images, one paragraph per and one phrase per paragraph.
        phrase = chapter_xml.add_element("phrase")
        phrase.attrs["chapter"] = chapter
        phrase.attrs["verse"] = verse
        phrase.text = verse_text

    return

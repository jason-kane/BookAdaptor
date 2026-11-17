"""Book-wide speech regeneration for one character."""
from contextlib import nullcontext
from pathlib import Path

from filelock import FileLock

import const
from . import characters


def regenerate_character_audio(chapter, character_tag, progress=None):
    from artifact_editor.chapter.chapter import Chapter
    from artifact_editor.phrases.phrases import generate_all_audio
    from artifact_editor.llm import configure_character_voice

    record = configure_character_voice(chapter, character_tag, infer_demographics=False)
    tag = record['tag']
    records = characters.get_all_characters(chapter, is_global=True)
    records.setdefault(tag, record)
    # Include unambiguous historic display names and aliases as well as tags.
    speaker_tags = {characters.name_to_tag(tag)}
    for name in characters.character_names(tag, record):
        try:
            if characters.resolve_character_tag(records, name) == tag:
                speaker_tags.add(characters.name_to_tag(name))
        except ValueError:
            continue
    root = Path(const.LIBRARY_DIR) / chapter.bookdir / 'chapter'
    numbers = sorted(int(path.name) for path in root.iterdir() if path.is_dir() and path.name.isdigit())
    count = 0
    for position, number in enumerate(numbers):
        local = chapter if number == chapter.number else Chapter(
            chapter.author, chapter.title, number, language=chapter.language)
        # The coordinator already holds the originating chapter's render lock.
        lock = nullcontext() if number == chapter.number else FileLock(
            str(Path(const.LIBRARY_DIR) / local.chapterdir / '.render.lock'))
        with lock:
            local.load_xml(force=True)
            matches = [phrase for phrase in local.get_xml().find_all('phrase')
                       if characters.name_to_tag(phrase.get('speaker', 'Narrator')) in speaker_tags
                       and phrase.get('type') != 'dinkus']
            if not matches:
                continue
            def report(percent, message):
                if progress:
                    progress((position + percent / 100) / max(1, len(numbers)) * 100,
                             f'Chapter {number}: {message}')
            generate_all_audio(local, force=True, batch=True, progress=report,
                               speaker_tags=speaker_tags)
            count += len(matches)
    return count

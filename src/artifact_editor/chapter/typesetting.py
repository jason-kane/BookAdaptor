"""Submit chapter typesetting to CPU workers without serializing live Chapters."""
from pathlib import Path
import json


def enqueue_typesetting(chapter, *, wait=False, source='xml'):
    from cpu_queue import Batch
    from worker_startup import ensure_worker_services

    identity = dict(author=chapter.author.name, title=chapter.title,
                    chapter_number=chapter.number, language=chapter.language, aspect=chapter.get_aspect())
    batch = Batch(chapter=identity, track_status=True)
    try:
        job_id = batch.submit('typeset_latex', {'source': source},
                              f"Typeset LaTeX · {chapter.title} · Chapter {chapter.number}")
        # The task and its queued status are durable before starting consumers.
        # Startup failure leaves a visible job available for a later restart.
        try:
            ensure_worker_services(cpu_only=True)
        except RuntimeError:
            if wait:
                raise
        if wait:
            batch.wait()
        return job_id
    finally:
        batch.client.close()


def execute_typesetting(task):
    from filelock import FileLock
    import const
    from artifact_editor.author.author import Author
    from artifact_editor.chapter.chapter import Chapter
    from .section_latex import typeset_chapter_xml, typeset_documents

    identity = task['chapter']
    chapter = Chapter(Author(identity['author']), identity['title'],
                      int(identity['chapter_number']), identity['language'])
    directory = Path(const.LIBRARY_DIR) / chapter.get_chapterdir() / chapter.language
    directory.mkdir(parents=True, exist_ok=True)
    # Serialize paired output updates from repeated clicks on the same chapter.
    with FileLock(str(directory / '.typeset.lock')):
        if task['arguments'].get('source', 'xml') == 'latex':
            documents = tuple((directory / f'{name}.latex').read_text(encoding='utf-8')
                              for name in ('chapter', 'chapter.rainbow'))
            typeset_documents(documents, directory)
        else:
            chapter.load_xml(force=True)
            sections_file = directory / 'chapter.sections.json'
            sections = json.loads(sections_file.read_text(encoding='utf-8')) if sections_file.is_file() else None
            typeset_chapter_xml(chapter.soup, directory, const.GEOMETRY[chapter.get_aspect()]['TEXT_WIDTH'],
                               sections=sections)


def ensure_text_layers(chapter):
    """Rebuild the paired text PNGs when a video render needs missing layers."""
    from filelock import FileLock
    import const
    from .section_latex import typeset_chapter_xml

    directory = Path(const.LIBRARY_DIR) / chapter.get_chapterdir() / chapter.language
    text_image = directory / "chapter-1.png"
    rainbow_image = directory / "chapter.rainbow-1.png"
    if text_image.is_file() and rainbow_image.is_file():
        return False

    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / ".typeset.lock")):
        if text_image.is_file() and rainbow_image.is_file():
            return False
        chapter.load_xml(force=True)
        sections_file = directory / "chapter.sections.json"
        sections = json.loads(sections_file.read_text(encoding="utf-8")) if sections_file.is_file() else None
        typeset_chapter_xml(
            chapter.get_xml(),
            directory,
            const.GEOMETRY[chapter.get_aspect()]["TEXT_WIDTH"],
            sections=sections,
        )
    return True

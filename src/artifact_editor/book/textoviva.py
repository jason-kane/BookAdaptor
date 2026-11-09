"""Assemble a complete book using existing chapter frames and narration only."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave

from filelock import FileLock

from .pages import classify, sections_path


def chapters(book):
    from artifact_editor.chapter.chapter import Chapter
    directory = Path(book.bookdir) / 'chapter'
    numbers = sorted({int(p.name.split('_')[0]) for p in directory.iterdir()
                      if p.is_dir() and p.name.split('_')[0].isdigit()}) if directory.is_dir() else []
    return [Chapter(book.author, book.title, number, 'english') for number in numbers]


def output_path(book):
    return Path(book.bookdir) / 'textoviva' / 'book.mp4'


def availability(book):
    items = chapters(book)
    missing = [c.number for c in items if not Path(c.get_video_filename()).is_file()
               or not Path(c.get_video_filename()).stat().st_size]
    return dict(enabled=bool(items) and not missing, count=len(items), missing=missing,
                ready=output_path(book).is_file())


def compact(text):
    return ''.join(text.split())


def phrase_roles(chapter, phrases):
    """Align editor roles to a rendered timeline without regenerating any assets."""
    path = sections_path(chapter)
    if path.is_file():
        sections = json.loads(path.read_text())
        sections = [part for section in sections for part in
                    ([section] if 'section_type' in section else
                     classify(section['ops'], chapter, section['typesetting_method']))]
        # Source illustration markers aren't narrated.
        from artifact_editor.chapter.illustrations import MARKER
        text, roles = '', []
        for section in sections:
            value = compact(MARKER.sub('', ''.join(op['insert'] for op in section['ops'])))
            text += value
            roles.extend([section.get('section_type', 'chapter_text')] * len(value))
        if text == ''.join(compact(p.get_text()) for p in phrases):
            result, cursor = [], 0
            for phrase in phrases:
                length = len(compact(phrase.get_text()))
                kinds = set(roles[cursor:cursor+length])
                if len(kinds) > 1:
                    raise ValueError(f'Chapter {chapter.number}: a phrase crosses page sections. Import and render this chapter again.')
                result.append(next(iter(kinds), 'chapter_text'))
                cursor += length
            return result
        # Changing text after rendering makes frame cuts unsafe.
        raise ValueError(f'Chapter {chapter.number}: text has changed since import. Import and render this chapter again.')
    if all(p.get('section_type') for p in phrases):
        return [p['section_type'] for p in phrases]
    # Legacy chapters have no section file. Infer only exact leading headers.
    sections = classify([{'insert': ''.join(p.get_text() for p in phrases)}], chapter)
    roles = [s['section_type'] for s in sections for _ in compact(''.join(op['insert'] for op in s['ops']))]
    result, cursor = [], 0
    for phrase in phrases:
        length = len(compact(phrase.get_text()))
        kinds = set(roles[cursor:cursor+length])
        if len(kinds) > 1:
            raise ValueError(f'Chapter {chapter.number}: separate the Book Page in Text, then import and render again.')
        result.append(next(iter(kinds), 'chapter_text'))
        cursor += length
    return result


def kept_ranges(chapter, first=False):
    """Return half-open frame intervals, including silences with their page."""
    soup = chapter.get_xml()
    phrases = soup.find_all('phrase')
    if not phrases:
        raise ValueError(f'Chapter {chapter.number} has no narration timeline.')
    roles = iter(phrase_roles(chapter, phrases)) if not first else None
    role = 'chapter_text'
    ranges, frame = [], 0
    for item in soup.find_all(['phrase', 'silence']):
        if item.name == 'phrase' and roles is not None:
            role = next(roles)
        count = int(item.get('frames', 0))
        if count <= 0:
            raise ValueError(f'Chapter {chapter.number} has missing frame timing. Render this chapter again.')
        if first or role != 'book_page':
            if ranges and ranges[-1][1] == frame:
                ranges[-1] = (ranges[-1][0], frame + count)
            else:
                ranges.append((frame, frame + count))
        frame += count
    if not ranges:
        raise ValueError(f'Chapter {chapter.number} contains only Book Page sections.')
    return ranges, frame


def plan(book):
    import const
    items = chapters(book)
    if not items:
        raise ValueError('This book has no chapters.')
    result, aspect = [], None
    for position, chapter in enumerate(items):
        video = Path(chapter.get_video_filename())
        if not video.is_file() or not video.stat().st_size:
            raise ValueError(f'Chapter {chapter.number} needs a chapter video first.')
        current_aspect = chapter.get_aspect()
        if aspect is not None and aspect != current_aspect:
            raise ValueError('Render every chapter with the same aspect ratio before assembling the book.')
        aspect = current_aspect
        ranges, total = kept_ranges(chapter, first=position == 0)
        root = Path(const.LIBRARY_DIR) / chapter.chapterdir
        audio = root / 'audio.wav'
        frames = root / 'frames' / aspect
        if not audio.is_file() or not frames.is_dir():
            raise ValueError(f'Chapter {chapter.number} needs its rendered frames and audio. Render this chapter again.')
        with wave.open(str(audio), 'rb') as track:
            if abs(track.getnframes() / track.getframerate() - total / const.FPS) > 1 / const.FPS:
                raise ValueError(f'Chapter {chapter.number}: rendered audio differs from the timeline. Render again.')
        result.append(dict(number=chapter.number, ranges=ranges, total=total,
                           frames=str(frames.resolve()), audio=str(audio.resolve()),
                           video=str(video.resolve()), video_stamp=video.stat().st_mtime_ns,
                           audio_stamp=audio.stat().st_mtime_ns))
    return result


def fingerprint(items):
    return hashlib.sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()


def enqueue(book):
    from cpu_queue import Batch
    from worker_startup import ensure_worker_services
    items = plan(book)
    directory = output_path(book).parent
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(str(directory / '.submit.lock')):
        batch = Batch(chapter=dict(author=book.author.name, title=book.title, language='english'), track_status=True)
        try:
            key = f'book_textoviva:{book.author.name}:{book.title}'
            existing = batch.client.get(key)
            if existing:
                status = json.loads(batch.client.get(f'cpu_job:{existing}') or '{}')
                if status.get('state') in ('queued', 'running'):
                    return existing
            job = batch.submit('assemble_book', dict(author=book.author.name, title=book.title,
                                                    fingerprint=fingerprint(items)),
                               f'Textoviva · {book.title} · all {len(items)} chapters')
            batch.client.set(key, job, ex=86400)
            try:
                ensure_worker_services(cpu_only=True)
            except RuntimeError:
                pass  # The queued task remains durable and visible.
            return job
        finally:
            batch.client.close()


def execute(task):
    import const
    from artifact_editor.author.author import Author
    from types import SimpleNamespace
    from .pages import book_for
    from artifact_editor.tools import assemble_mp4
    book = book_for(SimpleNamespace(author=Author(task['arguments']['author']), title=task['arguments']['title']))
    destination = output_path(book)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        stack.enter_context(FileLock(str(destination.parent / '.assemble.lock')))
        for chapter in chapters(book):
            stack.enter_context(FileLock(str(Path(const.LIBRARY_DIR) / chapter.chapterdir / '.render.lock')))
        items = plan(book)
        if fingerprint(items) != task['arguments']['fingerprint']:
            raise ValueError('Chapter assets changed while this book was queued. Create the book video again.')
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='assembly-', dir=destination.parent))
        directory = Path(temporary)
        frame_index = 0
        audio_output = stack.enter_context(wave.open(str(directory / 'audio.wav'), 'wb'))
        audio_output.setparams((2, 2, 48000, 0, 'NONE', 'not compressed'))
        for chapter_index, item in enumerate(items):
            for range_index, (start, end) in enumerate(item['ranges']):
                for index in range(start, end):
                    source = Path(item['frames']) / f'frame_{index:06d}.png'
                    if not source.is_file():
                        raise ValueError(f"Chapter {item['number']}: missing rendered frame {index}. Render this chapter again.")
                    os.symlink(source, directory / f'frame_{frame_index:06d}.png')
                    frame_index += 1
                segment = directory / f'audio-{chapter_index}-{range_index}.wav'
                subprocess.run(['/usr/bin/ffmpeg', '-nostdin', '-v', 'error', '-y',
                                '-i', item['audio'], '-af',
                                f'atrim=start={start / const.FPS:.9f}:end={end / const.FPS:.9f},asetpts=PTS-STARTPTS',
                                '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', str(segment)], check=True)
                with wave.open(str(segment), 'rb') as audio:
                    while chunk := audio.readframes(65536):
                        audio_output.writeframesraw(chunk)
                segment.unlink()
        audio_output.close()
        assemble_mp4(fps=const.FPS, framedir=str(directory), wavfile=str(directory / 'audio.wav'),
                     videofile=str(destination), image_match='frame_%06d.png', start_number=0, max_frames=frame_index)

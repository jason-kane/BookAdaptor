"""Assemble a complete book using existing chapter frames and narration only."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import wave
from datetime import datetime, timezone

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


def companion_path(book, kind):
    suffix = {'chapters': '.chapters.json', 'toc': '.toc.txt'}[kind]
    return output_path(book).with_suffix(suffix)


def file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def chapter_manifest(book, items, fps, *, previous=None, segment_stats=None):
    """Map retained chapter ranges into the encoded book's frame time base."""
    entries, cursor = [], 0
    previous={entry['number']:entry for entry in (previous or {}).get('chapters',[])}
    has_segment_stats=segment_stats is not None
    segment_stats=segment_stats or {}
    for item in items:
        start, segments = cursor, []
        for source_start, source_end in item['ranges']:
            end = cursor + source_end - source_start
            segments.append(dict(source_start_frame=source_start, source_end_frame=source_end,
                                 book_start_frame=cursor, book_end_frame=end))
            cursor = end
        old=previous.get(item['number'],{})
        same_source=source_unchanged(old,item) if old else False
        entry=dict(number=item['number'], title=item['title'],
                            start_frame=start, end_frame=cursor, frame_count=cursor-start,
                            start_seconds=start/fps, end_seconds=cursor/fps,
                            duration_seconds=(cursor-start)/fps,
                            source_video=item['video'], source_video_mtime_ns=item['video_stamp'],
                            source_video_size_bytes=item['video_size'],
                            source_video_sha256=old.get('source_video_sha256') if same_source else file_sha256(item['video']),
                            source_audio=item['audio'], source_audio_mtime_ns=item['audio_stamp'],
                            source_audio_size_bytes=item['audio_size'],source_audio_sha256=(old.get('source_audio_sha256')
                                if same_source else file_sha256(item['audio'])),
                            aspect=item['aspect'],
                            source_total_frames=item['total'], segments=segments)
        if has_segment_stats:
            stats=segment_stats[item['number']]
            entry.update(segment_file=stats['path'].name,segment_size_bytes=stats['size'],segment_mtime_ns=stats['mtime_ns'])
        entries.append(entry)
    return dict(schema_version=2 if has_segment_stats else 1, author=book.author.name, title=book.title,
                video=output_path(book).name, fps=dict(numerator=fps, denominator=1),
                frame_convention='zero-based, end-exclusive', total_frames=cursor,
                duration_seconds=cursor/fps, source_fingerprint=fingerprint(items),
                created_at=datetime.now(timezone.utc).isoformat(), chapters=entries)


def table_of_contents(manifest):
    """Plain timestamps for chapter navigation; exact cuts live in the manifest."""
    lines = []
    fps = manifest['fps']['numerator']
    for chapter in manifest['chapters']:
        seconds = chapter['start_frame'] // fps
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        title = ' '.join(chapter['title'].split())
        lines.append(f'{hours:02d}:{minutes:02d}:{seconds:02d} Chapter {chapter["number"]}: {title}')
    return '\n'.join(lines) + '\n'


def availability(book):
    items = chapters(book)
    missing = [c.number for c in items if not Path(c.get_video_filename()).is_file()
               or not Path(c.get_video_filename()).stat().st_size]
    return dict(enabled=bool(items) and not missing, count=len(items), missing=missing,
                ready=output_path(book).is_file())


def segment_path(book, number):
    return output_path(book).parent / 'segments' / f'chapter_{number:04d}.mp4'


def current_manifest(book):
    video, path = output_path(book), companion_path(book, 'chapters')
    try:
        manifest=json.loads(path.read_text(encoding='utf-8'))
        stat=video.stat()
        if (manifest.get('video_size_bytes'),manifest.get('video_mtime_ns'))!=(stat.st_size,stat.st_mtime_ns):
            return None
        return manifest
    except (OSError,ValueError,TypeError):
        return None


def source_unchanged(old,item):
    source_ranges=[(part.get('source_start_frame'),part.get('source_end_frame')) for part in old.get('segments',[])]
    return (old.get('source_video_mtime_ns')==item['video_stamp']
            and old.get('source_video_size_bytes')==item['video_size']
            and old.get('source_audio_mtime_ns')==item['audio_stamp']
            and old.get('source_audio_size_bytes')==item['audio_size']
            and old.get('source_total_frames')==item['total']
            and old.get('aspect')==item['aspect'] and source_ranges==item['ranges'])


def cached_segment_valid(book,old):
    path=segment_path(book,old['number'])
    try:
        stat=path.stat()
        return (old.get('segment_file')==path.name and old.get('segment_size_bytes')==stat.st_size
                and old.get('segment_mtime_ns')==stat.st_mtime_ns and stat.st_size>0)
    except (OSError,KeyError):
        return False


def chapter_updates(book):
    """Describe source chapters that differ from the currently published book."""
    current=chapters(book)
    if not output_path(book).is_file():
        return [dict(number=chapter.number,state='new') for chapter in current]
    manifest=current_manifest(book)
    import const
    if (not manifest or manifest.get('schema_version')!=2
            or manifest.get('fps')!={'numerator':const.FPS,'denominator':1}):
        return [dict(number=chapter.number,state='segment_setup') for chapter in current]
    previous={entry['number']:entry for entry in manifest.get('chapters',[])}
    try:
        planned={item['number']:item for item in plan(book)}
    except (OSError,ValueError,KeyError,TypeError):
        planned={}
    updates=[]
    for chapter in current:
        old=previous.get(chapter.number)
        item=planned.get(chapter.number)
        if not old or not item:
            updates.append(dict(number=chapter.number,state='updated' if old else 'new'))
            continue
        if not source_unchanged(old,item) or not cached_segment_valid(book,old):
            updates.append(dict(number=chapter.number,state='updated'))
    for number in sorted(set(previous)-{chapter.number for chapter in current}):
        updates.append(dict(number=number,state='removed'))
    return updates


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
        result.append(dict(number=chapter.number, title=chapter.chapter_title, ranges=ranges, total=total,
                           frames=str(frames.resolve()), audio=str(audio.resolve()),
                           video=str(video.resolve()), video_stamp=video.stat().st_mtime_ns,
                           video_size=video.stat().st_size,audio_stamp=audio.stat().st_mtime_ns,
                           audio_size=audio.stat().st_size,aspect=aspect))
    return result


def fingerprint(items):
    # Titles label the companion, but don't change encoded frames. Excluding
    # them also keeps already queued assembly plans compatible.
    sources = [{key: value for key, value in item.items() if key != 'title'} for item in items]
    return hashlib.sha256(json.dumps(sources, sort_keys=True).encode()).hexdigest()


def enqueue(book):
    from cpu_queue import Batch
    from worker_startup import ensure_worker_services
    items = plan(book)
    changes=chapter_updates(book)
    changed=[item['number'] for item in changes]
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
                               f'Textoviva · {book.title} · '+
                               (f'updated chapters {", ".join(map(str,changed))}' if changed else 'up to date'))
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
        previous=current_manifest(book)
        if (not previous or previous.get('schema_version')!=2
                or previous.get('fps')!={'numerator':const.FPS,'denominator':1}):
            previous=None
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix='assembly-', dir=destination.parent))
        directory = Path(temporary)
        prior={entry['number']:entry for entry in previous.get('chapters',[])} if previous else {}
        staged_segments={}
        segment_sources=[]
        for item in items:
            old=prior.get(item['number'])
            cached=segment_path(book,item['number'])
            if old and source_unchanged(old,item) and cached_segment_valid(book,old):
                segment_sources.append(cached)
                continue
            segment_dir=directory / f'chapter_{item["number"]:04d}'
            frames_dir=segment_dir / 'frames'
            frames_dir.mkdir(parents=True)
            audio_output_path=segment_dir / 'audio.wav'
            frame_index=0
            with wave.open(str(audio_output_path),'wb') as audio_output:
                audio_output.setparams((2,2,48000,0,'NONE','not compressed'))
                for range_index,(start,end) in enumerate(item['ranges']):
                    for index in range(start,end):
                        source=Path(item['frames']) / f'frame_{index:06d}.png'
                        if not source.is_file():
                            raise ValueError(f"Chapter {item['number']}: missing rendered frame {index}. Render this chapter again.")
                        os.symlink(source,frames_dir / f'frame_{frame_index:06d}.png')
                        frame_index+=1
                    trimmed=segment_dir / f'audio-{range_index}.wav'
                    subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-y','-i',item['audio'],'-af',
                        f'atrim=start={start / const.FPS:.9f}:end={end / const.FPS:.9f},asetpts=PTS-STARTPTS',
                        '-ar','48000','-ac','2','-c:a','pcm_s16le',str(trimmed)],check=True)
                    with wave.open(str(trimmed),'rb') as audio:
                        while chunk:=audio.readframes(65536):
                            audio_output.writeframesraw(chunk)
                    trimmed.unlink()
            staged=directory / f'chapter_{item["number"]:04d}.mp4'
            assemble_mp4(fps=const.FPS,framedir=str(frames_dir),wavfile=str(audio_output_path),
                         videofile=str(staged),image_match='frame_%06d.png',start_number=0,max_frames=frame_index)
            staged_segments[cached]=staged
            segment_sources.append(staged)
        if not segment_sources:
            raise ValueError('This book has no chapter segments to assemble.')
        concat_list=directory / 'segments.txt'
        def quote_concat(path):
            return str(path).replace('\\','\\\\').replace("'","'\\''")
        concat_list.write_text(''.join(f"file '{quote_concat(path)}'\n" for path in segment_sources),encoding='utf-8')
        staged_video = directory / destination.name
        subprocess.run(['/usr/bin/ffmpeg','-nostdin','-v','error','-y','-f','concat','-safe','0','-i',str(concat_list),
                        '-c','copy','-movflags','+faststart',str(staged_video)],check=True)
        if not staged_video.is_file() or not staged_video.stat().st_size:
            raise RuntimeError('FFmpeg did not produce an assembled book video.')
        probe=subprocess.run(['/usr/bin/ffprobe','-v','error','-show_streams','-of','json',str(staged_video)],
                             capture_output=True,text=True,check=True)
        streams=json.loads(probe.stdout).get('streams',[])
        video_stream=next((stream for stream in streams if stream.get('codec_type')=='video'),None)
        audio_stream=next((stream for stream in streams if stream.get('codec_type')=='audio'),None)
        expected_frames=sum(end-start for item in items for start,end in item['ranges'])
        if video_stream is None or audio_stream is None or int(video_stream.get('nb_frames',0))!=expected_frames:
            raise RuntimeError('The assembled book video is missing frames or an audio stream.')
        segment_stats={}
        for item,source in zip(items,segment_sources):
            stat=source.stat()
            segment_stats[item['number']]={'path':segment_path(book,item['number']),'size':stat.st_size,'mtime_ns':stat.st_mtime_ns}
        manifest = chapter_manifest(book, items, const.FPS, previous=previous,segment_stats=segment_stats)
        if manifest['total_frames'] != expected_frames:
            raise RuntimeError('The chapter manifest does not match the assembled video.')
        stat = staged_video.stat()
        manifest['video_size_bytes'] = stat.st_size
        manifest['video_mtime_ns'] = stat.st_mtime_ns
        staged_manifest = directory / 'book.chapters.json'
        staged_toc = directory / 'book.toc.txt'
        staged_manifest.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        staged_toc.write_text(table_of_contents(manifest), encoding='utf-8')
        for cached,staged in staged_segments.items():
            cached.parent.mkdir(parents=True,exist_ok=True)
            os.replace(staged,cached)
        os.replace(staged_video, destination)
        os.replace(staged_toc, companion_path(book, 'toc'))
        os.replace(staged_manifest, companion_path(book, 'chapters'))
        if previous:
            current_numbers={item['number'] for item in items}
            for old in previous.get('chapters',[]):
                if old['number'] not in current_numbers:
                    try:
                        segment_path(book,old['number']).unlink(missing_ok=True)
                    except OSError:
                        pass

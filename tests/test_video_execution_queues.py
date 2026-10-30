"""Render a small complete chapter through production frame/audio/MP4 code.

Model services are excluded: narration and illustrations are fixtures. A local
queue adapter runs the same CPU task payloads so ffmpeg output can be inspected.
"""
import ast
import contextlib
from decimal import Decimal
from glob import glob
import itertools
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import traceback
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
import wave

from bs4 import BeautifulSoup, Tag
from PIL import Image
import pytest

import cpu_queue
import gpu_tasks
from task_queue import VIDEO_QUEUE, RENDER_QUEUE, enqueue_task, task_provider

ROOT = Path(__file__).parents[1]


def load_functions(relative, names, namespace):
    path = ROOT / relative
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    for node in nodes:
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), 'exec'), namespace)
    return namespace


def wav(path, value, duration=1):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        output.writeframes(struct.pack('<h', value) * round(16000 * duration))


def audio_helpers():
    return load_functions('src/artifact_editor/phrases/phrases.py',
                          {'assemble', 'get_first_file', 'get_wav_duration'},
                          dict(os=os, wave=wave, contextlib=contextlib, decimal=__import__('decimal'), log=Mock()))


def test_render_commands_leave_gpu_worker_available_for_dependencies():
    for task in [dict(action='textoviva_rerender', job_id='one'),
                 dict(action='textoviva_regenerate_animation', job_id='two'),
                 dict(action='chapter_missing_images', job_id='images'),
                 dict(action='chapter_missing_animations', job_id='animations'),
                 dict(job_id='three', aspect='widescreen')]:
        client = Mock()
        enqueue_task(client, VIDEO_QUEUE, task)
        queue, payload = client.lpush.call_args.args
        assert queue == RENDER_QUEUE
        assert json.loads(payload)['provider'] == 'none'
    assert task_provider(dict(action='gpu_workflow', provider='comfyui')) == 'comfyui'


@pytest.mark.parametrize('action', ['chapter_missing_images', 'chapter_missing_animations',
                                  'textoviva_rerender', 'gpu_workflow'])
def test_retry_routes_coordinators_away_from_gpu_consumer(action, monkeypatch):
    import worker_startup
    from task_queue import PRIORITY_QUEUE, TAG_QUEUE, IDLE_QUEUE, RENDER_ACTIONS
    startup = Mock()
    monkeypatch.setattr(worker_startup, 'ensure_worker_services', startup)
    task = dict(job_id='retry-me', action=action, author='Aesop', title='Fables',
                chapter_number=27, language='english', arguments={'input': 'same'})
    client = Mock()
    client.get.return_value = None
    client.lrange.return_value = []
    client.set.return_value = True
    ns = load_functions('src/websocket/server.py', {'retry_failed_task'}, dict(
        json=json, log=Mock(), recent_failures=lambda _: [dict(task, state='failed')],
        recent_completions=lambda _: [], enqueue_task=enqueue_task,
        PRIORITY_QUEUE=PRIORITY_QUEUE, VIDEO_QUEUE=VIDEO_QUEUE, TAG_QUEUE=TAG_QUEUE,
        IDLE_QUEUE=IDLE_QUEUE, styler_queue_entry=lambda item, priority: dict(item, job_type=action)))
    result = ns['retry_failed_task'](client, task['job_id'])
    assert result['state'] == 'retry_queued'
    queue, payload = client.lpush.call_args.args
    retried = json.loads(payload)
    assert retried['arguments'] == task['arguments']
    assert retried['job_id'] == task['job_id']
    assert queue == (RENDER_QUEUE if action in RENDER_ACTIONS else PRIORITY_QUEUE)
    assert startup.call_count == (1 if action in RENDER_ACTIONS else 0)


def test_render_is_durable_before_worker_startup_can_disconnect_request(monkeypatch):
    import worker_startup
    from task_queue import VIDEO_EVENTS
    from uuid import UUID
    client, socket = MagicMock(), Mock()
    task = dict(job_id='b' * 32, action='textoviva_rerender', author='Aesop',
                title='Fables', chapter_number=25, language='english')
    socket.receive.return_value = json.dumps(task)
    monkeypatch.setattr(worker_startup, 'ensure_worker_services',
                        Mock(side_effect=SystemExit('startup replaced caller')))
    namespace = dict(json=json, uuid=SimpleNamespace(UUID=UUID), VIDEO_QUEUE=VIDEO_QUEUE,
                     VIDEO_EVENTS=VIDEO_EVENTS, enqueue_task=enqueue_task,
                     redis_connection=lambda: client)
    load_functions('src/websocket/server.py', {'handle_websocket'}, namespace)
    with pytest.raises(SystemExit, match='replaced caller'):
        namespace['handle_websocket'](socket)
    queue, payload = client.lpush.call_args.args
    assert queue == RENDER_QUEUE
    assert json.loads(payload)['title'] == 'Fables'
    status = client.hset.call_args.kwargs['mapping']
    assert status['state'] == 'queued'
    assert status['chapter_number'] == 25
    assert status['author'] == 'Aesop'
    assert status['execution_queue'] == 'render'


def test_queued_video_is_visible_before_any_frame_task_exists():
    client = Mock()
    task = dict(job_id='render', title='Fables', chapter_number=25, action='textoviva_rerender')
    client.get.side_effect = lambda key: json.dumps({'workers': 24}) if key == cpu_queue.POOL else None
    client.hvals.return_value = []
    client.hgetall.return_value = {'state': 'queued', 'message': 'Queued for video generation'}
    client.llen.side_effect = lambda key: 1 if key == RENDER_QUEUE else 0
    client.lrange.side_effect = lambda key, *_: [json.dumps(task)] if key == RENDER_QUEUE else []
    plan = cpu_queue.snapshot(client)
    assert plan['render_count'] == 1
    assert plan['queued_count'] == 0
    assert plan['renders'][0]['state'] == 'queued'


def test_gpu_queue_exposes_parent_identity_for_dependency_connectors():
    namespace = load_functions('src/websocket/server.py', {'styler_queue_entry'}, {})
    entry = namespace['styler_queue_entry']({
        'job_id': 'gpu-child', 'parent_job_id': 'cpu-render', 'action': 'gpu_workflow',
        'arguments': {'workflow': {'private_prompt': 'omitted'}}, 'provider': 'comfyui',
    })
    assert entry['parent_job_id'] == 'cpu-render'
    assert 'arguments' not in entry


def test_missing_phrase_audio_fails_instead_of_silently_shortening_chapter(tmp_path):
    ns = load_functions('src/artifact_editor/chapter/timeline.py', {'chapter_audio_tracks'},
                        {'Path': Path})
    soup = BeautifulSoup('<chapter><paragraph index="0"><phrase index="0" src="first.wav">'
                         'First</phrase><phrase index="1">Last words</phrase></paragraph></chapter>', 'xml')
    wav(tmp_path / '0' / 'first.wav', 1000)
    chapter = SimpleNamespace(get_xml=lambda: soup, get_paragraph_dir=lambda index: str(index))
    with pytest.raises(ValueError, match='Phrase 1'):
        ns['chapter_audio_tracks'](chapter, str(tmp_path), Mock())
    audio = audio_helpers()
    with pytest.raises(FileNotFoundError):
        audio['assemble']([str(tmp_path / '0/first.wav'), str(tmp_path / 'absent.wav')], str(tmp_path / 'all.wav'))


def test_xml_image_starts_after_preceding_narration(tmp_path):
    ns = dict(os=os, json=json, Tag=Tag, Chapter=object, log=Mock(),
              const=SimpleNamespace(FPS=4, LIBRARY_DIR=str(tmp_path)),
              as_int=int, as_float=float, as_str=str, tags_to_dict=lambda _: {})
    load_functions('src/artifact_editor/masterplan/masterplan.py',
                   {'ImagePlan', 'WordPlan', 'Plan', 'Masterplan', 'generate_masterplan', 'frame_to_image'}, ns)
    soup = BeautifulSoup('<chapter><paragraph index="0"><phrase index="0" frames="4" duration="1">Intro</phrase>'
                         '<image index="3" src="red.png"/><phrase index="1" frames="8" duration="2">'
                         'Middle</phrase><image index="8" src="blue.png"/><phrase index="2" frames="4" duration="1">'
                         'Last</phrase></paragraph></chapter>', 'xml')
    chapter = SimpleNamespace(chapterdir='chapter', paragraphs=lambda: soup.find_all('paragraph'),
                              get_paragraph_dir=lambda _: 'chapter/0', save_xml=Mock())
    plan = ns['generate_masterplan'](chapter, force=True)
    assert [(image.index, image.start_frame, image.end_frame) for image in plan.images] == [(3, 4, 11), (8, 12, 15)]
    assert ns['frame_to_image'](plan, 0) is None
    assert ns['frame_to_image'](plan, 4).index == 3
    assert ns['frame_to_image'](plan, 15).index == 8


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='ffmpeg required')
def test_cpu_frames_encode_all_phrases_and_change_images_at_xml_boundary(tmp_path, monkeypatch):
    geometry = {'SIZE': (32, 32), 'HSIZE': 32, 'VSIZE': 32}
    const = SimpleNamespace(LIBRARY_DIR=str(tmp_path), FPS=4, GEOMETRY={'widescreen': geometry})
    audio = audio_helpers()
    ns = dict(os=os, json=json, subprocess=subprocess, log=Mock())
    encode = load_functions('src/artifact_editor/tools.py', {'assemble_mp4'}, ns)['assemble_mp4']
    frame_ns = dict(os=os, glob=glob, itertools=itertools, traceback=traceback, Image=Image,
                    const=const, log=Mock(), frame_is_current=lambda *args: False,
                    tools=SimpleNamespace(expand_dir=lambda pattern, reverse=False: sorted(glob(pattern), reverse=reverse)),
                    invalidate_frame_metadata=Mock(), save_frame_metadata=Mock())
    draw = load_functions('src/artifact_editor/frames/frames.py', {'draw_frame'}, frame_ns)['draw_frame']
    tracks = []
    for index, value in enumerate((1000, 2000, 3000)):
        track = tmp_path / 'chapter/0' / f'phrase{index}.wav'
        wav(track, value)
        tracks.append(str(track))
    for name, color in [('red', 'red'), ('blue', 'blue')]:
        Image.new('RGB', (32, 32), color).save(tmp_path / 'chapter/0' / f'{name}.png')
    words = [SimpleNamespace(start_frame=i * 4, end_frame=i * 4 + 3,
                             paragraph_index=0, index=i, fullscreen=True, paragraph_tags={}) for i in range(3)]
    def image(index, start, end, name):
        data = dict(index=index, start_frame=start, end_frame=end, image=name + '.png',
                    paragraph_dir='chapter/0', fullscreen=True, mode='scale')
        return SimpleNamespace(start_frame=start, as_dict=lambda: dict(data))
    plan = SimpleNamespace(words=words, images=[image(0, 0, 7, 'red'), image(1, 8, 11, 'blue')])
    output = str(tmp_path / 'chapter/video.mp4')
    chapter = SimpleNamespace(chapterdir='chapter', author=SimpleNamespace(name='Author'), title='Title',
                              number=1, language='english', get_aspect=lambda: 'widescreen',
                              get_masterplan=lambda **_: plan, get_all_audio_tracks=lambda: tracks,
                              build_frame_to_camera=Mock(), get_video_filename=lambda: output)
    submitted = []
    class LocalBatch:
        def __init__(self, **kwargs):
            self.tasks = []
            self.client = Mock()
            self.key = 'batch'
        def submit(self, action, arguments, label):
            self.tasks.append((action, arguments))
            submitted.append((action, arguments))
        def wait(self, progress=None):
            for count, (action, args) in enumerate(self.tasks, 1):
                if action == 'draw_frame':
                    draw(chapter, animate_lock=None, **args)
                else:
                    encode(**args)
                if progress:
                    progress(count)
    monkeypatch.setattr(cpu_queue, 'Batch', LocalBatch)
    ns = dict(os=os, const=const, phrases=SimpleNamespace(**{key: audio[key] for key in ('assemble', 'get_wav_duration')}),
              camera=SimpleNamespace(frame_to_camera=lambda _: None))
    render = load_functions('src/artifact_editor/video/video.py', {'book_to_video'}, ns)['book_to_video']
    render(chapter)
    assert [action for action, _ in submitted].count('draw_frame') == 12
    assert submitted[-1][0] == 'encode_video'
    probe = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', output]))
    streams = {stream['codec_type']: stream for stream in probe['streams']}
    assert int(streams['video']['nb_frames']) == 12
    assert float(streams['audio']['duration']) >= 3 - 0.04
    decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', output, '-an', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    assert decoded[:3][0] > 240 and decoded[:3][2] < 10
    assert decoded[8 * 32 * 32 * 3 + 2] > 240
    # Every source sample, including the last phrase, reached assembled narration.
    with wave.open(str(tmp_path / 'chapter/audio.wav'), 'rb') as track:
        samples = struct.unpack('<' + 'h' * track.getnframes(), track.readframes(track.getnframes()))
    assert samples == (1000,) * 16000 + (2000,) * 16000 + (3000,) * 16000
    # Re-rendering failure preserves the previous playable output.
    before = Path(output).read_bytes()
    (tmp_path / 'absent').mkdir()
    with pytest.raises(subprocess.CalledProcessError):
        encode(4, str(tmp_path / 'absent'), None, output)
    assert Path(output).read_bytes() == before


def test_gpu_workflow_wait_happens_before_acquiring_gpu_ownership(monkeypatch):
    import inspect
    from functools import wraps
    monkeypatch.setenv('RENDER_WORKER_PROCESS', '1')
    monkeypatch.delenv('VIDEO_WORKER_PROCESS', raising=False)
    submit = Mock(return_value=None)
    monkeypatch.setattr(gpu_tasks, 'submit', submit)
    class Workflow:
        def serialize(self):
            return {'nodes': []}
    ns = dict(os=os, wraps=wraps, inspect=inspect, Workflow=Workflow,
              const=SimpleNamespace(COMFYUI_JOB_TIMEOUT_SECONDS=1800))
    load_functions('src/comfy.py', {'_requires_gpu_service'}, ns)
    def run_workflow(workflow, duration_seconds=None):
        pytest.fail('The coordinator must delegate instead of executing GPU work')
    ns['_requires_gpu_service']('comfy')(run_workflow)(Workflow(), duration_seconds=2)
    assert submit.call_args.args[0] == 'gpu_workflow'
    assert submit.call_args.args[1] == {'workflow': {'nodes': []}, 'duration_seconds': 2}


def test_cpu_worker_failure_is_recorded_and_claim_acknowledged(monkeypatch):
    client = MagicMock()
    client.hget.return_value = None
    pipe = client.pipeline.return_value.__enter__.return_value
    monkeypatch.setattr(cpu_queue, 'execute', Mock(side_effect=ValueError('bad frame')))
    task = dict(job_id='frame-1', batch_id='batch-1', action='draw_frame', arguments={})
    payload = json.dumps(task)
    cpu_queue.run_task(client, payload, '1', {}, 'processing:1')
    pipe.hincrby.assert_called_once_with('cpu_batch:batch-1', 'failed', 1)
    pipe.lrem.assert_called_once_with('processing:1', 1, payload)
    pipe.execute.assert_called_once()


def test_cpu_workers_default_to_available_core_count(monkeypatch):
    monkeypatch.setattr(os, 'sched_getaffinity', lambda _: {0, 1, 2, 3})
    assert cpu_queue.core_count() == 4


def test_freshly_built_camera_is_usable_without_a_previous_disk_cache():
    ns = dict(_frame_loaded=False, _frame_to_camera=[], log=Mock())
    load_functions('src/artifact_editor/camera.py', {'set_frame_to_camera', 'frame_to_camera'}, ns)
    ns['set_frame_to_camera']([None, 12.5])
    assert ns['frame_to_camera'](0) is None
    assert ns['frame_to_camera'](1) == 12.5


def test_parallel_pan_frames_keep_source_immutable_and_use_resized_bounds(tmp_path):
    source = tmp_path / 'source.png'
    Image.new('RGB', (320, 80), 'red').save(source)
    ns = dict(const=SimpleNamespace(IMG_TARGET_WIDTH=32, IMG_TARGET_HEIGHT=32),
              math=__import__('math'))
    load_functions('src/artifact_editor/frames/frames.py', {'hpan'}, ns)
    with Image.open(source) as image:
        frame = ns['hpan'](str(source), image, 0, 9, 9)
    assert frame.size == (32, 32)
    assert frame.getpixel((31, 31)) == (255, 0, 0)
    with Image.open(source) as image:
        assert image.size == (320, 80)


@pytest.mark.parametrize('options, animate', [
    ({}, False), ({'force': True}, False),
    ({'regenerate_image_index': 0}, True),
    ({'regenerate_image_indices': [1]}, True),
    ({'regenerate_insufficient': True}, True),
])
def test_generate_video_accepts_stills_and_reuses_existing_animation(tmp_path, monkeypatch, options, animate):
    import artifact_editor
    paragraph_dir = tmp_path / 'chapter/0'
    paragraph_dir.mkdir(parents=True)
    audio = paragraph_dir / 'phrase.wav'
    wav(audio, 1000)
    soup = BeautifulSoup('<chapter><paragraph index="0"><image index="0" src="still.png"/>'
                         '<phrase index="0">First</phrase><image index="1" src="clip.png"/>'
                         '<phrase index="1">Last</phrase></paragraph></chapter>', 'xml')
    for name in ('still.png', 'clip.png'):
        Image.new('RGB', (32, 32), 'red').save(paragraph_dir / name)
    existing_clip = paragraph_dir / 'clip.mp4'
    existing_clip.touch()
    chapter = SimpleNamespace(
        get_video_filename=lambda: str(tmp_path / 'output.mp4'),
        get_xml=lambda: soup, phrases=lambda: [SimpleNamespace(phrase_xml=p, index=p['index']) for p in soup.find_all('phrase')],
        images=lambda: iter(soup.find_all('image')), save_xml=Mock(),
        get_paragraph_dir=lambda _: 'chapter/0', get_aspect=lambda: 'widescreen',
        build_frame_to_camera=Mock(), get_masterplan=Mock())
    tools = SimpleNamespace(extract_frames=Mock())
    monkeypatch.setattr(artifact_editor, 'tools', tools, raising=False)
    render = Mock()
    namespace = dict(Chapter=object, Tag=Tag, os=os, glob=SimpleNamespace(glob=glob), shutil=shutil, log=Mock(),
        const=SimpleNamespace(FPS=4, LIBRARY_DIR=str(tmp_path)), redis=SimpleNamespace(Redis=Mock()),
        images=SimpleNamespace(generate_metadata_from_text=Mock()),
        phrases=SimpleNamespace(get_wavfile_filename=lambda *_: str(audio),
                                wav_pad_to_frame=Mock(), get_wav_duration=lambda _: 1),
        video=SimpleNamespace(render_masterplan_widescreen=render))
    cls = load_functions('src/artifact_editor/textoviva/textoviva.py', {'Textoviva'}, namespace)['Textoviva']
    instance = cls(chapter)
    instance.generate_missing_images = Mock()
    instance.generate_missing_animations = Mock()
    instance.create_video(**options)
    assert instance.generate_missing_animations.called is animate
    render.assert_called_once()
    # Mixed still/animated chapters expand only the existing clip.
    tools.extract_frames.assert_called_once_with(str(existing_clip), str(paragraph_dir / 'animation/image_000001_00'))
    assert (paragraph_dir / 'still.png').is_file()


def test_selected_animation_does_not_require_or_prompt_unselected_stills(tmp_path):
    soup = BeautifulSoup('<chapter><paragraph index="0"><image index="0"/>'
                         '<image index="1"/></paragraph></chapter>', 'xml')
    still = tmp_path / 'selected.png'
    still.touch()
    chapter = SimpleNamespace(images=lambda: iter(soup.find_all('image')),
        get_image_filename=lambda image: str(still) if image['index'] == '1' else None,
        get_video_filename=lambda: str(tmp_path / 'output.mp4'), save_xml=Mock())
    # Stop at the first model boundary; inspect which asset reached it.
    prompt = Mock(side_effect=RuntimeError('model boundary'))
    namespace = dict(Chapter=object, Tag=Tag, os=os, images=SimpleNamespace(generate_animation_prompt=prompt))
    cls = load_functions('src/artifact_editor/textoviva/textoviva.py', {'Textoviva'}, namespace)['Textoviva']
    with pytest.raises(RuntimeError, match='model boundary'):
        cls(chapter).generate_missing_animations(regenerate_image_index=1)
    prompt.assert_called_once_with(chapter=chapter, image_xml=soup.find('image', index='1'))
    assert 'animation_prompt' not in soup.find('image', index='0').attrs

"""Narration changes invalidate videos and only animations too short for the new timeline."""
import ast
import importlib.util
import os
from pathlib import Path
import shutil
import sys
import time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
from filelock import FileLock
import pytest

ROOT = Path(__file__).resolve().parents[1]


def functions(relative, names, **namespace):
    path = ROOT / relative
    nodes = [node for node in ast.parse(path.read_text()).body
             if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace.setdefault('__package__', 'artifact_editor.chapter')
    exec(compile(ast.Module(body=nodes, type_ignores=[]),str(path),'exec'),namespace)
    return namespace


@pytest.fixture
def media(tmp_path, monkeypatch):
    saved = Mock()
    steps = {'chapter_import','chapter_missing_audio','chapter_missing_animations','textoviva_rerender'}
    monkeypatch.setitem(sys.modules,'artifact_editor.chapter.workflow', SimpleNamespace(
        _path=lambda chapter: tmp_path/'workflow.json', _save=saved,
        completed_steps=lambda chapter:set(steps)))
    timeline = functions('src/artifact_editor/chapter/timeline.py', {'phrase_timings','image_timings'},
                         Decimal=Decimal, MAX_ANIMATION_DURATION_SECONDS=Decimal('8'))
    monkeypatch.setitem(sys.modules,'artifact_editor.chapter.timeline', SimpleNamespace(image_timings=timeline['image_timings']))
    monkeypatch.setitem(sys.modules,'artifact_editor.chapter.readiness',
                        SimpleNamespace(_animation_is_disabled=lambda image:image.get('do_not_animate')=='1'))
    namespace = functions('src/artifact_editor/chapter/media_invalidation.py',
        {'_invalidate_steps','invalidate_chapter_video','invalidate_insufficient_animations'},
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path),FPS=10),
        Path=Path,time=time,shutil=shutil,FileLock=FileLock)
    return SimpleNamespace(**namespace), saved


def test_video_invalidation_preserves_active_shelf_link_and_other_assets(tmp_path, media):
    invalidate,saved=media
    active=tmp_path/'chapter'; active.mkdir()
    shelf=tmp_path/'shelf.mp4'; shelf.write_bytes(b'old video')
    link=active/'book.mp4'; link.symlink_to(shelf)
    still=active/'image.png'; still.write_bytes(b'still')
    for name in ('masterplan.json','camera.json'):(active/name).write_text('{}')
    chapter=SimpleNamespace(chapterdir='chapter',get_video_filename=lambda:str(link))
    invalidate.invalidate_chapter_video(chapter)
    assert link.is_symlink() and not link.exists()
    assert not shelf.exists()
    assert still.read_bytes()==b'still'
    assert not (active/'masterplan.json').exists()
    assert 'textoviva_rerender' not in saved.call_args.args[1]
    assert 'chapter_missing_animations' in saved.call_args.args[1]
    invalidate.invalidate_chapter_video(chapter)  # Already missing is harmless.


@pytest.mark.parametrize('available,invalidated', [(39,True),(40,False),(60,False),(None,True)])
def test_animation_adequacy_includes_silence_and_preserves_long_enough_clips(tmp_path,monkeypatch,media,available,invalidated):
    invalidate,saved=media
    xml=BeautifulSoup('<chapter><paragraph index="0"><image index="0" src="img_0_still.png" animated_src="img_0_generated.mp4" animation_oom_audio_frames="1"/>'
        '<phrase index="0" duration="3.1" frames="31"/><silence duration=".9" frames="9"/>'
        '<image index="1" src="img_1_still.png"/><phrase index="1" duration="3" frames="30"/>'
        '<image index="2" do_not_animate="1"/><phrase index="2" duration="5" frames="50"/>'
        '</paragraph></chapter>','xml')
    directory=tmp_path/'paragraph';directory.mkdir()
    expansion=directory/'animation/image_000000_00';expansion.mkdir(parents=True)
    (expansion/'frame.png').touch()
    companion=directory/'img_0_still.mp4';companion.touch()
    original=directory/'img_0_generated.mp4';original.touch()
    other=directory/'img_1_still.mp4';other.touch()
    measure=Mock(side_effect=lambda image,*a:available if image['index']=='0' else 30)
    monkeypatch.setitem(sys.modules,'artifact_editor.images.animation_source',SimpleNamespace(animation_frame_count=measure))
    chapter=SimpleNamespace(get_xml=lambda:xml,images=lambda:iter(xml.find_all('image')),
        get_paragraph_dir=lambda _:'paragraph',save_image=Mock())
    result=invalidate.invalidate_insufficient_animations(chapter)
    assert result==(['0'] if invalidated else [])
    assert measure.call_count==2
    image=xml.find('image',index='0')
    assert image['frames']=='40', 'Shot duration includes narration and silence'
    assert xml.find('image',index='2')['frames']=='50'
    assert ('animation_invalidated_ns' in image.attrs)==invalidated
    assert ('animated_src' not in image.attrs)==invalidated
    assert expansion.exists()!=invalidated
    assert companion.exists()!=invalidated
    assert original.exists() and other.exists()
    assert not xml.find('image',index='1').get('animation_invalidated_ns')
    assert not xml.find('image',index='2').get('animation_invalidated_ns')
    if invalidated:
        assert 'chapter_missing_animations' not in saved.call_args.args[1]
        assert 'textoviva_rerender' not in saved.call_args.args[1]
    else:
        saved.assert_not_called()


def test_invalidated_mp4s_and_extracted_frames_cannot_be_rediscovered(tmp_path,monkeypatch):
    monkeypatch.setitem(sys.modules,'artifact_editor.images.source_motion',SimpleNamespace(active_motion=lambda _:None))
    path=ROOT/'src/artifact_editor/images/animation_source.py'
    spec=importlib.util.spec_from_file_location('animation_invalidation_test',path)
    animation=importlib.util.module_from_spec(spec);spec.loader.exec_module(animation)
    monkeypatch.setattr(animation,'_video_frame_count',lambda *a:40)
    for name in ('img_0_generated.mp4','img_0_still.mp4','camera.mp4'):
        (tmp_path/name).touch();os.utime(tmp_path/name,ns=(10,10))
    frames=tmp_path/'animation/image_000000_00';frames.mkdir(parents=True)
    (frames/'old.png').touch();os.utime(frames/'old.png',ns=(10,10))
    image={'index':'0','src':'img_0_still.png','animated_src':'img_0_generated.mp4'}
    assert animation.animation_frame_count(image,tmp_path,10)==40
    image['animation_invalidated_ns']='20'
    assert animation.animation_filename(image,tmp_path) is None
    assert animation.animation_frame_count(image,tmp_path,10)==0
    fresh=tmp_path/'img_0_fresh.mp4';fresh.touch();os.utime(fresh,ns=(30,30))
    assert animation.animation_filename(image,tmp_path)=='img_0_fresh.mp4'
    assert animation.animation_frame_count(image,tmp_path,10)==40


def test_partial_audio_batch_persists_successful_timing_before_raising(tmp_path,monkeypatch):
    xml=BeautifulSoup('<chapter><paragraph><phrase index="1" speaker="Sad_man" frames="10" duration="1">one</phrase>'
                      '<phrase index="2" speaker="Sad_man" frames="10" duration="1">two</phrase></paragraph></chapter>','xml')
    phrases={phrase['index']:phrase for phrase in xml.find_all('phrase')}
    chapter=SimpleNamespace(chapterdir='chapter',get_xml=lambda:xml,get_phrase=lambda index:phrases[str(index)],
                            save_phrase=Mock(),save_timing=Mock())
    def prepare(chapter,phrase,speaker,wavfile,**kwargs):
        return {'ready':False,'workflow_fn':phrase['index'],'label':speaker,'wavfile':wavfile}
    def batch(workflows,on_complete,**kwargs):
        on_complete(0,{})
        return [{},RuntimeError('second track failed')]
    monkeypatch.setitem(sys.modules,'comfy',SimpleNamespace(run_workflow_fn_batch=batch))
    def collected(request):Path(request['wavfile']).touch()
    def recalculate(chapter):
        assert int(phrases['1']['frames'])==40
        assert phrases['2']['frames']=='10'
    run=functions('src/artifact_editor/phrases/phrases.py',{'generate_all_audio'},
        log=Mock(),os=os,name_to_tag=lambda name:name,
        get_wavfile_filename=lambda phrase,chapter:str(tmp_path/f"{phrase['index']}.wav"),
        llm=SimpleNamespace(text_2_audio=prepare,collect_text_2_audio=collected),
        comfy=SimpleNamespace(run_workflow_fn_batch=batch),
        const=SimpleNamespace(FPS=10,COMFY_DIRS={'artifactserver':{'OUTPUT_DIR':str(tmp_path)}}),
        wav_pad_to_frame=Mock(),get_wav_duration=lambda filename:4,
        recalculate_image_frames=recalculate)['generate_all_audio']
    with pytest.raises(RuntimeError,match='second track failed'):
        run(chapter,force=True,batch=True,speaker_tags={'Sad_man'})
    chapter.save_timing.assert_called_once()
    assert chapter.save_phrase.call_count==1


def test_candidate_picker_cannot_restore_an_invalidated_video(tmp_path):
    import glob
    directory=tmp_path/'paragraph';directory.mkdir()
    stale=directory/'img_0_old.mp4';stale.touch();os.utime(stale,ns=(10,10))
    fresh=directory/'img_0_new.mp4';fresh.touch();os.utime(fresh,ns=(30,30))
    xml=BeautifulSoup('<paragraph index="0"><image index="0" animation_invalidated_ns="20"/></paragraph>','xml')
    chapter=SimpleNamespace(nice='book',get_paragraph_dir=lambda _:'paragraph')
    picker=functions('src/artifact_editor/images/selector/htmx.py',{'gather_candidate_videos'},
        Tag=object,Chapter=object,glob=glob,os=os,shutil=shutil,log=Mock(),as_int=int,
        images=SimpleNamespace(all_videos_by_image_index=lambda *a:[str(stale),str(fresh)]),
        const=SimpleNamespace(LIBRARY_DIR=str(tmp_path),COMFY_DIRS={'artifactserver':{'OUTPUT_DIR':str(tmp_path/'empty')}}))['gather_candidate_videos']
    assert picker(xml.image,chapter)==[str(fresh)]

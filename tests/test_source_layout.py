import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import sys

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
import pytest

from artifact_editor.images import source_layout as layout, source, source_motion
from artifact_editor.book import pages


def artwork(size, box=None):
    image=Image.new('RGB',size,'white')
    ImageDraw.Draw(image).rectangle(box or (0,0,size[0]-1,size[1]-1),fill=(30,40,100))
    return image


def test_square_crop_uses_only_empty_margins():
    image=artwork((900,500),(220,50,670,450))
    plan=layout.layout_plan(image)
    assert plan['mode']=='square'
    l,t,r,b=plan['box']
    assert r-l==b-t==500
    assert l<=220 and r>=671 and t<=50 and b>=451
    assert plan['added_whitespace']==0


def test_near_square_art_can_add_a_small_paper_border():
    plan=layout.layout_plan(artwork((500,530)))
    assert plan['mode']=='square'
    assert 0 < plan['added_whitespace'] <= .1
    l,t,r,b=plan['box']
    assert r-l==b-t==530
    assert l<=0 and r>=500 and t<=0 and b>=530


@pytest.mark.parametrize('size,direction', [((1000,400),'x'),((400,1000),'y')])
def test_incompatible_aspects_pan_in_the_long_direction(size,direction):
    image=artwork(size)
    plan=layout.layout_plan(image)
    assert plan['mode']=='pan'
    motion=plan['motion']
    assert motion['first'][direction] < motion['last'][direction]
    other='y' if direction=='x' else 'x'
    assert motion['first'][other]==motion['last'][other]
    first=source_motion.crop_box(size,(100,100),motion['first'])
    last=source_motion.crop_box(size,(100,100),motion['last'])
    assert min(first[0],last[0])==0 and max(first[2],last[2])==size[0]
    assert min(first[1],last[1])==0 and max(first[3],last[3])==size[1]


def test_full_bleed_image_is_not_mistaken_for_empty_paper():
    image=Image.new('RGB',(1000,400),(20,30,40))
    assert layout.layout_plan(image)['bounds']==(0,0,1000,400)
    assert layout.layout_plan(image)['mode']=='pan'


@pytest.fixture
def chapter(tmp_path,monkeypatch):
    root=tmp_path/'Author/Book/source';root.mkdir(parents=True)
    artwork((900,500),(220,50,670,450)).save(root/'crop.png')
    artwork((400,1000)).save(root/'pan.png')
    tree=BeautifulSoup('<chapter><paragraph index="0"><image index="0" source_image="crop.png"/>'
        '<image index="1" source_image="pan.png"/></paragraph></chapter>','xml')
    c=SimpleNamespace(bookdir='Author/Book',get_xml=lambda:tree,save_image=Mock(),
        get_paragraph_dir=lambda _: 'Author/Book/paragraphs/0')
    monkeypatch.setitem(sys.modules,'const',SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    monkeypatch.setattr(layout,'output_size',lambda *_:(160,160))
    monkeypatch.setattr(source,'output_size',lambda *_:(160,160))
    monkeypatch.setattr(pages,'book_for',lambda _:SimpleNamespace(config={}))
    return c,tree,root


def test_import_prepares_all_sources_and_keeps_animation_enabled_only_for_square(chapter):
    c,tree,root=chapter
    before={p.name:p.read_bytes() for p in root.glob('*.png')}
    layout.prepare_chapter_sources(c)
    square,pan=tree.find_all('image')
    assert json.loads(square['auto_source_layout'])['mode']=='square'
    assert not square.get('source_motion') and square.get('do_not_animate')!='1'
    assert pan.get('do_not_animate')=='1' and source_motion.active_motion(pan)
    with Image.open(source.source_image_path(c,square['source_image'])) as image:
        assert image.size==(160,160)
    for name,contents in before.items():assert (root/name).read_bytes()==contents
    c.save_image.reset_mock()
    layout.prepare_chapter_sources(c)
    c.save_image.assert_not_called()


def test_pan_button_can_replace_existing_motion_with_an_animation_eligible_square(chapter):
    c,tree,_=chapter
    image=tree.find('image')
    image['do_not_animate']='1'
    image['source_motion']=json.dumps(source_motion.default_motion())
    assert layout.apply_source_layout(c,image,'crop.png')=='square'
    assert not image.get('source_motion') and not image.get('do_not_animate')


def test_manual_motion_is_not_replaced_by_import(chapter):
    c,tree,_=chapter
    image=tree.find('image')
    image['src']='manual.png';image['source_motion_src']='manual.png'
    image['source_motion']=json.dumps(source_motion.default_motion())
    layout.prepare_chapter_sources(c)
    assert image['src']=='manual.png'
    assert image['do_not_animate']=='1'
    assert not image.get('auto_source_layout')


def test_square_crop_does_not_reuse_animation_from_before_crop(chapter,tmp_path):
    from artifact_editor.images.animation_source import animation_filename,animation_frame_count
    c,tree,_=chapter
    image=tree.find('image')
    layout.apply_source_layout(c,image,'crop.png')
    old=tmp_path/'img_0_old.mp4';old.touch()
    cutoff=int(image['source_layout_updated_ns'])
    os.utime(old,ns=(cutoff-1000,cutoff-1000))
    frames=tmp_path/'animation/image_000000_old';frames.mkdir(parents=True)
    (frames/'frame.png').touch()
    assert animation_filename(image,tmp_path) is None
    assert animation_frame_count(image,tmp_path)==0
    fresh=tmp_path/'img_0_new.mp4';fresh.touch()
    os.utime(fresh,ns=(cutoff+1000,cutoff+1000))
    assert animation_filename(image,tmp_path)==fresh.name


@pytest.mark.parametrize('filename,mode',[('crop.png','square'),('pan.png','pan')])
def test_auto_pan_http_action_saves_layout_and_returns_updated_controls(chapter,monkeypatch,filename,mode):
    import ast
    import flask
    from flask import Flask
    from artifact_editor.images import completions
    c,tree,_=chapter
    c.kwargs={}
    c.get_image=lambda index:tree.find('image',index=str(index))
    published=Mock();monkeypatch.setattr(completions,'publish_image_completion',published)
    file=Path('src/artifact_editor/images/views.py')
    node=next(n for n in ast.parse(file.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='auto_pan_source_image')
    node.decorator_list=[]
    scope=dict(__package__='artifact_editor.images',g=flask.g,request=flask.request,
        abort=flask.abort,make_response=flask.make_response,url_for=flask.url_for,json=json,cache=Mock())
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(file),'exec'),scope)
    app=Flask(__name__,template_folder=str(Path('src/artifact_editor/images/templates').resolve()))
    for name,path in [('chapter_source_image','/images/<int:image_index>/source/<path:filename>'),
                      ('refresh_source_picker','/images/<int:image_index>/refresh'),
                      ('crop_source_picker_image','/images/<int:image_index>/crop'),
                      ('choose_chapter_source','/images/<int:image_index>/choose'),
                      ('show_image_by_index','/images/<int:image_index>/preview')]:
        app.add_url_rule(path,endpoint='library.book.chapter.images.'+name,view_func=lambda **_: '')
    app.add_url_rule('/images/<int:image_index>/auto_pan',endpoint='library.book.chapter.images.auto_pan_source_image',
        view_func=scope['auto_pan_source_image'],methods=['POST'])
    app.before_request(lambda:setattr(flask.g,'chapter',c))
    response=app.test_client().post('/images/0/auto_pan',data={'image_source':filename})
    assert response.status_code==200,response.text
    event=json.loads(response.headers['HX-Trigger'])['chapter-image-selected']
    assert event['source_layout']==mode
    assert 'Auto Pan' in response.text
    assert ('AI animation is enabled' if mode=='square' else 'AI animation is disabled') in response.text
    published.assert_called_once_with(c,tree.find('image',index='0'))


def test_shared_import_still_prepares_source_layout_when_text_is_unchanged(chapter,tmp_path):
    import ast
    import hashlib
    c,tree,_=chapter
    c.language='english'
    c.get_chapterdir=lambda:'Author/Book/chapter/0001'
    sections=[{'section_type':'chapter_text','typesetting_method':'plain','ops':[{'insert':'Hello.\n'}]}]
    digest=hashlib.sha256(json.dumps(sections,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    folder=tmp_path/c.get_chapterdir()/c.language;folder.mkdir(parents=True)
    (folder/'chapter-import-state.json').write_text(json.dumps({'version':2,'sections_sha256':digest}))
    file=Path('src/artifact_editor/chapter/views.py')
    node=next(n for n in ast.parse(file.read_text()).body if isinstance(n,ast.FunctionDef) and n.name=='import_chapter_content')
    scope=dict(BeautifulSoup=BeautifulSoup,os=os,json=json,const=sys.modules['const'])
    exec(compile(ast.Module(body=[node],type_ignores=[]),str(file),'exec'),scope)
    assert scope['import_chapter_content'](c,json.dumps(sections)) is False
    assert all(image.get('auto_source_layout') for image in tree.find_all('image'))
    readiness=Path('src/artifact_editor/chapter/readiness.py')
    nodes=[n for n in ast.parse(readiness.read_text()).body if isinstance(n,ast.FunctionDef)
           and n.name in {'_animation_is_disabled','animation_is_complete'}]
    checks={}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),str(readiness),'exec'),checks)
    animation_is_complete=checks['animation_is_complete']
    square,pan=tree.find_all('image')
    assert not animation_is_complete(square,0,100)
    assert animation_is_complete(pan,0,100)

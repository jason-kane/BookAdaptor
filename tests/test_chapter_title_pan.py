import json
from types import SimpleNamespace
from unittest.mock import Mock
import sys

from bs4 import BeautifulSoup
from PIL import Image, ImageDraw
import pytest

from artifact_editor.images import chapter_title, source, source_motion
from artifact_editor.book import pages


def opening_page(paper=(246, 232, 211)):
    page = Image.new('RGB', (600, 800), paper)
    draw = ImageDraw.Draw(page)
    draw.rectangle((45, 40, 555, 155), outline='black', width=5)
    # Decorative lettering is much taller than the ordinary text below it.
    for x in range(60, 530, 45):
        draw.rectangle((x, 170, x+25, 220), fill='black')
    for y in range(270, 750, 34):
        for x in range(60, 530, 30):
            draw.rectangle((x, y, x+18, y+17), fill='black')
    return page


@pytest.mark.parametrize('paper', [(255,255,255), (246,232,211)])
@pytest.mark.parametrize('target', [(160,160), (160,90), (90,160)])
def test_pan_keeps_decorative_title_above_body_text(paper, target):
    original = opening_page(paper)
    settings, box = chapter_title.title_motion(original, target)
    assert box[1] < 40 and 220 < box[3] < 270
    assert settings['first']['x'] < settings['last']['x']
    for region in settings.values():
        left,top,right,bottom = source_motion.crop_box(original.size,target,region)
        assert top >= box[1] - 1e-6
        assert bottom <= box[3] + 1e-6
        assert bottom < 270
        assert (right-left)/(bottom-top) == pytest.approx(target[0]/target[1])
    for progress in (0,.5,1):
        assert source_motion.render_frame(original,settings,progress,target).size == target


def test_unclear_separation_fails_without_inventing_a_crop():
    with pytest.raises(ValueError, match='no detectable'):
        chapter_title.illustration_box(Image.new('RGB',(600,800),'white'))


@pytest.fixture
def source_chapter(tmp_path, monkeypatch):
    root=tmp_path/'Author/Book/source'
    root.mkdir(parents=True)
    original=root/'opening.png'
    opening_page().save(original)
    opening_page().save(root/'later.png')
    xml=BeautifulSoup('<chapter><paragraph index="0"><image index="9"/>'
        '<image index="0" source_image="opening.png" workflow_image_template="source.chapter"/>'
        '<image index="1" source_image="later.png"/></paragraph></chapter>','xml')
    chapter=SimpleNamespace(bookdir='Author/Book', get_xml=lambda:xml,
        get_paragraph_dir=lambda _: 'Author/Book/paragraphs/0',save_image=Mock())
    book=SimpleNamespace(config={chapter_title.SETTING:True})
    monkeypatch.setattr(pages,'book_for',lambda _:book)
    monkeypatch.setattr(source_motion,'output_size',lambda *_:(160,160))
    monkeypatch.setattr(source,'output_size',lambda *_:(160,160))
    monkeypatch.setitem(sys.modules,'const',SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    return chapter,book,original,xml


def test_preparation_selects_first_source_only_and_reuses_real_camera_renderer(source_chapter):
    chapter,_,original,xml=source_chapter
    before=original.read_bytes()
    assert chapter_title.prepare_chapter_title(chapter) is True
    first=xml.find('image',index='0')
    assert source_motion.active_motion(first)
    assert first['do_not_animate']=='1'
    assert json.loads(first['auto_chapter_title_pan'])['version']==1
    assert not xml.find('image',index='1').get('source_motion')
    assert not xml.find('image',index='9').get('source_motion')
    assert original.read_bytes()==before
    chapter.save_image.reset_mock()
    assert chapter_title.prepare_chapter_title(chapter) is False
    chapter.save_image.assert_not_called()


def test_disabled_setting_keeps_source_untouched(source_chapter):
    chapter,book,_,xml=source_chapter
    book.config[chapter_title.SETTING]=False
    assert chapter_title.prepare_chapter_title(chapter) is False
    assert not xml.find('image',index='0').get('src')
    chapter.save_image.assert_not_called()


def test_manual_source_motion_is_preserved(source_chapter):
    chapter,_,_,xml=source_chapter
    first=xml.find('image',index='0')
    settings=source_motion.default_motion()
    first['src']='manual.png'
    first['source_motion_src']='manual.png'
    first['source_motion']=json.dumps(settings)
    chapter_title.prepare_chapter_title(chapter)
    assert first['src']=='manual.png'
    assert json.loads(first['source_motion'])==settings
    assert not first.get('auto_chapter_title_pan')


def test_automatic_pan_takes_precedence_over_old_generated_animation(tmp_path):
    from artifact_editor.images.animation_source import animation_filename
    image=BeautifulSoup('<image index="0" src="first.png" animated_src="old.mp4"/>','xml').image
    image['source_motion']=json.dumps(source_motion.default_motion())
    image['source_motion_src']='first.png'
    image['auto_chapter_title_pan']='{"version":1}'
    (tmp_path/'old.mp4').touch()
    assert animation_filename(image,tmp_path) is None


def test_last_frame_preview_uses_original_scan_not_the_already_cropped_first_frame(source_chapter, tmp_path):
    import ast
    import hashlib
    import os
    from pathlib import Path
    from bs4 import Tag
    from PIL import ImageChops
    chapter,_,original,xml=source_chapter
    chapter_title.prepare_chapter_title(chapter)
    image=xml.find('image',index='0')
    chapter.get_paragraphdir=chapter.get_paragraph_dir
    chapter.get_image_filename=lambda image: str(tmp_path/chapter.get_paragraph_dir(0)/image['src'])
    path=Path('src/artifact_editor/chapter/chapter.py')
    cls=next(n for n in ast.parse(path.read_text()).body if isinstance(n,ast.ClassDef) and n.name=='Chapter')
    method=next(n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name=='get_last_frame')
    scope=dict(Tag=Tag,log=Mock(),os=os,json=json,md5=hashlib.md5,Image=Image,as_int=int,
               const=SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    exec(compile(ast.Module(body=[method],type_ignores=[]),str(path),'exec'),scope)
    result=scope['get_last_frame'](chapter,image)
    with Image.open(original) as scan, Image.open(result) as actual:
        expected=source_motion.render_frame(scan,source_motion.active_motion(image),1,(160,160))
        assert ImageChops.difference(actual.convert('RGB'),expected.convert('RGB')).getbbox() is None


def test_opening_title_setting_supersedes_an_earlier_automatic_generic_pan(source_chapter):
    chapter,_,_,xml=source_chapter
    image=xml.find('image',index='0')
    image['src']='old-pan.png';image['source_motion_src']='old-pan.png'
    image['source_motion']=json.dumps(source_motion.default_motion())
    image['auto_source_layout']=json.dumps({'input':'opening.png','mode':'pan','origin':'import'})
    assert chapter_title.prepare_chapter_title(chapter)
    assert image.get('auto_chapter_title_pan')
    assert not image.get('auto_source_layout')
    assert json.loads(image['auto_chapter_title_pan'])['box'][3] < 270


def test_explicit_auto_pan_button_choice_is_not_overridden_by_title_default(source_chapter):
    chapter,_,_,xml=source_chapter
    image=xml.find('image',index='0')
    image['auto_source_layout']=json.dumps({'input':'opening.png','mode':'square','origin':'manual'})
    assert chapter_title.prepare_chapter_title(chapter) is False
    chapter.save_image.assert_not_called()

from types import SimpleNamespace

import pytest

from text_to_image.comfyui.workflow.ui import selected_image_workflow


@pytest.mark.parametrize('saved', [
    't2i.zit-paragraph', 'zit-paragraph',
    'ui.t2i.zit-paragraph_Z-Image Turbo Paragraph Image.json',
    'minimax_h3-MiniMax H3',
])
def test_preselects_workflow_used_for_generated_image(saved):
    templates = [
        SimpleNamespace(id='t2i.other', path='ui.t2i.other_Other.json'),
        SimpleNamespace(id='t2i.zit-paragraph',
                        path='ui.t2i.zit-paragraph_Z-Image Turbo Paragraph Image.json'),
    ]
    image = dict(index='2', workflow_image_template=saved,
                 src='/active/paragraphs/3/img_2_zit-paragraph_Z-Image Turbo Paragraph Image_00001_.png')
    assert selected_image_workflow(image, templates, 't2i.other') == 't2i.zit-paragraph'


def test_explicit_selection_takes_precedence_over_previous_image_filename():
    templates = [SimpleNamespace(id='t2i.new', path='ui.t2i.new_New.json'),
                 SimpleNamespace(id='t2i.old', path='ui.t2i.old_Old.json')]
    image = dict(index='0', workflow_image_template='t2i.new', src='img_0_old_Old.png')
    assert selected_image_workflow(image, templates, 't2i.old') == 't2i.new'

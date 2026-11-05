"""Chapter text, mood/theme, preview and timeline actions reachable from Library."""
import json
import pytest


def test_sections_load_from_delta_then_persist_edits(chapter_ui):
    ui = chapter_ui
    assert ui.client.get('/chapter/sections').json == [{'typesetting_method': 'verse', 'ops': [{'insert': 'A door.\n'}]}]
    sections = [{'typesetting_method': 'plain', 'ops': [{'insert': 'Revised.\n'}]}]
    assert ui.client.post('/chapter/sections', data={'sections': json.dumps(sections)}).status_code == 200
    assert ui.client.get('/chapter/sections').json == sections
    path = ui.root / 'book/chapter/0001/english/chapter.sections.json'
    assert json.loads(path.read_text()) == sections


@pytest.mark.parametrize('sections', ['bad json', '[]', '{}', '[{"typesetting_method":"bad","ops":[]}]', '[{"typesetting_method":"verse","ops":"bad"}]'])
def test_sections_reject_invalid_edits_without_writing(chapter_ui, sections):
    assert chapter_ui.client.post('/chapter/sections', data={'sections': sections}).status_code == 400
    assert not list(chapter_ui.root.rglob('chapter.sections.json'))


def test_sections_fall_back_to_plain_text(chapter_ui):
    chapter_ui.chapter.load_delta.return_value = None
    assert chapter_ui.client.get('/chapter/sections').json == [{'typesetting_method': 'verse', 'ops': [{'insert': 'A door.'}]}]


def test_title_edit_is_saved(chapter_ui):
    assert chapter_ui.client.put('/chapter/update_title', data={'chapter_title': 'The Door'}).status_code == 200
    chapter_ui.chapter.set_chapter_title.assert_called_once_with('The Door')


@pytest.mark.parametrize('field', ['mood', 'theme'])
def test_mood_and_theme_manual_edits_persist(chapter_ui, field):
    ui = chapter_ui
    assert ui.client.put('/chapter/set_' + field, data={field: 'Mystery'}).status_code == 200
    assert ui.chapter.config[field] == 'Mystery'
    assert getattr(ui.chapter, field) == 'Mystery'
    ui.chapter.save_config.assert_called_once()


@pytest.mark.parametrize('field', ['mood', 'theme'])
@pytest.mark.parametrize('error,status', [(ValueError('No text'), 422), (RuntimeError('Model unavailable'), 502)])
def test_generation_failures_are_visible(chapter_ui, field, error, status):
    def fail(chapter):
        raise error
    chapter_ui.scope['generate_chapter_' + field] = fail
    response = chapter_ui.client.post('/chapter/generate_' + field)
    assert response.status_code == status and str(error) in response.text


@pytest.mark.parametrize('kind,filename', [('image', 'chapter-1.png'), ('rainbow', 'chapter.rainbow-1.png')])
def test_text_previews_serve_correct_artifacts(chapter_ui, kind, filename):
    folder = chapter_ui.root / 'book/chapter/0001/english'
    folder.mkdir(parents=True)
    (folder / filename).write_bytes(b'preview')
    response = chapter_ui.client.get('/chapter/preview/' + kind + '.png')
    assert response.status_code == 200 and response.data == b'preview'
    assert chapter_ui.client.get('/chapter/preview/other.png').status_code == 404


@pytest.mark.parametrize('kind,index', [('image', 0), ('phrase', 0), ('silence', 0)])
def test_delete_timeline_item_preserves_other_items_and_saves(chapter_ui, kind, index):
    response = chapter_ui.client.post(f'/chapter/timeline/delete/{kind}/{index}')
    assert response.status_code == 200
    assert chapter_ui.tree.find(kind, index=str(index)) is None
    assert len(chapter_ui.tree.find_all(['phrase', 'image', 'silence'])) == 2
    chapter_ui.chapter.save_timeline.assert_called_once()


@pytest.mark.parametrize('kind,index', [('image', 99), ('unknown', 0)])
def test_delete_invalid_timeline_item_preserves_content(chapter_ui, kind, index):
    before = str(chapter_ui.tree)
    assert chapter_ui.client.post(f'/chapter/timeline/delete/{kind}/{index}').status_code == 400
    assert str(chapter_ui.tree) == before
    chapter_ui.chapter.save_timeline.assert_not_called()


def test_silence_duration_edit_updates_frames(chapter_ui):
    assert chapter_ui.client.post('/chapter/timeline/silence/0', data={'duration': '2.5'}).status_code == 200
    assert chapter_ui.tree.silence['duration'] == '2.500'
    assert chapter_ui.tree.silence['frames'] == '60'


@pytest.mark.parametrize('value', ['-1', 'nan', 'bad'])
def test_silence_rejects_invalid_duration(chapter_ui, value):
    assert chapter_ui.client.post('/chapter/timeline/silence/0', data={'duration': value}).status_code == 400
    assert chapter_ui.tree.silence['duration'] == '1'

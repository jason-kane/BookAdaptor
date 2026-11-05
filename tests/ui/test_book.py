"""Library author/book navigation, metadata and book character creation."""
import json
import pytest
from bs4 import BeautifulSoup


@pytest.mark.parametrize('state', ['queued', 'waiting_for_gpu', 'training', 'complete', 'failed'])
def test_book_lora_training_feedback_and_button(book_ui, state, monkeypatch):
    import sys
    from artifact_editor.book import lora_wizard, lora_training
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', ui.scope['const'])
    draft = lora_wizard.create(ui.book, {
        'name': '<Ink>', 'concept': 'Ink drawings', 'trigger': 'ink',
    })
    draft.update(status=state, error='Training failed')
    lora_wizard.save(ui.book, draft)
    folder = lora_wizard.directory(ui.book, draft['id'])
    (folder / 'training.json').write_text(json.dumps({'config': {
        'name': 'ink', 'process': [{'train': {'steps': 1200}}],
    }}))
    (folder / 'training.log').write_text('ink: 25% 300/1200 [01:00<03:00, 5it/s]')
    response = ui.client.get(ui.url + '/loras/status')
    assert response.status_code == 200
    soup = BeautifulSoup(response.text, 'html.parser')
    button = soup.select_one('wa-button')
    assert button.has_attr('disabled') == (state in lora_training.ACTIVE)
    assert button.has_attr('href') == (state not in lora_training.ACTIVE)
    assert soup.select_one('strong').text == '<Ink>'
    assert soup.select_one('#book-lora-training')['hx-trigger'] == 'every 5s'
    assert soup.select_one('a')['href'].endswith(f'?draft={draft["id"]}&step=4')
    if state == 'training':
        assert soup.select_one('progress')['value'] == '25'
        assert '300/1,200 steps' in soup.text
        assert 'estimated remaining 03:00' in soup.text
    if state in ('complete', 'failed'):
        assert soup.select_one('progress') is None


def test_book_lora_status_excludes_other_books(book_ui, monkeypatch):
    import sys
    from artifact_editor.book import lora_wizard
    from types import SimpleNamespace
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', ui.scope['const'])
    other = SimpleNamespace(bookdir=ui.book.bookdir + '/other')
    draft = lora_wizard.create(other, {'name': 'Other', 'concept': 'Other', 'trigger': 'other'})
    draft['status'] = 'training'
    lora_wizard.save(other, draft)
    soup = BeautifulSoup(ui.client.get(ui.url + '/loras/status').text, 'html.parser')
    assert not soup.select_one('wa-button').has_attr('disabled')
    assert 'Other' not in soup.text
    ui.book.bookdir = None
    assert ui.client.get(ui.url + '/loras/status').status_code == 404


def test_book_tabs_load_chapters_metadata_and_character_panel(book_ui):
    ui = book_ui
    response = ui.client.get(ui.url + '/')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    assert {tab['panel'] for tab in soup.select('wa-tab')} == {'meta', 'chapters', 'characters'}
    assert 'Chapter One' in response.text
    assert soup.select_one('[name="pretty_title"]')
    assert soup.select_one('[name="subtitle"]')
    assert soup.select_one('[name="default_style"]')
    assert soup.select_one('[name="aspect_ratio"]')
    assert soup.select_one('#book-characters-loader')['hx-get'] == '/library.book.characters_panel'


def test_metadata_edit_saves_style_and_other_fields(book_ui):
    ui = book_ui
    response = ui.client.put(ui.url + '/', data={'pretty_title': 'The Book', 'style': 'Ink', 'aspect_ratio': 'portrait'})
    assert response.status_code == 200
    assert ui.book.config == {'pretty_title': 'The Book', 'default_style': 'Ink', 'aspect_ratio': 'portrait'}
    ui.book.save_config.assert_called_once()


def test_book_image_generation_defaults_save_together(book_ui):
    ui = book_ui
    response = ui.client.put(ui.url + '/', data={
        'default_image_template': 't2i.qwen', 'default_loras': '["a", "b"]',
    })
    assert response.status_code == 200
    assert ui.book.config == {'default_image_template': 't2i.qwen', 'default_loras': ['a', 'b']}
    soup = BeautifulSoup(response.text, 'html.parser')
    assert soup.select_one('[name="default_image_template"]')
    assert soup.select_one('wa-select[multiple]')['value'] == 'a b'
    ui.book.save_config.assert_called_once()
    assert ui.client.put(ui.url + '/', data={'default_image_template': 'unknown'}).status_code == 400
    ui.book.save_config.assert_called_once()


def test_book_engine_choices_and_saved_selection(book_ui):
    from unittest.mock import Mock
    ui = book_ui
    ui.book.config['default_image_template'] = 't2i.qwen'
    ui.book.choice_field = Mock(return_value='')
    assert ui.client.get(ui.url + '/').status_code == 200
    args = next(call.args for call in ui.book.choice_field.call_args_list if call.args[1] == 'default_image_template')
    assert args[2] == 't2i.qwen'
    assert {'t2i.qwen', 't2i.flux', 't2i.krea2', 't2i.krea2-paragraph', 't2i.zit-paragraph'} <= dict(args[3]).keys()


def test_missing_book_is_404(book_ui):
    book_ui.book.bookdir = None
    assert book_ui.client.get(book_ui.url + '/').status_code == 404
    assert book_ui.client.put(book_ui.url + '/', data={'style': 'Ink'}).status_code == 404
    book_ui.book.save_config.assert_not_called()


def test_book_character_create_persists_global_catalog(book_ui):
    ui = book_ui
    response = ui.client.post(ui.url + '/characters', data={'name': ' Door Keeper ', 'description': ' Watches the sea. '})
    assert response.status_code == 200
    args, kw = ui.store.save_characters.call_args
    assert args[1]['door_keeper']['name'] == 'Door Keeper'
    assert args[1]['door_keeper']['description'] == 'Watches the sea.'
    assert args[1]['door_keeper']['is_global'] is True
    assert kw == {'is_global': True}


@pytest.mark.parametrize('name,status', [('', 400), ('Known', 409)])
def test_book_character_rejects_empty_and_duplicate_names(book_ui, name, status):
    book_ui.store.get_all_characters.return_value = {'known': {'name': 'Known'}}
    assert book_ui.client.post(book_ui.url + '/characters', data={'name': name}).status_code == status
    book_ui.store.save_characters.assert_not_called()


def test_add_book_creates_directory_and_empty_config(author_ui):
    response = author_ui.client.post('/library/Writer/add', data={'book_name': 'A Book; More,'})
    assert response.status_code == 200
    assert json.loads((author_ui.root / 'A Book More/config.json').read_text()) == {}


def test_book_lora_multiselect_saves_multiple_and_clears(book_ui):
    ui = book_ui
    response = ui.client.put(ui.url + '/', data={'default_loras': '["a", "b", "a"]'})
    assert response.status_code == 200
    assert ui.book.config['default_loras'] == ['a', 'b']
    soup = BeautifulSoup(response.text, 'html.parser')
    assert soup.select_one('wa-select[multiple]')
    assert len(soup.select('wa-option[selected]')) == 2
    assert ui.client.put(ui.url + '/', data={'default_loras': '[]'}).status_code == 200
    assert ui.book.config['default_loras'] == []
    assert ui.client.put(ui.url + '/', data={'default_loras': '["unknown"]'}).status_code == 400



def test_add_lora_navigates_to_creation_wizard(book_ui):
    ui = book_ui
    response = ui.client.get(ui.url + '/')
    soup = BeautifulSoup(response.text, 'html.parser')
    button = soup.find('wa-button', string='+ Add LoRA')
    assert button['href'] == ui.url + '/loras/new'
    assert not soup.select_one('#add-lora-dialog')
    response = ui.client.get(button['href'])
    assert response.status_code == 200
    assert 'Create a Z-Image Turbo LoRA' in response.text
    soup = BeautifulSoup(response.text, 'html.parser')
    assert soup.select_one('#cpu-job-queue')
    assert soup.select_one('#styler-job-queue')
    assert soup.select_one('script[data-styler-websocket]')['data-styler-websocket'] == 'ws://test/styler-ws'


def test_lora_wizard_prepares_captioned_dataset(book_ui, tmp_path, monkeypatch):
    import sys
    from io import BytesIO
    from types import SimpleNamespace
    from PIL import Image
    from artifact_editor.book import lora_wizard, lora_captioning
    monkeypatch.setattr(lora_captioning, "enqueue", lambda *args: None)
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    # Route tests use standalone blueprint endpoints rather than the production nesting.
    ui.scope['url_for'] = lambda endpoint, **kw: (ui.url + '/loras/new?draft=' + kw['draft'] + '&step=' + str(kw['step']))
    response = ui.client.post(ui.url + '/loras/new', data={
        'name': 'Ink style', 'concept': 'Bold black ink drawings', 'trigger': 'bookink'})
    assert response.status_code == 302
    location = response.headers['Location']
    assert 'step=2' in location
    response = ui.client.get(location)
    assert 'Choose book source images' in response.text
    assert not BeautifulSoup(response.text, 'html.parser').select_one('input[type="file"]')
    source = tmp_path / 'source/images'
    source.mkdir(parents=True)
    Image.new('RGB', (32, 48), 'blue').save(source / 'reference.png')
    original = (source / 'reference.png').read_bytes()
    assert ui.client.get(ui.url + '/loras/source?file=images/reference.png').status_code == 200
    response = ui.client.get(location)
    assert 'images/reference.png' in response.text
    response = ui.client.post(location, data={'source_images': 'images/reference.png'})
    assert response.status_code == 302
    location = response.headers['Location']
    assert 'step=3' in location
    assert (source / 'reference.png').read_bytes() == original
    draft_id = location.split('draft=')[1].split('&')[0]
    draft = lora_wizard.load(ui.book, draft_id)
    item = draft['images'][0]
    caption = 'bookink, a blue rectangle'
    response = ui.client.post(location, data={'caption_' + item['id']: caption})
    assert response.status_code == 302
    review = ui.client.get(response.headers['Location'])
    assert 'Review and train' in review.text
    assert caption in review.text
    assert not BeautifulSoup(review.text, 'html.parser').find('wa-button', string='Train LoRA').has_attr('disabled')
    dataset = lora_wizard.directory(ui.book, draft_id) / 'dataset'
    assert (dataset / item['filename']).with_suffix('.txt').read_text().strip() == caption
    assert ui.client.get(ui.url + '/loras/drafts/' + draft_id + '/images/' + item['id']).status_code == 200
    train_url = response.headers['Location']
    assert ui.client.post(train_url).status_code == 302
    assert lora_wizard.load(ui.book, draft_id)['status'] == 'queued'
    assert ui.client.post(train_url).status_code == 302
    assert ui.client.post(location, data={'caption_' + item['id']: 'changed'}).status_code == 409
    queued = ui.client.get(train_url)
    assert 'Queued for the local AI Toolkit worker' in queued.text
    assert (dataset.parent / 'training.json').is_file()


def test_lora_wizard_rejects_invalid_progress_and_images(book_ui, tmp_path, monkeypatch):
    import sys
    from io import BytesIO
    from types import SimpleNamespace
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    assert ui.client.get(ui.url + '/loras/new?step=4').status_code == 400
    assert ui.client.get(ui.url + '/loras/new?draft=../../escape').status_code == 404
    response = ui.client.post(ui.url + '/loras/new', data={'name': 'Missing fields'})
    assert 'Enter a name, concept, and trigger word' in response.text
    ui.scope['url_for'] = lambda endpoint, **kw: (ui.url + '/loras/new?draft=' + kw['draft'] + '&step=' + str(kw['step']))
    response = ui.client.post(ui.url + '/loras/new', data={'name': 'Test', 'concept': 'Ink', 'trigger': 'ink'})
    location = response.headers['Location']
    assert 'Select at least one' in ui.client.post(location).text
    response = ui.client.post(location, data={'source_images': '../outside.png'})
    assert 'no longer available' in response.text
    (tmp_path / 'source').mkdir()
    (tmp_path / 'source/bad.png').write_bytes(b'not an image')
    response = ui.client.post(location, data={'source_images': 'bad.png'})
    assert 'must be valid images' in response.text
    assert ui.client.get(ui.url + '/loras/source?file=../outside.png').status_code == 404


def test_automatic_caption_progress_refreshes_without_manual_input(book_ui, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from artifact_editor.book import lora_wizard
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    ui = book_ui
    draft = lora_wizard.create(ui.book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft.update(step=3, caption_state='running', images=[{'id': 'a', 'filename': 'a.png', 'original_name': 'images/a.png', 'caption': 'ink'}])
    lora_wizard.save(ui.book, draft)
    response = ui.client.get(ui.url + '/loras/new?draft=' + draft['id'] + '&step=3')
    soup = BeautifulSoup(response.text, 'html.parser')
    assert '0 of 1 captions saved' in response.text
    assert 'step=3' in soup.select_one('#lora-wizard')['hx-get']
    assert soup.select_one('wa-textarea').has_attr('disabled')


def test_image_character_multiselect_produces_bracketed_caption_names(book_ui, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from PIL import Image
    from artifact_editor.book import lora_wizard, lora_captioning
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    monkeypatch.setattr(lora_captioning, 'enqueue', lambda *args: None)
    ui.store.get_all_characters.return_value = {'tip': {'name': 'Tip'}, 'jack': {'name': 'Jack Pumpkinhead'}}
    source = tmp_path / 'source'
    source.mkdir()
    Image.new('RGB', (24, 24), 'red').save(source / 'scene.png')
    draft = lora_wizard.create(ui.book, {'name': 'Oz', 'concept': 'Oz illustrations', 'trigger': 'ozstyle'})
    url = ui.url + '/loras/new?draft=' + draft['id']
    ui.scope['url_for'] = lambda endpoint, **kw: url + '&step=' + str(kw['step'])
    response = ui.client.get(url + '&step=2')
    assert BeautifulSoup(response.text, 'html.parser').select_one('wa-select[multiple][name="characters_scene.png"]')
    response = ui.client.post(url + '&step=2', data={'source_images': 'scene.png', 'characters_scene.png': ['tip', 'jack']})
    assert response.status_code == 302
    saved = lora_wizard.load(ui.book, draft['id'])
    item = saved['images'][0]
    assert item['characters'] == ['Tip', 'Jack Pumpkinhead']
    caption = 'ozstyle, two figures in an ink illustration.'
    response = ui.client.post(url + '&step=3', data={
        'caption_' + item['id']: caption, 'characters_' + item['id']: ['tip', 'jack']})
    assert response.status_code == 302
    text = (lora_wizard.directory(ui.book, draft['id']) / 'dataset' / item['filename']).with_suffix('.txt').read_text()
    assert text.strip() == '[Tip] [Jack Pumpkinhead] ' + caption
    review = ui.client.get(url + '&step=4')
    assert '[Tip] [Jack Pumpkinhead]' in review.text
    # Updating character selection must retain the already-normalized image.
    assert ui.client.post(url + '&step=2', data={'source_images': 'scene.png', 'characters_scene.png': ['tip']}).status_code == 302
    assert (lora_wizard.directory(ui.book, draft['id']) / 'dataset' / item['filename']).is_file()


def test_remove_image_at_captions_preserves_other_edits_and_source(book_ui, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from artifact_editor.book import lora_wizard
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    draft = lora_wizard.create(ui.book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft.update(step=3, caption_state='complete', images=[
        {'id': name, 'filename': name + '.png', 'original_name': name + '.png', 'caption': 'ink, original'}
        for name in ('a', 'b')])
    lora_wizard.save(ui.book, draft)
    folder = lora_wizard.directory(ui.book, draft['id']) / 'dataset'
    folder.mkdir()
    for name in ('a', 'b'):
        (folder / (name + '.png')).write_bytes(b'normalized')
        (folder / (name + '.txt')).write_text('ink, original')
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'b.png').write_bytes(b'original source')
    url = ui.url + '/loras/new?draft=' + draft['id'] + '&step=3'
    ui.scope['url_for'] = lambda endpoint, **kw: url
    page = ui.client.get(url)
    assert BeautifulSoup(page.text, 'html.parser').select_one('button[name="remove_image"][value="b"]')
    response = ui.client.post(url, data={'remove_image': 'b', 'caption_a': 'ink, edited caption'})
    assert response.status_code == 302
    saved = lora_wizard.load(ui.book, draft['id'])
    assert [item['id'] for item in saved['images']] == ['a']
    assert saved['images'][0]['caption'] == 'ink, edited caption'
    assert saved['step'] == 3
    assert not (folder / 'b.png').exists()
    assert not (folder / 'b.txt').exists()
    assert (folder / 'a.txt').read_text().strip() == 'ink, edited caption'
    assert (source / 'b.png').read_bytes() == b'original source'
    assert 'Keep at least one image' in ui.client.post(url, data={'remove_image': 'a'}).text
    saved['status'] = 'queued'
    lora_wizard.save(ui.book, saved)
    assert ui.client.post(url, data={'remove_image': 'a'}).status_code == 409


def test_wizard_adds_book_character_using_name_only(book_ui):
    ui = book_ui
    response = ui.client.post(ui.url + '/loras/characters', data={'name': '  Jack Pumpkinhead  '})
    assert response.status_code == 201
    assert response.json == {'tag': 'jack_pumpkinhead', 'name': 'Jack Pumpkinhead'}
    args, kwargs = ui.store.save_characters.call_args
    assert args[0] is ui.book
    character = args[1]['jack_pumpkinhead']
    assert character['name'] == 'Jack Pumpkinhead'
    assert character['description'] == ''
    assert character['is_global'] is True
    assert kwargs == {'is_global': True}


def test_wizard_add_character_reuses_existing_and_validates_name(book_ui):
    ui = book_ui
    ui.store.get_all_characters.return_value = {'tip': {'name': 'Tip'}}
    response = ui.client.post(ui.url + '/loras/characters', data={'name': 'TIP'})
    assert response.status_code == 200
    assert response.json == {'tag': 'tip', 'name': 'Tip'}
    ui.store.save_characters.assert_not_called()
    assert ui.client.post(ui.url + '/loras/characters', data={'name': '  '}).status_code == 400
    assert ui.client.post(ui.url + '/loras/characters', data={'name': 'x' * 161}).status_code == 400


def test_caption_autosave_persists_each_entry_and_last_modified_target(book_ui, tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from artifact_editor.book import lora_wizard
    ui = book_ui
    monkeypatch.setitem(sys.modules, 'const', SimpleNamespace(LIBRARY_DIR=str(tmp_path)))
    ui.store.get_all_characters.return_value = {'tip': {'name': 'Tip'}}
    draft = lora_wizard.create(ui.book, {'name': 'Ink', 'concept': 'Ink', 'trigger': 'ink'})
    draft.update(step=3, caption_state='complete', images=[
        {'id': name, 'filename': name + '.png', 'original_name': name + '.png', 'caption': 'ink, original'}
        for name in ('a', 'b')])
    lora_wizard.save(ui.book, draft)
    folder = lora_wizard.directory(ui.book, draft['id']) / 'dataset'
    folder.mkdir()
    endpoint = ui.url + '/loras/drafts/' + draft['id'] + '/images/'
    assert ui.client.patch(endpoint + 'a', json={'caption': 'ink, a tree', 'character_tags': ['tip']}).status_code == 200
    assert (folder / 'a.txt').read_text().strip() == '[Tip] ink, a tree'
    assert ui.client.patch(endpoint + 'b', json={'caption': 'ink, a bird'}).status_code == 200
    saved = lora_wizard.load(ui.book, draft['id'])
    assert saved['last_modified_image_id'] == 'b'
    assert saved['images'][0]['caption'] == 'ink, a tree'
    assert saved['images'][0]['characters'] == ['Tip']
    assert saved['images'][1]['caption'] == 'ink, a bird'
    assert saved['step'] == 3
    page = ui.client.get(ui.url + '/loras/new?draft=' + draft['id'] + '&step=3')
    soup = BeautifulSoup(page.text, 'html.parser')
    assert soup.select_one('#lora-wizard')['data-last-modified-image'] == 'b'
    assert soup.select_one('#lora-image-b')['data-save-url'] == endpoint + 'b'
    assert soup.select_one('#lora-image-b [data-image-save-status]')
    assert json.loads(soup.select_one('#lora-image-a wa-select')['data-saved-characters']) == ['tip']
    assert soup.select_one('script[src*="lora-wizard.js"]')['src'].endswith('?v=wand-6')
    assert ui.client.patch(endpoint + 'b', json={'character_tags': []}).status_code == 200
    assert ui.client.patch(endpoint + 'b', json={'caption': 'x' * 4001}).status_code == 400
    assert ui.client.patch(endpoint + 'b', json={'character_tags': ['unknown']}).status_code == 400
    saved = lora_wizard.load(ui.book, draft['id'])
    saved['status'] = 'queued'
    lora_wizard.save(ui.book, saved)
    assert ui.client.patch(endpoint + 'b', json={'caption': 'too late'}).status_code == 409


def test_book_merge_uses_book_scope_and_refreshes_panel(book_ui):
    from unittest.mock import Mock
    ui = book_ui
    ui.store.merge_characters = Mock()
    response = ui.client.post(ui.url + '/characters/Jack/merge', data={'target_tag': 'Jack_Pumpkinhead'})
    assert response.status_code == 200
    ui.store.merge_characters.assert_called_once_with(
        ui.scope['Chapter'].return_value, 'Jack', 'Jack_Pumpkinhead', book_scope=True)
    assert 'Characters merged.' in response.text
    assert 'id="book-characters-panel"' in response.text
    ui.store.merge_characters.side_effect = ValueError('Choose two different existing characters.')
    response = ui.client.post(ui.url + '/characters/Jack/merge', data={'target_tag': ''})
    assert 'Choose two different existing characters.' in response.text


def test_book_merge_form_targets_book_panel(book_ui):
    from flask import render_template
    from types import SimpleNamespace
    app = book_ui.client.application
    from pathlib import Path
    from jinja2 import ChoiceLoader, FileSystemLoader
    app.jinja_env.loader = ChoiceLoader([app.jinja_env.loader, FileSystemLoader(
        str(Path(__file__).resolve().parents[2] / 'src/artifact_editor/characters/templates'))])
    with app.test_request_context('/'):
        html = render_template('character_aliases.html', book_scope=True, tag='Jack',
            character={'alias': []}, chapter=SimpleNamespace(
                author=SimpleNamespace(name='Writer'), title='Book', kwargs={}),
            catalog={'Jack': {'name': 'Jack'}, 'Jack_Pumpkinhead': {'name': 'Jack Pumpkinhead'}})
    form = BeautifulSoup(html, 'html.parser').select_one('form[hx-post]')
    assert form['hx-post'] == '/library.book.merge_book_character'
    assert form['hx-target'] == '#book-characters-panel'
    assert not form.has_attr('hx-select')
    assert form.select_one('wa-option')['value'] == 'Jack_Pumpkinhead'

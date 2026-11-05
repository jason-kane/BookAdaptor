"""Every control on Comfy Workflow Templates, with temporary workflow files."""
import pytest
from bs4 import BeautifulSoup


def test_catalog_exposes_edit_create_and_prime_controls(workflow_ui):
    ui = workflow_ui
    for name in ('ui.t2i.ink-Ink.json', 'ui.i2v.motion-Motion.json',
                 'api.t2i.hidden.json', 'prime.json', 'invalid.json'):
        (ui.templates / name).write_text('{}')
    page = ui.client.get('/workflow_templates/')
    assert page.status_code == 200
    soup = BeautifulSoup(page.data, 'html.parser')
    assert [n.get_text(strip=True) for n in soup.select('legend')] == ['i2v', 't2i']
    assert soup.select_one('[href="/workflow_templates/edit/t2i/ink"]').text == 'Ink'
    assert soup.select_one('[href="/workflow_templates/edit/i2v/motion"]').text == 'Motion'
    assert 'hidden' not in page.text
    for mode in ('t2i', 'i2v'):
        field = soup.select_one(f'[name="new_{mode}"]')
        form = field.find_parent('form')
        assert form.select_one('[hx-post="/workflow_templates/new"]')
        assert form.select_one(f'[href="/workflow_templates/prime_edit?mode={mode}"]')


@pytest.mark.parametrize('mode_prime', [True, False])
def test_create_copies_selected_prime_and_redirects_to_catalog(workflow_ui, mode_prime):
    ui = workflow_ui
    (ui.templates / 'prime.json').write_text('generic graph')
    if mode_prime:
        (ui.templates / 'prime.t2i.json').write_text('image graph')
    response = ui.client.post('/workflow_templates/new', data={'new_t2i': 'Ink - Wash'})
    assert response.status_code == 302
    assert response.headers['Location'] == '/workflow_templates/'
    assert (ui.templates / 'ui.t2i.ink___wash-Ink - Wash.json').read_text() == ('image graph' if mode_prime else 'generic graph')


@pytest.mark.parametrize('form', [{}, {'new_t2i': ''}, {'name': 'Ink'}])
def test_create_rejects_missing_mode_or_name(workflow_ui, form):
    assert workflow_ui.client.post('/workflow_templates/new', data=form).status_code == 400
    assert list(workflow_ui.templates.iterdir()) == []


@pytest.mark.parametrize('saved_edit', [True, False])
def test_edit_loads_template_using_sample_chapter_metadata(workflow_ui, saved_edit):
    ui = workflow_ui
    template = ui.templates / 'ui.t2i.ink-Ink.json'
    template.write_text('original')
    if saved_edit:
        (ui.workflows / 'edit.t2i.ink.json').write_text('saved changes')
    response = ui.client.get('/workflow_templates/edit/t2i/ink')
    assert response.status_code == 302
    assert response.headers['Location'] == 'http://comfy.test/?workflow=edit.t2i.ink.json'
    assert template.read_text() == ('saved changes' if saved_edit else 'original')
    call = ui.chapter.get_workflow_by_filename.call_args.kwargs
    assert call['workflow_filename'] == str(template)
    assert call['force_name'] == 'edit.t2i.ink.json'
    assert call['image_xml'] is ui.chapter.get_image.return_value
    assert call['template_environment'] == {}


@pytest.mark.parametrize('saved_prime', [True, False])
def test_edit_prime_reuses_saved_graph_and_opens_comfy(workflow_ui, saved_prime):
    ui = workflow_ui
    prime = ui.templates / 'prime.t2i.json'
    prime.write_text('original')
    if saved_prime:
        (ui.workflows / prime.name).write_text('saved prime')
    response = ui.client.get('/workflow_templates/prime_edit?mode=t2i')
    assert response.status_code == 302
    assert response.headers['Location'] == 'http://comfy.test/?workflow=prime.t2i.json'
    assert prime.read_text() == ('saved prime' if saved_prime else 'original')
    assert ui.chapter.get_workflow_by_filename.call_args.kwargs['workflow_filename'] == str(prime)


def test_prime_requires_mode(workflow_ui):
    assert workflow_ui.client.get('/workflow_templates/prime_edit').status_code == 400
    workflow_ui.chapter.get_image.assert_not_called()


@pytest.mark.parametrize('url', ['/workflow_templates/prime_edit?mode=t2i', '/workflow_templates/edit/t2i/ink'])
def test_edit_reports_missing_sample_image(workflow_ui, url):
    (workflow_ui.templates / 'ui.t2i.ink-Ink.json').write_text('{}')
    workflow_ui.chapter.get_image.return_value = None
    assert workflow_ui.client.get(url).status_code == 400
    workflow_ui.chapter.get_workflow_by_filename.assert_not_called()

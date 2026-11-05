"""Reachable Styler picker and editing controls use an isolated style store."""
import pytest
from bs4 import BeautifulSoup


def test_styler_page_exposes_prompt_picker_search_and_queue(styler_ui):
    ui = styler_ui
    response = ui.client.get('/styles/?base_style_prompt=kid_friendly')
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    assert soup.select_one('select[name="base_style_prompt"] option[selected]')['value'] == 'kid_friendly'
    assert soup.select_one('#styler-job-queue')
    assert soup.select_one('[data-styler-search]')
    ui.backfill.stop.assert_called_once_with(ui.store.r)
    ui.jobs.clear_queued_samples.assert_called_once_with(ui.store.r)


def test_topics_and_subtopics_load_cached_tree(styler_ui):
    ui = styler_ui
    assert ui.client.get('/styles/topics').json['topics'] == [{'name': 'Art', 'description': 'Visual art'}]
    assert ui.client.get('/styles/topics/Art/subtopics').json['topics'] == [{'name': 'Drawing', 'description': 'Drawn marks'}]
    assert ui.client.get('/styles/topics/Art/tags').json['tags'] == ['ink', 'pen']
    assert ui.client.get('/styles/topics/Art/subtopics/Drawing/tags').json['tags'] == ['ink', 'pen']


def test_category_tabs_wire_selection_to_choose_tag(styler_ui):
    response = styler_ui.client.get('/styles/topics/Art/subtopics/Drawing/tags?base_style_prompt=kid_friendly', headers={'HX-Request': 'true'})
    assert response.status_code == 200
    soup = BeautifulSoup(response.data, 'html.parser')
    assert [n['data-style-category'] for n in soup.select('wa-tab')] == ['ink', 'pen']
    assert all(n['hx-put'] == '/styles/choose_tag' for n in soup.select('wa-tab'))
    assert soup.select_one('[data-base-style-prompt]')['data-base-style-prompt'] == 'kid_friendly'


@pytest.mark.parametrize('path', ['/topics/missing/tags', '/topics/missing/subtopics', '/topics/Art/subtopics/missing/tags'])
def test_unknown_topic_or_category_is_404(styler_ui, path):
    assert styler_ui.client.get('/styles' + path).status_code == 404


def test_topic_tree_pending_state_is_visible(styler_ui):
    styler_ui.taxonomy.cached_tree.return_value = None
    styler_ui.store.r.get.return_value = 'pending'
    assert styler_ui.client.get('/styles/topics').json == {'topics': [], 'ready': False, 'building': True, 'tag_count': 0}


@pytest.mark.parametrize('state,status', [('queued', 202), ('already_queued', 202), ('unavailable', 503)])
def test_rebuild_button_reports_queue_result(styler_ui, state, status):
    styler_ui.topic_jobs.enqueue_taxonomy_rebuild.return_value = {'state': state}
    assert styler_ui.client.post('/styles/topics/rebuild').status_code == status
    styler_ui.topic_jobs.enqueue_taxonomy_rebuild.assert_called_once_with(styler_ui.store.r, reason='manual')


def test_rebuild_error_is_displayable(styler_ui):
    styler_ui.topic_jobs.enqueue_taxonomy_rebuild.side_effect = RuntimeError('offline')
    response = styler_ui.client.post('/styles/topics/rebuild')
    assert response.status_code == 503 and response.json['error']


def test_search_short_query_avoids_loading_full_index(styler_ui):
    styler_ui.scope['_styler_style_search_index'] = lambda: pytest.fail('unnecessary full index')
    assert styler_ui.client.get('/styles/search_suggestions?q=a').json == {'results': []}


def test_search_matches_styles_and_taxonomy_ranked_by_name(styler_ui):
    styler_ui.scope['_styler_style_search_index'] = lambda: [{'kind': 'style', 'name': 'Ink wash', 'style_tag': 'ink_wash'}]
    results = styler_ui.client.get('/styles/search_suggestions?q=ink').json['results']
    assert [r['name'] for r in results] == ['ink', 'Ink wash']
    assert results[0]['kind'] == 'tag'


def test_target_card_and_navigation_preserve_selected_prompt(styler_ui):
    ui = styler_ui
    ui.scope['style_category_tree'] = lambda _: ({}, [], [{'parent': 'Art', 'category': 'Drawing', 'substyle': 'ink'}])
    response = ui.client.get('/styles/find_target?style=ink&base_style_prompt=kid_friendly')
    assert response.status_code == 302
    assert 'base_style_prompt=kid_friendly' in response.headers['Location']
    assert response.headers['Location'].endswith('#Art,Drawing,ink,ink')
    response = ui.client.get('/styles/target_card?style=ink&tag=pen')
    assert response.status_code == 200 and 'Ink card' in response.text
    ui.style.as_card.assert_called_once_with(tag='pen', oob=False)


@pytest.mark.parametrize('url', ['/styles/find_target', '/styles/target_card?style=ink'])
def test_target_requires_parameters(styler_ui, url):
    assert styler_ui.client.get(url).status_code == 400


def test_manual_tag_add_and_remove_persist_membership(styler_ui):
    ui = styler_ui
    ui.store.r.scard.return_value = 3
    assert ui.client.post('/styles/add_tag', data={'style_tag': 'ink', 'tag': ' Drawing '}).json == {'tag': 'Drawing', 'count': 3}
    ui.store.add_tag_to_style.assert_called_once_with(None, 'ink', 'Drawing')
    ui.store.r.eval.return_value = 2
    assert ui.client.delete('/styles/tag?style_tag=ink&tag=Drawing').json == {'tag': 'Drawing', 'count': 2}
    ui.style.remove_tag.assert_called_once_with('Drawing')


@pytest.mark.parametrize('data', [{}, {'style_tag': 'ink'}, {'style_tag': 'ink', 'tag': 'x'*201}])
def test_tag_add_rejects_invalid_input(styler_ui, data):
    assert styler_ui.client.post('/styles/add_tag', data=data).status_code == 400
    styler_ui.store.add_tag_to_style.assert_not_called()


def test_rename_saves_name(styler_ui):
    response = styler_ui.client.put('/styles/style/name', data={'style_tag': 'ink', 'name': ' New name '})
    assert response.status_code == 200
    assert styler_ui.style.style['name'] == 'New name'
    styler_ui.style.save_to_redis.assert_called_once()


@pytest.mark.parametrize('data', [{}, {'style_tag': 'ink', 'name': 'x'*161}])
def test_rename_rejects_missing_or_long_name(styler_ui, data):
    assert styler_ui.client.put('/styles/style/name', data=data).status_code == 400
    styler_ui.style.save_to_redis.assert_not_called()


@pytest.mark.parametrize('engine', ['zit', 'krea2', 'qwen'])
def test_tier_slider_saves_for_selected_engine(styler_ui, engine):
    response = styler_ui.client.post('/styles/ink/tier?engine=' + engine, data={'tier': '7'})
    assert response.status_code == 200
    styler_ui.style.set_engine_tier.assert_called_once_with(engine, 7)


def test_delete_style_returns_updated_lost_count(styler_ui):
    ui = styler_ui
    ui.store.r.scard.return_value = 4
    response = ui.client.delete('/styles/style?style_tag=Ink')
    assert response.json == {'state': 'style_deleted', 'style_tag': 'ink', 'lost_count': 4}
    ui.store.r.eval.assert_called_once()


@pytest.mark.parametrize('tag', ['', 'LOST', 'abyss'])
def test_tag_culling_rejects_missing_or_reserved_tags(styler_ui, tag):
    assert styler_ui.client.delete('/styles/tag/all', query_string={'tag': tag}).status_code == (400 if not tag else 409)

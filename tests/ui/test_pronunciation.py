"""Pronunciation remains reachable from the chapter column tabs."""
import pytest


def test_add_word_saves_trimmed_pronunciation(pronunciation_ui):
    ui = pronunciation_ui
    response = ui.client.post('/pronunciation/actions/add_global_pronunciation', data={'add_word': ' Sea ', 'add_pron': ' siː '})
    assert response.status_code == 200
    assert ui.dictionary['sea'] == {'word': 'Sea', 'pronunciation': 'siː', 'after': ''}
    ui.store.save_global_pronunciations.assert_called_once()


def test_empty_word_does_not_save(pronunciation_ui):
    assert pronunciation_ui.client.post('/pronunciation/actions/add_global_pronunciation', data={'add_word': ' '}).status_code == 200
    pronunciation_ui.store.save_global_pronunciations.assert_not_called()


def test_edit_word_returns_editor(pronunciation_ui):
    assert pronunciation_ui.client.get('/pronunciation/edit?key=door').status_code == 200
    assert pronunciation_ui.scope['htmx'].edit_row.call_args.kwargs == {'key': 'door'}


@pytest.mark.parametrize('changed', [True, False])
def test_saving_pronunciation_invalidates_audio_only_when_changed(pronunciation_ui, changed):
    ui = pronunciation_ui
    audio = ui.root / 'Writer/Book/pronunciation/door.wav'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'audio')
    value = 'dɔɹ' if changed else 'dɔːr'
    response = ui.client.post('/pronunciation/set_pronunciation', data={'key': 'door', 'pronunciation': value})
    assert response.status_code == 200
    assert ui.dictionary['door']['pronunciation'] == value
    assert audio.exists() is not changed
    ui.store.save_global_pronunciations.assert_called_once()


def test_delete_word_updates_shared_dictionary(pronunciation_ui):
    assert pronunciation_ui.client.delete('/pronunciation/door').status_code == 200
    assert pronunciation_ui.dictionary == {}
    pronunciation_ui.store.save_global_pronunciations.assert_called_once()


def test_scan_for_unknown_words_refreshes_table(pronunciation_ui):
    response = pronunciation_ui.client.post('/pronunciation/actions/find_unpronouncable_words')
    assert response.status_code == 200 and '<table>' in response.text
    pronunciation_ui.scope['phrases'].find_unpronouncable_words.assert_called_once()

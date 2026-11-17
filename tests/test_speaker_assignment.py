"""Attribution is invariant to TTS splitting and model batch boundaries."""
import json
import re
from types import SimpleNamespace
from unittest.mock import Mock

from bs4 import BeautifulSoup
import pytest

from artifact_editor.characters.speaker_assignment import assign_speakers, dialogue_turns


CATALOG = {'Bob': {'name': 'Bob'}, 'Alice': {'name': 'Alice'},
           'Jack_Pumpkinhead': {'name': 'Jack Pumpkinhead', 'alias': ['Jack']},
           'Scarecrow': {'name': 'Scarecrow'}, 'Tip': {'name': 'Tip'},
           'Saw_Horse': {'name': 'Saw-Horse'}}


def normalize(name):
    return re.sub(r'[^\w]+', '_', name).strip('_')


def chapter_for(paragraphs):
    soup = BeautifulSoup('<chapter/>', 'xml')
    index = 0
    for number, texts in enumerate(paragraphs):
        paragraph = soup.new_tag('paragraph', index=str(number))
        soup.chapter.append(paragraph)
        for text in texts:
            phrase = soup.new_tag('phrase', index=str(index), speaker='Narrator')
            phrase.string = text
            paragraph.append(phrase)
            index += 1
    return SimpleNamespace(get_xml=lambda: soup, save_phrases=Mock())


def run(chapter, tmp_path, response=None, **kwargs):
    request = Mock(side_effect=response) if callable(response) else Mock(return_value=response or [])
    changed = assign_speakers(chapter, CATALOG, request,
        lambda p: str(tmp_path / f"{p['index']}.wav"), normalize, **kwargs)
    return changed, request


@pytest.mark.parametrize('texts,expected', [
    (['"Hey"', 'said Bob,', '"why so sad?"'], ['Bob', 'Narrator', 'Bob']),
    (['“Hey,”', 'Bob said,', '“why so sad?”'], ['Bob', 'Narrator', 'Bob']),
    (['“Hey,”', 'Bob quietly asked Alice,', '“why so sad?”'], ['Bob', 'Narrator', 'Bob']),
    (['“Hey,”', 'Bob said to Alice,', '“why so sad?”'], ['Bob', 'Narrator', 'Bob']),
    (['“Hey,”', 'said Bob, voice full of emotion,', '“why so sad?”'], ['Bob', 'Narrator', 'Bob']),
    (['Bob said,', '“Hey,', 'why so', 'sad?”'], ['Narrator', 'Bob', 'Bob', 'Bob']),
    (['“Bob, why so sad?”', 'said Alice.'], ['Alice', 'Narrator']),
    (['“Hello,”', 'said the Scarecrow.'], ['Scarecrow', 'Narrator']),
    (['“Hello,”', 'Jack replied.'], ['Jack_Pumpkinhead', 'Narrator']),
    (['“Hi,”', 'said Bob.', '“Bye,”', 'said Alice.'], ['Bob', 'Narrator', 'Alice', 'Narrator']),
    (['“Hi,”', 'Bob said, and Alice replied,', '“Bye.”'], ['Bob', 'Narrator', 'Alice']),
])
def test_explicit_tags_assign_complete_turns_without_model(tmp_path, texts, expected):
    chapter = chapter_for([texts])
    _, request = run(chapter, tmp_path, batch_size=1)
    assert [p['speaker'] for p in chapter.get_xml().find_all('phrase')] == expected
    request.assert_not_called()


def test_pronoun_interruption_is_one_model_target_even_with_batch_size_one(tmp_path):
    chapter = chapter_for([['“Hey,”', 'he asked,', '“why so sad?”']])
    targets_seen = []

    def review(prompt, **kwargs):
        targets = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])
        targets_seen.append(targets)
        return [{'index': t['index'], 'speaker': 'Bob', 'evidence': 'he asked,'} for t in targets]

    changed, request = run(chapter, tmp_path, response=review, batch_size=1)
    phrases = chapter.get_xml().find_all('phrase')
    assert changed == 2
    assert [p['speaker'] for p in phrases] == ['Bob', 'Narrator', 'Bob']
    assert phrases[0]['speaker_turn'] == phrases[2]['speaker_turn']
    assert request.call_count == 2
    assert all(len(targets) == 1 and targets[0]['phrase_indices'] == ['0', '2'] for targets in targets_seen)


def test_review_can_correct_proposed_speaker_for_whole_turn(tmp_path):
    chapter = chapter_for([['“Hey,”', 'he asked,', '“why so sad?”']])
    def review(prompt, **kwargs):
        target = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])[0]
        speaker = 'Bob' if 'Verify speakers' in kwargs['label'] else 'Alice'
        return [dict(index=target['index'], speaker=speaker, evidence='he asked,')]
    run(chapter, tmp_path, response=review)
    assert [p['speaker'] for p in chapter.get_xml().find_all('phrase')] == ['Bob', 'Narrator', 'Bob']


@pytest.mark.parametrize('speaker', ['Narrator', 'Unknown', 'NotInCatalog'])
def test_uncertain_dialogue_is_not_marked_as_verified_narration(tmp_path, speaker):
    chapter = chapter_for([['“Hello.”']])
    def review(prompt, **kwargs):
        target = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])[0]
        return [dict(index=target['index'], speaker=speaker, evidence='“Hello.”')]
    run(chapter, tmp_path, response=review)
    assert chapter.get_xml().phrase['speaker_source'] == 'llm_unresolved'


def test_evidence_accepts_equivalent_straight_and_curly_quotes(tmp_path):
    chapter = chapter_for([['“Is this right?”']])

    def review(prompt, **kwargs):
        target = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])[0]
        return [{'index': target['index'], 'speaker': 'Bob',
                 'evidence': '"Is this right?"'}]

    run(chapter, tmp_path, response=review)
    assert chapter.get_xml().phrase['speaker'] == 'Bob'
    assert chapter.get_xml().phrase['speaker_source'] == 'llm'


def test_context_skips_empty_paragraphs_and_includes_saved_neighbor_speakers(tmp_path):
    chapter = chapter_for([
        ['An aged Winkie came forward.'],
        [],
        ['Scarecrow said,', '“Hello.”'],
        [],
        ['“What is the answer?”', 'asked the horse.'],
        [],
        ['“Yes, indeed.”'],
        [],
        ['Tip said,', '“What is next?”'],
        [],
        ['Scarecrow exclaimed,', '“Quite right.”'],
    ])
    phrases = chapter.get_xml().find_all('phrase')
    phrases[3]['speaker'], phrases[3]['speaker_source'] = 'Saw_Horse', 'llm'
    seen = []

    def review(prompt, **kwargs):
        context = prompt.split('BEGIN SOURCE CONTEXT\n', 1)[1].split('\nEND SOURCE CONTEXT', 1)[0]
        established = json.loads(prompt.split('Established turns: ', 1)[1].split('\n', 1)[0])
        seen.append((context, established))
        target = json.loads(prompt.split('Target phrases: ', 1)[1].split('\n', 1)[0])[0]
        return [{'index': target['index'], 'speaker': 'Scarecrow',
                 'evidence': '“Yes, indeed.”'}]

    run(chapter, tmp_path, response=review, target_phrase_index='5')
    assert all('An aged Winkie came forward.' in context
               and 'asked the horse.' in context and 'Tip said,' in context
               and 'Scarecrow exclaimed,' in context for context, _ in seen)
    assert all({turn['speaker'] for turn in established} >= {'Scarecrow', 'Saw_Horse', 'Tip'}
               for _, established in seen)
    assert phrases[5]['speaker'] == 'Scarecrow'


def test_preserved_manual_speaker_anchors_other_fragments(tmp_path):
    chapter = chapter_for([['“Hey,”', 'he asked,', '“why so sad?”']])
    phrase = chapter.get_xml().phrase
    phrase['speaker'], phrase['speaker_source'] = 'Bob', 'manual'
    _, request = run(chapter, tmp_path)
    phrases = chapter.get_xml().find_all('phrase')
    assert [p['speaker'] for p in phrases] == ['Bob', 'Narrator', 'Bob']
    assert phrases[0]['speaker_source'] == 'manual'
    request.assert_not_called()


def test_explicit_reassignment_invalidates_old_audio_and_sidecars(tmp_path):
    chapter = chapter_for([['“Hello,”', 'Bob replied.']])
    phrase = chapter.get_xml().phrase
    phrase['speaker'], phrase['speaker_source'] = 'Alice', 'manual'
    stale = [tmp_path / name for name in ('0.wav', '0.wav.pronunciation', '0.wav.pronunciation.txt', '0.pronunciation.txt')]
    for filename in stale:
        filename.write_text('old voice')
    changed, _ = run(chapter, tmp_path, reassign_all=True)
    assert changed == 1 and phrase['speaker'] == 'Bob'
    assert not any(filename.exists() for filename in stale)


@pytest.mark.parametrize('opening,closing', [('“', '”'), ('"', '"')])
def test_multi_paragraph_speech_keeps_one_turn(tmp_path, opening, closing):
    chapter = chapter_for([['Bob said,', opening + 'First paragraph.'], [opening + 'Second paragraph.' + closing]])
    _, request = run(chapter, tmp_path)
    assert [p['speaker'] for p in chapter.get_xml().find_all('phrase')] == ['Narrator', 'Bob', 'Bob']
    request.assert_not_called()


def test_mixed_legacy_phrase_requires_review_without_changing_voice(tmp_path):
    chapter = chapter_for([['“Hey,” said Bob, “why so sad?”']])
    _, request = run(chapter, tmp_path)
    assert chapter.get_xml().phrase['speaker_source'] == 'llm_unresolved'
    assert chapter.get_xml().phrase['speaker'] == 'Narrator'
    request.assert_not_called()


def test_action_containing_name_is_not_an_explicit_speech_tag(tmp_path):
    chapter = chapter_for([['Alice heard Bob say,', '“Hello.”']])
    _, request = run(chapter, tmp_path)
    assert chapter.get_xml().find_all('phrase')[1]['speaker_source'] == 'llm_unresolved'
    assert request.call_count == 2


def test_named_subject_carries_through_coordinated_action_to_speech_tag(tmp_path):
    chapter = chapter_for([
        ['The Scarecrow became greatly animated at this sight, and exclaimed:'],
        ['“How delighted I shall be to see my old friend the Tin Woodman again! ',
         'I hope that he rules his people more successfully than I have ruled mine!”'],
    ])
    _, request = run(chapter, tmp_path)
    phrases = chapter.get_xml().find_all('phrase')
    assert [phrase['speaker'] for phrase in phrases] == [
        'Narrator', 'Scarecrow', 'Scarecrow']
    assert [phrase.get('speaker_source') for phrase in phrases[1:]] == [
        'quote_rule', 'quote_rule']
    request.assert_not_called()


def test_targeted_reattribution_changes_only_the_selected_phrase(tmp_path):
    chapter = chapter_for([
        ['Bob said,', '“Hello,', 'there.”'],
        ['Alice said,', '“Goodbye.”'],
    ])
    phrases = chapter.get_xml().find_all('phrase')
    _, request = run(chapter, tmp_path, target_phrase_index='1')
    assert [phrase['speaker'] for phrase in phrases] == [
        'Narrator', 'Bob', 'Narrator', 'Narrator', 'Narrator']
    assert phrases[1]['speaker_source'] == 'quote_rule'
    assert phrases[2].get('speaker_source') is None
    saved_phrases = chapter.save_phrases.call_args.args[0]
    assert [phrase.get('index') for phrase in saved_phrases] == ['1']
    request.assert_not_called()


def test_different_quotes_in_one_paragraph_remain_separate():
    chapter = chapter_for([['“Hi.”', 'Bob waved.', '“Bye.”']])
    _, turns = dialogue_turns(chapter.get_xml(), CATALOG, normalize)
    assert len([turn for turn in turns if turn.kind == 'quote']) == 2


def test_invalid_model_response_still_raises(tmp_path):
    chapter = chapter_for([['“Hello.”']])
    with pytest.raises(ValueError, match='invalid list'):
        run(chapter, tmp_path, response={'bad': 'response'})


def test_incomplete_quote_remains_contextual_work():
    chapter = chapter_for([['Bob said,', '“An unfinished quotation']])
    _, turns = dialogue_turns(chapter.get_xml(), CATALOG, normalize)
    quote = next(turn for turn in turns if turn.kind == 'quote')
    assert quote.speaker is None


def test_preserved_manual_only_turn_does_not_call_model(tmp_path):
    chapter = chapter_for([['“Hello.”']])
    phrase = chapter.get_xml().phrase
    phrase['speaker'], phrase['speaker_source'] = 'Bob', 'manual'
    changed, request = run(chapter, tmp_path)
    assert changed == 0 and phrase['speaker_source'] == 'manual'
    request.assert_not_called()


def test_conflicting_manual_fragments_are_preserved_and_remaining_phrase_unresolved(tmp_path):
    chapter = chapter_for([['“Hey,', 'you there,', 'why so sad?”']])
    phrases = chapter.get_xml().find_all('phrase')
    for phrase, speaker in [(phrases[0], 'Bob'), (phrases[2], 'Alice')]:
        phrase['speaker'], phrase['speaker_source'] = speaker, 'manual'
    _, request = run(chapter, tmp_path)
    assert [p['speaker'] for p in phrases] == ['Bob', 'Narrator', 'Alice']
    assert phrases[1]['speaker_source'] == 'llm_unresolved'
    request.assert_not_called()


def test_ambiguous_and_string_aliases():
    records = {'Bob': {'name': 'Robert', 'alias': 'Bob'},
               'OtherBob': {'name': 'Bobby', 'alias': ['Bob']}, 'Narrator': {'name': 'Narrator'}}
    chapter = chapter_for([['“Hello,”', 'Bob said.']])
    _, turns = dialogue_turns(chapter.get_xml(), records, normalize)
    assert next(t for t in turns if t.kind == 'quote').speaker is None
    records.pop('OtherBob')
    _, turns = dialogue_turns(chapter.get_xml(), records, normalize)
    assert next(t for t in turns if t.kind == 'quote').speaker == 'Bob'


def test_spoken_mention_without_reporting_clause_is_not_a_rule():
    chapter = chapter_for([['Bob said nothing,', '“Hello.”']])
    _, turns = dialogue_turns(chapter.get_xml(), CATALOG, normalize)
    assert next(t for t in turns if t.kind == 'quote').speaker is None


@pytest.mark.parametrize('response_kind', ['not-object', 'missing-field', 'bad-evidence', 'wrong-id', 'duplicate'])
def test_invalid_model_entries_leave_dialogue_unresolved(tmp_path, response_kind):
    chapter = chapter_for([['“Hello.”']])
    def review(prompt, **kwargs):
        target = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])[0]
        record = dict(index=target['index'], speaker='Bob', evidence='“Hello.”')
        if response_kind == 'not-object':
            return [42]
        if response_kind == 'missing-field':
            del record['speaker']
        elif response_kind == 'bad-evidence':
            record['evidence'] = 'Invented evidence'
        elif response_kind == 'wrong-id':
            record['index'] = 'not-a-target'
        elif response_kind == 'duplicate':
            return [record, record]
        return [record]
    run(chapter, tmp_path, response=review)
    assert chapter.get_xml().phrase['speaker_source'] == 'llm_unresolved'


def test_saved_scarecrow_interruption_keeps_the_whole_quote_together(tmp_path):
    chapter = chapter_for([['“There is this difference between us,”', 'said the Scarecrow,',
                           '“that whereas I will bend,', 'but not break,', 'you will break, but not bend.”']])
    run(chapter, tmp_path)
    assert [p['speaker'] for p in chapter.get_xml().find_all('phrase')] == [
        'Scarecrow', 'Narrator', 'Scarecrow', 'Scarecrow', 'Scarecrow']


@pytest.mark.parametrize('answer,expected', [('Tin_Woodman', 'Tin_Woodman'),
                                           ('Tin Woodman', 'Narrator')])
def test_exact_catalog_identity_survives_duplicate_display_names(tmp_path, answer, expected):
    chapter = chapter_for([['“We have lost our way.”']])
    catalog = {'Tin_Woodman': {'name': 'Tin Woodman'},
               'Tin_Woodsman': {'name': 'Tin Woodman'}}

    def request(prompt, **kwargs):
        targets = json.loads(prompt.rsplit('Target phrases: ', 1)[1].split('\n')[0])
        return [{'index': targets[0]['index'], 'speaker': answer,
                 'evidence': '“We have lost our way.”'}]

    assign_speakers(chapter, catalog, request, lambda p: str(tmp_path / 'unused.wav'), normalize)
    assert chapter.get_xml().phrase['speaker'] == expected


@pytest.mark.parametrize('introduction', [
    'The Scarecrow presently stopped and said:',
    'That was why the Scarecrow presently stopped and said:',
])
def test_shared_subject_without_comma_resolves_without_model(tmp_path, introduction):
    chapter = chapter_for([[introduction], ['“We have lost our way.”']])
    _, request = run(chapter, tmp_path)
    assert chapter.get_xml().find_all('phrase')[1]['speaker'] == 'Scarecrow'
    request.assert_not_called()


def test_reply_review_receives_ordered_conversation_without_reusing_target_guess(tmp_path):
    chapter = chapter_for([
        ['Bob said:', '“We have lost our way.”'],
        ['“Why do you think that?”', 'asked Alice.'],
        ['“I have never seen this field before.”'],
    ])
    target = chapter.get_xml().find_all('phrase')[-1]
    target['speaker'], target['speaker_source'] = 'Alice', 'llm'
    seen = []

    def request(prompt, **kwargs):
        sequence = json.loads(prompt.split(
            'Conversation turns in source order (previous assignments may be wrong): ', 1
        )[1].split('\n')[0])
        seen.append(sequence)
        targets = json.loads(prompt.split('Target phrases: ', 1)[1].split('\n')[0])
        reply = targets[0]['reply_context']
        assert reply['questioner'] == 'Alice'
        assert reply['candidate_respondent'] == 'Bob'
        assert reply['preceding_claim'] == '“We have lost our way.”'
        return [{'index': sequence[-1]['index'], 'speaker': 'Bob',
                 'evidence': '“I have never seen this field before.”'}]

    run(chapter, tmp_path, response=request, target_phrase_index=target['index'])
    assert len(seen) == 2
    for sequence in seen:
        assert [entry['speaker'] for entry in sequence] == ['Bob', 'Alice', None]
        assert [entry['source'] for entry in sequence] == ['quote_rule', 'quote_rule', 'target']
    assert target['speaker'] == 'Bob'


@pytest.mark.parametrize('evidence,expected', [
    (['Bob looked over the fields.', 'he asked, wonderingly.'], 'Bob'),
    (['Bob looked over the fields.', 'he shouted angrily.'], 'Narrator'),
    (['he asked, wonderingly.', 'Bob looked over the fields.'], 'Narrator'),
    ('Bob looked over the fields. he asked, wonderingly.', 'Narrator'),
    ([], 'Narrator'),
])
def test_pronoun_evidence_excerpts_must_each_occur_in_source_order(tmp_path, evidence, expected):
    chapter = chapter_for([
        ['Bob looked over the fields.'],
        ['“What is approaching us?”', 'he asked, wonderingly.'],
    ])

    def request(prompt, **kwargs):
        target = json.loads(prompt.split('Target phrases: ', 1)[1].split('\n')[0])[0]
        return [{'index': target['index'], 'speaker': 'Bob', 'evidence': evidence}]

    run(chapter, tmp_path, response=request, target_phrase_index='1')
    assert chapter.get_xml().find_all('phrase')[1]['speaker'] == expected

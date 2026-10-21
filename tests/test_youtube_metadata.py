import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('youtube_metadata',
    Path(__file__).resolve().parents[1] / 'src/artifact_editor/publish/youtube/metadata.py')
metadata = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metadata)


def description(text, config=None):
    return metadata.description_default('Selected Poems', 'Robert Frost', 'The Pasture',
                                        1, text, config or {})


def test_short_poem_preserves_stanzas_and_adds_notes_and_hashtags():
    poem = "I'm going out to clean the pasture spring;\nAnother line.\n\nA second stanza."
    result = description(poem, {'description': 'A test run.'})
    assert result == ('From "Selected Poems" by Robert Frost. A test run.\n\n'
                      'THE PASTURE\n\n' + poem + '\n\n' + metadata.DESCRIPTION_HASHTAGS)


def test_existing_heading_is_not_repeated():
    result = description('THE PASTURE\n\nA poem.')
    assert result.count('THE PASTURE') == 1


def test_short_threshold_and_long_description_fallback():
    assert metadata.DESCRIPTION_HASHTAGS in description('x' * 1023)
    assert metadata.DESCRIPTION_HASHTAGS not in description('x' * 1024)
    assert description('x' * 1024, {'description': 'Existing description'}) == 'Existing description'
    assert 'THE PASTURE' not in description('')


def test_title_example():
    result = metadata.title_defaults('The Pasture', 'Robert Frost',
                                    {'genre': 'poetry', 'content_format': 'poem'})
    assert result['upload_title'] == 'The Pasture by Robert Frost #booktok #poetry #frost #poem'


def test_upload_tags_follow_content_format():
    for content_format in ('', 'novel', 'poem'):
        result = metadata.title_defaults('Book', 'An Author', {'content_format': content_format})
        expected = 'book,literature,story,audiobook' + (',poem' if content_format == 'poem' else '')
        assert result['upload_tags'] == expected


def test_title_includes_chapter_and_book():
    result = metadata.title_defaults('Selected Poems', 'Robert Frost',
                                    {'genre': 'poetry', 'content_format': 'poem'}, 'The Pasture')
    assert result['upload_title'] == 'The Pasture — Selected Poems by Robert Frost #booktok #poetry #frost #poem'
    assert result['title_title'] == 'The Pasture — Selected Poems'


def test_title_does_not_repeat_identical_book_and_chapter():
    result = metadata.title_defaults('The Pasture', 'Robert Frost', {}, 'THE PASTURE')
    assert result['title_title'] == 'The Pasture'

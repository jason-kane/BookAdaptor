"""Defaults for YouTube titles and descriptions derived from book metadata."""
DESCRIPTION_HASHTAGS = '#booktube #books #literature #audiobook #storytelling #story #storytime'
CATEGORIES = ('poetry', 'fiction', 'nonfiction', 'fantasy', 'sciencefiction',
              'romance', 'mystery', 'thriller', 'horror', 'history', 'biography', 'children')
FORMATS = ('poem', 'novel', 'shortstory', 'essay', 'audiobook')


def description_default(book_title, author, chapter_title, chapter_number, text, config):
    text = (text or '').strip()
    if not text or len(text) >= 1024:
        return config.get('description') or (
            f'{book_title} by {author}\nChapter {chapter_number}: {chapter_title}')
    intro = f'From "{book_title}" by {author}.'
    notes = (config.get('description') or '').strip()
    if notes:
        intro += ' ' + notes
    # Avoid repeating a heading already included in the original plain text.
    if text.splitlines()[0].strip().casefold() != chapter_title.strip().casefold():
        text = chapter_title.upper() + '\n\n' + text
    return '\n\n'.join((intro, text, DESCRIPTION_HASHTAGS))


def hashtag(value):
    return ''.join(c for c in str(value).lower() if c.isalnum() or c == '_')


def title_defaults(title, author, config, chapter_title=''):
    chapter_title = chapter_title.strip()
    if chapter_title and chapter_title.casefold() != title.strip().casefold():
        title = f'{chapter_title} — {title}'
    last_name = hashtag(author.split()[-1]) if author.split() else ''
    category = hashtag(config.get('genre') or config.get('category') or '')
    content_format = hashtag(config.get('content_format') or '')
    tags = ['booktok', category, last_name, content_format]
    generated_title = f'{title} by {author} ' + ' '.join('#' + tag for tag in tags if tag)
    return dict(upload_title=generated_title, title_title=title,
                upload_tags='book,literature,story,audiobook' + (',poem' if content_format == 'poem' else ''),
                title_author=author, author_hashtag=last_name,
                title_category=category, title_format=content_format,
                title_categories=tuple(dict.fromkeys((*CATEGORIES, *([category] if category else [])))),
                title_formats=tuple(dict.fromkeys((*FORMATS, *([content_format] if content_format else [])))))

import os
from functools import wraps
from uuid import uuid4

from flask import (
    Blueprint,
    render_template,
    request,
    send_from_directory,
    abort, redirect, url_for, jsonify,
)

import const
import logger


from .author import Author
from pathlib import Path
import json
from .research import research_author, research_bibliography, ResearchError
from .catalog import merge_discoveries
from .rename import rename_author, RenameError
from .contributors import profile_lock, validate_person, merge_people, FIELDS as PERSON_FIELDS
from .portraits import cached_portrait, download_portrait
from .profile import BIO_FIELDS, safe_name, year, web_url, read_json, write_json, bibliography, profile_data, full_date, bibliography_groups

log = logger.log(__name__)

bp = Blueprint(
    'author',
    __name__,
    template_folder=os.path.join(
        os.path.dirname(__file__),
        "templates"
    ),
)

@bp.route("/")
def author_base(author):
    from artifact_editor.library.storage import sync_shelf_catalog
    sync_shelf_catalog(author)
    author = Author(author)

    # some special cases to accomidate our
    # position at the top of the URL tree
    if author.name == "favicon.ico":
        log.warning('favicon')

        return send_from_directory(
            os.path.join(
                const.STATIC_DIR
            ), 
            "favicon.ico"
        )
    
    directory = author_directory(author.name)
    profile = profile_data(directory)
    books = bibliography(directory)
    return render_template(
        "author.html", author=author,
        profile=profile,
        portrait=cached_portrait(directory, profile.get('portrait_url', '')),
        books=books, book_groups=bibliography_groups(books),
        saved=request.args.get('saved'),
    )


def author_directory(name):
    try:
        safe_name(name)
    except ValueError:
        abort(404)
    directory = Path(const.LIBRARY_DIR) / name
    if not directory.is_dir() or directory.is_symlink():
        abort(404)
    return directory


def profile_writer(function):
    @wraps(function)
    def wrapped(author, *args, **kwargs):
        with profile_lock(author_directory(author)):
            return function(author, *args, **kwargs)
    return wrapped


@bp.route('/profile', methods=['POST'])
@profile_writer
def save_profile(author):
    directory = author_directory(author)
    try:
        values = {key: request.form.get(key, '').strip() for key in BIO_FIELDS}
        values['identity_type'] = values['identity_type'] or profile_data(directory)['identity_type']
        if values['identity_type'] not in ('individual', 'collective'):
            raise ValueError('Choose an individual or shared pen name.')
        values['pen_name'] = author.replace('_', ' ')
        values['birth_name'] = values['birth_name'] or request.form.get('full_name', '').strip()
        values['birth_date'] = full_date(values['birth_date'])
        values['death_date'] = full_date(values['death_date'])
        if values['birth_date'] and values['death_date'] and values['death_date'] < values['birth_date']:
            raise ValueError('Death date cannot precede birth date.')
        # Old forms may remain open during an app reload.
        for key in ('birth_year', 'death_year'):
            if key in request.form:
                year(request.form[key])
        if request.form.get('birth_year') and request.form.get('death_year') and int(request.form['death_year']) < int(request.form['birth_year']):
            raise ValueError('Death year cannot precede birth year.')
        for key in ('portrait_url', 'portrait_source'):
            values[key] = web_url(values[key])
        if values['portrait_url'] and not values['portrait_source']:
            raise ValueError('Include a source link for the portrait.')
    except ValueError as error:
        return str(error), 400
    profile = read_json(directory / 'author.json')
    profile.update(values)
    for kind in ('birth', 'death'):
        if values[kind + '_date']:
            profile[kind + '_year'] = values[kind + '_date'][:4]
        elif kind + '_year' in request.form:
            profile[kind + '_year'] = year(request.form[kind + '_year'])
    if 'research_sources' in request.form:
        try:
            sources = json.loads(request.form['research_sources'])
            if not isinstance(sources, list) or len(sources) > 10:
                raise ValueError('Invalid research sources.')
            profile['research_sources'] = [web_url(value) for value in sources if isinstance(value, str)]
        except (ValueError, TypeError):
            return 'Invalid research sources.', 400
    if values['portrait_url']:
        try:
            download_portrait(directory, values['portrait_url'])
        except ValueError as error:
            return str(error), 400
    write_json(directory / 'author.json', profile)
    return redirect(url_for('.author_base', author=author, saved='biography'), code=303)


@bp.route('/profile', methods=['PATCH'])
@profile_writer
def autosave_profile(author):
    directory = author_directory(author)
    changes = request.get_json(silent=True)
    if not isinstance(changes, dict) or not changes:
        return jsonify(error='No field changes supplied.'), 400
    allowed = set(BIO_FIELDS) - {'pen_name'} | {'research_sources'}
    if set(changes) - allowed:
        return jsonify(error='Unknown or read-only author field.'), 400
    profile = profile_data(directory)
    try:
        for key, value in changes.items():
            if key == 'research_sources':
                sources = json.loads(value) if isinstance(value, str) else value
                if not isinstance(sources, list) or len(sources) > 20 or any(not isinstance(v, str) for v in sources):
                    raise ValueError('Invalid research sources.')
                profile[key] = [web_url(v) for v in sources]
                continue
            if not isinstance(value, str):
                raise ValueError('Author fields must contain text.')
            value = value.strip()
            if key == 'identity_type' and value not in ('individual', 'collective'):
                raise ValueError('Choose an individual or shared pen name.')
            if key in ('birth_date', 'death_date'):
                value = full_date(value)
                # Clearing a full date must not resurrect an obsolete year.
                profile[key.replace('_date', '_year')] = value[:4] if value else ''
            elif key in ('portrait_url', 'portrait_source'):
                value = web_url(value)
            profile[key] = value
        if profile.get('birth_date') and profile.get('death_date') and profile['death_date'] < profile['birth_date']:
            raise ValueError('Death date cannot precede birth date.')
    except (ValueError, TypeError) as error:
        return jsonify(error=str(error)), 400
    profile['pen_name'] = author.replace('_', ' ')
    write_json(directory / 'author.json', profile)
    return jsonify(saved=True)


@bp.route('/bibliography', methods=['POST', 'PATCH'])
def save_bibliography(author):
    from artifact_editor.library.storage import is_shelf_link
    directory = author_directory(author)
    if request.method == 'PATCH':
        changes = request.get_json(silent=True)
        if not isinstance(changes, dict) or set(changes) - {'book_name', 'publication_year', 'source_url', 'copyright_status', 'copyright_source', 'series', 'series_source', 'publication_date'}:
            return jsonify(error='Invalid bibliography changes.'), 400
        try:
            title = safe_name(changes.get('book_name'))
            bookdir = directory / title
            if not bookdir.is_dir() or (bookdir.is_symlink() and not is_shelf_link(bookdir, author, title)):
                abort(404)
            config = read_json(bookdir / 'config.json')
            if 'series' in changes:
                if not isinstance(changes['series'], str) or len(changes['series']) > 250:
                    raise ValueError('Series must be text of at most 250 characters.')
                config['series'] = changes['series'].strip()
            if 'series_source' in changes:
                config['series_source'] = web_url(changes['series_source'])
            if 'publication_date' in changes:
                config['publication_date'] = full_date(changes['publication_date'])
                if config['publication_date']:
                    config['publication_year'] = config['publication_date'][:4]
            if 'copyright_status' in changes:
                if not isinstance(changes['copyright_status'], str) or len(changes['copyright_status']) > 2000:
                    raise ValueError('Copyright status must be text of at most 2000 characters.')
                config['copyright_status'] = changes['copyright_status'].strip()
            if 'copyright_source' in changes:
                config['copyright_source'] = web_url(changes['copyright_source'])
            if 'publication_year' in changes:
                config['publication_year'] = year(changes['publication_year'])
                if config.get('publication_date') and config['publication_date'][:4] != config['publication_year']:
                    config['publication_date'] = ''
            if 'source_url' in changes:
                config['bibliography_source'] = web_url(changes['source_url'])
        except (ValueError, TypeError, AttributeError) as error:
            return jsonify(error=str(error)), 400
        write_json(bookdir / 'config.json', config)
        return jsonify(saved=True, book={key: config.get(key, '') for key in ('series', 'publication_year', 'publication_date')})
    try:
        title = safe_name(request.form.get('book_name'))
        published = year(request.form.get('publication_year'))
        published_date = full_date(request.form.get('publication_date'))
        if published_date:
            published = published_date[:4]
        series = request.form.get('series', '').strip()
        if len(series) > 250:
            raise ValueError('Series must be at most 250 characters.')
        series_source = web_url(request.form.get('series_source'))
        source = web_url(request.form.get('source_url'))
        copyright_source = web_url(request.form.get('copyright_source'))
        copyright_status = request.form.get('copyright_status', '').strip()
        if len(copyright_status) > 2000:
            raise ValueError('Copyright status must be at most 2000 characters.')
    except ValueError as error:
        return str(error), 400
    bookdir = directory / title
    if bookdir.is_symlink() and not is_shelf_link(bookdir, author, title):
        abort(400)
    if request.form.get('create') and bookdir.exists():
        return 'That book already exists. Edit its bibliography entry below.', 409
    bookdir.mkdir(exist_ok=True)
    config = read_json(bookdir / 'config.json')
    config.update(publication_year=published, bibliography_source=source)
    for key, value in (('copyright_status', copyright_status), ('copyright_source', copyright_source),
                       ('series', series), ('series_source', series_source), ('publication_date', published_date)):
        if key in request.form:
            config[key] = value
    write_json(bookdir / 'config.json', config)
    return redirect(url_for('.author_base', author=author, saved='bibliography'), code=303)


def title_to_directory(book_title):
    book_title = book_title.replace(';', '')
    book_title = book_title.replace(',', '')
    return book_title

# http://localhost:8080/library/Mary%20Wollstonecraft%20Shelley/add
@bp.route("/add", methods=['POST'])
def add_book(author):
    directory = author_directory(author)
    try:
        title = safe_name(title_to_directory(request.form.get('book_name') or ''))
    except ValueError as error:
        return str(error), 400
    book_dir = directory / title
    if book_dir.exists():
        return 'That book already exists.', 409
    book_dir.mkdir()
    write_json(book_dir / 'config.json', {})
    return f"Created entry for {title}", 200


@bp.route('/research', methods=['POST'])
def research(author):
    directory = author_directory(author)
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify(error='Invalid research request.'), 400
    name = author.replace('_', ' ')
    if not isinstance(name, str) or len(name) > 250:
        return jsonify(error='Enter an author name of at most 250 characters.'), 400
    try:
        suggestions = research_author(name.strip())
    except ResearchError as error:
        return jsonify(error=str(error)), 502
    if suggestions.get('contributors'):
        profile, added, updated = merge_people(directory, suggestions['contributors'])
        suggestions['contributors_html'] = render_template('_contributors.html', author=Author(author), profile=profile_data(directory))
        suggestions['contributors_added'] = added
        suggestions['contributors_updated'] = updated
    return jsonify(suggestions)


@bp.route('/portrait', methods=['POST'])
def fetch_portrait(author):
    directory = author_directory(author)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('url'), str):
        return jsonify(error='Enter a portrait image URL.'), 400
    try:
        record = download_portrait(directory, payload['url'].strip())
    except ValueError as error:
        return jsonify(error=str(error)), 400
    return jsonify(thumbnail_url=url_for('.portrait_image', author=author, record=record, kind='thumb'),
                   image_url=url_for('.portrait_image', author=author, record=record, kind='image'))


@bp.route('/portrait/<record>/<kind>.jpg')
def portrait_image(author, record, kind):
    directory = author_directory(author)
    if len(record) != 64 or any(c not in '0123456789abcdef' for c in record) or kind not in ('image', 'thumb'):
        abort(404)
    filename = record + ('-thumb' if kind == 'thumb' else '') + '.jpg'
    return send_from_directory(directory / '.author-media', filename, mimetype='image/jpeg', max_age=31536000)


@bp.route('/bibliography/research', methods=['POST'])
def discover_bibliography(author):
    directory = author_directory(author)
    try:
        discoveries = research_bibliography(author.replace('_', ' '))
    except ResearchError as error:
        return jsonify(error=str(error)), 502
    counts = merge_discoveries(directory, discoveries['books'])
    books = bibliography(directory)
    return jsonify(**counts, total=len(books), warnings=discoveries.get('warnings', []),
                   html=render_template('_bibliography_books.html', author=Author(author), books=books, book_groups=bibliography_groups(books)))


@bp.route('/rename', methods=['POST'])
def change_pen_name(author):
    author_directory(author)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('pen_name'), str):
        return jsonify(error='Enter a pen name.'), 400
    try:
        name = rename_author(const.LIBRARY_DIR, author, payload['pen_name'])
    except RenameError as error:
        return jsonify(error=str(error)), error.status
    except Exception:
        log.exception('Author rename failed', author=author)
        return jsonify(error='The author could not be renamed. Check the service logs before retrying.'), 500
    return jsonify(saved=True, author=name, url=url_for('.author_base', author=name))


@bp.route('/contributors', methods=['POST'])
@profile_writer
def add_contributor(author):
    directory = author_directory(author)
    try:
        person = validate_person(request.get_json(silent=True))
    except ValueError as error:
        return jsonify(error=str(error)), 400
    profile = profile_data(directory)
    person['id'] = str(uuid4())
    profile['contributors'].append(person)
    profile['identity_type'] = 'collective'
    write_json(directory / 'author.json', profile)
    return jsonify(saved=True, id=person['id'], html=render_template('_contributors.html', author=Author(author), profile=profile))


@bp.route('/contributors/<person_id>', methods=['PATCH'])
@profile_writer
def save_contributor(author, person_id):
    directory = author_directory(author)
    profile = profile_data(directory)
    person = next((item for item in profile['contributors'] if item['id'] == person_id), None)
    if person is None:
        abort(404)
    try:
        updated = validate_person(request.get_json(silent=True), person)
    except ValueError as error:
        return jsonify(error=str(error)), 400
    person.update(updated)
    write_json(directory / 'author.json', profile)
    return jsonify(saved=True, name=person['name'])


@bp.route('/contributors/<person_id>/research', methods=['POST'])
def research_contributor(author, person_id):
    directory = author_directory(author)
    profile = profile_data(directory)
    person = next((item for item in profile['contributors'] if item['id'] == person_id), None)
    if person is None:
        abort(404)
    try:
        result = research_author(person['name'])
        if result.get('contributors') or (person.get('wikidata_id') and result.get('identity') != person['wikidata_id']):
            raise ResearchError('The source does not match this individual author.')
    except ResearchError as error:
        return jsonify(error=str(error)), 502
    fields = result['profile']
    changes = {key: value for key, value in fields.items() if key in PERSON_FIELDS}
    changes.update(name=person['name'], name_pronunciation=fields.get('pen_name_pronunciation', ''),
                   research_sources=result.get('sources', []), wikidata_id=result.get('identity', ''))
    with profile_lock(directory):
        profile = profile_data(directory)
        person = next((item for item in profile['contributors'] if item['id'] == person_id), None)
        if person is None:
            abort(404)
        sources = list(dict.fromkeys(person.get('research_sources', []) + changes.get('research_sources', [])))[:20]
        changes = {key: value for key, value in changes.items() if value and not person.get(key)}
        if sources:
            changes['research_sources'] = sources
        person.update(validate_person(changes, person))
        write_json(directory / 'author.json', profile)
    return jsonify(saved=True, html=render_template('_contributor.html', author=Author(author), person=person), warnings=result.get('warnings', []))

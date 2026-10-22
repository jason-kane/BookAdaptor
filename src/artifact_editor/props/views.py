"""Prop editing and image reference endpoints."""
import uuid

from flask import Blueprint, abort, g, render_template, request, send_from_directory

from artifact_editor.generation_feedback import generation_failure_message

from . import props

bp = Blueprint('props', __name__, template_folder='templates')


def panel(error='', active_tag=None, message=''):
    records = props.get_all_props(g.chapter)
    return render_template('props.html', chapter=g.chapter, props=records,
                           error=error, message=message, active_tag=active_tag, job_ids={tag: uuid.uuid4().hex for tag in records}, image_tags={tag for tag in records
                           if (props.directory(g.chapter) / f'{tag}.png').exists()})


@bp.route('/', methods=['GET', 'POST'])
def base():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        if not name:
            return panel('Enter a prop name.'), 400
        records = props.get_all_props(g.chapter)
        tag = uuid.uuid4().hex
        records[tag] = {'name': name, 'description': request.form.get('description', '').strip()}
        props.save_props(g.chapter, records)
        return panel(active_tag=tag)
    return panel()


@bp.route('/<tag>', methods=['POST'])
def update(tag):
    records = props.get_all_props(g.chapter)
    if tag not in records:
        abort(404)
    records[tag]['description'] = request.form.get('description', '').strip()
    name = request.form.get('name', '').strip()
    if not name:
        if request.args.get('autosave') == '1':
            return 'Enter a prop name.', 400
        return panel('Enter a prop name.'), 400
    records[tag]['name'] = name
    props.save_props(g.chapter, records)
    if request.args.get('autosave') == '1':
        return '', 200
    return panel(active_tag=tag)


@bp.route('/<tag>/image.png')
def image(tag):
    if tag not in props.get_all_props(g.chapter):
        abort(404)
    response = send_from_directory(props.directory(g.chapter), f'{tag}.png')
    response.headers['Cache-Control'] = 'no-cache'
    return response


@bp.route('/<tag>/generate_description', methods=['POST'])
def generate_description(tag):
    if tag not in props.get_all_props(g.chapter):
        abort(404)
    try:
        props.generate_description(g.chapter, tag, name=request.form.get('name'))
    except (RuntimeError, TimeoutError) as exc:
        return panel(generation_failure_message(exc), active_tag=tag)
    except ValueError as exc:
        return panel(str(exc), active_tag=tag)
    return panel(active_tag=tag)


@bp.route('/extract', methods=['POST'])
def extract():
    try:
        count = props.extract_props(g.chapter)
    except (RuntimeError, TimeoutError) as exc:
        return panel(generation_failure_message(exc))
    except ValueError as exc:
        return panel(str(exc))
    return panel(message=f'Added {count} new prop' + ('s.' if count != 1 else '.'))


@bp.route('/<tag>', methods=['DELETE'])
def delete(tag):
    try:
        props.delete_prop(g.chapter, tag)
    except KeyError:
        abort(404)
    return panel(message='Prop deleted.')


@bp.route('/<tag>/workflow')
def open_workflow(tag):
    from flask import redirect
    from artifact_editor.reference_images import open_reference_workflow
    prop = props.get_all_props(g.chapter).get(tag)
    if prop is None:
        abort(404)
    try:
        return redirect(open_reference_workflow('props', request.args.get('description', prop.get('description', ''))))
    except ValueError as exc:
        return str(exc), 400

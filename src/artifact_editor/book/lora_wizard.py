"""Book-scoped LoRA training drafts and normalized, captioned datasets."""
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

STEPS = ('Concept', 'Book source images', 'Captions', 'Review and train')
MIN_TRAINING_IMAGES = 30
MAX_TRAINING_IMAGES = 50


def directory(book, draft_id):
    from const import LIBRARY_DIR
    identity = str(UUID(draft_id))
    return Path(LIBRARY_DIR) / book.bookdir / 'lora_training' / identity


def load(book, draft_id):
    return json.loads((directory(book, draft_id) / 'draft.json').read_text())


def save(book, draft):
    folder = directory(book, draft['id'])
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f'.{uuid4()}.json'
    temporary.write_text(json.dumps(draft, indent=2))
    os.replace(temporary, folder / 'draft.json')


def create(book, form):
    name = form.get('name', '').strip()
    concept = form.get('concept', '').strip()
    trigger = form.get('trigger', '').strip()
    if not name or not concept or not trigger:
        raise ValueError('Enter a name, concept, and trigger word.')
    if len(name) > 160 or len(concept) > 4000 or len(trigger) > 100:
        raise ValueError('Keep the name under 160 characters, concept under 4,000, and trigger under 100.')
    draft = {'id': str(uuid4()), 'name': name, 'concept': concept, 'trigger': trigger,
             'images': [], 'selected_source_images': [], 'status': 'draft', 'step': 2}
    save(book, draft)
    return draft


def set_characters(item, tags, catalog):
    tags = list(dict.fromkeys(tags))
    if any(tag not in catalog for tag in tags):
        raise ValueError('Choose characters from this book’s character list.')
    if (tags != item.get('character_tags', []) or (not tags and item.get('characters'))) and item.get('caption'):
        for name in item.get('characters', []):
            item['caption'] = item['caption'].replace(f'[{name}]', '').strip(' ,')
    item['character_tags'] = tags
    item['characters'] = [catalog[tag].get('name', tag) for tag in tags]


def caption_text(item, trigger=""):
    """Keep editable prose separate from the selected character name tokens."""
    prose = item['caption'].strip()
    tokens = [f'[{name}]' for name in item.get('characters', [])]
    if trigger.startswith('[') and trigger.endswith(']'):
        tokens.insert(0, trigger)
        for token in tokens:
            prose = prose.replace(token, '').strip(' ,')
        return ' '.join([*dict.fromkeys(tokens), prose]).strip()
    return ' '.join([*(token for token in tokens if token not in prose), prose]).strip()


def needs_caption(item, trigger):
    if item.get('caption_edited'):
        return False
    description = item.get('caption', '')
    for token in [trigger, *(f'[{name}]' for name in item.get('characters', []))]:
        if token:
            description = description.replace(token, '')
    return not item.get('caption_generated') or not description.strip(' ,\n\t')


def update(book, draft, step, form, character_catalog=None):
    character_catalog = character_catalog or {}
    if step == 1:
        name, concept, trigger = (form.get(key, '').strip() for key in ('name', 'concept', 'trigger'))
        if not name or not concept or not trigger:
            raise ValueError('Enter a name, concept, and trigger word.')
        if len(name) > 160 or len(concept) > 4000 or len(trigger) > 100:
            raise ValueError('Name, concept, or trigger word is too long.')
        draft.update(name=name, concept=concept, trigger=trigger)
    elif step == 2:
        from artifact_editor.images.source import source_image_path, source_images
        selected = list(dict.fromkeys(form.getlist('source_images')))
        if not MIN_TRAINING_IMAGES <= len(selected) <= MAX_TRAINING_IMAGES:
            raise ValueError('Select between 30 and 50 source images for training.')
        available = set(source_images(book))
        if any(filename not in available for filename in selected):
            raise ValueError('A selected source image is no longer available.')
        folder = directory(book, draft['id']) / 'dataset'
        folder.mkdir(parents=True, exist_ok=True)
        existing = {item.get('source_path'): item for item in draft['images']}
        images, additions, created = [], [], []
        try:
            for filename in selected:
                if filename in existing:
                    item = dict(existing[filename])
                    set_characters(item, form.getlist('characters_' + filename), character_catalog)
                    images.append(item)
                    continue
                image_id = str(uuid4())
                destination = folder / f'{image_id}.png'
                created.append(destination)
                with Image.open(source_image_path(book, filename)) as source:
                    if source.width * source.height > 40_000_000:
                        raise ValueError('Reference images must be under 40 megapixels.')
                    image = ImageOps.exif_transpose(source).convert('RGB')
                    image.thumbnail((2048, 2048))
                    image.save(destination)
                item = {'id': image_id, 'filename': destination.name, 'source_path': filename,
                        'original_name': filename, 'caption': draft['trigger']}
                set_characters(item, form.getlist('characters_' + filename), character_catalog)
                additions.append(item)
                images.append(item)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as error:
            for destination in created:
                destination.unlink(missing_ok=True)
            raise ValueError('Selected source images must be valid images under 40 megapixels.') from error
        kept_ids = {item['id'] for item in images}
        for item in draft['images']:
            if item['id'] not in kept_ids:
                (folder / item['filename']).unlink(missing_ok=True)
                (folder / item['filename']).with_suffix('.txt').unlink(missing_ok=True)
        draft['images'] = images
        draft['selected_source_images'] = selected
        draft['caption_state'] = 'draft'
    elif step == 3:
        captions = [form.get('caption_' + item['id'], '').strip() for item in draft['images']]
        if not captions or any(not caption or len(caption) > 4000 for caption in captions):
            raise ValueError('Give every reference image a caption of 1–4,000 characters.')
        selections = [dict(item) for item in draft['images']]
        for item in selections:
            set_characters(item, form.getlist('characters_' + item['id']), character_catalog)
        folder = directory(book, draft['id']) / 'dataset'
        for item, caption in zip(selections, captions):
            item['caption'] = caption
            item['caption_generated'] = True
            (folder / item['filename']).with_suffix('.txt').write_text(caption_text(item, draft['trigger']) + '\n')
        draft['images'] = selections
    draft['step'] = min(step + 1, 4)
    save(book, draft)
    return draft


def save_source_selection(book, draft, form, character_catalog=None):
    """Persist step 2 checkbox changes without advancing the wizard."""
    from artifact_editor.images.source import source_images

    selected = list(dict.fromkeys(form.getlist('source_images')))
    available = set(source_images(book))
    if len(selected) > MAX_TRAINING_IMAGES or any(filename not in available for filename in selected):
        raise ValueError('Invalid source image selection.')
    draft['selected_source_images'] = selected
    save(book, draft)
    return draft


def remove_image(book, draft, image_id, form, character_catalog):
    """Remove a training copy while preserving other unsaved caption edits."""
    target = next((item for item in draft['images'] if item['id'] == image_id), None)
    if target is None:
        raise ValueError('Image is no longer in this dataset.')
    if len(draft['images']) <= MIN_TRAINING_IMAGES:
        raise ValueError('Keep at least 30 images in the training dataset.')
    kept = [dict(item) for item in draft['images'] if item['id'] != image_id]
    for item in kept:
        key = 'caption_' + item['id']
        if key in form:
            caption = form[key].strip()
            if len(caption) > 4000:
                raise ValueError('Captions must be under 4,000 characters.')
            item['caption'] = caption
            set_characters(item, form.getlist('characters_' + item['id']), character_catalog)
    folder = directory(book, draft['id']) / 'dataset'
    # Move copies out of the trainer's dataset; source illustrations are never changed.
    excluded = directory(book, draft['id']) / 'excluded'
    excluded.mkdir(exist_ok=True)
    for path in (folder / target['filename'], (folder / target['filename']).with_suffix('.txt')):
        if path.exists():
            os.replace(path, excluded / path.name)
    removed_index = next(index for index, item in enumerate(draft['images']) if item['id'] == image_id)
    draft.update(images=kept, step=3, last_modified_image_id=kept[min(removed_index, len(kept) - 1)]['id'])
    save(book, draft)
    for item in kept:
        (folder / item['filename']).with_suffix('.txt').write_text(caption_text(item, draft['trigger']) + '\n')
    return draft


def save_image_edit(book, draft, image_id, changes, character_catalog):
    item = next((item for item in draft['images'] if item['id'] == image_id), None)
    if item is None:
        raise ValueError('Image is no longer in this dataset.')
    if 'caption' in changes:
        if not isinstance(changes['caption'], str) or len(changes['caption']) > 4000:
            raise ValueError('Captions must be under 4,000 characters.')
        if changes['caption'] != item.get('caption', ''):
            item['caption_generated'] = True
            item['caption_edited'] = True
        item['caption'] = changes['caption']
    if 'character_tags' in changes:
        tags = changes['character_tags']
        if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
            raise ValueError('Invalid character selection.')
        set_characters(item, tags, character_catalog)
    import time
    item['modified_at'] = time.time_ns()
    draft['last_modified_image_id'] = image_id
    (directory(book, draft['id']) / 'dataset' / item['filename']).with_suffix('.txt').write_text(caption_text(item, draft['trigger']) + '\n')
    save(book, draft)
    return item

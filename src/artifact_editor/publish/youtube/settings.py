"""Chapter-local YouTube upload form persistence."""
import json
import os
from pathlib import Path
import tempfile

FIELDS = ('title', 'description', 'title_category', 'title_format', 'privacy_status',
          'license', 'made_for_kids', 'ai_use', 'paid_promotion', 'tags', 'category_id')
DEFAULTS = dict(privacy_status='private', license='youtube', made_for_kids='false',
                ai_use='true', paid_promotion='false', category_id='24')


def settings_path(chapter):
    return Path(chapter.get_video_filename()).parent / 'upload.json'


def load_settings(chapter):
    try:
        data = json.loads(settings_path(chapter).read_text())
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: data[key] for key in FIELDS if isinstance(data.get(key), str)}


def save_settings(chapter, values, body):
    data = {key: values.get(key, '') for key in FIELDS}
    # Persist the effective API defaults too, for older or incomplete forms.
    for key, value in DEFAULTS.items():
        data[key] = values.get(key, value)
    data['category_id'] = body['snippet']['categoryId']
    path = settings_path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2, ensure_ascii=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

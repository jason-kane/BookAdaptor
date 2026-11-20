"""Editable per-model prompting methods, snapshotted before each search."""
import hashlib
import os
from pathlib import Path
import re

import yaml
from flask import current_app

DIRECTORY='home/polish/prompts'
DEFAULT_BODY='''Return exactly one JSON object matching the requested schema. Do not include explanations outside it.
Code fields contain source code only, never thinking tags, chat delimiters, JSON fragments, or markdown fences.
Copy search text exactly from the supplied source. Preserve indentation on every replacement line.
Make one small, low-risk polish. Do not solve the whole goal or change unrelated behavior.
'''


def profile_path(provider, model):
    slug=re.sub(r'[^a-zA-Z0-9_-]+','-',model).strip('-')[:70]
    fingerprint=hashlib.sha256((provider+':'+model).encode()).hexdigest()[:8]
    return f'{DIRECTORY}/{provider}-{slug}-{fingerprint}.md'


def parse_profile(text, path):
    if len(text)>16000 or not text.startswith('---\n'):
        raise ValueError('Prompt profile requires YAML frontmatter and must be under 16,000 characters.')
    header,separator,body=text[4:].partition('\n---\n')
    if not separator or not body.strip():
        raise ValueError('Prompt profile requires instructions after its frontmatter.')
    try:
        settings=yaml.safe_load(header)
    except yaml.YAMLError as error:
        raise ValueError('Prompt profile frontmatter is invalid YAML.') from error
    required={'provider','model','format','delivery','thinking'}
    allowed=required|{'indentation'}
    if not isinstance(settings,dict) or not required.issubset(settings) or not set(settings).issubset(allowed):
        raise ValueError('Prompt profile requires provider, model, format, delivery, and thinking fields.')
    if settings['provider'] not in ('ollama','openai') or not isinstance(settings['model'],str):
        raise ValueError('Prompt profile has an invalid provider/model identity.')
    if profile_path(settings['provider'],settings['model'])!=path:
        raise ValueError('Prompt profile identity does not match its file path.')
    if settings['format'] not in ('schema','json','none') or settings['delivery'] not in ('system','user'):
        raise ValueError('Prompt profile format/delivery is unsupported.')
    if settings['thinking'] is not None and not isinstance(settings['thinking'],bool):
        raise ValueError('Prompt profile thinking must be true, false, or null.')
    if settings['provider']=='openai' and settings['thinking'] is not None:
        raise ValueError('The thinking toggle is specific to Ollama profiles.')
    settings.setdefault('indentation','absolute')
    if settings['indentation'] not in ('absolute','relative'):
        raise ValueError('Prompt profile indentation must be absolute or relative.')
    return {**settings,'instructions':body.strip(),'path':path,'revision':hashlib.sha256(text.encode()).hexdigest()}


def load_profile(model, *, create=False):
    existing=model.get('prompt_profile')
    if existing:
        return {'indentation':'absolute',**existing}
    provider,name=model['provider'],model['model']
    relative=profile_path(provider,name)
    settings={'provider':provider,'model':name,'format':'schema','delivery':'system',
              'thinking':False if provider=='ollama' and name.startswith('qwen3:') else None,
              'indentation':'relative' if provider=='ollama' and name.startswith('qwen3:') else 'absolute'}
    instructions=DEFAULT_BODY
    if settings['indentation']=='relative':
        instructions=instructions.replace('Preserve indentation on every replacement line.',
            'For a Python whole-line edit, return a dedented fragment with correct relative indentation; the tool restores block indentation.')
    default='---\n'+yaml.safe_dump(settings,sort_keys=False)+'---\n'+instructions
    root=current_app.config.get('POLISH_WORKSPACE')
    if root:
        from .application import file_path
        path=file_path({'path':relative})
        if not path.exists() and create:
            path.parent.mkdir(parents=True,exist_ok=True)
            try:
                with path.open('x') as output:
                    output.write(default)
                owner=path.parent.stat()
                os.chown(path,owner.st_uid,owner.st_gid)
            except FileExistsError:
                pass
        if path.exists():
            return parse_profile(path.read_text(),relative)
    return parse_profile(default,relative)


def summary(profile):
    return {key:profile.get(key,'absolute') for key in ('path','revision','format','delivery','thinking','indentation')}

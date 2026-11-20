"""Per-model prompting methods remain editable without changing past job evidence."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from home.app import create_app
from home.polish import database
from home.polish.prompt_profiles import load_profile, parse_profile
from home.polish.seeking import state_put, enqueue_search
from home.polish.validation import syntax_check
from home.polish.worker import build_context
from home.polish.providers import generate

MODEL={'id':'ollama:qwen3:14b','provider':'ollama','model':'qwen3:14b','label':'Ollama · qwen3:14b','version':''}


def test_profiles_are_model_specific_and_job_snapshots_do_not_follow_later_edits(tmp_path):
    app=create_app({'TESTING':True,'TODO_DIR':str(tmp_path/'data'),'POLISH_WORKSPACE':str(tmp_path/'repo')})
    Path(app.config['POLISH_WORKSPACE']).mkdir()
    with app.app_context():
        first=load_profile(MODEL,create=True)
        assert first['thinking'] is False
        other=load_profile({**MODEL,'model':'qwen3:8b'},create=True)
        assert other['path']!=first['path']
        with database() as c: state_put(c,'models',{'models':[MODEL]})
        with patch('home.polish.seeking.start_worker'):
            job=enqueue_search('G001',MODEL['id'])
        path=Path(app.config['POLISH_WORKSPACE'])/first['path']
        path.write_text(path.read_text().replace('Make one small, low-risk polish.','Prefer one minimal polish.'))
        changed=load_profile(MODEL)
        assert changed['revision']!=first['revision']
        with database() as c:
            snapshot=json.loads(c.execute('SELECT model_json FROM polish_jobs WHERE id=?',(job['id'],)).fetchone()[0])
        assert snapshot['prompt_profile']['revision']==first['revision']


def test_prompt_profile_methods_control_generation_without_leaking_thinking(tmp_path):
    app=create_app({'POLISH_WORKSPACE':str(tmp_path)})
    response={'model':'qwen3:14b','message':{'content':json.dumps(dict(kind='goal',description='Small change',path='',search='',replacement='',goal_id='G001',title='Shorter',evidence='Same meaning'))}}
    with app.app_context(),patch('home.polish.providers.http_json',return_value=response) as request:
        output,metadata=generate(MODEL,'Goal context')
        payload=request.call_args.kwargs['payload']
        assert payload['think'] is False
        assert payload['messages'][0]['role']=='system'
        assert payload['messages'][-1]['content']=='Goal context'
        assert payload['format']['type']=='object'
        assert metadata['prompt_profile']['revision']
        assert output['title']=='Shorter'


def test_bad_profile_metadata_cannot_be_applied(tmp_path):
    with create_app({'POLISH_WORKSPACE':str(tmp_path)}).app_context():
        profile=load_profile(MODEL,create=True)
        path=Path(tmp_path)/profile['path']
        with pytest.raises(ValueError,match='identity'):
            syntax_check(profile['path'],path.read_text().replace('qwen3:14b','another-model'))
        with pytest.raises(ValueError,match='format/delivery'):
            parse_profile(path.read_text().replace('format: schema','format: invalid'),profile['path'])


def test_prompt_polishing_goal_exposes_selected_models_profile_as_editable_source(tmp_path):
    import subprocess
    app=create_app({'TODO_DIR':str(tmp_path/'data'),'POLISH_WORKSPACE':str(tmp_path/'repo')})
    root=Path(app.config['POLISH_WORKSPACE']);root.mkdir()
    subprocess.run(['git','init','-q',str(root)],check=True)
    with app.app_context():
        profile=load_profile(MODEL,create=True)
        with database() as c:
            c.execute("INSERT INTO goals (id,title,evidence) VALUES ('G018','Improve model prompts','Extract clean valid responses.')")
            state_put(c,'models',{'models':[MODEL]})
        with patch('home.polish.seeking.start_worker'):
            job=enqueue_search('G018',MODEL['id'])
        with database() as c: row=dict(c.execute('SELECT * FROM polish_jobs WHERE id=?',(job['id'],)).fetchone())
        context=build_context(row)
        assert next(iter(context['sources']))==profile['path']

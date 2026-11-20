"""One selected provider; no fallback models, model downloads, or automatic retries."""
import json
from flask import current_app
from .seeking import http_json
from .prompt_profiles import load_profile, summary

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {key: {'type': 'string'} for key in (
        'kind', 'description', 'path', 'search', 'replacement', 'goal_id', 'title', 'evidence')},
    'required': ['kind', 'description', 'path', 'search', 'replacement', 'goal_id', 'title', 'evidence'],
}


class ModelOutputError(ValueError):
    def __init__(self,message,content):
        self.raw_output=content
        super().__init__(message)


def generate(model, prompt):
    timeout = current_app.config.get('POLISH_MODEL_TIMEOUT', 180)
    profile=load_profile(model)
    messages=[{'role':'user','content':prompt}]
    if profile['delivery']=='system':
        messages.insert(0,{'role':'system','content':profile['instructions']})
    else:
        messages[0]['content']=profile['instructions']+'\n\n'+prompt
    if model['provider'] == 'ollama':
        if model.get('version'):
            tags = http_json(current_app.config['OLLAMA_BASE_URL'].rstrip('/') + '/api/tags')
            current = next((item for item in tags.get('models', []) if item.get('name') == model['model']), None)
            if not current or current.get('digest') != model['version']:
                raise ValueError('Ollama model changed or was removed. Refresh models and select it again.')
        payload={'model':model['model'],'messages':messages,'stream':False,'keep_alive':0,
                 'options':{'num_predict':4096,'num_ctx':16384}}
        if profile['format']!='none':
            payload['format']=SCHEMA if profile['format']=='schema' else 'json'
        if profile['thinking'] is not None:
            payload['think']=profile['thinking']
        response = http_json(current_app.config['OLLAMA_BASE_URL'].rstrip('/') + '/api/chat', timeout=timeout,payload=payload)
        if response.get('done_reason') == 'length':
            raise ValueError('Model output was truncated. No review entry was created.')
        content = response.get('message', {}).get('content', '')
    elif model['provider'] == 'openai':
        key = current_app.config.get('OPENAI_API_KEY')
        if not key:
            raise ValueError('OpenAI API key is not configured on the Polish worker.')
        payload={'model':model['model'],'input':messages[-1]['content'],'store':False,'max_output_tokens':4096}
        if profile['delivery']=='system': payload['instructions']=profile['instructions']
        if profile['format']=='schema':
            payload['text']={'format':{'type':'json_schema','name':'polish_improvement','strict':True,'schema':SCHEMA}}
        elif profile['format']=='json': payload['text']={'format':{'type':'json_object'}}
        response = http_json('https://api.openai.com/v1/responses', key=key, timeout=timeout,payload=payload)
        if response.get('status') != 'completed':
            raise ValueError('OpenAI did not complete the proposal. No review entry was created.')
        content = ''.join(part.get('text', '') for item in response.get('output', [])
                          for part in item.get('content', []) if part.get('type') == 'output_text')
    else:
        raise ValueError('Unsupported model provider.')
    try:
        proposal = json.loads(content)
    except (ValueError, TypeError) as error:
        raise ModelOutputError('Model did not return a valid JSON proposal.',content) from error
    if not isinstance(proposal, dict) or set(proposal) != set(SCHEMA['required']) or not all(
        isinstance(value, str) for value in proposal.values()):
        raise ModelOutputError('Model proposal does not match the expected format.',content)
    metadata = {key: response[key] for key in ('model', 'usage', 'prompt_eval_count', 'eval_count', 'done_reason') if key in response}
    metadata['prompt_profile']=summary(profile)
    return proposal, metadata

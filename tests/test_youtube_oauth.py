"""Exercise OAuth browser binding, expiry, and private token persistence offline."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
import pytest

spec = importlib.util.spec_from_file_location(
    'youtube_oauth', Path(__file__).resolve().parents[1] /
    'src/artifact_editor/publish/youtube/oauth.py')
oauth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oauth)


@pytest.fixture
def setup(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, YOUTUBE_OAUTH_DIR=str(tmp_path))
    app.register_blueprint(oauth.bp)
    app.add_url_rule('/connect', view_func=lambda: oauth.start_connection('/chapter/youtube/'))
    credentials = SimpleNamespace(
        refresh_token='refresh', has_scopes=lambda scopes: True,
        to_json=lambda: json.dumps({'refresh_token': 'refresh', 'token': 'access'}))
    flow = SimpleNamespace(
        redirect_uri='http://localhost/oauth/youtube/callback',
        code_verifier='private-verifier', credentials=credentials,
        authorization_url=lambda **kw: ('https://accounts.google.com/authorize', 'valid_state'),
        fetch_token=lambda **kw: None)
    with patch.object(oauth, 'make_flow', return_value=flow) as factory:
        yield app, app.test_client(), tmp_path, flow, factory


def test_connection_persists_tokens_and_consumes_attempt(setup):
    app, client, directory, flow, factory = setup
    response = client.get('/connect')
    assert response.location.startswith('https://accounts.google.com/')
    assert 'HttpOnly' in response.headers['Set-Cookie']
    response = client.get('/oauth/youtube/callback?state=valid_state&code=code')
    assert response.location == '/chapter/youtube/?youtube_connection=connected'
    assert factory.call_args.kwargs['code_verifier'] == 'private-verifier'
    tokens = directory / 'credentials.json'
    assert json.loads(tokens.read_text())['refresh_token'] == 'refresh'
    assert tokens.stat().st_mode & 0o777 == 0o600
    assert not (directory / 'valid_state.pending').exists()
    assert client.get('/oauth/youtube/callback?state=valid_state&code=code').status_code == 400


def test_callback_requires_initiating_browser(setup):
    app, client, directory, *_ = setup
    client.get('/connect')
    assert app.test_client().get('/oauth/youtube/callback?state=valid_state&code=code').status_code == 400
    assert client.get('/oauth/youtube/callback?state=wrong&code=code').status_code == 400
    assert not (directory / 'credentials.json').exists()


def test_expired_attempt_cannot_save_tokens(setup):
    app, client, directory, *_ = setup
    client.get('/connect')
    with patch.object(oauth.time, 'time', return_value=10**12):
        assert client.get('/oauth/youtube/callback?state=valid_state&code=code').status_code == 400
    assert not (directory / 'credentials.json').exists()


def test_denial_preserves_previous_connection(setup):
    app, client, directory, *_ = setup
    (directory / 'credentials.json').write_text('{"refresh_token":"existing"}')
    client.get('/connect')
    response = client.get('/oauth/youtube/callback?state=valid_state&error=access_denied')
    assert response.location.endswith('youtube_connection=denied')
    assert json.loads((directory / 'credentials.json').read_text())['refresh_token'] == 'existing'


def test_token_failure_does_not_expose_details(setup):
    app, client, directory, flow, _ = setup
    client.get('/connect')
    flow.fetch_token = lambda **kw: (_ for _ in ()).throw(ValueError('sensitive'))
    response = client.get('/oauth/youtube/callback?state=valid_state&code=code')
    assert response.location.endswith('youtube_connection=failed')
    assert b'sensitive' not in response.data
    assert not (directory / 'credentials.json').exists()

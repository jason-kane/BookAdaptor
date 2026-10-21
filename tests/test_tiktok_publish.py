import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

from flask import Flask
import pytest

ROOT = Path(__file__).resolve().parents[1] / 'src/artifact_editor/publish/tiktok'
package = ModuleType('test_tiktok_package')
package.__path__ = [str(ROOT)]
sys.modules[package.__name__] = package

def load(name):
    spec = importlib.util.spec_from_file_location(package.__name__ + '.' + name, ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

client = load('client')
package.client = client
oauth = load('oauth')


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config.update(TESTING=True, TIKTOK_OAUTH_DIR=str(tmp_path))
    app.register_blueprint(oauth.bp)
    app.add_url_rule('/connect', view_func=lambda: oauth.start('/chapter/tiktok/'))
    with app.app_context():
        client.save(tmp_path / 'client_secret.json', {
            'client_key': 'key', 'client_secret': 'secret',
            'redirect_uri': 'https://localhost/oauth/tiktok/callback'})
    return app


def test_oauth_browser_binding_and_single_use(app):
    browser = app.test_client()
    response = browser.get('/connect', base_url='https://localhost')
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(response.location).query)['state'][0]
    callback = '/oauth/tiktok/callback?state=' + state + '&code=code'
    assert app.test_client().get(callback, base_url='https://localhost').status_code == 400
    with patch.object(client, 'token') as token:
        response = browser.get(callback, base_url='https://localhost')
        assert response.location.endswith('tiktok_connection=connected')
        assert token.call_args.kwargs['code'] == 'code'
        assert browser.get(callback, base_url='https://localhost').status_code == 400


def test_oauth_expiry(app):
    browser = app.test_client()
    response = browser.get('/connect', base_url='https://localhost')
    from urllib.parse import parse_qs, urlsplit
    state = parse_qs(urlsplit(response.location).query)['state'][0]
    with patch.object(oauth.time, 'time', return_value=10**12):
        assert browser.get('/oauth/tiktok/callback?state='+state+'&code=x', base_url='https://localhost').status_code == 400


def test_token_refresh_and_private_storage(app):
    with app.app_context():
        client.save(client.directory() / 'credentials.json', {'refresh_token': 'old', 'expires_at': 0})
        response = MagicMock()
        response.json.return_value = dict(access_token='access', refresh_token='new',
                                         scope='video.upload', expires_in=3600)
        with patch.object(client.requests, 'post', return_value=response) as post:
            assert client.access_token() == 'access'
            assert post.call_args.kwargs['data']['refresh_token'] == 'old'
        assert (client.directory() / 'credentials.json').stat().st_mode & 0o777 == 0o600


def test_chunk_plan():
    for size in (1, 4*1024**2, 10*1024**2, 21*1024**2, 4*1024**3):
        chunk, count = client.chunk_plan(size)
        assert 1 <= count <= 1000
        assert size - chunk*(count-1) <= 128*1024**2
    with pytest.raises(ValueError):
        client.chunk_plan(0)


def test_upload_tracks_id_before_transmission_and_ranges(app, tmp_path):
    video = tmp_path / 'video.mp4'
    video.write_bytes(b'x' * (21*1024**2))
    initialized = MagicMock()
    responses = [MagicMock(status_code=206), MagicMock(status_code=201)]
    def transmit(*args, **kwargs):
        initialized.assert_called_once_with('publish123')
        return responses.pop(0)
    with app.app_context(), patch.object(client, 'access_token', return_value='access'), patch.object(
        client, 'post', return_value={'publish_id': 'publish123', 'upload_url': 'https://upload.us.tiktokapis.com/video/'}), patch.object(
        client.requests, 'put', side_effect=transmit) as put:
        assert client.upload_video(str(video), initialized) == 'publish123'
    assert put.call_args_list[0].kwargs['headers']['Content-Range'] == f'bytes 0-10485759/{video.stat().st_size}'
    assert len(put.call_args_list[1].kwargs['data']) == 11*1024**2


def test_api_error_is_not_treated_as_success():
    response = MagicMock()
    response.json.return_value = {'error': {'code': 'scope_not_authorized'}}
    with patch.object(client.requests, 'post', return_value=response), pytest.raises(ValueError):
        client.post('endpoint', 'secret', {})

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import sys

import pytest
from flask import Flask, current_app, g, jsonify, request

ROOT = Path(__file__).resolve().parents[1] / 'src/artifact_editor/publish/youtube'
spec = importlib.util.spec_from_file_location('youtube_upload', ROOT / 'upload.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
settings_spec = importlib.util.spec_from_file_location('youtube_settings', ROOT / 'settings.py')
settings = importlib.util.module_from_spec(settings_spec)
settings_spec.loader.exec_module(settings)


def values(**updates):
    return dict(title='Chapter One', description='A story', privacy_status='private',
                made_for_kids='false', tags='fiction, audiobook', category_id='', **updates)


def test_metadata_maps_all_form_values():
    body = module.upload_body(values())
    assert body == {'snippet': {'title': 'Chapter One', 'description': 'A story',
                              'categoryId': '24', 'tags': ['fiction', 'audiobook']},
                    'status': {'privacyStatus': 'private', 'license': 'youtube', 'selfDeclaredMadeForKids': False, 'containsSyntheticMedia': True},
                    'paidProductPlacementDetails': {'hasPaidProductPlacement': False}}
    data = values()
    data.update(category_id='10', privacy_status='unlisted', made_for_kids='true')
    body = module.upload_body(data)
    assert body['snippet']['categoryId'] == '10'
    assert body['status'] == {'privacyStatus': 'unlisted', 'license': 'youtube', 'selfDeclaredMadeForKids': True, 'containsSyntheticMedia': True}


@pytest.mark.parametrize('field,value', [('title', ''), ('title', 'x'*101),
    ('description', 'x'*5001), ('privacy_status', 'invalid'),
    ('made_for_kids', ''), ('category_id', 'abc')])
def test_invalid_metadata(field, value):
    data = values()
    data[field] = value
    with pytest.raises(ValueError):
        module.upload_body(data)


def test_resumable_upload_and_stream_cleanup():
    factory = MagicMock()
    youtube = MagicMock()
    youtube.videos.return_value.insert.return_value.next_chunk.side_effect = [
        (object(), None), (None, {'id': 'video123'})]
    with patch.dict(sys.modules, {'googleapiclient': SimpleNamespace(),
                                 'googleapiclient.http': SimpleNamespace(MediaFileUpload=factory)}):
        assert module.upload_video(youtube, '/chapter.mp4', {'snippet': {}}) == 'video123'
    assert factory.call_args.kwargs['resumable'] is True
    assert 'paidProductPlacementDetails' in youtube.videos.return_value.insert.call_args.kwargs['part'].split(',')
    factory.return_value.stream.return_value.close.assert_called_once()
    assert youtube.videos.return_value.insert.call_args.kwargs['body'] == {'snippet': {}}


def test_route_validation_and_saved_client(tmp_path):
    # Load the route in isolation from the editor's unrelated GPU/Redis dependencies.
    tree = ast.parse((ROOT / 'views.py').read_text())
    route = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'upload')
    route.decorator_list = []
    upload_video = MagicMock(return_value='video123')
    client_factory = MagicMock(return_value='saved-client')
    connected = MagicMock(return_value=True)
    namespace = dict(current_app=current_app, g=g, jsonify=jsonify, request=request,
                     connected=connected, get_youtube_client=client_factory,
                     upload_body=module.upload_body, upload_video=upload_video,
                     save_settings=settings.save_settings, os=__import__('os'))
    exec(compile(ast.Module(body=[route], type_ignores=[]), '<upload-route>', 'exec'), namespace)
    app = Flask(__name__)
    filename = tmp_path / 'chapter.mp4'
    chapter_config = {}
    save_config = MagicMock()
    @app.before_request
    def chapter():
        g.chapter = SimpleNamespace(get_video_filename=lambda: str(filename),
                                    config=chapter_config, save_config=save_config)
    app.add_url_rule('/upload', view_func=namespace['upload'], methods=['POST'])
    client = app.test_client()
    headers = {'X-Requested-With': 'XMLHttpRequest'}
    assert client.post('/upload', data=values()).status_code == 403
    assert client.post('/upload', data=values(), headers={**headers, 'Origin': 'https://other.test'}).status_code == 403
    connected.return_value = False
    assert client.post('/upload', data=values(), headers=headers).status_code == 401
    connected.return_value = True
    assert client.post('/upload', data=values(), headers=headers).status_code == 400
    upload_video.assert_not_called()
    filename.write_bytes(b'video')
    def transmit(*args):
        saved = settings.load_settings(SimpleNamespace(get_video_filename=lambda: str(filename)))
        assert saved['title'] == 'Chapter One'
        assert saved['license'] == 'youtube'
        return 'video123'
    upload_video.side_effect = transmit
    response = client.post('/upload', data=values(), headers=headers)
    assert response.json['video_id'] == 'video123'
    assert response.json['url'] == 'https://www.youtube.com/watch?v=video123'
    assert chapter_config['youtube'] == response.json['url']
    save_config.assert_called_once_with()
    assert upload_video.call_args.args[0] == 'saved-client'
    chapter_config['youtube'] = 'https://www.youtube.com/watch?v=previous'
    save_config.reset_mock()
    upload_video.side_effect = RuntimeError('sensitive detail')
    response = client.post('/upload', data=values(), headers=headers)
    assert response.status_code == 502
    assert b'sensitive detail' not in response.data
    assert chapter_config['youtube'].endswith('=previous')
    save_config.assert_not_called()
    upload_video.side_effect = None
    save_config.side_effect = OSError('disk full')
    response = client.post('/upload', data=values(), headers=headers)
    assert response.status_code == 200
    assert response.json['video_id'] == 'video123'
    assert 'badge could not be saved' in response.json['warning']


def test_ai_disclosure_defaults_to_yes_and_accepts_explicit_no():
    assert module.upload_body(values())['status']['containsSyntheticMedia'] is True
    data = values()
    data['ai_use'] = 'false'
    assert module.upload_body(data)['status']['containsSyntheticMedia'] is False
    data['ai_use'] = 'invalid'
    with pytest.raises(ValueError):
        module.upload_body(data)


def test_paid_promotion_defaults_to_no_and_accepts_yes():
    assert module.upload_body(values())['paidProductPlacementDetails']['hasPaidProductPlacement'] is False
    data = values()
    data['paid_promotion'] = 'true'
    assert module.upload_body(data)['paidProductPlacementDetails']['hasPaidProductPlacement'] is True
    data['paid_promotion'] = 'invalid'
    with pytest.raises(ValueError):
        module.upload_body(data)


def test_license_defaults_and_selection():
    assert module.upload_body(values())['status']['license'] == 'youtube'
    data = values()
    data['license'] = 'creativeCommon'
    assert module.upload_body(data)['status']['license'] == 'creativeCommon'
    data['license'] = 'invalid'
    with pytest.raises(ValueError):
        module.upload_body(data)


def test_settings_round_trip_all_fields(tmp_path):
    chapter = SimpleNamespace(get_video_filename=lambda: str(tmp_path / 'chapter.mp4'))
    data = values()
    data.update(title_category='poetry', title_format='poem', license='creativeCommon',
                ai_use='false', paid_promotion='true', made_for_kids='true', privacy_status='unlisted')
    settings.save_settings(chapter, data, module.upload_body(data))
    saved = settings.load_settings(chapter)
    assert set(saved) == set(settings.FIELDS)
    assert saved == {**data, 'category_id': '24'}
    assert (tmp_path / 'upload.json').exists()


def test_settings_missing_or_malformed(tmp_path):
    chapter = SimpleNamespace(get_video_filename=lambda: str(tmp_path / 'chapter.mp4'))
    assert settings.load_settings(chapter) == {}
    (tmp_path / 'upload.json').write_text('broken')
    assert settings.load_settings(chapter) == {}

import importlib.util
from pathlib import Path
from unittest.mock import patch, Mock
from urllib.parse import parse_qs, urlsplit

import httpx

spec = importlib.util.spec_from_file_location('comfy_launcher', Path(__file__).resolve().parents[1] / 'src/comfy_launcher.py')
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_ready_workflow_opens_directly_with_encoded_filename():
    with patch.object(launcher.httpx, 'get', return_value=Mock(status_code=200)) as get:
        url = launcher.workflow_destination('Fables #2 & image.json', 'http://comfyui:8188/',
            'http://localhost:8188/', 'http://localhost:8082/')
    get.assert_called_once_with('http://comfyui:8188/system_stats', timeout=2)
    assert urlsplit(url).netloc == 'localhost:8188'
    assert parse_qs(urlsplit(url).query) == {'workflow': ['Fables #2 & image.json']}


def test_stopped_or_starting_service_uses_home_loader_and_preserves_workflow():
    for result in (Mock(status_code=503), httpx.ConnectError('stopped')):
        with patch.object(launcher.httpx, 'get', side_effect=result if isinstance(result, Exception) else None,
                          return_value=result):
            url = launcher.workflow_destination('Aesop_25_2.json', 'http://comfyui:8188/',
                'http://localhost:8188/', 'http://myhost:8082/')
        parts = urlsplit(url)
        assert parts.netloc == 'myhost:8080'
        assert parts.path == '/programs/comfyui'
        assert parse_qs(parts.query)['workflow'] == ['Aesop_25_2.json']


def test_custom_home_origin():
    with patch.object(launcher.httpx, 'get', side_effect=httpx.ConnectError('stopped')):
        url = launcher.workflow_destination('test.json', 'http://comfyui:8188/',
            'http://localhost:8188/', 'http://localhost:8082/', 'https://home.example/')
    assert url.startswith('https://home.example/programs/comfyui?')

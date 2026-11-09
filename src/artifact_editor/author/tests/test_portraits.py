from io import BytesIO

import pytest
from PIL import Image

from artifact_editor.author import portraits


def test_download_creates_local_thumbnail_and_reuses_cache(tmp_path, monkeypatch):
    buffer = BytesIO()
    Image.new('RGB', (800, 1000), 'navy').save(buffer, format='PNG')
    class Response:
        is_redirect = False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
        def iter_content(self, size): yield buffer.getvalue()
    class Session:
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs): return Response()
    monkeypatch.setattr(portraits, 'public_url', lambda url: url)
    monkeypatch.setattr(portraits.requests, 'Session', Session)
    url = 'https://example.org/portrait.png'
    record = portraits.download_portrait(tmp_path, url)
    with Image.open(tmp_path / '.author-media' / f'{record}-thumb.jpg') as image:
        assert image.width <= 240 and image.height <= 320
    assert portraits.cached_portrait(tmp_path, url) == record
    monkeypatch.setattr(portraits.requests, 'Session', lambda: pytest.fail('Cached portrait was downloaded again'))
    assert portraits.download_portrait(tmp_path, url) == record


@pytest.mark.parametrize('url', ['http://localhost/image', 'http://127.0.0.1/image', 'http://169.254.169.254/image', 'file:///tmp/image'])
def test_private_and_non_web_portraits_rejected(url):
    with pytest.raises(ValueError):
        portraits.public_url(url)

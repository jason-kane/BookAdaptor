"""Download validated portraits and serve local image/thumbnail pairs."""
import hashlib
import ipaddress
import socket
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests
from PIL import Image, ImageOps, UnidentifiedImageError

from .profile import read_json, write_json, web_url

MAX_BYTES = 12 * 1024 * 1024


def public_url(url):
    web_url(url)
    parts = urlsplit(url)
    if parts.username or parts.password or parts.port not in (None, 80, 443):
        raise ValueError('Use a public HTTP or HTTPS image URL.')
    try:
        addresses = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == 'https' else 80))
    except OSError as error:
        raise ValueError('The portrait host could not be reached.') from error
    if not addresses or any(not ipaddress.ip_address(item[4][0]).is_global for item in addresses):
        raise ValueError('Use a publicly hosted portrait image.')
    return url


def cached_portrait(directory, url):
    folder = Path(directory) / '.author-media'
    record = read_json(folder / 'portraits.json').get(url, '')
    if len(record) == 64 and all(c in '0123456789abcdef' for c in record):
        if all((folder / f'{record}{suffix}.jpg').is_file() for suffix in ('', '-thumb')):
            return record
    return ''


def download_portrait(directory, url):
    if not url:
        raise ValueError('Enter a portrait image URL.')
    record = cached_portrait(directory, url)
    if record:
        return record
    try:
        with requests.Session() as session:
            session.headers['User-Agent'] = 'BookAdaptor/1.0 (author portrait download)'
            current = url
            for _ in range(5):
                public_url(current)
                with session.get(current, timeout=(3, 12), stream=True, allow_redirects=False) as response:
                    if response.is_redirect:
                        current = urljoin(current, response.headers['Location'])
                        continue
                    response.raise_for_status()
                    data = bytearray()
                    for chunk in response.iter_content(65536):
                        data.extend(chunk)
                        if len(data) > MAX_BYTES:
                            raise ValueError('Portrait images must be smaller than 12 MB.')
                    break
            else:
                raise ValueError('The portrait URL redirected too many times.')
        with Image.open(BytesIO(data)) as original:
            if original.width * original.height > 25_000_000:
                raise ValueError('Portrait images must be smaller than 25 megapixels.')
            image = ImageOps.exif_transpose(original).convert('RGB')
        record = hashlib.sha256(data).hexdigest()
        folder = Path(directory) / '.author-media'
        folder.mkdir(exist_ok=True)
        image.thumbnail((1600, 1600))
        image.save(folder / f'{record}.jpg', quality=90)
        image.thumbnail((240, 320))
        image.save(folder / f'{record}-thumb.jpg', quality=85)
        manifest = read_json(folder / 'portraits.json')
        manifest[url] = record
        write_json(folder / 'portraits.json', manifest)
        return record
    except (requests.RequestException, UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError('Could not download a valid portrait image. Check the image URL and try again.') from error

"""TikTok web OAuth and inbox upload transport for the local editor."""
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.parse import urlsplit

import requests
from flask import current_app

API = 'https://open.tiktokapis.com/v2/'


def directory():
    path = Path(current_app.config.get('TIKTOK_OAUTH_DIR') or os.environ.get(
        'TIKTOK_OAUTH_DIR', str(Path(__file__).resolve().parents[3] / '.tiktok-oauth')))
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def save(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, indent=2)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def config():
    data = json.loads((directory() / 'client_secret.json').read_text())
    callback = urlsplit(data['redirect_uri'])
    if not data['client_key'] or not data['client_secret'] or callback.scheme != 'https' or not callback.netloc or callback.path != '/oauth/tiktok/callback' or callback.query or callback.fragment:
        raise ValueError('Invalid TikTok app configuration')
    return data


def connected():
    try:
        return bool(json.loads((directory() / 'credentials.json').read_text()).get('refresh_token'))
    except (OSError, ValueError):
        return False


def token(**parameters):
    app = config()
    response = requests.post(API + 'oauth/token/', data={
        'client_key': app['client_key'], 'client_secret': app['client_secret'], **parameters}, timeout=30)
    response.raise_for_status()
    data = response.json()
    if not data.get('access_token') or not data.get('refresh_token') or 'video.upload' not in data.get('scope', '').split(','):
        raise ValueError('TikTok upload permission was not granted')
    data['expires_at'] = time.time() + data['expires_in']
    save(directory() / 'credentials.json', data)
    return data


def access_token():
    data = json.loads((directory() / 'credentials.json').read_text())
    if data.get('expires_at', 0) < time.time() + 60:
        data = token(grant_type='refresh_token', refresh_token=data['refresh_token'])
    return data['access_token']


def post(endpoint, access, body):
    response = requests.post(API + endpoint, headers={'Authorization': 'Bearer ' + access},
                             json=body, timeout=30)
    response.raise_for_status()
    result = response.json()
    if result.get('error', {}).get('code') != 'ok':
        raise ValueError('TikTok rejected the request')
    return result['data']


def chunk_plan(size):
    if size <= 0 or size > 4 * 1024**3:
        raise ValueError('Video must be nonempty and no larger than 4 GB.')
    chunk = min(size, 10 * 1024**2)
    return chunk, max(1, size // chunk)


def upload_video(filename, on_initialized):
    size = os.path.getsize(filename)
    chunk, count = chunk_plan(size)
    access = access_token()
    data = post('post/publish/inbox/video/init/', access, {'source_info': {
        'source': 'FILE_UPLOAD', 'video_size': size, 'chunk_size': chunk, 'total_chunk_count': count}})
    target = urlsplit(data['upload_url'])
    if target.scheme != 'https' or not (target.hostname or '').endswith('.tiktokapis.com') or target.username:
        raise ValueError('Unexpected TikTok upload address')
    on_initialized(data['publish_id'])
    with open(filename, 'rb') as stream:
        offset = 0
        for index in range(count):
            payload = stream.read(size - offset if index == count - 1 else chunk)
            response = requests.put(data['upload_url'], data=payload, headers={
                'Content-Type': 'video/mp4', 'Content-Length': str(len(payload)),
                'Content-Range': f'bytes {offset}-{offset + len(payload) - 1}/{size}'},
                timeout=180, allow_redirects=False)
            if response.status_code != (201 if index == count - 1 else 206):
                raise ValueError('TikTok video transfer failed')
            offset += len(payload)
    return data['publish_id']

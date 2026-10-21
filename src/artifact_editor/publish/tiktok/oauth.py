import hmac
import json
import re
import secrets
import time
from urllib.parse import urlencode, urlsplit

from flask import Blueprint, redirect, request
from . import client

bp = Blueprint('tiktok_oauth', __name__)
COOKIE = 'tiktok_oauth_state'


def start(return_url):
    try:
        config = client.config()
    except (OSError, ValueError, KeyError):
        return 'Configure TikTok Login Kit first; see publish/tiktok/README.md.', 503
    if urlsplit(config['redirect_uri']).netloc != request.host:
        return 'Open the editor using the HTTPS origin registered for TikTok before connecting.', 400
    state = secrets.token_urlsafe(32)
    client.save(client.directory() / (state + '.pending'), {'created': time.time(), 'return_url': return_url})
    response = redirect('https://www.tiktok.com/v2/auth/authorize/?' + urlencode({
        'client_key': config['client_key'], 'scope': 'video.upload', 'response_type': 'code',
        'redirect_uri': config['redirect_uri'], 'state': state}))
    response.set_cookie(COOKIE, state, max_age=600, secure=True, httponly=True,
                        samesite='Lax', path='/oauth/tiktok/callback')
    return response


@bp.get('/oauth/tiktok/callback')
def callback():
    state = request.args.get('state', '')
    cookie = request.cookies.get(COOKIE, '')
    if not re.fullmatch(r'[A-Za-z0-9_-]{43}', state) or not hmac.compare_digest(state, cookie):
        return 'Invalid TikTok authorization. Start again from the TikTok tab.', 400
    try:
        path = client.directory() / (state + '.pending')
        data = json.loads(path.read_text())
        path.unlink()
        if time.time() - data['created'] > 600:
            raise ValueError('Expired')
    except (OSError, ValueError, KeyError):
        return 'TikTok authorization expired. Start again.', 400
    result = 'denied'
    if request.args.get('code') and not request.args.get('error'):
        try:
            client.token(grant_type='authorization_code', code=request.args['code'],
                         redirect_uri=client.config()['redirect_uri'])
            result = 'connected'
        except Exception:
            result = 'failed'
    response = redirect(data['return_url'] + '?tiktok_connection=' + result)
    response.delete_cookie(COOKIE, path='/oauth/tiktok/callback', secure=True, samesite='Lax')
    return response

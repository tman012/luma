"""One Trakt GET for every caller: Trakt's required headers (trakt-api-version 2, trakt-api-key, JSON
content type, and an identifying User-Agent, which Trakt asks every app to send and which its Cloudflare
front may otherwise block), and a WARNING log line whenever the answer isn't 200 or the request raises,
so a Trakt failure is never silent. The client ID travels only in its header and is never logged."""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)

API = 'https://api.trakt.tv'
USER_AGENT = 'Luma/1.0 (+https://github.com/tman012/luma)'


def headers(client_id: str) -> Dict[str, str]:
    return {'Content-Type': 'application/json', 'trakt-api-version': '2', 'trakt-api-key': client_id,
            'User-Agent': USER_AGENT}


def get(url: str, client_id: str, params: Optional[Dict[str, Any]] = None, timeout: float = 10, **kw):
    """requests.get with Trakt's headers; logs (path and status only) on a non-200 or an exception, then
    returns the response or re-raises, so each caller keeps its own handling."""
    if not url.startswith('http'):
        url = API + url
    path = urlsplit(url).path
    try:
        resp = requests.get(url, headers=headers(client_id), params=params, timeout=timeout, **kw)
    except Exception as e:
        logger.warning('Trakt %s failed: %s', path, type(e).__name__)
        raise
    if resp.status_code != 200:
        logger.warning('Trakt %s: HTTP %s', path, resp.status_code)
    return resp

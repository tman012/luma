"""Small OpenAI REST client (chat completions and model lookup).

Luma talks to the OpenAI HTTP API with ``requests`` instead of the ``openai``
package, so no extra dependency is needed. The object shapes mirror the parts
of the official client that Luma uses: ``client.chat.completions.create(...)``
returns ``.choices[i].message`` / ``.finish_reason``, and
``client.models.retrieve(name)`` returns ``.id``.
"""

import os
from typing import Any, Dict

import requests

DEFAULT_BASE_URL = 'https://api.openai.com/v1'


class APIError(Exception):
    status = 0


class AuthenticationError(APIError):
    status = 401


class NotFoundError(APIError):
    status = 404


class RateLimitError(APIError):
    status = 429


class Obj(dict):
    """dict with attribute access; nested dicts and lists are wrapped too.

    Being a dict, a message can be sent straight back in the next request."""

    def __getattr__(self, name):
        try:
            return _wrap(self[name])
        except KeyError:
            return None


def _wrap(v):
    if isinstance(v, dict) and not isinstance(v, Obj):
        return Obj(v)
    if isinstance(v, list):
        return [_wrap(x) for x in v]
    return v


def _raise_for(resp) -> None:
    if resp.status_code < 400:
        return
    try:
        msg = (resp.json().get('error') or {}).get('message') or resp.text
    except ValueError:
        msg = resp.text
    cls = {401: AuthenticationError, 404: NotFoundError, 429: RateLimitError}.get(resp.status_code, APIError)
    err = cls(f'{resp.status_code}: {msg}')
    err.status = resp.status_code
    raise err


class _Completions:
    def __init__(self, client):
        self._c = client

    def create(self, timeout: float = 60, **kwargs: Any) -> Obj:
        return self._c._post('/chat/completions', kwargs, timeout)


class _Chat:
    def __init__(self, client):
        self.completions = _Completions(client)


class _Models:
    def __init__(self, client):
        self._c = client

    def retrieve(self, model: str) -> Obj:
        return self._c._get(f'/models/{model}', 15)


class OpenAI:
    def __init__(self, api_key: str, max_retries: int = 0, base_url: str = ''):
        self.api_key = api_key
        self.base_url = (base_url or os.environ.get('OPENAI_BASE_URL') or DEFAULT_BASE_URL).rstrip('/')
        self.chat = _Chat(self)
        self.models = _Models(self)

    def _headers(self) -> Dict[str, str]:
        return {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}

    def _post(self, path: str, body: Dict[str, Any], timeout: float) -> Obj:
        resp = requests.post(self.base_url + path, json=body, headers=self._headers(), timeout=timeout)
        _raise_for(resp)
        return _wrap(resp.json())

    def _get(self, path: str, timeout: float) -> Obj:
        resp = requests.get(self.base_url + path, headers=self._headers(), timeout=timeout)
        _raise_for(resp)
        return _wrap(resp.json())

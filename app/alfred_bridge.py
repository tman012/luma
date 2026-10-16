"""
Alfred Bridge — forwards complex voice commands to the Alfred agent.

Alfred agent HTTP API bridge.
Handles communication, timeout, and response normalization.
"""

import logging
import requests
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Default structured response when Alfred is unreachable
_FALLBACK_RESPONSE: Dict[str, Any] = {
    "type": "error",
    "status": "error",
    "message": "Alfred is not available right now. Please try again later.",
    "data": {},
}


class AlfredBridge:
    """Client for the Alfred agent API."""

    def __init__(self, base_url: str, api_key: Optional[str] = None,
                 timeout: int = 60):
        self.base_url = base_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self._session = requests.Session()
        if api_key:
            self._session.headers['Authorization'] = f'Bearer {api_key}'
        self._session.headers['Content-Type'] = 'application/json'

    def send_command(self, transcript: str,
                     context: Optional[Dict[str, Any]] = None,
                     history: Optional[list] = None) -> Dict[str, Any]:
        """Send a command to Alfred and return structured JSON.

        Args:
            transcript: The user's natural-language command.
            context: Optional UI context (current page, active modal, etc.).
            history: Optional conversation history for multi-turn context.

        Returns:
            Structured response dict matching the AlfredResponse contract.
        """
        payload: Dict[str, Any] = {
            'command': transcript,
            'source': 'luma_voice',
        }
        if context:
            payload['context'] = context
        if history:
            payload['history'] = history

        try:
            resp = self._session.post(
                f'{self.base_url}/api/command',
                json=payload,
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            # Normalize: ensure required fields exist
            return {
                'type': data.get('type', 'generic'),
                'status': data.get('status', 'ok'),
                'message': data.get('message', ''),
                'data': data.get('data', {}),
                'suggested_ui_action': data.get('suggested_ui_action'),
            }

        except requests.Timeout:
            logger.warning('Alfred request timed out after %ds', self.timeout)
            return {
                **_FALLBACK_RESPONSE,
                'message': 'Alfred took too long to respond.',
            }
        except requests.ConnectionError:
            logger.warning('Cannot connect to Alfred at %s', self.base_url)
            return _FALLBACK_RESPONSE.copy()
        except requests.HTTPError as e:
            logger.error('Alfred returned HTTP %s: %s', e.response.status_code,
                         e.response.text[:200] if e.response else '')
            return {
                'type': 'error',
                'status': 'error',
                'message': f'Alfred returned an error ({e.response.status_code}).',
                'data': {},
            }
        except Exception as e:
            logger.error('Alfred bridge error: %s', e)
            return _FALLBACK_RESPONSE.copy()

    def health_check(self) -> Dict[str, Any]:
        """Check if Alfred is reachable."""
        try:
            resp = self._session.get(
                f'{self.base_url}/api/health',
                timeout=5,
            )
            resp.raise_for_status()
            return {'available': True, **resp.json()}
        except Exception:
            return {'available': False}

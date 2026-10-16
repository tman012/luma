"""Quick-response handlers — instant answers that don't need an LLM or API."""

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('America/New_York')


def handle_time() -> dict:
    now = datetime.now(TZ)
    spoken = now.strftime("It's %-I:%M %p")
    return {
        'type': 'quick',
        'status': 'ok',
        'message': spoken,
        'data': {'time': now.isoformat()},
    }


def handle_date() -> dict:
    now = datetime.now(TZ)
    spoken = now.strftime("Today is %A, %B %-d, %Y")
    return {
        'type': 'quick',
        'status': 'ok',
        'message': spoken,
        'data': {'date': now.date().isoformat()},
    }

"""TV-specific handlers: calendar, next-episode, season ownership."""

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests

from config import SONARR_URL, SONARR_API_KEY
from handlers.title_match import best_match

logger = logging.getLogger(__name__)

FLASK_BASE = os.getenv('FLASK_BASE', 'http://localhost:5001')
SERVICE_API_KEY = os.getenv('SERVICE_API_KEY', '')


def _auth_headers() -> Dict[str, str]:
    return {'X-API-Key': SERVICE_API_KEY} if SERVICE_API_KEY else {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fetch_calendar(days: int = 14, limit: int = 24) -> List[Dict[str, Any]]:
    """Fetch the Sonarr calendar via Luma."""
    try:
        resp = requests.get(
            f'{FLASK_BASE}/api/sonarr/calendar',
            headers=_auth_headers(),
            params={'days': days, 'limit': limit},
            timeout=12,
        )
        resp.raise_for_status()
        body = resp.json() or {}
        return body.get('episodes', []) or []
    except Exception as e:
        logger.warning('Sonarr calendar fetch failed: %s', e)
        return []


def _period_to_window(period: Optional[str]) -> Tuple[datetime, datetime, str]:
    """Resolve a spoken period into (window_start, window_end, friendly_label)."""
    now = datetime.now(timezone.utc)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tomorrow = today + timedelta(days=1)

    p = (period or '').lower().strip()
    if p in ('tonight', 'today'):
        return today, tomorrow, 'today'
    if p in ('tomorrow', 'tomorrow night'):
        return tomorrow, tomorrow + timedelta(days=1), 'tomorrow'
    if p in ('yesterday', 'last night'):
        yesterday = today - timedelta(days=1)
        return yesterday, today, 'yesterday'
    if p in ('this week', 'the week'):
        return today, today + timedelta(days=7), 'this week'
    if p in ('next week',):
        return today + timedelta(days=7), today + timedelta(days=14), 'next week'
    if p in ('this weekend', 'the weekend'):
        # Saturday + Sunday of the current calendar week
        weekday = today.weekday()  # 0=Mon
        days_until_sat = (5 - weekday) % 7
        sat = today + timedelta(days=days_until_sat)
        return sat, sat + timedelta(days=2), 'the weekend'
    # Default: next 14 days
    return today, today + timedelta(days=14), 'the next two weeks'


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except Exception:
        return None


def _normalize(s: str) -> str:
    s = re.sub(r'^the\s+', '', (s or '').lower().strip())
    return re.sub(r'\s+', ' ', s)


def _sonarr_lookup(title: str) -> Optional[Dict[str, Any]]:
    """Resolve a spoken show title to a Sonarr series record (incl. tvdbId)."""
    if not (SONARR_URL and SONARR_API_KEY):
        return None
    try:
        resp = requests.get(
            f'{SONARR_URL}/api/v3/series/lookup',
            params={'term': title},
            headers={'X-Api-Key': SONARR_API_KEY},
            timeout=10,
        )
        resp.raise_for_status()
        results = resp.json() or []
    except Exception as e:
        logger.warning('Sonarr lookup for "%s" failed: %s', title, e)
        return None
    if not results:
        return None
    # Adapt to title_match input shape
    candidates = [{
        'title': r.get('title', ''),
        'year': r.get('year'),
        'tmdb_id': r.get('tmdbId'),
        'tvdb_id': r.get('tvdbId'),
        'popularity': r.get('popularity', 0),
    } for r in results]
    pick = best_match(title, candidates)
    if not pick:
        return None
    # Return the full original Sonarr record for whichever candidate matched
    for r in results:
        if r.get('tvdbId') == pick.get('tvdb_id'):
            return r
    return results[0]


def _fetch_episode_ownership(tvdb_id: int, title: Optional[str] = None,
                             year: Optional[int] = None) -> Dict[str, Any]:
    try:
        params = {}
        if title:
            params['title'] = title
        if year:
            params['year'] = year
        resp = requests.get(
            f'{FLASK_BASE}/api/tv/ownership/{tvdb_id}/cached',
            headers=_auth_headers(),
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json() or {}
    except Exception as e:
        logger.warning('TV ownership fetch failed for tvdb=%s: %s', tvdb_id, e)
        return {}


def _series_title_matches(query: str, candidate: str) -> bool:
    nq = _normalize(query)
    nc = _normalize(candidate)
    if not nq or not nc:
        return False
    return nq == nc or nq in nc or nc in nq


def _format_weekday(dt: datetime) -> str:
    # Compare against today for friendly labels
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    day = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    delta = (day - today).days
    if delta == 0:
        return 'today'
    if delta == 1:
        return 'tomorrow'
    if -6 < delta < 0:
        return 'last ' + dt.strftime('%A')
    if 0 < delta < 7:
        return dt.strftime('%A')
    return dt.strftime('%A, %b %-d')


def _format_time(dt: datetime) -> str:
    # Convert to local-ish (ET approximation — same convention used elsewhere)
    local = dt - timedelta(hours=4)
    return local.strftime('%-I:%M %p').strip().lstrip('0').replace(' 0', ' ')


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle_tv_airing(period: Optional[str] = None) -> Dict[str, Any]:
    """Answer 'what's airing tonight/this week/last night'."""
    start, end, label = _period_to_window(period)

    # For a "last night" / yesterday window Sonarr's forward-looking calendar
    # isn't useful; we'd need /episodes?date=... but Sonarr's calendar does
    # historical too via start/end params. The /api/sonarr/calendar endpoint
    # is forward-only — warn the user honestly when they ask for history.
    now = datetime.now(timezone.utc)
    if end < now:
        return {
            'type': 'tv_airing',
            'status': 'ok',
            'message': (f"I can't look back at {label} through the Sonarr calendar — "
                        "try watch history instead."),
            'data': {'period': label},
        }

    # Always fetch a 14-day window, then filter — Sonarr's days param covers N
    # days forward from today, so short windows need local filtering to remove
    # items that fall outside the user's actual intent.
    days = max(1, int((end - now).days) + 1)
    episodes = _fetch_calendar(days=max(days, 1), limit=24)

    def in_window(ep: Dict[str, Any]) -> bool:
        t = _parse_iso(ep.get('air_date_utc'))
        if not t:
            return False
        return start <= t <= end

    hits = [ep for ep in episodes if in_window(ep)]
    if not hits:
        return {
            'type': 'tv_airing',
            'status': 'ok',
            'message': f"Nothing new airing {label}.",
            'data': {'count': 0, 'period': label},
            'suggested_ui_action': {'action': 'navigate', 'path': '/daily'},
        }

    # Build a voice summary — top 3
    parts: List[str] = []
    for ep in hits[:3]:
        series = ep.get('series_title') or 'Unknown'
        s = ep.get('season')
        e = ep.get('episode')
        t = _parse_iso(ep.get('air_date_utc'))
        when = _format_time(t) if t else ''
        ep_ref = f"S{s}E{e}" if s and e else ''
        when_hint = f" at {when}" if when else ''
        parts.append(f"{series} {ep_ref}{when_hint}".strip())
    summary = '; '.join(parts)
    count = len(hits)
    if count <= 3:
        msg = f"Airing {label}: {summary}."
    else:
        msg = f"{count} airing {label} — including {summary}."
    msg = msg[:260]

    return {
        'type': 'tv_airing',
        'status': 'ok',
        'message': msg,
        'data': {
            'count': count,
            'period': label,
            'episodes': [{
                'series_title': ep.get('series_title'),
                'season': ep.get('season'),
                'episode': ep.get('episode'),
                'episode_title': ep.get('episode_title'),
                'air_date_utc': ep.get('air_date_utc'),
                'network': ep.get('network'),
                'tvdb_id': ep.get('tvdb_id'),
            } for ep in hits[:20]],
        },
        'suggested_ui_action': {'action': 'navigate', 'path': '/daily'},
    }


def handle_tv_next_episode(title: str) -> Dict[str, Any]:
    """Answer 'when's the next episode of X' / 'when does X return'."""
    if not title:
        return {
            'type': 'tv_next_episode',
            'status': 'error',
            'message': "Which show should I check?",
            'data': {},
        }

    # Sonarr's calendar already has series_title strings; scan the 30-day window
    episodes = _fetch_calendar(days=30, limit=24)
    matching = [ep for ep in episodes if _series_title_matches(title, ep.get('series_title', ''))]

    if matching:
        nxt = matching[0]
        series = nxt.get('series_title', title)
        s = nxt.get('season')
        e = nxt.get('episode')
        t = _parse_iso(nxt.get('air_date_utc'))
        when_day = _format_weekday(t) if t else 'soon'
        when_time = _format_time(t) if t else ''
        when_hint = f" at {when_time}" if when_time else ''
        ep_ref = f"S{s}E{e}" if s and e else 'a new episode'
        msg = f"Next up for {series}: {ep_ref} {when_day}{when_hint}."
        return {
            'type': 'tv_next_episode',
            'status': 'ok',
            'message': msg,
            'data': {
                'series_title': series,
                'season': s,
                'episode': e,
                'episode_title': nxt.get('episode_title'),
                'air_date_utc': nxt.get('air_date_utc'),
                'network': nxt.get('network'),
                'tvdb_id': nxt.get('tvdb_id'),
            },
        }

    # Nothing in the calendar — report status based on whether Sonarr monitors it
    series_record = _sonarr_lookup(title)
    if series_record:
        name = series_record.get('title', title)
        status = (series_record.get('status') or '').lower()
        if status == 'continuing':
            msg = (f"No upcoming episodes of {name} in the next 30 days — it's continuing "
                   "but the next airdate isn't set.")
        elif status == 'ended':
            msg = f"{name} has ended — no more episodes coming."
        else:
            msg = f"No upcoming episodes of {name} in the next 30 days."
        return {
            'type': 'tv_next_episode',
            'status': 'ok',
            'message': msg,
            'data': {'series_title': name, 'series_status': status},
        }

    return {
        'type': 'tv_next_episode',
        'status': 'ok',
        'message': f"I couldn't find a show called \"{title}\" on your calendar.",
        'data': {'title': title},
    }


def handle_tv_season_ownership(title: str, season: Optional[int] = None) -> Dict[str, Any]:
    """Answer 'do I have all of season N of X' / 'which episodes am I missing'."""
    if not title:
        return {
            'type': 'tv_season_ownership',
            'status': 'error',
            'message': "Which show should I check?",
            'data': {},
        }

    series = _sonarr_lookup(title)
    if not series or not series.get('tvdbId'):
        return {
            'type': 'tv_season_ownership',
            'status': 'ok',
            'message': f"I couldn't resolve \"{title}\" — is that the right name?",
            'data': {'title': title},
        }

    tvdb_id = int(series['tvdbId'])
    display_title = series.get('title', title)
    year = series.get('year')
    sonarr_seasons = series.get('seasons') or []

    ownership_body = _fetch_episode_ownership(tvdb_id, title=display_title, year=year)
    owned_keys = set((ownership_body.get('episodes') or {}).keys())

    # Helper — which episodes exist per season (per Sonarr)
    def _expected_episodes_for_season(sn: int) -> int:
        for s in sonarr_seasons:
            if s.get('seasonNumber') == sn:
                return int((s.get('statistics') or {}).get('totalEpisodeCount') or 0)
        return 0

    if season is None:
        # Global summary: count owned per season
        by_season: Dict[int, int] = {}
        for k in owned_keys:
            try:
                sn = int(k.split('e')[0][1:])
            except Exception:
                continue
            by_season[sn] = by_season.get(sn, 0) + 1

        if not by_season:
            return {
                'type': 'tv_season_ownership',
                'status': 'ok',
                'message': f"I don't see any episodes of {display_title} in your library.",
                'data': {'title': display_title, 'tvdb_id': tvdb_id, 'owned_by_season': {}},
            }

        total_owned = sum(by_season.values())
        seasons_sorted = sorted(by_season.keys())
        season_bits = []
        for sn in seasons_sorted[:4]:
            expected = _expected_episodes_for_season(sn)
            got = by_season[sn]
            if expected:
                season_bits.append(f"S{sn}: {got}/{expected}")
            else:
                season_bits.append(f"S{sn}: {got}")
        msg = f"You have {total_owned} episodes of {display_title} — {', '.join(season_bits)}."
        return {
            'type': 'tv_season_ownership',
            'status': 'ok',
            'message': msg,
            'data': {
                'title': display_title,
                'tvdb_id': tvdb_id,
                'total_owned': total_owned,
                'owned_by_season': by_season,
            },
            'suggested_ui_action': {
                'action': 'open_modal',
                'media_type': 'tv',
                'tmdb_id': series.get('tmdbId'),
            } if series.get('tmdbId') else None,
        }

    # Specific season requested
    sn = int(season)
    expected = _expected_episodes_for_season(sn)
    owned_in_season = [int(k.split('e')[1]) for k in owned_keys
                       if k.startswith(f's{sn}e')]
    owned_in_season.sort()
    owned_count = len(owned_in_season)

    if expected:
        if owned_count >= expected:
            msg = f"Yes — all {expected} episodes of season {sn} of {display_title} are in your library."
        elif owned_count == 0:
            msg = f"Season {sn} of {display_title} isn't in your library yet."
        else:
            missing = sorted(set(range(1, expected + 1)) - set(owned_in_season))
            missing_fmt = ', '.join(f"episode {m}" for m in missing[:6])
            more = '' if len(missing) <= 6 else f" (+{len(missing) - 6} more)"
            msg = (f"{owned_count} of {expected} episodes of season {sn} of {display_title} "
                   f"in your library — missing {missing_fmt}{more}.")
    else:
        msg = f"You have {owned_count} episode{'s' if owned_count != 1 else ''} of season {sn} of {display_title}."

    return {
        'type': 'tv_season_ownership',
        'status': 'ok',
        'message': msg,
        'data': {
            'title': display_title,
            'tvdb_id': tvdb_id,
            'season': sn,
            'owned_count': owned_count,
            'expected_count': expected,
            'missing_episodes': sorted(set(range(1, expected + 1)) - set(owned_in_season)) if expected else [],
        },
        'suggested_ui_action': {
            'action': 'open_modal',
            'media_type': 'tv',
            'tmdb_id': series.get('tmdbId'),
            'season': sn,
        } if series.get('tmdbId') else None,
    }

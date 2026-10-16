"""Sports handler — live scores, schedules, and events via ESPN's free API."""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

# ESPN scoreboard endpoints (free, no auth required)
_ESPN_ENDPOINTS = {
    'mlb': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/scoreboard',
    'nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard',
    'nba': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard',
    'nhl': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard',
    'ncaaf': 'https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard',
    'ncaab': 'https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/scoreboard',
    'mls': 'https://site.api.espn.com/apis/site/v2/sports/soccer/usa.1/scoreboard',
    'epl': 'https://site.api.espn.com/apis/site/v2/sports/soccer/eng.1/scoreboard',
}

# Common team name aliases → (league, ESPN team name substring)
_TEAM_ALIASES = {
    # MLB
    'orioles': ('mlb', 'BAL'), 'o\'s': ('mlb', 'BAL'), 'baltimore': ('mlb', 'BAL'),
    'yankees': ('mlb', 'NYY'), 'yanks': ('mlb', 'NYY'),
    'red sox': ('mlb', 'BOS'), 'sox': ('mlb', 'BOS'),
    'mets': ('mlb', 'NYM'), 'phillies': ('mlb', 'PHI'), 'phils': ('mlb', 'PHI'),
    'braves': ('mlb', 'ATL'), 'nationals': ('mlb', 'WSH'), 'nats': ('mlb', 'WSH'),
    'blue jays': ('mlb', 'TOR'), 'jays': ('mlb', 'TOR'),
    'rays': ('mlb', 'TB'), 'marlins': ('mlb', 'MIA'),
    'cubs': ('mlb', 'CHC'), 'white sox': ('mlb', 'CHW'),
    'cardinals': ('mlb', 'STL'), 'cards': ('mlb', 'STL'),
    'reds': ('mlb', 'CIN'), 'brewers': ('mlb', 'MIL'),
    'pirates': ('mlb', 'PIT'), 'tigers': ('mlb', 'DET'),
    'twins': ('mlb', 'MIN'), 'guardians': ('mlb', 'CLE'),
    'royals': ('mlb', 'KC'), 'astros': ('mlb', 'HOU'),
    'rangers': ('mlb', 'TEX'), 'athletics': ('mlb', 'ATH'), 'a\'s': ('mlb', 'ATH'),
    'angels': ('mlb', 'LAA'), 'mariners': ('mlb', 'SEA'),
    'padres': ('mlb', 'SD'), 'giants': ('mlb', 'SF'),
    'dodgers': ('mlb', 'LAD'), 'rockies': ('mlb', 'COL'),
    'diamondbacks': ('mlb', 'ARI'), 'd-backs': ('mlb', 'ARI'),
    # NFL
    'ravens': ('nfl', 'BAL'), 'steelers': ('nfl', 'PIT'),
    'bengals': ('nfl', 'CIN'), 'browns': ('nfl', 'CLE'),
    'bills': ('nfl', 'BUF'), 'dolphins': ('nfl', 'MIA'),
    'patriots': ('nfl', 'NE'), 'pats': ('nfl', 'NE'),
    'jets': ('nfl', 'NYJ'), 'texans': ('nfl', 'HOU'),
    'colts': ('nfl', 'IND'), 'jaguars': ('nfl', 'JAX'), 'jags': ('nfl', 'JAX'),
    'titans': ('nfl', 'TEN'), 'chiefs': ('nfl', 'KC'),
    'chargers': ('nfl', 'LAC'), 'broncos': ('nfl', 'DEN'),
    'raiders': ('nfl', 'LV'), 'cowboys': ('nfl', 'DAL'),
    'eagles': ('nfl', 'PHI'), 'commanders': ('nfl', 'WSH'),
    'packers': ('nfl', 'GB'), 'bears': ('nfl', 'CHI'),
    'lions': ('nfl', 'DET'), 'vikings': ('nfl', 'MIN'),
    'falcons': ('nfl', 'ATL'), 'panthers': ('nfl', 'CAR'),
    'saints': ('nfl', 'NO'), 'buccaneers': ('nfl', 'TB'), 'bucs': ('nfl', 'TB'),
    '49ers': ('nfl', 'SF'), 'niners': ('nfl', 'SF'),
    'seahawks': ('nfl', 'SEA'), 'rams': ('nfl', 'LAR'),
    # NBA
    'celtics': ('nba', 'BOS'), 'nets': ('nba', 'BKN'),
    'knicks': ('nba', 'NY'), 'sixers': ('nba', 'PHI'), '76ers': ('nba', 'PHI'),
    'raptors': ('nba', 'TOR'), 'cavaliers': ('nba', 'CLE'), 'cavs': ('nba', 'CLE'),
    'pistons': ('nba', 'DET'), 'pacers': ('nba', 'IND'),
    'bucks': ('nba', 'MIL'), 'bulls': ('nba', 'CHI'),
    'heat': ('nba', 'MIA'), 'magic': ('nba', 'ORL'),
    'hawks': ('nba', 'ATL'), 'hornets': ('nba', 'CHA'),
    'wizards': ('nba', 'WSH'), 'nuggets': ('nba', 'DEN'),
    'timberwolves': ('nba', 'MIN'), 'wolves': ('nba', 'MIN'),
    'thunder': ('nba', 'OKC'), 'trail blazers': ('nba', 'POR'), 'blazers': ('nba', 'POR'),
    'jazz': ('nba', 'UTAH'), 'warriors': ('nba', 'GS'),
    'clippers': ('nba', 'LAC'), 'lakers': ('nba', 'LAL'),
    'suns': ('nba', 'PHX'), 'kings': ('nba', 'SAC'),
    'spurs': ('nba', 'SA'), 'mavericks': ('nba', 'DAL'), 'mavs': ('nba', 'DAL'),
    'rockets': ('nba', 'HOU'), 'grizzlies': ('nba', 'MEM'),
    'pelicans': ('nba', 'NO'),
    # NHL
    'capitals': ('nhl', 'WSH'), 'caps': ('nhl', 'WSH'),
    'penguins': ('nhl', 'PIT'), 'pens': ('nhl', 'PIT'),
    'flyers': ('nhl', 'PHI'), 'islanders': ('nhl', 'NYI'),
    'devils': ('nhl', 'NJ'), 'bruins': ('nhl', 'BOS'),
    'canadiens': ('nhl', 'MTL'), 'habs': ('nhl', 'MTL'),
    'senators': ('nhl', 'OTT'), 'sens': ('nhl', 'OTT'),
    'maple leafs': ('nhl', 'TOR'), 'leafs': ('nhl', 'TOR'),
    'sabres': ('nhl', 'BUF'), 'hurricanes': ('nhl', 'CAR'), 'canes': ('nhl', 'CAR'),
    'blue jackets': ('nhl', 'CBJ'), 'red wings': ('nhl', 'DET'),
    'lightning': ('nhl', 'TB'), 'panthers': ('nhl', 'FLA'),
    'predators': ('nhl', 'NSH'), 'preds': ('nhl', 'NSH'),
    'blackhawks': ('nhl', 'CHI'),
    'wild': ('nhl', 'MIN'), 'blues': ('nhl', 'STL'),
    'jets': ('nhl', 'WPG'), 'avalanche': ('nhl', 'COL'), 'avs': ('nhl', 'COL'),
    'stars': ('nhl', 'DAL'), 'flames': ('nhl', 'CGY'),
    'oilers': ('nhl', 'EDM'), 'canucks': ('nhl', 'VAN'),
    'kraken': ('nhl', 'SEA'), 'golden knights': ('nhl', 'VGK'),
    'coyotes': ('nhl', 'UTA'), 'ducks': ('nhl', 'ANA'),
    'sharks': ('nhl', 'SJ'),
}


def _fetch_scoreboard(league: str) -> Optional[Dict]:
    """Fetch today's scoreboard from ESPN."""
    url = _ESPN_ENDPOINTS.get(league)
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error('ESPN %s fetch failed: %s', league, e)
        return None


def _find_team_game(data: Dict, abbr: str) -> Optional[Dict]:
    """Find a game involving the given team abbreviation."""
    for event in data.get('events', []):
        for comp in event.get('competitions', []):
            for team in comp.get('competitors', []):
                if team.get('team', {}).get('abbreviation', '').upper() == abbr.upper():
                    return event
    return None


def _format_game(event: Dict) -> str:
    """Format a game event into a TTS-friendly string."""
    comp = event.get('competitions', [{}])[0]
    teams = comp.get('competitors', [])
    status = event.get('status', {}).get('type', {})
    detail = event.get('status', {}).get('displayClock', '')
    period = status.get('detail', '')
    state = status.get('state', '')

    if len(teams) != 2:
        return "I found the game but couldn't read the score."

    # ESPN: index 0 = home, index 1 = away
    home = teams[0]
    away = teams[1]
    home_name = home.get('team', {}).get('shortDisplayName', 'Home')
    away_name = away.get('team', {}).get('shortDisplayName', 'Away')
    home_score = home.get('score', '0')
    away_score = away.get('score', '0')

    if state == 'in':
        # Game in progress
        return f"The {away_name} and {home_name} are {away_score} to {home_score}, {period}."
    elif state == 'post':
        # Game finished
        h = int(home_score)
        a = int(away_score)
        if h > a:
            return f"The {home_name} beat the {away_name} {h} to {a}."
        elif a > h:
            return f"The {away_name} beat the {home_name} {a} to {h}."
        else:
            return f"The {home_name} and {away_name} tied {h} to {a}."
    else:
        # Scheduled / pre-game
        return f"The {away_name} play the {home_name} later today — {period}."


def handle_score(team: str) -> Dict[str, Any]:
    """Look up the live score for a team."""
    team_lower = team.lower().strip()

    # Try to resolve team alias
    match = _TEAM_ALIASES.get(team_lower)
    if not match:
        # Try partial matching
        for alias, info in _TEAM_ALIASES.items():
            if alias in team_lower or team_lower in alias:
                match = info
                break

    if not match:
        return {
            'type': 'score',
            'status': 'error',
            'message': f"I don't recognize the team \"{team}\". Try using the team name like Orioles, Ravens, or Lakers.",
            'data': {},
        }

    league, abbr = match
    data = _fetch_scoreboard(league)
    if not data:
        return {
            'type': 'score',
            'status': 'error',
            'message': f"I couldn't reach ESPN for {league.upper()} scores right now.",
            'data': {},
        }

    game = _find_team_game(data, abbr)
    if not game:
        # No game today — look up the next one so the answer is actually useful.
        # "Do the Orioles play today?" → "No, but they play the Giants tomorrow at 7:15."
        next_suffix = ''
        try:
            schedule = _fetch_team_schedule(league, abbr)
            if schedule:
                upcoming = _find_next_game(schedule)
                if upcoming:
                    when = _format_time_et(upcoming.get('date', ''))
                    comps = (upcoming.get('competitions') or [{}])[0]
                    teams_arr = comps.get('competitors') or []
                    home = next((c for c in teams_arr if c.get('homeAway') == 'home'), {})
                    away = next((c for c in teams_arr if c.get('homeAway') == 'away'), {})
                    home_name = home.get('team', {}).get('shortDisplayName', 'home')
                    away_name = away.get('team', {}).get('shortDisplayName', 'away')
                    is_home = home.get('team', {}).get('abbreviation', '').lower() == abbr.lower()
                    opponent = away_name if is_home else home_name
                    verb = 'host' if is_home else 'play'
                    next_suffix = f" Next game: they {verb} the {opponent} {when}."
        except Exception:
            logger.exception('Failed to look up next game for %s', team)
        return {
            'type': 'score',
            'status': 'ok',
            'message': f"The {team.title()} don't have a game today.{next_suffix}",
            'data': {},
        }

    message = _format_game(game)
    return {
        'type': 'score',
        'status': 'ok',
        'message': message,
        'data': {
            'league': league,
            'team': abbr,
        },
    }


# ── ESPN base URLs for team schedules ──

_ESPN_SCHEDULE_BASES = {
    'mlb': 'https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams',
    'nfl': 'https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams',
    'nba': 'https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams',
    'nhl': 'https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/teams',
}


def _fetch_team_schedule(league: str, abbr: str) -> Optional[List[Dict]]:
    """Fetch a team's schedule from ESPN."""
    base = _ESPN_SCHEDULE_BASES.get(league)
    if not base:
        return None
    try:
        resp = requests.get(
            f'{base}/{abbr.lower()}/schedule',
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json().get('events', [])
    except Exception as e:
        logger.error('ESPN schedule fetch failed for %s/%s: %s', league, abbr, e)
        return None


def _find_next_game(events: List[Dict]) -> Optional[Dict]:
    """Find the next upcoming game from a schedule."""
    now = datetime.now(timezone.utc)
    for event in events:
        date_str = event.get('date', '')
        try:
            dt = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue
        if dt > now:
            return event
    return None


# Weekday names → Python weekday() index (Mon=0..Sun=6)
_WEEKDAY_INDEX = {
    'monday': 0, 'mon': 0,
    'tuesday': 1, 'tue': 1, 'tues': 1,
    'wednesday': 2, 'wed': 2, 'weds': 2,
    'thursday': 3, 'thu': 3, 'thur': 3, 'thurs': 3,
    'friday': 4, 'fri': 4,
    'saturday': 5, 'sat': 5,
    'sunday': 6, 'sun': 6,
}


def _resolve_day_range(day: Optional[str]):
    """Resolve a spoken day reference to a (start, end) date pair in ET.

    Returns (start_date, end_date, spoken_label) or (None, None, None) if
    the string can't be parsed. The window is inclusive on both ends and
    is in Eastern local time (same zone as _format_time_et).
    """
    if not day:
        return None, None, None
    from datetime import date, timedelta

    # Compute "today" in ET (UTC-4 approximation — matches _format_time_et)
    today_et = (datetime.now(timezone.utc) - timedelta(hours=4)).date()
    d = day.lower().strip()

    if d in ('today', 'tonight', 'this evening'):
        return today_et, today_et, 'today'
    if d in ('tomorrow', 'tomorrow night'):
        t = today_et + timedelta(days=1)
        return t, t, 'tomorrow'
    if d in ('this weekend', 'the weekend', 'weekend'):
        # Saturday and Sunday of the current week (or next if already past)
        days_to_sat = (5 - today_et.weekday()) % 7
        if today_et.weekday() > 5:  # already Sunday → next weekend
            days_to_sat = 6
        sat = today_et + timedelta(days=days_to_sat)
        sun = sat + timedelta(days=1)
        return sat, sun, 'this weekend'

    # "next <weekday>" or bare "<weekday>"
    next_prefix = d.startswith('next ')
    key = d[5:].strip() if next_prefix else d
    if key in _WEEKDAY_INDEX:
        target_idx = _WEEKDAY_INDEX[key]
        days_ahead = (target_idx - today_et.weekday()) % 7
        if days_ahead == 0 and next_prefix:
            days_ahead = 7
        target = today_et + timedelta(days=days_ahead)
        label = (
            'today' if days_ahead == 0
            else 'tomorrow' if days_ahead == 1
            else target.strftime('%A')
        )
        return target, target, label

    return None, None, None


def _find_game_in_range(events: List[Dict], start_date, end_date) -> Optional[Dict]:
    """Return the first scheduled game whose ET date falls in [start, end]."""
    from datetime import timedelta
    for event in events:
        date_str = event.get('date', '')
        try:
            dt_utc = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            continue
        et_date = (dt_utc - timedelta(hours=4)).date()
        if start_date <= et_date <= end_date:
            return event
    return None


def _format_time_et(iso_date: str) -> str:
    """Convert ISO date to Eastern time for TTS."""
    try:
        dt = datetime.fromisoformat(iso_date.replace('Z', '+00:00'))
        # Convert to ET (UTC-4 during EDT)
        from datetime import timedelta
        et = dt - timedelta(hours=4)
        return et.strftime('%A at %-I:%M %p')
    except Exception:
        return 'time TBD'


def _format_schedule_message(team_title: str, abbr: str,
                             game: Dict, prefix: str = '') -> str:
    """Render a single scheduled game into TTS-friendly text.

    prefix is an optional lead-in like "Yes, " or "No game Saturday — ".
    """
    comp = game.get('competitions', [{}])[0]
    teams = comp.get('competitors', [])
    when = _format_time_et(game.get('date', ''))
    if len(teams) == 2:
        home = teams[0].get('team', {}).get('shortDisplayName', '?')
        away = teams[1].get('team', {}).get('shortDisplayName', '?')
        home_abbr = teams[0].get('team', {}).get('abbreviation', '')
        if home_abbr.upper() == abbr.upper():
            return f"{prefix}The {home} host the {away} {when}."
        return f"{prefix}The {away} play at the {home} {when}."
    return f"{prefix}The {team_title} play {when}."


def handle_next_game(team: str, day: Optional[str] = None) -> Dict[str, Any]:
    """Look up when a team plays next, optionally filtered to a specific day.

    day: free-form spoken day reference — "today", "tomorrow", "Saturday",
         "next Friday", "this weekend", etc. When provided, the handler
         checks if there's a game in that window specifically.
    """
    team_lower = team.lower().strip()

    match = _TEAM_ALIASES.get(team_lower)
    if not match:
        for alias, info in _TEAM_ALIASES.items():
            if alias in team_lower or team_lower in alias:
                match = info
                break

    if not match:
        return {
            'type': 'schedule',
            'status': 'error',
            'message': f"I don't recognize the team \"{team}\".",
            'data': {},
        }

    league, abbr = match
    events = _fetch_team_schedule(league, abbr)
    if events is None:
        return {
            'type': 'schedule',
            'status': 'error',
            'message': f"I couldn't reach ESPN for the {team.title()} schedule.",
            'data': {},
        }

    team_title = team.title()

    # If user asked about a specific day, check that window first
    if day:
        start, end, label = _resolve_day_range(day)
        if start is not None:
            windowed = _find_game_in_range(events, start, end)
            if windowed:
                return {
                    'type': 'schedule',
                    'status': 'ok',
                    'message': _format_schedule_message(team_title, abbr, windowed),
                    'data': {'league': league, 'team': abbr, 'filtered_day': label},
                }
            # No game in the asked window — say so, then tack on the next real game
            next_game = _find_next_game(events)
            if next_game:
                next_msg = _format_schedule_message(team_title, abbr, next_game, prefix='Next game: ')
                return {
                    'type': 'schedule',
                    'status': 'ok',
                    'message': f"The {team_title} don't play {label}. {next_msg}",
                    'data': {'league': league, 'team': abbr, 'filtered_day': label},
                }
            return {
                'type': 'schedule',
                'status': 'ok',
                'message': f"The {team_title} don't play {label}.",
                'data': {'league': league, 'team': abbr, 'filtered_day': label},
            }
        # Couldn't parse the day — fall through to default "next" behavior

    next_game = _find_next_game(events)
    if not next_game:
        return {
            'type': 'schedule',
            'status': 'ok',
            'message': f"I don't see any upcoming games for the {team_title}.",
            'data': {},
        }

    return {
        'type': 'schedule',
        'status': 'ok',
        'message': _format_schedule_message(team_title, abbr, next_game),
        'data': {'league': league, 'team': abbr},
    }


# ── Event-based queries (Final Four, Super Bowl, World Series, etc.) ──

_EVENT_KEYWORDS = {
    'final four': ('ncaab', "Final Four"),
    'march madness': ('ncaab', None),
    'ncaa tournament': ('ncaab', None),
    'super bowl': ('nfl', "Super Bowl"),
    'world series': ('mlb', "World Series"),
    'nba finals': ('nba', "Finals"),
    'stanley cup': ('nhl', "Stanley Cup"),
    'nba playoffs': ('nba', "Playoff"),
    'nfl playoffs': ('nfl', "Playoff"),
    'mlb playoffs': ('mlb', "Playoff"),
    'nhl playoffs': ('nhl', "Playoff"),
    'college football playoff': ('ncaaf', "Playoff"),
    'all star game': ('mlb', "All-Star"),
    'mls cup': ('mls', "Cup"),
}


def handle_event(event_name: str) -> Dict[str, Any]:
    """Look up scores/schedule for a named event (Final Four, Super Bowl, etc.)."""
    event_lower = event_name.lower().strip()

    match = None
    filter_text = None
    for keyword, (league, filt) in _EVENT_KEYWORDS.items():
        if keyword in event_lower:
            match = league
            filter_text = filt
            break

    if not match:
        return {
            'type': 'event',
            'status': 'error',
            'message': f"I don't recognize the event \"{event_name}\".",
            'data': {},
        }

    data = _fetch_scoreboard(match)
    if not data:
        return {
            'type': 'event',
            'status': 'error',
            'message': f"I couldn't reach ESPN for {match.upper()} info right now.",
            'data': {},
        }

    # Filter events by notes/name if we have a filter
    relevant = []
    for event in data.get('events', []):
        if filter_text:
            # Check event notes and name for the filter text
            notes = ' '.join(
                n.get('headline', '')
                for c in event.get('competitions', [])
                for n in c.get('notes', [])
            )
            name = event.get('name', '')
            if filter_text.lower() not in notes.lower() and filter_text.lower() not in name.lower():
                continue
        relevant.append(event)

    if not relevant:
        # No filtered events — show all events for that league today
        relevant = data.get('events', [])
        if not relevant:
            return {
                'type': 'event',
                'status': 'ok',
                'message': f"No {match.upper()} games today.",
                'data': {},
            }

    # Format all relevant games
    lines = []
    for event in relevant[:4]:  # Cap at 4 for TTS
        lines.append(_format_game(event))

    message = ' '.join(lines)
    return {
        'type': 'event',
        'status': 'ok',
        'message': message,
        'data': {'league': match},
    }

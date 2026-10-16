"""
Rotten Tomatoes reviews for the movie modal's "Ratings & Reviews" row.

Source: the RT title page itself (server-rendered), via the existing
rt_scraper session + slug matching:
  * critics  -> the "Critics Reviews" carousel (<review-card-critic> tiles:
                name, publication, avatar, date, Fresh/Rotten, original score,
                quote, full-review link). Top Critics are sorted first.
  * audience -> the embedded reviewsData JSON (verified audience first),
                used only when the page has no critic reviews.

RT's JSON reviews API (/napi/rtcf/v1/.../reviews, used by the old Reviews
panel) now answers 401 to non-browser clients (bot protection), so we don't
use it here.

Ratings are normalised to Plex-style 0-10 (5 stars with halves):
  "3/4" -> 7.5, "7/10" -> 7, "B+" -> 8, audience 4.5 stars -> 9; no score -> None.
Results are cached in memory: 6h with reviews, 1h when empty, 10m on errors.
"""

import html as _html
import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.rt_scraper import rt_scraper

logger = logging.getLogger(__name__)

MAX_REVIEWS = 10
_TTL_OK = 6 * 3600
_TTL_EMPTY = 3600
_TTL_ERR = 600
_CACHE_MAX = 2000
_cache: Dict[str, Tuple[float, float, Dict[str, Any]]] = {}
_lock = threading.Lock()

# Letter grades -> stars out of 5 (halves)
_LETTER_STARS = {
    'A+': 5, 'A': 5, 'A-': 4.5,
    'B+': 4, 'B': 3.5, 'B-': 3,
    'C+': 3, 'C': 2.5, 'C-': 2,
    'D+': 2, 'D': 1.5, 'D-': 1,
    'F': 0, 'E': 0,
}


def score_to_rating10(score: Optional[str]) -> Optional[float]:
    """Convert a critic's original score ("3/4", "B+", "7/10", "4 out of 5") to 0-10."""
    if not score:
        return None
    s = _html.unescape(str(score)).strip().upper().replace('½', '.5').replace(' ', '')
    if not s:
        return None
    letter = re.fullmatch(r'([A-F])([+-]?)', s)
    if letter:
        stars = _LETTER_STARS.get(letter.group(1) + letter.group(2))
        return None if stars is None else float(stars * 2)
    frac = re.fullmatch(r'(\d+(?:\.\d+)?)(?:/|OUTOF)(\d+(?:\.\d+)?)(?:STARS?)?', s)
    if frac:
        num, den = float(frac.group(1)), float(frac.group(2))
        if den <= 0 or num > den:
            return None
        return round(num / den * 10 * 2) / 2  # keep a half-point grid
    plain = re.fullmatch(r'(\d+(?:\.\d+)?)(?:STARS?)?', s)
    if plain:
        v = float(plain.group(1))
        den = 5 if v <= 5 else 10 if v <= 10 else 100 if v <= 100 else None
        return None if den is None else round(v / den * 10 * 2) / 2
    return None


def _iso_date(display: str) -> Optional[str]:
    display = (display or '').strip()
    for fmt in ('%b %d, %Y', '%B %d, %Y', '%Y-%m-%d'):
        try:
            return datetime.strptime(display, fmt).strftime('%Y-%m-%dT12:00:00')
        except ValueError:
            continue
    return None


def _text(fragment: str) -> str:
    t = re.sub(r'<[^>]+>', ' ', fragment or '')
    t = _html.unescape(t)
    return re.sub(r'[ \t\r\f\v]+', ' ', re.sub(r'\s*\n\s*', '\n', t)).strip()


def _attr(tag: str, name: str) -> Optional[str]:
    m = re.search(r'\b%s="([^"]*)"' % re.escape(name), tag)
    return _html.unescape(m.group(1)) if m else None


def _slot(block: str, slot: str, tag: str = r'[a-z-]+') -> Optional[re.Match]:
    return re.search(r'<(%s)\b[^>]*\bslot="%s"[^>]*>(.*?)</\1>' % (tag, re.escape(slot)), block, re.S)


def parse_critic_cards(page: str) -> List[Dict[str, Any]]:
    reviews: List[Dict[str, Any]] = []
    for i, m in enumerate(re.finditer(r'<review-card-critic\b([^>]*)>(.*?)</review-card-critic>', page, re.S)):
        attrs, block = m.group(1), m.group(2)
        name_m = _slot(block, 'name')
        quote_m = _slot(block, 'review')
        name = _text(name_m.group(2)) if name_m else ''
        quote = _text(quote_m.group(2)) if quote_m else ''
        if not name or not quote:
            continue
        pub_m = _slot(block, 'publication')
        ts_m = _slot(block, 'timestamp')
        rating_m = _slot(block, 'rating', 'div')
        sentiment, score_text = None, ''
        if rating_m:
            sm = re.search(r'sentiment="([A-Z]+)"', rating_m.group(2))
            sentiment = sm.group(1) if sm else None
            sp = re.search(r'<span[^>]*>(.*?)</span>', rating_m.group(2), re.S)
            score_text = _text(sp.group(1)) if sp else ''
        avatar_tag = re.search(r'<avatar-icon\b[^>]*>', block)
        icon = _attr(avatar_tag.group(0), 'icon') if avatar_tag else None
        link_tag = re.search(r'<rt-link\b[^>]*\bslot="review-link"[^>]*>', block)
        url = _attr(link_tag.group(0), 'href') if link_tag else None
        display_date = _text(ts_m.group(2)) if ts_m else ''
        top = bool(re.search(r'\btop-critic\b', attrs))
        reviews.append({
            'id': f'rtc-{i}',
            'kind': 'critic',
            'username': name,
            'display_name': None,
            'publication': _text(pub_m.group(2)) if pub_m else None,
            'avatar': icon if icon and icon.startswith('http') else None,
            'date': _iso_date(display_date),
            'rating': score_to_rating10(score_text),
            'score_text': score_text or None,
            'fresh': None if sentiment is None else sentiment == 'POSITIVE',
            'top_critic': top,
            'verified': False,
            'message': quote,
            'url': url if url and url.startswith('http') else None,
            'has_spoilers': False,
        })
    # Top Critics first (stable within each group)
    reviews.sort(key=lambda r: 0 if r['top_critic'] else 1)
    return reviews


def parse_audience_reviews(page: str) -> List[Dict[str, Any]]:
    for m in re.finditer(r'<script[^>]*data-json="reviewsData"[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        items = data.get('reviews') if isinstance(data, dict) else None
        if not isinstance(items, list):
            continue
        out: List[Dict[str, Any]] = []
        for i, r in enumerate(items):
            if not isinstance(r, dict):
                continue
            text = _text(r.get('review') or '')
            if not text:
                continue
            user = r.get('user') or {}
            img = user.get('profileImage') if isinstance(user, dict) else None
            stars = r.get('rating')
            try:
                rating = None if stars is None else max(0.0, min(10.0, float(stars) * 2))
            except (TypeError, ValueError):
                rating = None
            out.append({
                'id': f"rta-{r.get('ratingId') or i}",
                'kind': 'audience',
                'username': (r.get('displayName') or (user.get('displayName') if isinstance(user, dict) else None) or 'Audience member').strip(),
                'display_name': None,
                'publication': None,
                'avatar': img if isinstance(img, str) and img.startswith('http') else None,
                'date': _iso_date(r.get('displayDate') or ''),
                'rating': rating,
                'score_text': None,
                'fresh': None,
                'top_critic': False,
                'verified': bool(r.get('isVerified')),
                'message': text,
                'url': None,
                'has_spoilers': False,
            })
        out.sort(key=lambda x: 0 if x['verified'] else 1)  # verified audience first
        return out
    return []


def _page_year(page: str) -> Optional[int]:
    m = re.search(r'"releaseYear"\s*:\s*"?(\d{4})', page)
    return int(m.group(1)) if m else None


def _fetch_title_page(title: str, year: Optional[int], media_type: str) -> Tuple[Optional[str], Optional[str]]:
    """Find the RT title page using rt_scraper's slug candidates. Returns (html, url)."""
    prefix = '/tv/' if media_type == 'tv' else '/m/'
    for slug in rt_scraper.title_to_slug(title, year):
        url = f'{rt_scraper.BASE_URL}{prefix}{slug}'
        try:
            resp = rt_scraper.session.get(url, timeout=10)
        except Exception as e:  # network hiccup on one slug
            logger.debug(f'RT fetch failed {url}: {e}')
            continue
        if resp.status_code != 200 or 'tomatometer' not in resp.text.lower():
            continue
        page = resp.text
        py = _page_year(page)
        if year and py and abs(py - year) > 1 and media_type != 'tv':
            continue  # same title, different film (remake etc.)
        return page, resp.url
    return None, None


def get_top_reviews(title: str, year: Optional[int] = None, media_type: str = 'movie') -> Dict[str, Any]:
    title = (title or '').strip()
    empty: Dict[str, Any] = {'source': None, 'reviews': [], 'url': None}
    if not title:
        return empty
    key = f'{media_type}|{title.lower()}|{year or ""}'
    now = time.time()
    with _lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < hit[1]:
            return hit[2]

    payload, ttl = dict(empty), _TTL_ERR
    try:
        page, url = _fetch_title_page(title, year, media_type)
        if page:
            critics = parse_critic_cards(page)
            if critics:
                payload = {'source': 'rt_critic', 'reviews': critics[:MAX_REVIEWS], 'url': url}
            else:
                audience = parse_audience_reviews(page)
                payload = {'source': 'rt_audience' if audience else None, 'reviews': audience[:MAX_REVIEWS], 'url': url}
        ttl = _TTL_OK if payload['reviews'] else _TTL_EMPTY
    except Exception as e:
        logger.warning(f'RT top reviews failed for {title!r} ({year}): {e}')

    with _lock:
        if len(_cache) >= _CACHE_MAX:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[: _CACHE_MAX // 4]:
                _cache.pop(k, None)
        _cache[key] = (now, ttl, payload)
    return payload

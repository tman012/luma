"""
Tracker selection shared by the movie acquisition automations.

Every automation that searches indexers itself has the same three problems:

  1. Which of the configured indexers is it even sensible to search? That is a
     judgement per automation, not a lookup — Prowlarr's advertised capabilities
     will happily tell you MoreThanTV carries Movies/UHD, and searching it for a
     remux is wasted traffic regardless.
  2. The user's selection has to gate the request *and* the results. Prowlarr can
     be told which indexer ids to hit; a Torznab aggregate feed is one endpoint
     with no per-indexer addressing, so there the selection can only be applied
     as a name filter afterwards.
  3. A selection naming trackers that no longer exist must be reported, not
     silently widened to "search everything" — that reads as a working
     automation that quietly never grabs.

This module owns that logic once. Callers supply their own allow-list and the
settings key their selection lives under, because the right trackers for a 4K
remux and for a 1080p WEB-DL are not the same question.
"""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


def eligible(client, allowed_names) -> List[Dict[str, Any]]:
    """Configured indexers from *allowed_names*, as Prowlarr reports them.

    Also backs the settings pickers, so the UI can only offer trackers the
    automation would really use.
    """
    allow = {n.strip().lower() for n in allowed_names}
    try:
        available = client.get_indexers()
    except Exception as e:
        logger.warning(f'Tracker selection: could not list indexers: {e}')
        return []
    return [ix for ix in available if ix['name'].strip().lower() in allow]


def selected(settings, settings_key: str) -> List[str]:
    """Raw selection. Empty means "all eligible", not "all configured".

    Stored as names rather than Prowlarr indexer ids: names are what the release
    rows carry, so one setting can gate a Prowlarr request up-front *and* filter
    a Torznab aggregate feed after the fact.
    """
    raw = settings.get(settings_key, None)
    if isinstance(raw, str):
        raw = [p for p in raw.split(',')]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(t).strip() for t in raw if str(t).strip()]


def plan(settings, client, allowed_names, settings_key: str) -> Dict[str, Any]:
    """What a run may search: {'names', 'ids', 'unknown', 'error'}.

    `ids` restricts the Prowlarr request so excluded trackers are never
    contacted; `names` filters the results, which is the only lever a Torznab
    aggregate feed offers and a cheap cross-check on Prowlarr. `ids` is empty
    with `names` populated exactly when the client can't address indexers
    individually.
    """
    allowed_names = tuple(allowed_names)
    try:
        available = client.get_indexers()
    except Exception as e:
        logger.warning(f'Tracker selection: could not list indexers: {e}')
        available = []

    allow = {n.strip().lower() for n in allowed_names}
    usable = [ix for ix in available if ix['name'].strip().lower() in allow]
    if available and not usable:
        return {'names': [], 'ids': [], 'unknown': [],
                'error': 'None of the movie trackers ('
                         + ', '.join(allowed_names)
                         + ') are configured in Prowlarr'}

    picked = selected(settings, settings_key)

    if not usable:
        # Torznab: one aggregate feed, no indexer list to intersect against and
        # no ids to target. The selection can only be applied as a name filter,
        # and can only be validated against the constant.
        names = [n for n in picked if n.strip().lower() in allow]
        return {'names': names if picked else list(allowed_names),
                'ids': [],
                'unknown': [n for n in picked if n.strip().lower() not in allow],
                'error': None}

    by_name = {ix['name'].strip().lower(): ix for ix in usable}
    # An empty `keep` from a selection that matched nothing must stay empty, so
    # the caller can report it rather than silently searching everything.
    keep = ([by_name[n.strip().lower()] for n in picked
             if n.strip().lower() in by_name] if picked else usable)

    return {
        'names': [ix['name'] for ix in keep],
        'ids': [int(ix['id']) for ix in keep],
        'unknown': [n for n in picked if n.strip().lower() not in by_name],
        'error': None,
    }

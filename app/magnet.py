"""The one magnet info-hash parser, shared by TV and movie grabs (v171+, Reviewer on 607f36a).

A magnet counts only when it carries exactly one BitTorrent v1 exact topic (xt=urn:btih:) as a
real parameter, whose whole value is a 40-character hex or 32-character base32 hash:

* anchored: the xt is the first parameter (magnet:?xt=...) or follows '&', so an 'xt=urn:btih:'
  sitting inside another value (dn=..., tr=...) is not one;
* whole value: the hash is followed by '&' or the end, so '-zz', '%20zz', ';x', '#x' or '.x'
  after it is refused instead of read as a prefix;
* one btih: two btih xt parameters (xt=, xt.1=, ...) are refused, even if one is valid, so qBit
  and Luma can never disagree on which torrent this is;
* a v2-only magnet (urn:btmh:) has no btih and is refused (Luma tracks v1 hashes).
"""
from __future__ import annotations

import base64
import re
from typing import Optional

BTIH_RE = re.compile(r'(?:^magnet:\?|&)xt=urn:btih:([0-9a-f]{40}|[a-z2-7]{32})(?=&|$)', re.I)
# Every btih exact-topic parameter, valid or not (xt, xt.1, xt.2, ...), to refuse more than one.
_ANY_BTIH_XT_RE = re.compile(r'(?:^magnet:\?|&)xt(?:\.\d+)?=urn:btih:', re.I)


def btih(magnet) -> Optional[str]:
    """Lowercase 40-hex v1 info hash of *magnet*, or None when it isn't exactly one valid btih."""
    s = str(magnet or '')
    if not s.lower().startswith('magnet:?'):
        return None
    if len(_ANY_BTIH_XT_RE.findall(s)) != 1:
        return None
    m = BTIH_RE.search(s)
    if not m:
        return None
    h = m.group(1)
    if len(h) == 32:
        try:
            h = base64.b32decode(h.upper()).hex()
        except ValueError:
            return None
    return h.lower()

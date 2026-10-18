"""Every tracker XML parse goes through here (v169, tracker port step 1).

defusedxml (PSF license) refuses entity expansion (billion laughs, quadratic
blowup) and external entities / DTD fetches, which a tracker or anything in
front of it could otherwise send us. Anything refused, malformed, or a
missing defusedxml raises xml.etree.ElementTree.ParseError, so callers keep
their one existing except clause, and a missing dependency fails closed (no
parse at all) instead of falling back to the stdlib parser.

v171: forbid_dtd=True, so any DOCTYPE (an external DTD with no entities included) is refused;
tracker feeds and Plex answers have none.

v171+ (Reviewer on 607f36a): fromstring_news() is the one exception, for the kiosk news feed an
owner configured: RSS 0.91 feeds open with a DOCTYPE, so it is allowed there, while defusedxml
still refuses every entity declaration and any external DTD or entity fetch. Tracker and Plex
parses keep fromstring() (no DOCTYPE at all)."""
import xml.etree.ElementTree as ET
from typing import Union

ParseError = ET.ParseError


def fromstring(data: Union[str, bytes]) -> ET.Element:
    return _parse(data, forbid_dtd=True)          # v171 (Tester): no DOCTYPE at all, external or not


def fromstring_news(data: Union[str, bytes]) -> ET.Element:
    """The kiosk news RSS only: a DOCTYPE is fine; entities and external fetches are still refused."""
    return _parse(data, forbid_dtd=False)


def _parse(data: Union[str, bytes], forbid_dtd: bool) -> ET.Element:
    try:
        from defusedxml import DefusedXmlException
        from defusedxml.ElementTree import fromstring as _defused
    except ImportError:                                     # fail closed
        raise ParseError('defusedxml is not installed; tracker XML is not parsed') from None
    try:
        return _defused(data, forbid_dtd=forbid_dtd, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as e:
        raise ParseError(f'unsafe XML refused: {type(e).__name__}') from None

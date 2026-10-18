"""Every tracker XML parse goes through here (v169, tracker port step 1).

defusedxml (PSF license) refuses entity expansion (billion laughs, quadratic
blowup) and external entities / DTD fetches, which a tracker or anything in
front of it could otherwise send us. Anything refused, malformed, or a
missing defusedxml raises xml.etree.ElementTree.ParseError, so callers keep
their one existing except clause, and a missing dependency fails closed (no
parse at all) instead of falling back to the stdlib parser."""
import xml.etree.ElementTree as ET
from typing import Union

ParseError = ET.ParseError


def fromstring(data: Union[str, bytes]) -> ET.Element:
    try:
        from defusedxml import DefusedXmlException
        from defusedxml.ElementTree import fromstring as _defused
    except ImportError:                                     # fail closed
        raise ParseError('defusedxml is not installed; tracker XML is not parsed') from None
    try:
        return _defused(data)
    except DefusedXmlException as e:
        raise ParseError(f'unsafe XML refused: {type(e).__name__}') from None

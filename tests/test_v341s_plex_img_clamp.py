"""v341s (Designer D-SHARP): the Plex image proxy allows the desktop sheet's 2560x1440 art (the clamp was 2000)."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parents[1] / 'app' / 'main.py').read_text()


def test_clamp_allows_2560_and_still_caps():
    m = re.search(r"def _clamp_dim\(raw\):(.*?)\n\n", SRC, re.S)
    assert m, 'clamp helper not found'
    body = m.group(1)
    assert 'min(v, 2560)' in body and 'max(16,' in body
    ns = {}
    exec('def _clamp_dim(raw):' + body, ns)
    c = ns['_clamp_dim']
    assert c('2560') == 2560 and c('1440') == 1440 and c('9999') == 2560 and c('4') == 16 and c('x') is None and c('0') is None

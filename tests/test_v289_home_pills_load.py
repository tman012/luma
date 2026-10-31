"""v289: phone Home pills stay under CW skeleton during load; SW v289."""
import os
import re


def test_sw_v289_or_newer():
    root = os.path.join(os.path.dirname(__file__), '..')
    sw = open(os.path.join(root, 'static', 'sw.js')).read()
    stamp = open(os.path.join(root, 'static', 'sw-stamp.json')).read()
    m = re.search(r"CACHE_VERSION = 'media-kiosk-v(\d+)'", sw)
    assert m and int(m.group(1)) >= 289
    assert f'"cache_version": "media-kiosk-v{m.group(1)}"' in stamp


def test_minimal_home_skeleton_hosts_pills_after_first_rail():
    """Loading path must not paint HomePills above SkeletonRails (top→jump)."""
    root = os.path.join(os.path.dirname(__file__), '..')
    home = open(os.path.join(root, 'frontend', 'src', 'pages', 'MinimalHomePage.tsx')).read()
    rails = open(os.path.join(root, 'frontend', 'src', 'components', 'home', 'Rails.tsx')).read()
    assert 'SkeletonRails afterFirst={<HomePills after />}' in home
    assert '<>\n        <HomePills />\n        <SkeletonRails />' not in home
    assert 'afterFirst' in rails
    assert 'r === 0 && (afterH != null ? <div style={{ height: afterH }} /> : afterFirst)' in rails   # Home crossfade ghost keeps the slot's height

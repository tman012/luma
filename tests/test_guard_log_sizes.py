"""Tester ab1c841 #5: the guard log reads MB amounts as MB (never "freed 0 GB")
and has no "— preferred" placeholder when the chosen trackers gave nothing."""
from app import downloads_guard as dg

MB, GB = 1024 ** 2, 1024 ** 3


def _r(**kw):
    base = {'triggered': True, 'dry_run': False, 'freed_bytes': 0, 'freed_preferred_bytes': 0,
            'freed_fallback_bytes': 0, 'free_after_bytes': 99 * GB, 'deleted': [1, 2, 3], 'failed': []}
    base.update(kw)
    return base


def test_size_units():
    assert dg._size(0) == '0 MB'
    assert dg._size(8 * MB) == '8 MB'
    assert dg._size(150 * GB) == '150 GB'
    assert dg._size(int(1.5 * 1024 * GB)) == '1.5 TB'
    assert dg._size(12 * 1024 * GB) == '12 TB'


def test_mb_run_is_not_zero_gb():
    line = dg.summarise(_r(freed_bytes=8 * MB, freed_fallback_bytes=8 * MB, short=True))
    assert line == ('freed 8 MB across 3 entries · 8 MB from other trackers · '
                    'short of target — ran out of eligible entries · 99 GB free now')
    assert '0 GB' not in line and 'preferred' not in line


def test_both_shares():
    line = dg.summarise(_r(freed_bytes=3 * GB, freed_preferred_bytes=GB, freed_fallback_bytes=2 * GB, dry_run=True))
    assert line == 'would free 3 GB across 3 entries · 1 GB from chosen trackers + 2 GB other'

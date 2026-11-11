"""v207 (PM): on test (LUMA_MINIMAL) the phone movie sheet is plain navy as prod shows: neither the poster
tint nor the /api/tmdb/colors sample reaches it; prod's path is unchanged."""
import re
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
F = 'frontend/src/components/media/MovieDetailModal/CompactLayout.tsx'


def test_minimal_sheet_has_no_palette():
    s = (ROOT / F).read_text()
    assert 'ultraBlurColors: paletteColors,' in s
    assert 'const ultraBlurColors = isMinimal() ? null : paletteColors;' in s
    i = s.index('const ultraBlurColors = isMinimal()')
    assert i < s.index('sheetBase(ultraBlurColors)') and i < s.index('useBackdropEffects(detail?.backdropUrl, ultraBlurColors)')
    assert "?? '#15102a'" in s                       # prod's navy fallback


def test_prod_palette_path_unchanged():
    for f in ('frontend/src/hooks/useModalColors.ts', 'frontend/src/services/modalColors.ts',
              'frontend/src/utils/posterColors.ts', 'frontend/src/components/media/UltraBlurOverlay.tsx'):
        prod = subprocess.run(['git', 'show', f'1b4ea74:{f}'], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        cur = (ROOT / f).read_text()
        if f.endswith('UltraBlurOverlay.tsx'):
            cur = _undo_v365_flat_blur(cur)
        # v326 (MOTION §7 #12, Designer): the sheet base mixes into #111318; the UltraBlur blobs drop will-change
        # (perf, same pixels). Everything else stays prod's.
        cur = cur.replace("// The sheet base mixes into the app surface #111318 (MOTION §7 #12), not the navy token.\nconst NAVY: [number, number, number] = [0x11, 0x13, 0x18];",
                          "const NAVY: [number, number, number] = [0x15, 0x10, 0x2a];")
        cur = re.sub(r"              // No will-change: the blobs never move;[^\n]*\n", "              willChange: lite ? undefined : 'transform',\n", cur)
        # v359 (motion tokens): the overlay's fade timings read the shared tokens; undone here, the rest is prod's.
        if f.endswith('UltraBlurOverlay.tsx'):
            cur = (cur.replace("import { MOTION } from '@/theme/motion';\n", '')
                   .replace('opacity var(--m-dur-reveal) var(--m-ease-reveal)', 'opacity 0.45s ease-out')
                   .replace('${animationName} var(--m-dur-reveal) var(--m-ease-reveal) both', '${animationName} 0.45s ease-out both')
                   .replace('MOTION.dur.palette', '600')
                   .replace('${CROSSFADE_MS}ms var(--m-ease-standard) both', '${CROSSFADE_MS}ms ease both'))
        if f.endswith('modalColors.ts'):
            cur = _undo_colors_abort(_undo_v365_answered(cur))
        if f.endswith('useModalColors.ts'):
            cur = _undo_v365_fetched(cur)
        assert prod == cur, f


# v341l: modalColors.ts adds only an abort path (dropOtherColors + an AbortController per palette fetch) so a tap
# frees the connections its art needs. Each piece is removed by exact text; the palette logic (cache, miss TTL,
# has(), the fetch/parse chain) must still equal prod 1b4ea74 byte for byte.
_ABORT_EDITS = [
    ("const inflight = new Map<string, AbortController | null>();\nexport function dropOtherColors(", None),
    ("    const ac = typeof AbortController === 'undefined' ? null : new AbortController();\n"
     "    if (ac) { inflight.set(url, ac); }\n"
     "    const done = () => { if (inflight.get(url) === ac) inflight.delete(url); };\n", ""),
    ("apiFetch(url, ac ? { signal: ac.signal } : undefined)", "apiFetch(url)"),
    ("        if (cache.get(url) === p) cache.delete(url); // offline / aborted", "        cache.delete(url); // offline / aborted"),
    ("    p.then(done, done);\n", ""),
]


def _undo_colors_abort(cur):
    i = cur.index('/** Palette requests in flight (abortable)')
    j = cur.index('function cached(url: string)')
    block = cur[i:j]
    assert _ABORT_EDITS[0][0] in block and block.count('\n}\n') == 1, 'dropOtherColors block grew beyond the abort'
    cur = cur[:i] + cur[j:]
    for old, new in _ABORT_EDITS[1:]:
        assert cur.count(old) == 1, old
        cur = cur.replace(old, new)
    return cur


# v365: the phone sheet draws one palette, so the colors service remembers what has answered (peekModalColors), the hook
# also returns the fetched palette alone, and the phone blur is one element of radial gradients (no filter raster).
# Each piece is removed by exact text; what the popup's `colors`/`settled` do must still equal prod 1b4ea74.
def _undo_v365_answered(cur):
    i = cur.index('/** The palette `modalColors` would answer right now')
    j = cur.index('/** Warm the palette from a card')
    cur = cur[:i] + cur[j:]
    for old, new in [
        ('/** Palettes that have already answered (null: a definite miss), so a hook can read one on its first render. */\n'
         'const answered = new Map<string, UltraBlurColors | null>();\n\n', ''),
        (' answered.delete(url);', ''),
        (' answered.delete(k);', ''),
        ('        if (has(c)) { answered.set(url, c); return c; }', '        if (has(c)) return c;'),
        ('        answered.set(url, null);\n', ''),
        ('  answered.clear();\n', ''),
    ]:
        assert old in cur, old
        cur = cur.replace(old, new)
    return cur


def _undo_v365_fetched(cur):
    for old, new in [
        (' palette that arrives after the wait is dropped for this opening.\n *\n'
         " * `colors` keeps the desktop popup's behavior (the tapped poster's seed first, the fetched palette replacing it).\n"
         " * `fetched` is the fetched palette alone, null until it has answered: the phone sheets draw only this one, so\n"
         ' * their blur is the neutral color and then the final palette once, never the poster seed. */',
         ' palette that arrives after the wait is dropped for this opening. */'),
        ('getSeedColors, peekModalColors, type', 'getSeedColors, type'),
        ('{ colors: UltraBlurColors | null; settled: boolean; fetched: UltraBlurColors | null }', '{ colors: UltraBlurColors | null; settled: boolean }'),
        ('  const [fetched, setFetched] = useState<UltraBlurColors | null>(() => (enabled ? peekModalColors(kind, ratingKey, tmdbId) ?? null : null));\n', ''),
        ('    setFetched(enabled ? peekModalColors(kind, ratingKey, tmdbId) ?? null : null);\n', ''),
        ('    let current = true;\n', ''),
        ("      if (!current) return;\n      // The phone sheet's palette still lands after the popup's wait: it only ever fades in, once.\n      setFetched(c ?? null);\n", ''),
        ('live = false; current = false; window', 'live = false; window'),
        ('return { colors, settled, fetched };', 'return { colors, settled };'),
    ]:
        assert old in cur, old
        cur = cur.replace(old, new)
    return cur


def _undo_v365_flat_blur(cur):
    a = cur.index('\n// ---------------------------------------------------------------------------\n// Phone sheet blobs')
    b = cur.index('// ---------------------------------------------------------------------------\n// Component\n')
    cur = cur[:a] + cur[b:]
    a = cur.index("  const flat = variant === 'mobile' && !lite;")
    b = cur.index('  // Build animation style for delayed fade-in\n')
    return cur[:a] + cur[b:]

"""PR3 Sources UI on direct tracker results: the adapter, the IPT preference
and standard (TFlix profile) rules on tracker rows, the empty state with no
trackers, and a Sources button on every details modal."""

import re
from pathlib import Path

from tests.frontend_ts import FRONTEND, needs_ts, run_ts

SRC = FRONTEND / 'src'

FETCH_STUB = """
const calls: string[] = [];
let answer: any = null;
(globalThis as any).window = { location: { origin: 'http://localhost' } };
(globalThis as any).document = { querySelector: () => null };
(globalThis as any).fetch = async (url: string) => {
  calls.push(String(url));
  if (answer instanceof Error) throw answer;
  return { ok: answer.status === 200, status: answer.status, json: async () => answer.body };
};
"""


@needs_ts
def test_tracker_rows_keep_their_tracker_type():
    out = run_ts(FETCH_STUB + """
import { fetchAllResults } from '@src/services/allSourcesApi';
answer = { status: 200, body: { releases: [
  { guid: 'g1', indexer: 'My private site', indexer_id: 3, title: 'The.Nut.Job.2014.1080p.BluRay.x264-GRP',
    size: 4831838208, seeders: 1234, leechers: 56, quality: 'BLURAY-1080P', protocol: 'torrent',
    download_url: '/api/trackers/release/abcdefghijklmnopqrstuvwx', info_hash: '', pub_date: '',
    origin: 'indexer', rejected: false, rejection_reasons: [], tracker_type: 'iptorrents' },
  { guid: 'g2', indexer: 'My IPT', title: 'x', size: 1, seeders: 1 },
] } };
fetchAllResults('The Nut Job', 'movie', 2014).then((rows) => console.log(JSON.stringify({ rows, calls })));
""")
    a, b = out['rows']
    assert a['trackerType'] == 'iptorrents' and a['indexer'] == 'My private site'
    assert a['downloadUrl'] == '/api/trackers/release/abcdefghijklmnopqrstuvwx'
    assert a['seeders'] == 1234 and a['size'] == 4831838208
    assert b['trackerType'] == ''
    assert '/api/sources/all-results?' in out['calls'][0]


@needs_ts
def test_ipt_preference_uses_the_tracker_type_whatever_the_owner_named_it():
    out = run_ts("""
import { pickBestInGroup, isIptRelease, recommendationsForResolution } from '@src/components/media/releaseRecommend';
const base = { resolution: '1080p', sourceType: 'WEB-DL', quality: 'WEBDL-1080P' };
const other = { ...base, title: 'Movie.2020.1080p.WEB-DL.H.264-A', indexer: 'Beyond', seeders: 900, guid: 'a' };
const ipt = { ...base, title: 'Movie.2020.1080p.WEB-DL.H.264-B', indexer: 'My private site', seeders: 20, guid: 'b',
              trackerType: 'iptorrents' };
const named = { ...base, title: 'Movie.2020.1080p.WEB-DL.H.264-C', indexer: 'IPTorrents', seeders: 10, guid: 'c' };
console.log(JSON.stringify({
  best: pickBestInGroup([other, ipt])!.guid,
  bestNamed: pickBestInGroup([other, named])!.guid,
  flags: [isIptRelease(ipt), isIptRelease(named), isIptRelease(other)],
  rec: recommendationsForResolution([other, ipt], '1080p').map((r) => r.recommended.guid),
}));
""")
    assert out['best'] == 'b' and out['bestNamed'] == 'c'
    assert out['flags'] == [True, True, False]
    assert out['rec'] == ['b']


@needs_ts
def test_standard_profile_rules_apply_to_tracker_rows():
    out = run_ts("""
import { matchesStandard, recommendedBadgeLabel, pickBestInGroup } from '@src/components/media/releaseRecommend';
const row = (title: string, resolution: string, sourceType: string, extra: any = {}) =>
  ({ title, resolution, sourceType, quality: '', indexer: 'Beyond', seeders: 5, guid: title, ...extra });
const x264 = row('Movie.2020.1080p.WEB-DL.H.264-A', '1080p', 'WEB-DL');
const x265 = row('Movie.2020.1080p.WEB-DL.x265-A', '1080p', 'WEB-DL', { seeders: 500 });
const remux = row('Movie.2020.1080p.BluRay.REMUX.AVC-A', '1080p', 'REMUX');
const uhd = row('Movie.2020.2160p.WEB-DL.DV.HEVC-A', '2160p', 'WEB-DL');
console.log(JSON.stringify({
  std: [matchesStandard(x264), matchesStandard(x265), matchesStandard(remux), matchesStandard(uhd)],
  badges: [recommendedBadgeLabel(x264), recommendedBadgeLabel(uhd)],
  best: pickBestInGroup([x265, x264])!.guid,
}));
""")
    assert out['std'] == [True, False, False, False]
    assert out['badges'] == ['Recommended · Standard', 'Recommended']
    assert out['best'] == 'Movie.2020.1080p.WEB-DL.H.264-A'


@needs_ts
def test_indexer_configured_reads_the_server_and_defaults_to_true():
    out = run_ts(FETCH_STUB + """
import { fetchIndexerConfigured } from '@src/services/sourcesAvailability';
(async () => {
  const res: any[] = [];
  answer = { status: 200, body: { movie_provider: 'radarr', radarr_configured: false, indexer_configured: false } };
  res.push(await fetchIndexerConfigured());
  answer = { status: 200, body: { indexer_configured: true } };
  res.push(await fetchIndexerConfigured());
  answer = { status: 200, body: { movie_provider: 'radarr' } };
  res.push(await fetchIndexerConfigured());
  answer = { status: 500, body: {} };
  res.push(await fetchIndexerConfigured());
  answer = new Error('offline');
  res.push(await fetchIndexerConfigured());
  console.log(JSON.stringify({ res, calls }));
})();
""")
    assert out['res'] == [False, True, True, True, True]
    assert all(c.endswith('/api/sources/config') for c in out['calls'])


def _read(rel):
    return (SRC / rel).read_text(encoding='utf-8')


LAYOUTS = ['components/media/MovieDetailModal/CompactLayout.tsx', 'components/media/MovieDetailModal/ExpandedLayout.tsx',
           'components/media/TvDetailModal/CompactLayout.tsx', 'components/media/TvDetailModal/ExpandedLayout.tsx']


def test_every_details_modal_has_a_marked_sources_button():
    for f in LAYOUTS:
        src = _read(f)
        m = re.search(r'<span data-modal-sources="" style=\{\{ display: \'contents\' \}\}>(.*?)</span>', src, re.S)
        assert m, f
        assert 'label="Sources"' in m.group(1), f


def test_tv_sources_open_without_a_tvdb_id():
    for f in LAYOUTS[2:]:
        src = _read(f)
        assert 'showSources && tvdbId' not in src, f
        assert 'tvdbId={tvdbId ?? 0}' in src, f
        assert 'onOpenSources={tvdbId && isOwner' not in src, f
    hook = _read('components/media/TvDetailModal/useTvSourcesData.ts')
    assert 'if (!tvdbId) {' in hook
    assert '!sonarrOk && !indexerOk && merged.length === 0' in hook


def test_zero_trackers_empty_state_is_clean():
    for f in ['components/media/MovieDetailModal/SourcesPanel.tsx', 'components/media/TvDetailModal/TvSourcesPanel.tsx']:
        src = _read(f)
        i = src.index('noSources && indexerConfigured === false')
        block = src[i:src.index(')}', i)]
        assert '{NO_SOURCES}' in block and 'Sources not found' not in src   # "No sources" (Designer)
        assert block.count('<span') == 1 and '<button' not in block, f   # title only: no helper, no action
        assert 'noSources && indexerConfigured === true' in src



def test_sources_panels_use_the_kiosk_theme():
    glow = _read('components/start/StartUI.tsx')
    assert "export const START_GLOW = 'radial-gradient(" in glow
    assert 'background: START_GLOW' in glow
    for rel in ('components/media/MovieDetailModal/SourcesPanel.tsx',
                'components/media/TvDetailModal/TvSourcesPanel.tsx'):
        src = _read(rel)
        assert "import { START_GLOW } from '@/components/start/StartUI';" in src
        assert "backgroundColor: 'var(--color-kiosk-black)', backgroundImage: START_GLOW" in src
        assert "linear-gradient(rgba(255,255,255,0.06), rgba(255,255,255,0.06)), #111318" in src  # v325: MOTION.md §7 #12, #111318 (was #15102a)
        assert "border: '1px solid rgba(255,255,255,0.08)'" in src
        assert "expanded ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)'" in src
        assert '...srcPrimaryBtn' in src and "'#1d4ed8'" not in src   # primary = theme accent #3b82f6
        assert ".rejected ? '#2a2520' : 'rgba(255,255,255,0.04)'" in src
        assert ']:hover { background: rgba(255,255,255,0.08) !important' in src
        if 'MovieDetailModal' in rel:
            # Above the web dimming layer (absolute, z-index 0) so the card is not greyed out.
            assert "maxWidth: '90%', border: '1px solid rgba(255,255,255,0.08)',\n          position: 'relative', zIndex: 1," in src
        for old in ("background: '#000'", "background: '#222'", "'#111112'", "'#1a1a1c'", "'#333'", '#444 !important'):
            assert old not in src, (rel, old)

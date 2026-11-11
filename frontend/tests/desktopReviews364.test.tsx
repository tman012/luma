/** v364: the desktop movie sheet drops Similar, Collection and Upgrade, and Reviews is its own view apart from Cast.
 *  The mobile movie sheet and the TV sheets keep their buttons and code. */
import { act, render, cleanup, fireEvent } from '@testing-library/react';
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const layoutMode = vi.hoisted(() => ({ mode: 'expanded' as 'compact' | 'expanded' }));
vi.mock('@/contexts/LayoutModeContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/LayoutModeContext')>()),
  useLayoutMode: () => ({ mode: layoutMode.mode, isTablet: false, isCompact: layoutMode.mode === 'compact', isExpanded: layoutMode.mode === 'expanded', layoutMode: layoutMode.mode }) }));
vi.mock('@/contexts/AuthContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/AuthContext')>()), useAuth: () => ({ implicit: false, authenticated: true, user: { id: 1, username: 't', isAdmin: true, is_admin: true }, isAdmin: true }) }));
vi.mock('@/contexts/ModalHostContext', async (orig) => ({ ...(await orig<typeof import('@/contexts/ModalHostContext')>()), useModalHost: () => ({ openMovie: vi.fn(), openTv: vi.fn(), openPerson: vi.fn(), registerBackHandler: vi.fn(() => () => {}), closeAll: vi.fn() }) }));
import { MovieDetailModal } from '@/components/media/MovieDetailModal';
import { openWhenReady, _resetModalPrefetch } from '@/services/modalPrefetch';
import { _resetImageLoader } from '@/loading/imageLoader';

const SRC = resolve(__dirname, '../src');
const read = (p: string) => readFileSync(resolve(SRC, p), 'utf8');

class FakeImage {
  private s = ''; fetchPriority = ''; decoding = ''; onload: null | (() => void) = null; onerror: null | (() => void) = null;
  set src(v: string) { this.s = v; if (v) setTimeout(() => this.onload?.(), 10); }
  get src() { return this.s; }
  removeAttribute() { this.s = ''; }
  decode = () => Promise.resolve();
  naturalWidth = 10; complete = true;
}
Object.defineProperty(HTMLImageElement.prototype, 'complete', { configurable: true, get: () => true });
Object.defineProperty(HTMLImageElement.prototype, 'naturalWidth', { configurable: true, get: () => 300 });

const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { 'Content-Type': 'application/json' } });
const REVIEWS = [
  { id: 'r1', kind: 'critic', username: 'Pat Critic', display_name: 'Pat Critic', publication: 'The Daily', avatar: null, date: '2024-05-01', rating: 8, score_text: '4/5', fresh: true, top_critic: true, verified: false, message: 'A sharp, tense thriller.', url: null, has_spoilers: false },
  { id: 'r2', kind: 'critic', username: 'Sam Reviewer', display_name: 'Sam Reviewer', publication: 'Film Weekly', avatar: null, date: '2024-05-02', rating: 6, score_text: '3/5', fresh: true, top_critic: false, verified: false, message: 'Solid and well acted.', url: null, has_spoilers: false },
];
const cfg = { reviews: REVIEWS as unknown[] };
let urls: string[] = [];

beforeEach(() => {
  _resetModalPrefetch(); _resetImageLoader(); urls = []; cfg.reviews = REVIEWS;
  layoutMode.mode = 'expanded';
  vi.useFakeTimers(); vi.stubGlobal('Image', FakeImage);
  vi.stubGlobal('fetch', vi.fn(async (u: string) => {
    const url = String(u); urls.push(url);
    if (url.includes('/api/reviews/top')) return json({ reviews: cfg.reviews });
    if (url.includes('/api/plex/movie/5003')) return json({ title: 'Jaws', year: 1975, tmdb_id: 578, art: '/library/metadata/5003/art/1', cast: [{ name: 'Roy Scheider', role: 'Chief Brody' }], directors: [] });
    if (url.includes('/api/movie/578')) {
      return json({ id: 578, title: 'Jaws', backdrop_path: '/j.jpg', belongs_to_collection: { id: 1, name: 'Jaws Collection' }, videos: { results: [{ site: 'YouTube', type: 'Trailer', key: 'abc', official: true }] },
        credits: { cast: [{ id: 7, name: 'Roy Scheider', character: 'Chief Brody', profile_path: '/rs.jpg' }], crew: [] } });
    }
    return json({});
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

async function openSheet() {
  const row = document.createElement('button'); row.setAttribute('aria-label', 'Jaws'); document.body.appendChild(row);
  void openWhenReady('movie', 5003, 578, row, () => {}, undefined, []);
  render(<MovieDetailModal ratingKey={5003} tmdbId={578} onClose={() => {}} />);
  for (let i = 0; i < 80; i++) await act(async () => { await vi.advanceTimersByTimeAsync(50); });
}
const pillRow = () => document.querySelector('[data-modal-actions]') as HTMLElement | null;
const labels = () => Array.from(pillRow()?.querySelectorAll('button') ?? []).map((b) => (b.textContent ?? '').trim());
const pill = (label: string) => Array.from(pillRow()?.querySelectorAll('button') ?? []).find((b) => (b.textContent ?? '').trim() === label) as HTMLButtonElement;
const flush = async () => { for (let i = 0; i < 10; i++) await act(async () => { await vi.advanceTimersByTimeAsync(100); }); };

describe('v364: the desktop movie sheet button row', () => {
  it('an owned movie in a collection with similar titles has no Similar, Collection or Upgrade button', async () => {
    await openSheet();
    expect(pillRow(), 'the sheet opened to its button row').not.toBeNull();
    const l = labels();
    expect(l).toEqual(expect.arrayContaining(['Play', 'Trailer', 'Sources', 'Cast', 'Reviews']));
    for (const gone of ['Similar', 'Collection', 'Upgrade']) expect(l, gone).not.toContain(gone);
    expect(l.some((x) => /similar|collection|upgrade|cast & reviews/i.test(x))).toBe(false);
    expect(l.indexOf('Reviews')).toBe(l.indexOf('Cast') + 1);
  });

  it('opening the sheet fetches no similar, collection or upgrade data', async () => {
    await openSheet();
    expect(urls.filter((u) => /similar|collections|upgrade/i.test(u))).toEqual([]);
    for (const label of ['Cast', 'Reviews']) { act(() => { fireEvent.click(pill(label)); }); await flush(); act(() => { fireEvent.keyDown(window, { key: 'Escape' }); }); await flush(); }
    expect(urls.filter((u) => /similar|collections|upgrade/i.test(u))).toEqual([]);
  });

  it('Reviews is the same pill as Cast: same component, classes of style, focus zone and tokens', async () => {
    await openSheet();
    const cast = pill('Cast'); const reviews = pill('Reviews');
    for (const prop of ['padding', 'fontSize', 'borderRadius', 'background', 'color', 'fontWeight', 'transition', 'backdropFilter', 'gap', 'display']) {
      expect(reviews.style.getPropertyValue(prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)), prop)
        .toBe(cast.style.getPropertyValue(prop.replace(/[A-Z]/g, (c) => `-${c.toLowerCase()}`)));
    }
    expect(reviews.style.transition).toContain('var(--m-dur-hover)');
    expect(reviews.getAttribute('type')).toBe('button');
    expect(reviews.getAttribute('data-focus-zone') ?? reviews.getAttribute('data-zone')).toBe(cast.getAttribute('data-focus-zone') ?? cast.getAttribute('data-zone'));
    expect(reviews.getAttribute('tabindex')).toBe(cast.getAttribute('tabindex'));
    expect(reviews.querySelector('svg')?.getAttribute('width')).toBe(cast.querySelector('svg')?.getAttribute('width'));
  });
});

describe('v364: Reviews opens the reviews view and Cast shows cast only', () => {
  it('Reviews opens the cards, a card opens the full review, and the sheet stays open', async () => {
    await openSheet();
    expect(document.querySelector('[data-reviews-section]')).toBeNull();
    act(() => { fireEvent.click(pill('Reviews')); }); await flush();
    expect(document.querySelector('[data-reviews-section]')).not.toBeNull();
    expect(document.querySelectorAll('[data-review-card]').length).toBe(2);
    expect(urls.some((u) => u.includes('/api/reviews/top?title=Jaws'))).toBe(true);
    expect(document.body.textContent).not.toContain('Roy Scheider');
    act(() => { fireEvent.click(document.querySelectorAll('[data-review-card]')[0]!); }); await flush();
    expect(document.querySelector('[data-review-sheet]')).not.toBeNull();
    expect(document.querySelector('[data-modal="MovieDetailModal"]')).not.toBeNull();
  });

  it('Reviews and the content row use the one shelf transition as Cast: the content hides and the logo lifts', async () => {
    await openSheet();
    const content = () => pillRow()!.parentElement!.parentElement as HTMLElement;
    const logoBox = () => (document.querySelector('[data-modal-logo]') as HTMLElement).firstElementChild as HTMLElement;
    expect(content().style.opacity).toBe('1');
    const lifts: Record<string, string> = {};
    const shelves: Record<string, string> = {};
    for (const label of ['Cast', 'Reviews']) {
      act(() => { fireEvent.click(pill(label)); }); await flush();
      expect(content().style.opacity, label).toBe('0');
      lifts[label] = logoBox().style.transform;
      const shelf = Array.from(document.querySelectorAll<HTMLElement>('div')).find((d) => d.style.animation.includes('castShelfBounce'));
      shelves[label] = shelf?.style.animation ?? '';
      act(() => { fireEvent.keyDown(window, { key: 'Escape' }); }); await flush();
      expect(document.querySelector('[data-reviews-section]'), `${label} closes on Escape`).toBeNull();
      expect(content().style.opacity, `${label} restores the row`).toBe('1');
    }
    expect(lifts.Reviews).toBe('translateY(-15vh)');
    expect(lifts.Reviews).toBe(lifts.Cast);
    expect(shelves.Reviews).toBe('castShelfBounce var(--m-dur-modal-enter) var(--m-ease-modal-enter)');
    expect(shelves.Reviews).toBe(shelves.Cast);
  });

  it('Cast shows the cast and no reviews, and does not fetch them for the view', async () => {
    await openSheet();
    act(() => { fireEvent.click(pill('Cast')); }); await flush();
    expect(document.body.textContent).toContain('Roy Scheider');
    expect(document.querySelector('[data-reviews-section]')).toBeNull();
    expect(document.querySelectorAll('[data-review-card]').length).toBe(0);
    expect(document.body.textContent).not.toMatch(/Ratings & Reviews|Cast & crew|Pat Critic|A sharp, tense thriller/);
  });

  it('only one view is open at a time: Reviews closes Cast and Cast closes Reviews', async () => {
    await openSheet();
    act(() => { fireEvent.click(pill('Cast')); }); await flush();
    act(() => { fireEvent.click(pill('Reviews')); }); await flush();
    expect(document.querySelectorAll('[data-reviews-section]').length).toBe(1);
    expect(document.body.textContent).not.toContain('Roy Scheider');
    act(() => { fireEvent.click(pill('Cast')); }); await flush();
    expect(document.querySelector('[data-reviews-section]')).toBeNull();
    expect(document.body.textContent).toContain('Roy Scheider');
  });

  it('a title with no reviews opens an empty Reviews view once the lookup has settled; the row never moves', async () => {
    cfg.reviews = [];
    await openSheet();
    const before = labels();
    act(() => { fireEvent.click(pill('Reviews')); }); await flush();
    expect(document.querySelector('[data-reviews-empty]')?.textContent).toBe('No reviews');
    expect(document.querySelectorAll('[data-review-card]').length).toBe(0);
    act(() => { fireEvent.keyDown(window, { key: 'Escape' }); }); await flush();
    expect(labels()).toEqual(before);
  });
});

describe('v364: the Reviews view uses the shared motion tokens and no new literals', () => {
  const x = read('components/media/MovieDetailModal/ExpandedLayout.tsx');
  const slice = (from: string, to: string) => x.slice(x.indexOf(from), x.indexOf(to));
  it('ShelfSurface and ReviewsShelf carry no literal duration or easing', () => {
    const code = slice('function ShelfSurface', '// PillButton');
    expect(code).toContain("'castShelfBounce var(--m-dur-modal-enter) var(--m-ease-modal-enter)'");
    expect(code).not.toMatch(/\d+(\.\d+)?m?s\b|cubic-bezier|ease-in|ease-out|ease-in-out|\blinear\b(?!-gradient)|transition:/);
  });
  it('the Reviews pill is a PillButton in the movie-detail-modal focus zone with the row tokens', () => {
    const pillSrc = x.slice(x.indexOf('focusKey="mm-info-reviews"') - 40, x.indexOf('onClick={handleReviewsOpen}'));
    expect(pillSrc).toMatch(/<PillButton/);
    expect(pillSrc).toContain('zoneId="movie-detail-modal"');
    expect(pillSrc).toContain('padding={t.pill.padding}');
    expect(pillSrc).toContain('fontSize={t.pill.fontSize}');
    expect(pillSrc).toContain('width={t.pill.iconSize}');
  });
  it('Reviews joins every place Cast joins the shelf logic (hide row, lift logo, Escape, one view at a time)', () => {
    expect(x).toMatch(/transform: showCastShelf \|\| showReviewsShelf \? 'translateY\(-15vh\)'/);
    expect(x).toMatch(/const shelfOpen = \(showUpgradeShelf \|\| showSourcesShelf \|\| showCastShelf \|\| showReviewsShelf\) && !openReview;/);
    expect(x.match(/setShowReviewsShelf\(false\)/g)!.length).toBeGreaterThanOrEqual(5);
  });
  it('the desktop sheet no longer imports or renders Similar or Collections', () => {
    expect(x).not.toMatch(/SimilarPanel|CollectionsSheet|setShowSimilar|setShowCollection|showSimilar|showCollection|similarTmdbId|collectionId|mm-info-(similar|collection|upgrade)/);
    expect(x).not.toMatch(/label="(Upgrade|Similar|Collection)"/);
    expect(x).not.toMatch(/Cast & Reviews|Cast &amp; crew|Cast & crew/);
  });
  it('the Watchlist flow that shared the Upgrade shelf is intact', () => {
    expect(x).toMatch(/onClick=\{\(\) => inWatchlist \? setShowAlreadyInWatchlist\(true\) : isMinimal\(\) \? setShowNativeAdd\(true\) : handleUpgradeOpen\(\)\}/);
    expect(x).toContain("apiFetch('/api/watchlist'");
    expect(x).toContain('data-testid="sources-add-watchlist-desktop"');
    expect(x).toContain('<WatchlistAddSheet');
  });
});

describe('v364: the sheet opens exactly as SW 363 does', () => {
  const x = read('components/media/MovieDetailModal/ExpandedLayout.tsx');
  it('one reveal, the card motion hold and the frame are untouched', () => {
    expect(x).toContain("const modalEntering = useModalMotion(phase, overlayRef, cardRef, 'modal', phase !== 'gone');");
    expect(x).toContain("const reveal = useOneReveal(props.openKey, !loading && !!liveDetail, sheetHero.state !== 'hold' && artInFrame, ratingsSettle.shown,\n    sheetHero.deadline, !!trailerKey, liveDetail?.isOwned ?? false, ownedKnown, sheetHero.artDeadline);");
    expect(x).toContain('<Hold hold={modalEntering}>');
    expect(x).toContain('const detail = useRevealFreeze(liveDetail, reveal.revealed, props.openKey, MOVIE_TEXT);');
    expect(x).toContain("...((loading && !liveDetail) || error ? revealStyle('text') : reveal.revealed ? { opacity: 1, ...CONTENT_WITH_HERO } : { opacity: 0, transition: 'none' }),");
    expect(x).toMatch(/\{\(loading \|\| \(!reveal\.revealed && !error\)\) && \(\s*<div data-sheet-pending=""/);
  });
  it('the shelf views mount only on tap, so opening the sheet mounts neither', async () => {
    await openSheet();
    expect(document.querySelector('[data-reviews-section]')).toBeNull();
    expect(document.querySelector('[data-reviews-empty]')).toBeNull();
    expect(Array.from(document.querySelectorAll<HTMLElement>('div')).some((d) => d.style.animation.includes('castShelfBounce'))).toBe(false);
  });
});

describe('v364: the mobile and TV sheets are unchanged', () => {
  const sha = (p: string) => createHash('sha256').update(readFileSync(resolve(SRC, p))).digest('hex');
  // SHA-256 of each file as it shipped in SW 363 (5678619).
  const PINNED: Record<string, string> = {
    'components/media/MovieDetailModal/CompactLayout.tsx': 'bdfb0d891ef536a2c67825d760557af0c0563545165c575b20a47153cda600cd',
    'components/media/MovieDetailModal/SimilarPanel.tsx': '0b6acd98f4510146457337e974635c9a341369ea935a586dac2b9fd4d80ea582',
    'components/media/MovieDetailModal/MobileUpgradeSheet.tsx': 'a175a6f84de94419051b878cf5152e840ea596eb44622c1391211f3af6e2ab11',
    'components/media/MovieDetailModal/index.tsx': '6a51154c8cb2deafd0d8499b8903d695b902387e5e8cf43de5017d0e59aa071d',
    'components/media/MovieDetailModal/types.ts': '41ac13b5bc43f2f740c198ed9bfcd4a90bc53c6e0c52e992f443cbbbbbca526d',
    'components/collections/CollectionsSheet.tsx': 'c25377c101f0aea8771da402cbefbadcdd227ef4d70d5899fd7a46d7ce083e15',
    'hooks/useSimilarMovies.ts': '298b40c12ea1fb3ba767bfa84b12ecfa0f49b3b76349bb926d7dd2c83cf7ca88',
    'components/media/TvDetailModal/AddToSonarrModal.tsx': '2ccd05f06723398003a484561dce1262ad1cbffe3057dc4cc328f3c38db39a1b',
    'components/media/TvDetailModal/CompactLayout.tsx': 'be1094e5b8e4a00e0e95d885401d5c33e74a3c08b95c4c5c48c8dd331108b030',
    'components/media/TvDetailModal/EpisodeHeader.tsx': '396780530372314dced2ab5636b4b983772459a132500e82d65442a5f771c6bd',
    'components/media/TvDetailModal/ExpandedLayout.tsx': '95fa7795f1416614c429fcb644644069d3512650127dc2eed34ed29442a0780a',
    'components/media/TvDetailModal/OngoingShowPrompt.tsx': 'ec133144c725f4b95943b80f17eea6a369e373175bea24078a8d65e1df329429',
    'components/media/TvDetailModal/ShowMonitorSheet.tsx': 'c8de7b0b09dc0bf9a4f156a2fbcfb45008bb1cf01ec9469a9dd25a5b551eee4c',
    'components/media/TvDetailModal/SonarrStatus.tsx': '5c413396c830e40a3b64a92a6dc76cc300ff806277001da822e744cedf96e64b',
    'components/media/TvDetailModal/TvSourcesPanel.tsx': 'b248687e6c52194144e8cf6ae0f2c20f30cc19a22b1fc5894a3e3558a97e37f4',
    'components/media/TvDetailModal/expandedLayoutTokens.ts': '941e5d76260c404efb3dd153db800d10a2a2eb02678d549166d2f664097286e6',
    'components/media/TvDetailModal/index.tsx': '1cbf6ba70890c8589373bddb7a6cfad453a6ea6502613627d228e2ab2c7a0d6d',
    'components/media/TvDetailModal/seasonLabels.ts': '64a8b8657552c982d730e06266ea2fbe652f972ef63d00c7701c32e70fd3ad52',
    'components/media/TvDetailModal/tvSourcesUtils.ts': '998d8f7301412ffdecf8f37778bd86688d32b75e850f7e27f4cad5e11bc2c16c',
    'components/media/TvDetailModal/types.ts': '7e076df77e0cb1f560afb35dd475638d438d5d858522ee17beebcc09b92f5b73',
    'components/media/TvDetailModal/useTvSourcesData.ts': '00097bcb94817c66a604a323e907562fefdc7507abeda09bf0ab7610739f337f',
  };
  it('the phone sheet and every TV sheet file keep their SW 365 bytes (the open fix re-pinned the files it touches)', () => {
    for (const [file, hash] of Object.entries(PINNED)) expect(sha(file), file).toBe(hash);
  });
  it('the phone sheet still shows Similar, Collection and Upgrade for an owned movie in a collection', async () => {
    layoutMode.mode = 'compact';
    await openSheet();
    const text = Array.from(document.querySelectorAll('[data-action-row] > div')).map((b) => (b.textContent ?? '').trim());
    expect(text).toEqual(expect.arrayContaining(['Upgrade', 'Similar', 'Collection']));
  });
  it('the phone sheet keeps its reviews row on the sheet itself', () => {
    expect(read('components/media/MovieDetailModal/CompactLayout.tsx')).toMatch(/<TopReviewsRow reviews=\{topReviews\} variant="compact" onOpen=\{setOpenReview\} \/>/);
  });
});

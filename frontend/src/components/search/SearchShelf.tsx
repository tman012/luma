/** Prod 1b4ea74's phone search shelf (AuthenticatedHomePage.tsx:430-796), moved out of the page so the minimal
 * build's search host can mount it too (v261). */
import { useRef, useState, useMemo, useCallback, useEffect } from 'react';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import {
  performSearch,
  getTitle,
  getYear,
  type SearchMovie,
  type SearchPerson,
  type SearchResults,
} from '@/services/searchApi';
import { tmdbImg } from '@/utils/image';

// ---------------------------------------------------------------------------
// Search shelf — slides up from bottom like Plex
// ---------------------------------------------------------------------------



type SearchFilter = 'all' | 'movies' | 'tv' | 'people';

const FILTER_PILLS: { id: SearchFilter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'movies', label: 'Movies' },
  { id: 'tv', label: 'TV' },
  { id: 'people', label: 'People' },
];

/** v261: `onPersonSearch` is prod's Home handler (AuthenticatedHomePage.tsx:854-862), handed to the popups the
 * shelf opens so a cast tap with only a name still searches (FLAG 2 in luma-qa/v261/search-diff.md). */
export function SearchShelf({ onClose, initialQuery, onPersonSearch }: {
  onClose: () => void;
  initialQuery?: string;
  onPersonSearch?: (name: string, personId?: number) => void;
}) {
  const [query, setQuery] = useState(initialQuery ?? '');
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<SearchFilter>('all');
  const [phase, setPhase] = useState<'entering' | 'visible' | 'exiting'>('entering');
  const inputRef = useRef<HTMLInputElement>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const modalHost = useModalHost();

  // Paint html background to match sheet — prevents safe-area bleed-through
  useEffect(() => {
    const prev = document.documentElement.style.background;
    document.documentElement.style.background = '#000';
    return () => { document.documentElement.style.background = prev; };
  }, []);

  // Animate in + auto-focus after animation settles
  useEffect(() => {
    requestAnimationFrame(() => setPhase('visible'));
    const focusTimer = setTimeout(() => {
      inputRef.current?.focus({ preventScroll: true });
    }, 100);
    return () => {
      clearTimeout(focusTimer);
      if (abortRef.current) abortRef.current.abort();
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // Dismiss keyboard when user touches/scrolls results area
  useEffect(() => {
    const el = resultsRef.current;
    if (!el) return;
    const dismiss = () => inputRef.current?.blur();
    el.addEventListener('touchstart', dismiss, { passive: true });
    el.addEventListener('scroll', dismiss, { passive: true });
    return () => {
      el.removeEventListener('touchstart', dismiss);
      el.removeEventListener('scroll', dismiss);
    };
  }, []);

  const handleClose = useCallback(() => {
    setPhase('exiting');
    setTimeout(onClose, 300);
  }, [onClose]);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) { setResults(null); setLoading(false); return; }
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    try {
      const res = await performSearch(q, controller.signal);
      if (!controller.signal.aborted) setResults(res);
    } catch (err: any) {
      if (err?.name !== 'AbortError') setResults({ owned: [], unowned: [], ownedPeople: [], unownedPeople: [] });
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  // Seed the search when opened pre-filled (e.g. a cast tap with no person id).
  useEffect(() => {
    if (initialQuery && initialQuery.trim().length >= 2) {
      doSearch(initialQuery.trim());
    }
  }, [initialQuery, doSearch]);

  const handleQueryChange = useCallback((value: string) => {
    setQuery(value);
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (!value.trim()) { setResults(null); setLoading(false); return; }
    setLoading(true);
    debounceRef.current = setTimeout(() => doSearch(value.trim()), 300);
  }, [doSearch]);

  const handleResultClick = useCallback((item: SearchMovie) => {
    handleClose();
    setTimeout(() => {
      const rk = item.rating_key;
      const tmdbId = item.tmdb_id || item.id;
      // Route through the shared modal-navigation util so movie/TV resolution
      // matches every other surface (no per-page divergence).
      openModalForItem(
        modalHost,
        {
          mediaType: item.media_type === 'tv' ? 'tv' : 'movie',
          tmdbId: tmdbId ?? undefined,
          ratingKey: (item.isOwned && rk) ? rk : undefined,
        },
        'search',
        { searchQuery: query.trim() || undefined, ...(onPersonSearch ? { onPersonSearch } : {}) },
      );
    }, 320);
  }, [handleClose, modalHost, query, onPersonSearch]);

  // Build deduped lists by type
  type ResultEntry =
    | { kind: 'media'; item: SearchMovie }
    | { kind: 'person'; item: SearchPerson };

  const allEntries = useMemo(() => {
    if (!results) return [] as ResultEntry[];
    const list: ResultEntry[] = [];
    const seen = new Set<string>();
    for (const item of [...results.owned, ...results.unowned]) {
      const rawTitle = getTitle(item).toLowerCase().replace(/[^a-z0-9]/g, '');
      const key = `${item.media_type || 'movie'}_${rawTitle}_${getYear(item) ?? ''}`;
      if (!seen.has(key)) { seen.add(key); list.push({ kind: 'media', item }); }
    }
    const seenP = new Set<string>();
    for (const p of [...(results.ownedPeople ?? []), ...(results.unownedPeople ?? [])]) {
      const k = p.id != null ? String(p.id) : p.name;
      if (!seenP.has(k)) { seenP.add(k); list.push({ kind: 'person', item: p }); }
    }
    return list;
  }, [results]);

  const filtered = useMemo(() => {
    if (filter === 'all') return allEntries;
    if (filter === 'movies') return allEntries.filter((e) => e.kind === 'media' && (e.item as SearchMovie).media_type !== 'tv');
    if (filter === 'tv') return allEntries.filter((e) => e.kind === 'media' && (e.item as SearchMovie).media_type === 'tv');
    return allEntries.filter((e) => e.kind === 'person');
  }, [allEntries, filter]);

  const resultCount = filtered.length;

  const [headerH, setHeaderH] = useState(0);
  const headerRef = useRef<HTMLDivElement>(null);

  // Measure header height so results area starts below it
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const measure = () => setHeaderH(el.getBoundingClientRect().height);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Resolve poster/profile image for a result entry
  // v261: through tmdbImg so the minimal build loads TMDb art via /api/img/tmdb (a no-op elsewhere).
  const getThumb = (entry: ResultEntry): string | null => tmdbImg(rawThumb(entry));
  const rawThumb = (entry: ResultEntry): string | null => {
    if (entry.kind === 'person') {
      const p = entry.item as SearchPerson;
      if (p.profile_url) return p.profile_url;
      if (p.thumb) return p.thumb;
      if (p.profile_path) return `https://image.tmdb.org/t/p/w185${p.profile_path}`;
      return null;
    }
    const m = entry.item as SearchMovie;
    if (m.poster_url) return m.poster_url;
    if (m.poster_path) return `https://image.tmdb.org/t/p/w185${m.poster_path}`;
    return null;
  };

  return (
    <div
      data-testid="search-shelf"
      style={{
        position: 'fixed', inset: 0, zIndex: 10000,
        background: 'var(--color-kiosk-black)',
        opacity: phase === 'visible' ? 1 : 0,
        transition: 'opacity 0.2s ease',
      }}
    >
      {/* Fixed search header — pinned to top, never scrolls */}
      <div
        ref={headerRef}
        style={{
          position: 'absolute', top: 0, left: 0, right: 0, zIndex: 1,
          paddingTop: 'env(safe-area-inset-top, 0px)',
          background: 'var(--color-kiosk-black)',
        }}
      >
        {/* Header: X + "Search" title */}
        <div style={{ display: 'flex', alignItems: 'center', padding: '16px 16px 12px' }}>
          <button
            type="button"
            onClick={handleClose}
            style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 36, height: 36, background: 'none', border: 'none', cursor: 'pointer', padding: 0, color: 'rgba(255,255,255,0.7)' }}
            aria-label="Close"
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
          <span style={{ flex: 1, textAlign: 'center', fontSize: 17, fontWeight: 600, color: 'rgba(255,255,255,0.9)', marginRight: 36 }}>Search</span>
        </div>

        {/* Search input */}
        <div style={{ padding: '0 16px 12px' }}>
          <input
            ref={inputRef}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            value={query}
            onChange={(e) => handleQueryChange(e.target.value)}
            placeholder="Search for titles, people, and more..."
            autoFocus
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            style={{
              width: '100%', boxSizing: 'border-box',
              padding: '14px 16px', borderRadius: 12,
              background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.12)',
              fontSize: 16, color: 'white', fontFamily: 'inherit', outline: 'none',
            }}
          />
        </div>

        {/* Filter pills */}
        <div style={{ display: 'flex', gap: 10, padding: '0 16px 14px', overflowX: 'auto', scrollbarWidth: 'none' }}>
          {FILTER_PILLS.map((pill) => {
            const active = filter === pill.id;
            return (
              <button
                key={pill.id}
                type="button"
                onClick={() => setFilter(pill.id)}
                style={{
                  padding: '7px 18px', borderRadius: 999, flexShrink: 0,
                  fontSize: 14, fontWeight: 600, fontFamily: 'inherit',
                  cursor: 'pointer', outline: 'none',
                  touchAction: 'manipulation',
                  background: active ? 'transparent' : 'rgba(255,255,255,0.06)',
                  border: active ? 'none' : '1px solid rgba(255,255,255,0.15)',
                  color: active ? '#3b82f6' : 'rgba(255,255,255,0.6)',
                }}
              >
                {pill.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Results — scrollable area under fixed header */}
      <div ref={resultsRef} data-scroll-container className="luma-scroll-y" style={{ position: 'absolute', top: headerH, left: 0, right: 0, bottom: 0, overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y' }}>
        {/* Result count */}
        {!loading && results && query.length >= 2 && (
          <div style={{ padding: '8px 20px 4px', fontSize: 13, color: 'rgba(255,255,255,0.35)' }}>
            {resultCount} {resultCount === 1 ? 'Result' : 'Results'}
          </div>
        )}

        {loading && query.length >= 2 && (
          <div style={{ display: 'flex', justifyContent: 'center', padding: '32px 0' }}>
            <div style={{ width: 28, height: 28, borderRadius: '50%', border: '2px solid rgba(255,255,255,0.1)', borderTopColor: 'rgba(255,255,255,0.5)', animation: 'sp 0.7s linear infinite' }} />
          </div>
        )}
        {!loading && resultCount === 0 && query.length >= 2 && (
          <p style={{ padding: '32px 24px', textAlign: 'center', color: 'rgba(255,255,255,0.35)', fontSize: 15, margin: 0 }}>No results found</p>
        )}

        {!loading && filtered.map((entry, i) => {
          const isPerson = entry.kind === 'person';
          const person = isPerson ? (entry.item as SearchPerson) : null;
          const media = !isPerson ? (entry.item as SearchMovie) : null;
          const thumb = getThumb(entry);
          const title = isPerson ? person!.name : getTitle(media!);
          const year = !isPerson && media ? getYear(media) : null;
          const typeLabel = isPerson
            ? 'Person'
            : media?.media_type === 'tv'
              ? 'TV Show'
              : 'Movie';
          const subtitle = year ? `${year} \u00B7 ${typeLabel}` : typeLabel;
          const department = isPerson && person?.known_for_department ? person.known_for_department : null;

          return (
            <button
              key={`result-${i}`}
              type="button"
              data-testid="search-result"
              onClick={() => {
                if (!isPerson) {
                  handleResultClick(media!);
                } else if (person?.id) {
                  // Open the person modal instead of silently closing the sheet.
                  handleClose();
                  const pid = person.id;
                  const pname = person.name;
                  setTimeout(() => modalHost.openPerson({ personId: pid, personName: pname }), 320);
                } else {
                  handleClose();
                }
              }}
              style={{
                display: 'flex', alignItems: 'center', gap: 16,
                width: 'calc(100% - 32px)', margin: '0 16px',
                padding: '12px 14px',
                background: 'rgba(255,255,255,0.05)',
                borderRadius: 12,
                border: 'none', cursor: 'pointer',
                fontFamily: 'inherit', outline: 'none', textAlign: 'left',
                touchAction: 'manipulation',
                marginBottom: 6,
              }}
            >
              {/* Poster / profile thumbnail */}
              <div style={{
                width: 62, height: 88, borderRadius: 8, overflow: 'hidden', flexShrink: 0,
                background: 'rgba(255,255,255,0.08)',
              }}>
                {thumb ? (
                  <img
                    src={thumb} alt="" loading="lazy" draggable={false}
                    style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                  />
                ) : (
                  <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    {isPerson ? (
                      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="1.5"><circle cx="12" cy="8" r="4" /><path d="M4 21v-1a6 6 0 0 1 12 0v1" /></svg>
                    ) : (
                      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.2)" strokeWidth="1.5"><rect x="4" y="2" width="16" height="20" rx="2" /><line x1="8" y1="6" x2="16" y2="6" /></svg>
                    )}
                  </div>
                )}
              </div>

              {/* Text info */}
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 16, fontWeight: 600, color: 'white',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {title}
                </div>
                <div style={{
                  fontSize: 13, color: 'rgba(255,255,255,0.45)', marginTop: 4,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }}>
                  {isPerson ? (department || 'Person') : subtitle}
                </div>
              </div>
            </button>
          );
        })}

        {/* End spacer (Luma: no bottom safe-area padding) */}
        <div style={{ height: 24 }} />
      </div>
    </div>
  );
}

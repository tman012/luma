import { isMinimal } from '@/minimal';
import { fetchNativeWatchlistIds } from '@/services/watchlistApi';
import { WatchlistAddSheet } from '@/components/movies/WatchlistAddSheet';
/**
 * KioskSearch — full-featured search that mirrors /web behavior.
 *
 * Features:
 *  - Selector pills: Movies / TV / People (radio — one active at a time)
 *  - Typeahead: results populate on 300ms debounce after 2+ chars
 *  - Two-tier results: "In Your Library" + "Find More" expandable
 *  - People section with filmography drill-down
 *  - Same ranking (IMDb votes DESC) and dedup as /web
 *  - Imperative openSearch() for cast/crew → People jump
 */

import { useState, useEffect, useRef, useCallback, useImperativeHandle, forwardRef } from 'react';
import { cn } from '@/utils/cn';
import { useModalHost } from '@/contexts/ModalHostContext';
import { useUiDensity } from '@/hooks/useUiDensity';
import { uiDensityTokens } from '@/ui/uiDensityTokens';
import {
  performSearch,
  fetchPersonCredits,
  batchCheckOwnership,
  getPosterUrl,
  getProfileUrl,
  warmResult,
  getTitle,
  getYear,
  TMDB_BUSY_TEXT,
  type SearchMovie,
  type SearchPerson,
  type PersonCredit,
  type SearchResults,
} from '@/services/searchApi';
import { sizedPlexImg, tmdbImg } from '@/utils/image';
import { mediaKey } from '@/services/ownership';

// ---------------------------------------------------------------------------
// Public handle type for imperative control
// ---------------------------------------------------------------------------

export interface KioskSearchHandle {
  openSearch: (query: string, tab: 'all' | 'movies' | 'tv' | 'people', personId?: number) => void;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function SearchResultCard({
  item,
  onClick,
  wl,
}: {
  item: SearchMovie;
  onClick: () => void;
  /** v202 (test install): an unowned movie's Add to watchlist / In watchlist (the native watchlist) */
  wl?: { inList: boolean; onAdd: () => void };
}) {
  if (wl) {
    return (
      <div className="flex flex-col items-stretch" data-testid="search-watchlist-card">
        <SearchResultCard item={item} onClick={onClick} />
        {wl.inList ? (
          <span data-testid="search-in-watchlist"
            style={{ alignSelf: 'center', display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 18px', borderRadius: 50,
              background: 'rgba(74,222,128,0.12)', border: '1px solid rgba(74,222,128,0.3)', fontSize: 14, fontWeight: 600, color: '#4ade80' }}>
            <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#4ade80' }} />In watchlist
          </span>
        ) : (
          <button type="button" data-testid="search-add-watchlist" onClick={wl.onAdd}
            style={{ alignSelf: 'center', display: 'inline-flex', alignItems: 'center', gap: 8, padding: '8px 18px', borderRadius: 12,
              background: '#3b82f6', color: '#fff', border: 'none', fontSize: 14, fontWeight: 600, cursor: 'pointer' }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Add to watchlist
          </button>
        )}
      </div>
    );
  }
  const title = getTitle(item);
  const year = getYear(item);
  const poster = sizedPlexImg(getPosterUrl(item), 400, 600);
  const warm = () => warmResult(item);

  return (
    <button
      type="button"
      onClick={onClick}
      onPointerEnter={warm}
      onPointerDown={warm}
      onFocus={warm}
      className="group flex flex-col items-center gap-2 rounded-2xl p-2 lg:p-3 transition-colors hover:bg-glass-10"
    >
      <div className="relative aspect-[2/3] w-full shrink-0 overflow-hidden rounded-xl lg:rounded-2xl bg-glass-5 shadow-lg">
        {poster ? (
          <img
            src={poster}
            alt={title}
            className="h-full w-full object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-text-faint">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="20" height="20" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" /></svg>
          </div>
        )}
      </div>
      <div className="w-full text-center">
        <p className="text-[16px] lg:text-[20px] font-medium leading-tight text-text-primary line-clamp-2">
          {title}
        </p>
        <p className="mt-1 text-[14px] lg:text-[17px] text-text-muted">
          {year || ''}{item.media_type === 'tv' ? ' · TV' : ''}
        </p>
      </div>
    </button>
  );
}

function PersonCard({
  person,
  onClick,
}: {
  person: SearchPerson;
  onClick: () => void;
}) {
  const d = useUiDensity();
  const tk = uiDensityTokens(d);
  const profileUrl = getProfileUrl(person);

  return (
    <button
      type="button"
      onClick={onClick}
      className="group flex flex-col items-center gap-2 rounded-2xl p-3 transition-colors hover:bg-glass-10"
    >
      <div className={cn(
        'relative shrink-0 overflow-hidden rounded-xl bg-glass-5 shadow-lg',
        tk.person.cardImg,
      )}>
        {profileUrl ? (
          <img
            src={profileUrl}
            alt={person.name}
            className="h-full w-full object-cover"
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-text-faint">
            <svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2" /><circle cx="12" cy="7" r="4" /></svg>
          </div>
        )}
      </div>
      <div className={cn('text-center', tk.person.cardTextW)}>
        <p className="text-[16px] font-medium leading-tight text-text-primary line-clamp-2">
          {person.name}
        </p>
        <p className={cn('mt-0.5 text-text-muted', tk.person.roleText)}>
          {person.role || person.known_for_department || ''}
        </p>
      </div>
    </button>
  );
}

// ---------------------------------------------------------------------------
// Tabs type
// ---------------------------------------------------------------------------

type TabState = { all: boolean; movies: boolean; tv: boolean; people: boolean };

// ---------------------------------------------------------------------------
// Relevance scoring for unified "All" view
// ---------------------------------------------------------------------------

type UnifiedResult =
  | { kind: 'media'; item: SearchMovie; owned: boolean }
  | { kind: 'person'; item: SearchPerson };

/** What fraction of query words appear (prefix-match) in the label? */
export function wordMatchRatio(query: string, label: string): number {
  // Strip bare years (e.g. "Wrestlemania 2026") — years are backend filter hints,
  // not title words, so they shouldn't count against the match ratio.
  const strippedQuery = query.replace(/\b(?:19|20)\d{2}\b/g, '').trim();
  const qWords = (strippedQuery || query).toLowerCase().split(/\s+/).filter(Boolean);
  if (qWords.length === 0) return 0;
  const lWords = label.toLowerCase().split(/\s+/).filter(Boolean);
  let matched = 0;
  for (const qw of qWords) {
    if (lWords.some((lw) => lw.startsWith(qw) || qw.startsWith(lw))) matched++;
  }
  return matched / qWords.length;
}

/** Popularity signal — owned items without TMDb stats get a strong baseline */
function popSignal(entry: UnifiedResult): number {
  if (entry.kind === 'person') {
    return (entry.item as SearchPerson).popularity || 0;
  }
  const m = entry.item as SearchMovie;
  const tmdbPop = m.popularity && m.popularity > 0 ? m.popularity : 0;
  const votePop = (m.vote_count || 0) / 50;
  const raw = Math.max(tmdbPop, votePop);
  // Owned items from Plex have no TMDb popularity — give them a strong floor
  // (if you own it, it's at least as relevant as a popular TMDb result)
  if (entry.owned && raw < 50) return 50;
  return raw;
}

export const MIN_MATCH_RATIO = 0.5; // at least half of query words must match
const MAX_ALL_RESULTS = 20;

function relevanceScore(entry: UnifiedResult, query: string): number {
  const label =
    entry.kind === 'person'
      ? (entry.item.name || '')
      : getTitle(entry.item);

  const ratio = wordMatchRatio(query, label);
  // Skip word ratio filter for owned items - backend fuzzy search already matched them
  const isOwned = entry.kind === 'media' && entry.owned;
  if (!isOwned && ratio < MIN_MATCH_RATIO) return -1; // filtered out

  const pop = popSignal(entry);
  // text quality: ratio 0-1, with bonus for exact / starts-with
  // Strip year for string comparison so "Wrestlemania 2026" matches "WrestleMania XLI"
  const q = query.replace(/\b(?:19|20)\d{2}\b/g, '').trim().toLowerCase();
  const l = label.toLowerCase();
  let textBonus = ratio;
  if (l === q) textBonus += 2;
  else if (l.startsWith(q)) textBonus += 1.5;
  else if (l.includes(q)) textBonus += 1;

  // Owned items get a significant boost — your library should surface first
  const ownedBoost = isOwned ? 3 : 1;

  return textBonus * Math.log10(pop + 2) * 1000 * ownedBoost;
}

// ---------------------------------------------------------------------------
// URL param sync helper (replaceState — no history entries)
// ---------------------------------------------------------------------------

function syncUrlParams(q: string, activeTab: string, isActive: boolean) {
  const params = new URLSearchParams(window.location.search);
  if (isActive && q) {
    params.set('q', q);
    params.set('type', activeTab);
  } else {
    params.delete('q');
    params.delete('type');
  }
  const qs = params.toString();
  const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname;
  window.history.replaceState(null, '', newUrl);
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export const KioskSearch = forwardRef<KioskSearchHandle>(function KioskSearch(_, ref) {
  // Search state
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [showResults, setShowResults] = useState(false);

  // Tab state (radio — exactly one active)
  const [tabs, setTabs] = useState<TabState>({ all: true, movies: false, tv: false, people: false });

  // Expanded-mode local filter for the unified All-tab grid
  const [expandedFilter, setExpandedFilter] = useState<'all' | 'movies' | 'tv' | 'people'>('all');

  // Expand state
  const [ownedExpanded, setOwnedExpanded] = useState(false);
  const [unownedExpanded, setUnownedExpanded] = useState(false);

  // Person filmography drill-down
  const [personView, setPersonView] = useState<{
    name: string;
    profileUrl: string | null;
    owned: PersonCredit[];
    unowned: PersonCredit[];
  } | null>(null);
  const [personLoading, setPersonLoading] = useState(false);
  const [ownershipLoading, setOwnershipLoading] = useState(false);

  // Focus state (tracks whether the input is focused)
  const [focused, setFocused] = useState(false);

  const { openMovie, openTv, close } = useModalHost();
  const d = useUiDensity();
  const tk = uiDensityTokens(d);

  // Refs
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const tabsRef = useRef(tabs);
  tabsRef.current = tabs;

  // ---------------------------------------------------------------------------
  // Debounced search
  // ---------------------------------------------------------------------------

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) {
      setShowResults(false);
      setResults(null);
      return;
    }

    // Abort previous
    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    setLoading(true);
    setShowResults(true);
    // Reset expand state on new search
    setOwnedExpanded(false);
    setUnownedExpanded(false);
    setPersonView(null);

    // Sync URL params
    const currentTabs = tabsRef.current;
    const activeTab = currentTabs.all ? 'all' : currentTabs.movies ? 'movies' : currentTabs.tv ? 'tv' : 'people';
    syncUrlParams(q, activeTab, true);

    try {
      const res = await performSearch(q, controller.signal);
      if (!controller.signal.aborted) {
        setResults(res);
        // Auto-expand unowned if no owned results for active tab (movies/TV only, not 'all')
        const ct = tabsRef.current;
        if (!ct.people && !ct.all && res.owned.length === 0 && res.unowned.length > 0) {
          setUnownedExpanded(true);
        }

        // Batch-check ownership on unowned results in background so modals
        // open instantly with ratingKey already resolved (no per-click lookup).
        if (res.unowned.length > 0) {
          const movieItems = res.unowned.filter((i) => i.media_type !== 'tv');
          const tvItems = res.unowned.filter((i) => i.media_type === 'tv');
          const payload: Parameters<typeof batchCheckOwnership>[0] = {};
          if (movieItems.length > 0) {
            payload.movies = movieItems.map((i) => ({
              title: getTitle(i),
              year: getYear(i),
              tmdb_id: (i.tmdb_id || i.id)!,
            }));
          }
          if (tvItems.length > 0) {
            payload.tv = tvItems.map((i) => ({
              title: getTitle(i),
              year: getYear(i),
              tmdb_id: (i.tmdb_id || i.id)!,
            }));
          }
          batchCheckOwnership(payload)
            .then((ownershipMap) => {
              if (controller.signal.aborted) return;
              // Promote newly-discovered owned items
              const newOwned: SearchMovie[] = [];
              const stillUnowned: SearchMovie[] = [];
              for (const item of res.unowned) {
                const key = mediaKey({ title: getTitle(item), year: getYear(item), mediaType: item.media_type === 'tv' ? 'tv' : 'movie' });
                const match = ownershipMap[key];
                if (match?.owned) {
                  newOwned.push({
                    ...item,
                    isOwned: true,
                    rating_key: match.rating_key,
                  });
                } else {
                  stillUnowned.push(item);
                }
              }
              if (newOwned.length > 0) {
                setResults((prev) =>
                  prev
                    ? {
                        ...prev,
                        owned: [...prev.owned, ...newOwned],
                        unowned: stillUnowned,
                      }
                    : prev,
                );
              }
            })
            .catch(() => {});
        }
      }
    } catch (err: any) {
      if (err?.name !== 'AbortError') {
        setResults({ owned: [], unowned: [], ownedPeople: [], unownedPeople: [] });
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  const handleInput = useCallback(
    (value: string) => {
      setQuery(value);
      setExpandedFilter('all');
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => doSearch(value.trim()), 300);
    },
    [doSearch],
  );

  // ---------------------------------------------------------------------------
  // Clear search (full reset back to hub state)
  // ---------------------------------------------------------------------------

  const clearSearch = useCallback(() => {
    setQuery('');
    setResults(null);
    setShowResults(false);
    setFocused(false);
    setOwnedExpanded(false);
    setUnownedExpanded(false);
    setPersonView(null);
    setPersonLoading(false);
    setOwnershipLoading(false);
    setTabs({ all: true, movies: false, tv: false, people: false });
    inputRef.current?.blur();
    if (abortRef.current) abortRef.current.abort();
    if (debounceRef.current) clearTimeout(debounceRef.current);
    syncUrlParams('', '', false);
  }, []);

  // ---------------------------------------------------------------------------
  // Go to person filmography — shared by openSearch and handlePersonSearch.
  // When personId is available, goes directly. Otherwise searches by name
  // and auto-selects the matching person.
  // ---------------------------------------------------------------------------

  const goToPerson = useCallback(
    (name: string, personId?: number) => {
      setTabs({ all: false, movies: false, tv: false, people: true });
      setQuery(name);
      setFocused(true);
      setShowResults(true);
      close();
      setPersonView(null);
      setOwnedExpanded(false);
      setUnownedExpanded(false);

      if (personId) {
        handlePersonClick({ id: personId, name } as SearchPerson);
        return;
      }

      // No personId — search by name and auto-select matching person
      setPersonLoading(true);
      performSearch(name.trim())
        .then((data) => {
          const people = [
            ...(data.ownedPeople ?? []),
            ...(data.unownedPeople ?? []),
          ];
          const match =
            people.find((p) => p.name?.toLowerCase() === name.toLowerCase()) ||
            people[0];
          if (match?.id) {
            handlePersonClick(match);
          } else {
            // No matching person — show search results as fallback
            setResults(data);
            setPersonLoading(false);
          }
        })
        .catch(() => {
          setPersonLoading(false);
        });
    },
    [],
  );

  // ---------------------------------------------------------------------------
  // Imperative API for external callers (cast/crew → People search)
  // ---------------------------------------------------------------------------

  useImperativeHandle(ref, () => ({
    openSearch(q: string, tab: 'all' | 'movies' | 'tv' | 'people', personId?: number) {
      if (tab === 'people') {
        goToPerson(q, personId);
        return;
      }

      setTabs({
        all: tab === 'all',
        movies: tab === 'movies',
        tv: tab === 'tv',
        people: false,
      });
      setQuery(q);
      setFocused(true);
      close();
      setPersonView(null);
      setOwnedExpanded(false);
      setUnownedExpanded(false);
      doSearch(q.trim());
      setTimeout(() => { inputRef.current?.focus(); }, 80);
    },
  }), [doSearch, goToPerson]);

  // ---------------------------------------------------------------------------
  // Handle person search from modal cast/crew click (internal)
  // ---------------------------------------------------------------------------

  const handlePersonSearch = useCallback(
    (name: string, personId?: number) => {
      close();
      goToPerson(name, personId);
    },
    [close, goToPerson],
  );

  // ---------------------------------------------------------------------------
  // Close on Escape / click outside — skip when modal is open
  // ---------------------------------------------------------------------------

  // Track detail-modal open state so closing the modal doesn't also wipe
  // the underlying search results. We keep a short "grace window" after the
  // modal closes to swallow any synthetic tap/mousedown that fires on the
  // now-empty spot (iOS replays touches as mouse events after touchend).
  const modalOpenRef = useRef(false);
  const modalClosedAtRef = useRef(0);
  useEffect(() => {
    const onOpen = () => { modalOpenRef.current = true; };
    const onClose = () => {
      modalOpenRef.current = false;
      modalClosedAtRef.current = Date.now();
    };
    window.addEventListener('modal-opened', onOpen);
    window.addEventListener('modal-closed', onClose);
    return () => {
      window.removeEventListener('modal-opened', onOpen);
      window.removeEventListener('modal-closed', onClose);
    };
  }, []);

  const shouldSuppressOutsideDismiss = () => {
    if (modalOpenRef.current) return true;
    // Ignore stray events for a short window after the modal closed.
    return Date.now() - modalClosedAtRef.current < 400;
  };

  useEffect(() => {
    if (!showResults && !focused) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !shouldSuppressOutsideDismiss()) {
        clearSearch();
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [showResults, focused, clearSearch]);

  useEffect(() => {
    if (!showResults && !focused) return;
    const handler = (e: MouseEvent) => {
      if (shouldSuppressOutsideDismiss()) return;
      if (panelRef.current && !panelRef.current.contains(e.target as Node)) {
        clearSearch();
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [showResults, focused, clearSearch]);

  // ---------------------------------------------------------------------------
  // Read URL search params on mount (supports hard-refresh deep links)
  // ---------------------------------------------------------------------------

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const q = params.get('q');
    const type = params.get('type');
    const personIdParam = params.get('personId');
    if (q) {
      const tab = (type === 'all' || type === 'movies' || type === 'tv' || type === 'people') ? type : 'all';
      setTabs({ all: tab === 'all', movies: tab === 'movies', tv: tab === 'tv', people: tab === 'people' });
      setQuery(q);
      setFocused(true);
      if (tab === 'people' && personIdParam) {
        goToPerson(q, Number(personIdParam));
      } else {
        doSearch(q.trim());
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------------------------------------------------------------------------
  // Handle result click → open detail modal
  // ---------------------------------------------------------------------------

  // v202 (test install): Add to watchlist on unowned movie results (the native watchlist; never Radarr)
  const [wlIds, setWlIds] = useState<Set<number> | null>(null);
  const [wlAdd, setWlAdd] = useState<SearchMovie | null>(null);
  useEffect(() => {
    if (!isMinimal()) return;
    void fetchNativeWatchlistIds().then(setWlIds);
  }, []);
  const wlFor = (item: SearchMovie): { inList: boolean; onAdd: () => void } | undefined => {
    const id = item.tmdb_id || item.id;
    if (!wlIds || !id || item.media_type === 'tv' || item.isOwned) return undefined;
    return { inList: wlIds.has(id), onAdd: () => setWlAdd(item) };
  };
  const wlSheet = wlAdd && (wlAdd.tmdb_id || wlAdd.id) ? (
    <WatchlistAddSheet tmdbId={(wlAdd.tmdb_id || wlAdd.id)!} title={getTitle(wlAdd)} year={getYear(wlAdd) || null}
      onClose={() => setWlAdd(null)}
      onAdded={() => { const id = (wlAdd.tmdb_id || wlAdd.id)!; setWlIds(p => new Set([...(p ?? []), id])); setWlAdd(null); }} />
  ) : null;

  const handleResultClick = (item: SearchMovie) => {
    const rk = item.rating_key;
    const tmdbId = item.tmdb_id || item.id;
    const opts = {
      ratingKey: (item.isOwned && rk) ? rk : undefined,
      tmdbId: tmdbId ?? undefined,
      origin: 'search' as const,
      onPersonSearch: handlePersonSearch,
    };
    if (!opts.ratingKey && !opts.tmdbId) return;
    if (item.media_type === 'tv') {
      openTv(opts);
    } else {
      openMovie(opts);
    }
  };

  // ---------------------------------------------------------------------------
  // Handle person click → fetch filmography, batch-check ownership
  // ---------------------------------------------------------------------------

  const handlePersonClick = async (person: SearchPerson) => {
    const tmdbId = person.id;
    if (!tmdbId) return;

    setPersonLoading(true);
    setPersonView(null);
    setOwnedExpanded(false);
    setUnownedExpanded(false);

    try {
      const data = await fetchPersonCredits(tmdbId);
      const credits = data.credits || [];

      // Phase 1: Show all credits as "unowned" immediately
      setPersonView({
        name: data.person?.name || person.name,
        profileUrl: data.person?.profile_url || null,
        owned: [],
        unowned: credits.map((c) => ({ ...c, isOwned: false })),
      });
      setPersonLoading(false);
      setOwnershipLoading(true);

      // Phase 2: Batch check ownership in background, split when ready
      const movieCredits = credits.filter((c) => c.media_type !== 'tv');
      const tvCredits = credits.filter((c) => c.media_type === 'tv');
      const ownershipMap = await batchCheckOwnership({
        movies: movieCredits.map((c) => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id })),
        tv: tvCredits.map((c) => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id })),
      });

      const ownedCredits: PersonCredit[] = [];
      const unownedCredits: PersonCredit[] = [];

      for (const credit of credits) {
        const key = mediaKey({ title: credit.title, year: credit.year, mediaType: credit.media_type === 'tv' ? 'tv' : 'movie' });
        const ownership = ownershipMap[key];
        if (ownership?.owned) {
          ownedCredits.push({
            ...credit,
            isOwned: true,
            rating_key: ownership.rating_key,
          });
        } else {
          unownedCredits.push({ ...credit, isOwned: false });
        }
      }

      setPersonView({
        name: data.person?.name || person.name,
        profileUrl: data.person?.profile_url || null,
        owned: ownedCredits,
        unowned: unownedCredits,
      });
      setOwnershipLoading(false);
    } catch {
      setPersonView(null);
      setPersonLoading(false);
      setOwnershipLoading(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Filter results by active tabs
  // ---------------------------------------------------------------------------

  const filteredOwned = (results?.owned ?? []).filter((item) => {
    if (tabs.all) return true;
    if (item.media_type === 'movie' && tabs.movies) return true;
    if (item.media_type === 'tv' && tabs.tv) return true;
    return false;
  });

  const filteredUnowned = (results?.unowned ?? []).filter((item) => {
    if (!getPosterUrl(item)) return false; // skip items without posters
    if (tabs.all) return true;
    if (item.media_type === 'movie' && tabs.movies) return true;
    if (item.media_type === 'tv' && tabs.tv) return true;
    return false;
  });

  const filteredOwnedPeople = (tabs.people || tabs.all) ? (results?.ownedPeople ?? []) : [];
  const filteredUnownedPeople = (tabs.people || tabs.all) ? (results?.unownedPeople ?? []) : [];

  // Build unified "All" results list — filtered by word-match, sorted by relevance, capped
  const unifiedResults: UnifiedResult[] = (() => {
    if (!tabs.all || !results) return [];
    const candidates: UnifiedResult[] = [];
    for (const p of filteredOwnedPeople) candidates.push({ kind: 'person', item: p });
    for (const p of filteredUnownedPeople) candidates.push({ kind: 'person', item: p });
    for (const m of filteredOwned) candidates.push({ kind: 'media', item: m, owned: true });
    for (const m of filteredUnowned) candidates.push({ kind: 'media', item: m, owned: false });
    // Score, filter junk (score < 0), sort, cap
    const scored = candidates
      .map((c) => ({ entry: c, score: relevanceScore(c, query) }))
      .filter((s) => s.score >= 0)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_ALL_RESULTS);
    return scored.map((s) => s.entry);
  })();

  // Per-kind counts (used by expanded-mode filter pills)
  const unifiedCounts = {
    all: unifiedResults.length,
    movies: unifiedResults.filter(e => e.kind === 'media' && e.item.media_type === 'movie').length,
    tv: unifiedResults.filter(e => e.kind === 'media' && e.item.media_type === 'tv').length,
    people: unifiedResults.filter(e => e.kind === 'person').length,
  };

  // Filtered version for the expanded-mode grid based on the local pill state
  const expandedFilteredResults = (() => {
    if (expandedFilter === 'all') return unifiedResults;
    if (expandedFilter === 'people') return unifiedResults.filter(e => e.kind === 'person');
    return unifiedResults.filter(e =>
      e.kind === 'media' &&
      (expandedFilter === 'movies' ? e.item.media_type === 'movie' : e.item.media_type === 'tv'),
    );
  })();

  // Unified counts for the active tab
  const ownedCount = tabs.people
    ? filteredOwnedPeople.length
    : tabs.all
      ? unifiedResults.length
      : filteredOwned.length;
  const unownedCount = tabs.people
    ? filteredUnownedPeople.length
    : tabs.all
      ? 0 // unified view doesn't split
      : filteredUnowned.length;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  // Whether the search is "active" (focused or has results showing)
  const isActive = focused || showResults;

  return (
    <>
      {wlSheet}
      {/* Background dim overlay — click to dismiss; z-[60] in person view to cover TopBar */}
      {isActive && (
        <div
          className={cn(
            'fixed inset-0',
            personView || personLoading ? 'z-[60] bg-kiosk-black' : 'z-20 bg-kiosk-black/95 kiosk-search-dim',
          )}
          onClick={() => clearSearch()}
          onTouchEnd={(e) => { e.preventDefault(); clearSearch(); }}
        />
      )}

      <div
        ref={panelRef}
        data-search-panel
        className={cn(
          'absolute inset-x-0 flex flex-col items-center transition-all duration-300 ease-in-out',
          tk.spacing.searchPadX,
          personView || personLoading
            ? 'z-[70] top-0'
            : cn(
                'z-30',
                isActive ? tk.search.activeTop : tk.search.idleTop,
              ),
        )}
      >
      {/* Search bar — hidden when idle or viewing person filmography */}
      {!personView && !personLoading && isActive && (
        <div className={cn('flex w-full min-w-0 flex-col rounded-2xl border border-white/15 shadow-lg shadow-black/50', tk.search.barMaxW)} style={{ background: 'rgba(255,255,255,0.06)', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)' }}>
          <div className="flex items-center gap-4 px-6 py-4">
            {/* Search icon — larger in expanded mode */}
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.75"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="shrink-0 text-text-muted"
            >
              <circle cx="11" cy="11" r="8" />
              <path d="M21 21l-4.35-4.35" />
            </svg>
            <input
              ref={inputRef}
              type="search"
              inputMode="search"
              enterKeyHint="search"
              value={query}
              onChange={(e) => handleInput(e.target.value)}
              onFocus={() => { setFocused(true); if (results) setShowResults(true); }}
              onBlur={() => { /* focused cleared by click-outside / Escape */ }}
              placeholder="Search"
              className="w-full min-w-0 h-12 md:h-14 bg-transparent text-[clamp(16px,2vw,22px)] text-text-primary placeholder:text-text-faint outline-none"
            />
            {query && (
              <button
                type="button"
                onClick={clearSearch}
                aria-label="Clear search"
                className={cn('shrink-0 flex items-center justify-center text-text-muted hover:text-text-primary', tk.targets.minBtn)}
              >
                <svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M18 6 6 18" /><path d="m6 6 12 12" />
                </svg>
              </button>
            )}
          </div>

          {/* Google-style autocomplete suggestions — compact (phone) only */}
          {d.mode === 'compact' && tabs.all && showResults && !loading && unifiedResults.length > 0 && (
            <div data-scroll-container className="luma-scroll-y max-h-[55vh] border-t border-white/10 divide-y divide-white/[0.06]">
              {unifiedResults.map((entry, i) => {
                const isPerson = entry.kind === 'person';
                const person = isPerson ? (entry.item as SearchPerson) : null;
                const media = !isPerson ? (entry.item as SearchMovie) : null;
                const label = isPerson ? person!.name : getTitle(media!);
                const year = !isPerson && media ? getYear(media) : null;
                const typeLabel = isPerson
                  ? 'Person'
                  : media?.media_type === 'tv'
                    ? 'TV show'
                    : 'Movie';

                return (
                  <button
                    key={`suggestion-${i}`}
                    type="button"
                    onClick={() => {
                      if (isPerson) {
                        handlePersonClick(person!);
                      } else {
                        handleResultClick(media!);
                      }
                    }}
                    onPointerDown={() => { if (media) warmResult(media); }}
                    className="flex w-full items-center gap-4 px-4 py-3.5 text-left transition-colors hover:bg-white/[0.07]"
                  >
                    {/* Small poster thumbnail */}
                    <div className="w-[36px] h-[54px] shrink-0 overflow-hidden rounded-lg bg-glass-5">
                      {(() => {
                        const posterSrc = isPerson
                          ? getProfileUrl(person!)
                          : sizedPlexImg(getPosterUrl(media!), 120, 180);
                        return posterSrc ? (
                          <img src={posterSrc} alt="" className="h-full w-full object-cover" onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
                        ) : (
                          <div className="flex h-full w-full items-center justify-center text-text-faint">
                            <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><rect x="2" y="2" width="20" height="20" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><path d="m21 15-5-5L5 21"/></svg>
                          </div>
                        );
                      })()}
                    </div>
                    {/* Text */}
                    <div className="flex-1 min-w-0">
                      <span className="block text-[17px] lg:text-[19px] font-medium text-text-primary leading-snug truncate">
                        {label}
                      </span>
                      <span className="block text-[13px] text-text-faint mt-0.5">
                        {year ? `${year} · ${typeLabel}` : typeLabel}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}

          {/* All tab — loading spinner inline (compact only) */}
          {d.mode === 'compact' && tabs.all && showResults && loading && (
            <div className="flex items-center justify-center border-t border-white/10 py-8">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
            </div>
          )}

          {/* All tab — no results (compact only) */}
          {d.mode === 'compact' && tabs.all && showResults && !loading && results && unifiedResults.length === 0 && (
            <div className="border-t border-white/10 py-8 text-center text-[16px] text-text-muted">
              {results?.rateLimited ? TMDB_BUSY_TEXT : 'No results'}
            </div>
          )}
        </div>
      )}

      {/* Person photo + name — sticky above results, covers TopBar */}
      {(personView || personLoading) && (
        <div className="sticky top-0 z-10 flex w-full max-w-[1200px] flex-col items-center pt-6 pb-4">
          <button
            type="button"
            onClick={clearSearch}
            className="absolute left-0 top-8 flex h-12 w-12 items-center justify-center rounded-full bg-white/10 text-white transition-colors hover:bg-white/20"
            aria-label="Back"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          {personView?.profileUrl && (
            <div className="h-[100px] w-[100px] overflow-hidden rounded-full bg-glass-10 shadow-lg shadow-black/40">
              <img
                src={tmdbImg(personView.profileUrl) ?? undefined}
                alt={personView.name}
                className="h-full w-full object-cover"
              />
            </div>
          )}
          <p className="mt-3 text-[22px] font-semibold text-white/90">
            {personView?.name ?? query}
          </p>
        </div>
      )}

      {/* Results panel — grid view for Movies/TV/People tabs + person filmography.
          In expanded mode (kiosk/touch), the All tab also renders here as a poster grid. */}
      {showResults && (!tabs.all || personView || personLoading || d.mode !== 'compact') && (
        <div
          className={cn(
            'w-full min-w-0 overflow-y-auto',
            personView || personLoading
              ? 'mt-0 max-w-[1200px] p-6 pb-10'
              : cn('mt-3 rounded-3xl border border-white/15 p-6 pb-10', tk.search.barMaxW),
          )}
          style={{
            // Cap to ~2 rows of poster cards (poster + full title + year visible).
            // min() with viewport-calc keeps it from ever exceeding the available
            // space on shorter viewports.
            maxHeight: personView || personLoading
              ? 'calc(var(--vvh, 100vh) * 0.80)'
              : 'min(820px, calc(var(--vvh, 100vh) - 220px))',
            ...(personView || personLoading
              ? {}
              : { background: 'rgba(255,255,255,0.06)', backdropFilter: 'blur(24px)', WebkitBackdropFilter: 'blur(24px)' }),
          }}
        >
          {/* Loading */}
          {loading && (
            <div className="flex items-center justify-center py-12">
              <div className="h-8 w-8 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
            </div>
          )}

          {/* No results */}
          {!loading && results && ownedCount === 0 && unownedCount === 0 && (tabs.all ? unifiedResults.length === 0 : true) && !personView && !personLoading && (
            <p className="py-12 text-center text-[20px] text-text-muted">
              {results?.rateLimited ? TMDB_BUSY_TEXT : 'No results'}
            </p>
          )}

          {/* Person loading spinner */}
          {personLoading && (
            <div className="flex flex-col items-center justify-center py-16 gap-4">
              <div className="h-10 w-10 animate-spin rounded-full border-2 border-glass-20 border-t-accent" />
              <p className="text-[18px] text-text-muted">Loading filmography…</p>
            </div>
          )}

          {!loading && !personLoading && results && !personView && (
            <>

              {/* ---- All tab (expanded mode only): filter pills + unified poster grid ---- */}
              {tabs.all && d.mode !== 'compact' && unifiedResults.length > 0 && (
                <>
                  {/* Filter pills — narrow the grid to Movies / TV / People */}
                  <div className="mb-4 flex items-center justify-center gap-2 flex-wrap">
                    {([
                      { id: 'all',    label: 'All',     count: unifiedCounts.all },
                      { id: 'movies', label: 'Movies',  count: unifiedCounts.movies },
                      { id: 'tv',     label: 'TV',      count: unifiedCounts.tv },
                      { id: 'people', label: 'People',  count: unifiedCounts.people },
                    ] as const).map(({ id, label, count }) => {
                      const isActive = expandedFilter === id;
                      const disabled = count === 0 && id !== 'all';
                      return (
                        <button
                          key={id}
                          type="button"
                          disabled={disabled}
                          onClick={() => setExpandedFilter(id)}
                          className={cn(
                            'inline-flex items-center gap-1.5 rounded-full border px-4 py-2 text-[15px] transition-colors',
                            isActive
                              ? 'border-accent/40 bg-accent/20 text-white font-semibold'
                              : 'border-white/15 bg-white/[0.04] text-text-secondary hover:bg-white/[0.08]',
                            disabled && 'opacity-40 cursor-not-allowed',
                          )}
                        >
                          {label}
                          <span className={cn(
                            'text-[12px] font-normal',
                            isActive ? 'text-white/60' : 'text-text-faint',
                          )}>
                            {count}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  {expandedFilteredResults.length > 0 ? (
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                      {expandedFilteredResults.map((entry, i) => {
                        if (entry.kind === 'person') {
                          return (
                            <PersonCard
                              key={`unified-${i}`}
                              person={entry.item}
                              onClick={() => handlePersonClick(entry.item)}
                            />
                          );
                        }
                        return (
                          <SearchResultCard
                            key={`unified-${i}`}
                            item={entry.item}
                            wl={entry.owned ? undefined : wlFor(entry.item)}
                            onClick={() => handleResultClick(entry.item)}
                          />
                        );
                      })}
                    </div>
                  ) : (
                    <p className="py-8 text-center text-[16px] text-text-muted">
                      No {expandedFilter} results.
                    </p>
                  )}
                </>
              )}

              {/* ---- People tab: show all results directly (no owned/unowned split) ---- */}
              {tabs.people && (
                <>
                  {filteredOwnedPeople.length === 0 && filteredUnownedPeople.length === 0 ? null : (
                    <div className="flex flex-wrap gap-1">
                      {[...filteredOwnedPeople, ...filteredUnownedPeople].map((p, i) => (
                        <PersonCard key={`person-${i}`} person={p} onClick={() => handlePersonClick(p)} />
                      ))}
                    </div>
                  )}
                </>
              )}

              {/* ---- Movies / TV: In Your Library (collapsed: 7 max) ---- */}
              {!tabs.people && !unownedExpanded && filteredOwned.length > 0 && (
                <div className="mb-8">
                  {!ownedExpanded && (
                    <h3 className="mb-4 text-[18px] font-semibold text-text-primary">In your library</h3>
                  )}
                  {ownedExpanded && (
                    <button
                      type="button"
                      onClick={() => setOwnedExpanded(false)}
                      className="mb-3 flex items-center gap-2 text-[18px] font-semibold text-text-primary hover:text-white transition-colors"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
                      In your library
                    </button>
                  )}
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    {(ownedExpanded ? filteredOwned : filteredOwned.slice(0, 7)).map((item, i) => (
                      <SearchResultCard
                        key={`owned-${i}`}
                        item={item}
                        onClick={() => handleResultClick(item)}
                      />
                    ))}
                  </div>
                  {!ownedExpanded && filteredOwned.length > 7 && (
                    <button
                      type="button"
                      onClick={() => setOwnedExpanded(true)}
                      className={cn('mt-3 text-[16px] font-medium text-accent hover:text-accent-hover transition-colors', d.isTablet && 'min-h-[48px] inline-flex items-center')}
                    >
                      Show More ({filteredOwned.length - 7} more)
                    </button>
                  )}
                </div>
              )}

              {/* ---- Movies / TV: Find More button / Unowned section ---- */}
              {!tabs.people && !ownedExpanded && filteredUnowned.length > 0 && (
                <div>
                  {!unownedExpanded ? (
                    <button
                      type="button"
                      onClick={() => setUnownedExpanded(true)}
                      className="flex w-full items-center justify-center gap-2 py-3 text-[18px] font-medium text-white/80 transition-colors hover:text-white"
                    >
                      Find More
                      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
                    </button>
                  ) : (
                    <>
                      <button
                        type="button"
                        onClick={() => setUnownedExpanded(false)}
                        className="mb-3 flex w-full items-center justify-center gap-2 text-[18px] font-semibold text-text-secondary hover:text-text-primary transition-colors"
                      >
                        <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
                        Find More
                      </button>
                      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        {filteredUnowned.map((item, i) => (
                          <SearchResultCard
                            key={`unowned-${i}`}
                            item={item}
                            wl={wlFor(item)}
                            onClick={() => handleResultClick(item)}
                          />
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}
            </>
          )}

          {/* ---- Person filmography view ---- */}
          {!loading && personView && (
            <div>
              {/* Phase 1: ownership loading — show flat unsplit grid */}
              {ownershipLoading && personView.unowned.length > 0 && (
                <div>
                  <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                    {personView.unowned.map((credit, i) => (
                      <SearchResultCard
                        key={`pcred-all-${i}`}
                        item={{
                          title: credit.title,
                          year: credit.year,
                          poster_url: credit.poster_url,
                          media_type: credit.media_type,
                          tmdb_id: credit.tmdb_id,
                          isOwned: false,
                        }}
                        onClick={() =>
                          handleResultClick({
                            title: credit.title,
                            year: credit.year,
                            poster_url: credit.poster_url,
                            media_type: credit.media_type,
                            tmdb_id: credit.tmdb_id,
                            isOwned: false,
                          })
                        }
                      />
                    ))}
                  </div>
                </div>
              )}

              {/* Phase 2: ownership resolved — split into owned/unowned */}
              {!ownershipLoading && (
                <>
                  {/* Owned filmography — hidden when Find More is expanded */}
                  {!unownedExpanded && personView.owned.length > 0 && (
                    <div className="mb-5">
                      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        {personView.owned.map((credit, i) => (
                          <SearchResultCard
                            key={`pcred-owned-${i}`}
                            item={{
                              title: credit.title,
                              year: credit.year,
                              poster_url: credit.poster_url,
                              media_type: credit.media_type,
                              rating_key: credit.rating_key,
                              tmdb_id: credit.tmdb_id,
                              isOwned: true,
                            }}
                            onClick={() =>
                              handleResultClick({
                                title: credit.title,
                                year: credit.year,
                                poster_url: credit.poster_url,
                                media_type: credit.media_type,
                                rating_key: credit.rating_key,
                                tmdb_id: credit.tmdb_id,
                                isOwned: true,
                              })
                            }
                          />
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Find More — same collapsed button pattern as movies/TV */}
                  {!ownedExpanded && personView.unowned.length > 0 && (
                    <div>
                      {!unownedExpanded ? (
                        <button
                          type="button"
                          onClick={() => setUnownedExpanded(true)}
                          className="flex w-full items-center justify-center gap-2 py-3 text-[18px] font-medium text-white/80 transition-colors hover:text-white"
                        >
                          Find More
                          <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
                        </button>
                      ) : (
                        <>
                          <button
                            type="button"
                            onClick={() => setUnownedExpanded(false)}
                            className="mb-3 flex w-full items-center justify-center gap-2 text-[18px] font-semibold text-text-secondary hover:text-text-primary transition-colors"
                          >
                            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
                            Find More
                          </button>
                          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                            {personView.unowned.map((credit, i) => (
                              <SearchResultCard
                                key={`pcred-unowned-${i}`}
                                item={{
                                  title: credit.title,
                                  year: credit.year,
                                  poster_url: credit.poster_url,
                                  media_type: credit.media_type,
                                  tmdb_id: credit.tmdb_id,
                                  isOwned: false,
                                }}
                                onClick={() =>
                                  handleResultClick({
                                    title: credit.title,
                                    year: credit.year,
                                    poster_url: credit.poster_url,
                                    media_type: credit.media_type,
                                    tmdb_id: credit.tmdb_id,
                                    isOwned: false,
                                  })
                                }
                              />
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                  )}
                </>
              )}

              {personView.owned.length === 0 && personView.unowned.length === 0 && (
                <p className="py-8 text-center text-[18px] text-text-muted">No filmography found</p>
              )}
            </div>
          )}
        </div>
      )}
    </div>

    </>
  );
});

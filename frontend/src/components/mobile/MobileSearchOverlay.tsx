import { useState, useEffect, useRef, useCallback } from 'react';
import { useModalHost } from '@/contexts/ModalHostContext';
import {
  performSearch,
  fetchPersonCredits,
  batchCheckOwnership,

  getTitle,
  getYear,
  type SearchMovie,
  type SearchPerson,
  type SearchResults,
  type PersonCredit,
  warmResult,
  warmResults,
} from '@/services/searchApi';
import { mediaKey } from '@/services/ownership';
import { withBase } from '@/urlHelpers';
import { openWhenReady } from '@/services/modalPrefetch';
const MAX_SUGGESTIONS = 5;

/** Normalize title for dedup: lowercase, & → and, strip non-alphanum except spaces */
function dedupKey(title: string, year: number | null): string {
  const t = title.toLowerCase().replace(/&/g, 'and').replace(/[^a-z0-9 ]/g, '').replace(/\s+/g, ' ').trim();
  return `${t}_${year ?? ''}`;
}

interface PersonView {
  name: string;
  profileUrl: string | null;
  owned: PersonCredit[];
  unowned: PersonCredit[];
}

interface MobileSearchOverlayProps {
  initialQuery?: string;
  /** When set, go directly to this person's filmography on mount */
  initialPerson?: { name: string; personId: number };
  onClose: () => void;
}

export function MobileSearchOverlay({ initialQuery = '', initialPerson, onClose }: MobileSearchOverlayProps) {
  const [query, setQuery] = useState(initialPerson?.name ?? initialQuery);
  const [results, setResults] = useState<SearchResults | null>(null);
  const [loading, setLoading] = useState(false);
  const resultsRef = useRef<HTMLDivElement>(null);
  const { openMovie, openTv, close } = useModalHost();

  // Person filmography state
  const [personView, setPersonView] = useState<PersonView | null>(null);
  const [personLoading, setPersonLoading] = useState(false);
  const [ownershipLoading, setOwnershipLoading] = useState(false);
  const [findMoreExpanded, setFindMoreExpanded] = useState(false);

  const inputRef = useRef<HTMLInputElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const didInitialSearch = useRef(false);

  // Warm the rows on screen right away so a first open is like a second.
  useEffect(() => {
    if (!results) return;
    return warmResults([...results.owned, ...results.unowned]);
  }, [results]);

  // Paint html background for safe-area coverage
  useEffect(() => {
    const prev = document.documentElement.style.background;
    document.documentElement.style.background = 'var(--color-kiosk-black)';
    return () => { document.documentElement.style.background = prev; };
  }, []);

  // Dismiss keyboard when user touches the results area
  const handleResultsTouch = useCallback(() => {
    inputRef.current?.blur();
  }, []);

  // ---------------------------------------------------------------------------
  // Person filmography fetch
  // ---------------------------------------------------------------------------

  const handlePersonClick = useCallback(async (person: SearchPerson) => {
    const tmdbId = person.id;
    if (!tmdbId) return;

    setPersonLoading(true);
    setPersonView(null);
    setFindMoreExpanded(false);

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

      // Phase 2: Batch check ownership in background, split when ready
      setOwnershipLoading(true);
      const movieCredits = credits.filter((c) => c.media_type !== 'tv');
      const tvCredits = credits.filter((c) => c.media_type === 'tv');
      const ownershipMap = await batchCheckOwnership({
        movies: movieCredits.map((c) => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id })),
        tv: tvCredits.map((c) => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id })),
        use_media_paths: true,
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
  }, []);

  // ---------------------------------------------------------------------------
  // onPersonSearch — called from modal cast/crew clicks
  // ---------------------------------------------------------------------------

  const handlePersonSearch = useCallback((name: string, personId?: number) => {
    close(); // close the modal
    setQuery(name);
    setResults(null);
    setPersonView(null);
    if (personId) {
      handlePersonClick({ id: personId, name } as SearchPerson);
    } else {
      // Search by name and auto-select matching person
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
            setResults(data);
            setPersonLoading(false);
          }
        })
        .catch(() => {
          setPersonLoading(false);
        });
    }
  }, [close, handlePersonClick]);

  // Auto-focus on mount + trigger search or person lookup
  useEffect(() => {
    if (initialPerson) {
      handlePersonClick({ id: initialPerson.personId, name: initialPerson.name } as SearchPerson);
      return;
    }
    inputRef.current?.focus();
    if (initialQuery.trim().length >= 2 && !didInitialSearch.current) {
      didInitialSearch.current = true;
      doSearch(initialQuery.trim());
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const doSearch = useCallback(async (q: string) => {
    if (q.length < 2) {
      setResults(null);
      setLoading(false);
      return;
    }

    if (abortRef.current) abortRef.current.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);

    try {
      const res = await performSearch(q, controller.signal);
      if (!controller.signal.aborted) {
        setResults(res);
      }
    } catch (err: unknown) {
      if (err instanceof Error && err.name !== 'AbortError') {
        setResults({ owned: [], unowned: [], ownedPeople: [], unownedPeople: [] });
      }
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  }, []);

  const handleInput = useCallback(
    (value: string) => {
      setQuery(value);
      // If in person view, clear it when user types
      if (personView || personLoading) {
        setPersonView(null);
        setPersonLoading(false);
      }
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => doSearch(value.trim()), 300);
    },
    [doSearch, personView, personLoading],
  );

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (abortRef.current) abortRef.current.abort();
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, []);

  // --- Build unified suggestion list (Google-style: top 5, deduped) ---
  type Suggestion =
    | { kind: 'media'; item: SearchMovie }
    | { kind: 'person'; item: SearchPerson };

  const suggestions: Suggestion[] = [];
  if (results) {
    const seen = new Set<string>();
    // Owned media first
    for (const item of [...(results.owned ?? []), ...(results.unowned ?? [])]) {
      const key = dedupKey(getTitle(item), getYear(item));
      if (!seen.has(key)) {
        seen.add(key);
        suggestions.push({ kind: 'media', item });
      }
    }
    // People
    const seenPeople = new Set<string>();
    for (const p of [...(results.ownedPeople ?? []), ...(results.unownedPeople ?? [])]) {
      const key = p.name.toLowerCase();
      if (!seenPeople.has(key)) {
        seenPeople.add(key);
        suggestions.push({ kind: 'person', item: p });
      }
    }
  }
  const topSuggestions = suggestions.slice(0, MAX_SUGGESTIONS);

  const handleMediaClick = (item: SearchMovie, el?: HTMLElement | null) => {
    const tmdbId = item.tmdb_id || item.id;
    const rk = item.rating_key;
    const opts = {
      ratingKey: (item.isOwned && rk) ? rk : undefined,
      tmdbId: tmdbId ?? undefined,
      origin: 'search' as const,
      onPersonSearch: handlePersonSearch,
      searchQuery: query.trim() || undefined,
    };
    if (!opts.ratingKey && !opts.tmdbId) return;
    // The popup opens only once it can show fully composed (the tapped row
    // shows a pressed state meanwhile, 600 ms cap).
    const kind = item.media_type === 'tv' ? 'tv' : 'movie';
    void openWhenReady(kind, opts.ratingKey ? Number(opts.ratingKey) : undefined,
      opts.tmdbId ? Number(opts.tmdbId) : undefined, el ?? null,
      () => (kind === 'tv' ? openTv(opts) : openMovie(opts)));
    // Don't close search — user may come back from modal
  };

  // Handle filmography credit click
  const handleCreditClick = (credit: PersonCredit) => {
    const opts = {
      ratingKey: (credit.isOwned && credit.rating_key) ? credit.rating_key : undefined,
      tmdbId: credit.tmdb_id ?? undefined,
      origin: 'search' as const,
      onPersonSearch: handlePersonSearch,
    };
    if (!opts.ratingKey && !opts.tmdbId) return;
    if (credit.media_type === 'tv') {
      openTv(opts);
    } else {
      openMovie(opts);
    }
  };

  const handleClear = () => {
    setQuery('');
    setResults(null);
    setPersonView(null);
    setPersonLoading(false);
    setOwnershipLoading(false);
    setFindMoreExpanded(false);
    inputRef.current?.focus();
  };

  const handleBack = () => {
    if (personView || personLoading) {
      setPersonView(null);
      setPersonLoading(false);
      return;
    }
    onClose();
  };

  // Show search bar only when NOT in person view
  const inPersonMode = personView || personLoading;

  return (
    <div className="fixed inset-0 z-[100] bg-kiosk-black flex flex-col" style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}>
      {/* Header: search bar OR person header — stays fixed above scroll */}
      {inPersonMode ? (
        /* Person header */
        <div className="flex items-center gap-3 px-4 py-3 border-b border-white/10 bg-kiosk-black shrink-0">
          <button
            type="button"
            onClick={handleBack}
            className="flex h-10 w-10 items-center justify-center text-white/70"
            aria-label="Back"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          {personView?.profileUrl && (
            <div className="h-10 w-10 flex-shrink-0 overflow-hidden rounded-full bg-white/10">
              <img src={personView.profileUrl} alt="" className="h-full w-full object-cover" />
            </div>
          )}
          <p className="text-[18px] font-semibold text-white truncate">
            {personView?.name ?? query}
          </p>
        </div>
      ) : (
        /* Search bar */
        <div className="flex items-center gap-3 px-4 py-3 border-b border-white/10 bg-kiosk-black shrink-0">
          <button
            type="button"
            onClick={onClose}
            className="flex h-10 w-10 items-center justify-center text-white/70"
            aria-label="Back"
          >
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <polyline points="15 18 9 12 15 6" />
            </svg>
          </button>
          <input
            ref={inputRef}
            type="search"
            inputMode="search"
            enterKeyHint="search"
            value={query}
            onChange={(e) => handleInput(e.target.value)}
            placeholder="Search for titles, people, and more..."
            className="flex-1 h-10 bg-transparent text-base text-white placeholder:text-white/40 outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={handleClear}
              className="text-white/50"
              aria-label="Clear"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M18 6 6 18" /><path d="m6 6 12 12" />
              </svg>
            </button>
          )}
        </div>
      )}

      {/* Results / Filmography — touch dismisses keyboard */}
      <div ref={resultsRef} data-scroll-container className="luma-scroll-y flex-1" onTouchStart={handleResultsTouch}>
        {/* Loading states */}
        {(loading || personLoading) && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <div className="h-8 w-8 animate-spin rounded-full border-2 border-white/20 border-t-accent" />
            {personLoading && <p className="text-sm text-white/40">Loading filmography...</p>}
          </div>
        )}

        {/* No results */}
        {!loading && !personLoading && !personView && results && topSuggestions.length === 0 && (
          <p className="py-16 text-center text-sm text-white/40">
            No results found
          </p>
        )}

        {/* Google-style suggestion list */}
        {!loading && !personLoading && !personView && topSuggestions.length > 0 && (
          <div>
            {topSuggestions.map((entry, i) => {
              const isPerson = entry.kind === 'person';
              const person = isPerson ? (entry.item as SearchPerson) : null;
              const media = !isPerson ? (entry.item as SearchMovie) : null;
              const label = isPerson ? person!.name : getTitle(media!);
              const year = !isPerson && media ? getYear(media) : null;
              const typeLabel = isPerson
                ? 'Person'
                : media?.media_type === 'tv'
                  ? 'TV Show'
                  : 'Movie';

              return (
                <button
                  key={`suggestion-${i}`}
                  type="button"
                  onClick={(e) => {
                    if (isPerson) {
                      handlePersonClick(person!);
                    } else {
                      handleMediaClick(media!, e.currentTarget);
                    }
                  }}
                  onTouchStart={() => { if (media) void warmResult(media); }}
                  onFocus={() => { if (media) void warmResult(media); }}
                  className="luma-pressable flex w-full items-center justify-between px-5 py-4 text-left active:bg-white/5"
                >
                  <span className="truncate text-[17px] italic text-white">
                    {label}
                  </span>
                  <span className="ml-4 shrink-0 text-[13px] text-white/40">
                    {year ? `${year} · ${typeLabel}` : typeLabel}
                  </span>
                </button>
              );
            })}
          </div>
        )}

        {/* Person filmography */}
        {!personLoading && personView && (
          <div className="px-4 py-4">
            {/* Phase 1: ownership still loading — show flat unsplit list */}
            {ownershipLoading && personView.unowned.length > 0 && (
              <div>
                <h3 className="text-[16px] font-semibold text-white/70 mb-3">
                  Filmography
                  <span className="ml-2 inline-block h-3 w-3 animate-spin rounded-full border border-white/20 border-t-white/60 align-middle" />
                </h3>
                <div className="flex flex-col gap-0.5">
                  {personView.unowned.map((credit, i) => (
                    <CreditRow
                      key={`all-${i}`}
                      credit={credit}
                      onClick={() => handleCreditClick(credit)}
                    />
                  ))}
                </div>
              </div>
            )}

            {/* Phase 2: ownership resolved — split into owned/unowned */}
            {!ownershipLoading && (
              <>
                {/* In Library */}
                {personView.owned.length > 0 && !findMoreExpanded && (
                  <div className="mb-6">
                    <h3 className="text-[16px] font-semibold text-white/70 mb-3">In Your Library</h3>
                    <div className="flex flex-col gap-0.5">
                      {personView.owned.map((credit, i) => (
                        <CreditRow
                          key={`owned-${i}`}
                          credit={credit}
                          onClick={() => handleCreditClick(credit)}
                        />
                      ))}
                    </div>
                  </div>
                )}

                {/* Find More — collapsible button */}
                {personView.unowned.length > 0 && (
                  <div>
                    {!findMoreExpanded ? (
                      <button
                        type="button"
                        onClick={() => setFindMoreExpanded(true)}
                        className="flex w-full items-center justify-center gap-2 py-4 text-[16px] font-medium text-white/70 active:text-white transition-colors"
                      >
                        Find More
                        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"><path d="m6 9 6 6 6-6" /></svg>
                      </button>
                    ) : (
                      <>
                        <button
                          type="button"
                          onClick={() => setFindMoreExpanded(false)}
                          className="mb-3 flex w-full items-center justify-center gap-2 text-[16px] font-semibold text-white/60 active:text-white transition-colors"
                        >
                          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m15 18-6-6 6-6" /></svg>
                          Find More
                        </button>
                        <div className="flex flex-col gap-0.5">
                          {personView.unowned.map((credit, i) => (
                            <CreditRow
                              key={`unowned-${i}`}
                              credit={credit}
                              onClick={() => handleCreditClick(credit)}
                            />
                          ))}
                        </div>
                      </>
                    )}
                  </div>
                )}

                {personView.owned.length === 0 && personView.unowned.length === 0 && (
                  <p className="py-12 text-center text-sm text-white/40">No filmography found</p>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------


function CreditRow({ credit, onClick }: { credit: PersonCredit; onClick: () => void }) {
  const typeLabel = credit.media_type === 'tv' ? 'Show' : 'Movie';

  return (
    <button
      type="button"
      onClick={onClick}
      className="flex w-full items-center gap-4 px-4 py-3 text-left active:bg-white/5 rounded-lg"
    >
      <div className="h-[100px] w-[67px] flex-shrink-0 overflow-hidden rounded-lg bg-white/5">
        {credit.poster_url ? (
          <img
            src={credit.poster_url ? withBase(credit.poster_url) : undefined}
            alt={credit.title}
            className="h-full w-full object-cover"
            loading="lazy"
            onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center text-white/20 text-xs">
            N/A
          </div>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[16px] font-medium text-white truncate">{credit.title}</p>
        <p className="text-[13px] text-white/50 mt-1">
          {credit.year != null ? `${credit.year} \u00b7 ` : ''}{typeLabel}
        </p>
      </div>
    </button>
  );
}

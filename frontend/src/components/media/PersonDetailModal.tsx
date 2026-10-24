// ---------------------------------------------------------------------------
// PersonDetailModal — Full-screen modal for actor/director details.
//
// Dark theme, fit-to-viewport layout (no page scroll). Profile + name at top,
// collapsible bio, filter pills + sort dropdown, filmography grid fills
// remaining space with "Show more" expand.
// ---------------------------------------------------------------------------

import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { useModalHost } from '@/contexts/ModalHostContext';
import { useSwipeToDismiss } from '@/hooks/useSwipeToDismiss';
import { batchCheckOwnership } from '@/services/searchApi';
import { mediaKey } from '@/services/ownership';
import { apiFetch } from '../../apiClient';
// PERSON_FALLBACK_SVG no longer needed — profile image removed

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PersonData {
  name: string;
  profile_url: string | null;
  biography: string | null;
  birthday: string | null;
  deathday: string | null;
  birthplace: string | null;
  known_for: string | null;
}

interface Credit {
  tmdb_id: number;
  title: string;
  year: number | null;
  rating: number | null;
  vote_count: number;
  poster_url: string | null;
  media_type: string;
  credit_type?: string;
  department?: string;
  role?: string;
}

type FilterTab = 'movies' | 'tv' | 'director';
type SortBy = 'rating' | 'year' | 'title';

const SORT_LABELS: Record<SortBy, string> = {
  rating: 'Rating',
  year: 'Year',
  title: 'Title',
};

interface PersonDetailModalProps {
  personId: number;
  personName: string;
  onClose: () => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function PersonDetailModal({
  personId,
  personName,
  onClose,
}: PersonDetailModalProps) {
  const modalHost = useModalHost();
  const [person, setPerson] = useState<PersonData | null>(null);
  const [credits, setCredits] = useState<Credit[]>([]);
  const [loading, setLoading] = useState(true);
  const [bioOpen, setBioOpen] = useState(false);
  const [activeTab, setActiveTab] = useState<FilterTab>('movies');
  const [sortBy, setSortBy] = useState<SortBy>('rating');
  const [unownedOnly, setUnownedOnly] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const [ownedKeys, setOwnedKeys] = useState<Set<string>>(new Set());
  const [ownershipLoaded, setOwnershipLoaded] = useState(false);
  const { mode, isTablet } = useLayoutMode();
  const scrollRef = useRef<HTMLDivElement>(null);

  const isCompact = mode === 'compact';

  // Swipe-to-dismiss for compact mode
  const { handlers: swipeHandlers } = useSwipeToDismiss({
    scrollRef,
    onDismiss: onClose,
    enabled: isCompact && !bioOpen,
  });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const resp = await apiFetch(`/api/tmdb/person_by_id/${personId}/credits`);
        if (!resp.ok) throw new Error('Failed to fetch');
        const data = await resp.json();
        if (cancelled) return;
        setPerson(data.person);
        setCredits(data.credits ?? []);
      } catch {
        // leave empty
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [personId]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
    };
    window.addEventListener('keydown', handler, true);
    return () => window.removeEventListener('keydown', handler, true);
  }, [onClose]);

  // Batch-check ownership for credits so the sort/owned-badge logic works.
  // ownershipLoaded gates the Unowned filter so we never flash owned results.
  useEffect(() => {
    if (credits.length === 0) return;
    setOwnershipLoaded(false);
    let cancelled = false;
    (async () => {
      const movies = credits.filter(c => c.media_type === 'movie').map(c => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id }));
      const tv = credits.filter(c => c.media_type === 'tv').map(c => ({ title: c.title, year: c.year, tmdb_id: c.tmdb_id }));
      try {
        const map = await batchCheckOwnership({ movies, tv });
        if (cancelled) return;
        const owned = new Set<string>();
        for (const [key, val] of Object.entries(map)) {
          if (val.owned) owned.add(key);
        }
        setOwnedKeys(owned);
      } catch { /* ignore */ } finally {
        if (!cancelled) setOwnershipLoaded(true);
      }
    })();
    return () => { cancelled = true; };
  }, [credits]);

  const formatDate = useCallback((dateStr: string | null) => {
    if (!dateStr) return null;
    const d = new Date(dateStr + 'T00:00:00');
    return d.toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' });
  }, []);

  const availableTabs = useMemo(() => {
    const tabs: FilterTab[] = [];
    const hasMovies = credits.some(c => c.media_type === 'movie' && c.credit_type !== 'crew');
    const hasTv = credits.some(c => c.media_type === 'tv' && c.credit_type !== 'crew');
    const hasDirecting = credits.some(c => c.department === 'Directing');
    if (hasMovies) tabs.push('movies');
    if (hasTv) tabs.push('tv');
    if (hasDirecting) tabs.push('director');
    return tabs;
  }, [credits]);

  const isCreditOwned = useCallback(
    (c: Credit) => ownedKeys.has(mediaKey({ title: c.title, year: c.year, mediaType: c.media_type === 'tv' ? 'tv' : 'movie' })),
    [ownedKeys],
  );

  const filteredCredits = useMemo(() => {
    let result: Credit[];
    switch (activeTab) {
      case 'movies':
        result = credits.filter(c => c.media_type === 'movie' && c.credit_type !== 'crew');
        break;
      case 'tv':
        result = credits.filter(c => c.media_type === 'tv' && c.credit_type !== 'crew');
        break;
      case 'director':
        result = credits.filter(c => c.department === 'Directing');
        break;
    }
    // Apply Unowned filter — independent of sort
    if (unownedOnly) {
      result = result.filter(c => !isCreditOwned(c));
    }
    // Weighted rating fallback used as a tiebreaker for most sorts.
    const MIN_VOTES = 50;
    const weightedRating = (c: Credit) => {
      const v = c.vote_count ?? 0;
      const r = c.rating ?? 0;
      return (v / (v + MIN_VOTES)) * r + (MIN_VOTES / (v + MIN_VOTES)) * 5.0;
    };
    switch (sortBy) {
      case 'rating':
        result.sort((a, b) => weightedRating(b) - weightedRating(a));
        break;
      case 'year':
        result.sort((a, b) => (b.year ?? 0) - (a.year ?? 0) || weightedRating(b) - weightedRating(a));
        break;
      case 'title':
        result.sort((a, b) => a.title.localeCompare(b.title));
        break;
    }
    return result;
  }, [credits, activeTab, sortBy, unownedOnly, isCreditOwned]);

  // When Unowned is selected but ownership hasn't resolved yet, we must
  // suppress the list — otherwise we'd flash credits the user actually owns.
  const unownedPending = unownedOnly && !ownershipLoaded;

  // Compact uses a list view (10 rows initial, then "Show more" reveals the rest).
  // Expanded shows the full grid and scrolls.
  const initialItemLimit = 10;
  const renderableCredits = unownedPending ? [] : filteredCredits;
  const displayCredits = !isCompact || showAll
    ? renderableCredits
    : renderableCredits.slice(0, initialItemLimit);
  const hasMore = isCompact && renderableCredits.length > initialItemLimit;

  useEffect(() => { setShowAll(false); }, [activeTab]);

  // Cast/crew tap inside a credit's modal → open that person (stacked).
  const handlePersonSearch = useCallback((name: string, personId?: number) => {
    if (personId) modalHost.openPerson({ personId, personName: name });
  }, [modalHost]);

  const handleCreditClick = useCallback((tmdbId: number, mediaType: string) => {
    // Don't call onClose() — modalHost.openMovie/openTv now stacks the current
    // person so closing the credit modal restores this filmography. Pass
    // onPersonSearch so cast taps inside the credit modal aren't dead.
    if (mediaType === 'tv') {
      modalHost.openTv({ tmdbId, origin: 'search', onPersonSearch: handlePersonSearch });
    } else {
      modalHost.openMovie({ tmdbId, origin: 'search', onPersonSearch: handlePersonSearch });
    }
  }, [modalHost, handlePersonSearch]);

  const displayName = person?.name ?? personName;

  const tabLabels: Record<FilterTab, string> = {
    movies: 'Movies',
    tv: 'TV shows',
    director: 'Director',
  };

  const cardStyle: React.CSSProperties = isCompact
    ? {
        position: 'absolute',
        top: 'max(env(safe-area-inset-top, 0px), 6px)',
        left: 0, right: 0, bottom: 0,
        borderTopLeftRadius: '14px',
        borderTopRightRadius: '14px',
      }
    : {
        position: 'relative',
        maxWidth: '1500px',
        width: '100%',
        height: isTablet ? '100%' : '90vh',
        borderRadius: isTablet ? 0 : '20px',
        boxShadow: isTablet ? 'none' : '0 32px 100px rgba(0,0,0,0.8)',
      };

  const pad = isCompact ? '16px' : '48px';

  return createPortal(
    <div className="luma-bleed"
      data-modal="PersonDetailModal"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10000,
        display: 'flex',
        alignItems: isCompact ? 'stretch' : isTablet ? 'stretch' : 'center',
        justifyContent: 'center',
        backgroundColor: isCompact ? 'rgba(0,0,0,0.6)' : isTablet ? 'transparent' : 'var(--color-kiosk-black)',
        padding: isCompact ? 0 : isTablet ? 0 : '20px',
      }}
    >
      <div
        style={{
          ...cardStyle,
          overflow: 'hidden',
          backgroundColor: 'var(--color-kiosk-black)',
          display: 'flex',
          flexDirection: 'column',
        }}
      >
        {/* Slide indicator — compact only, signals swipe-to-dismiss affordance */}
        {isCompact && (
          <div
            style={{
              flexShrink: 0,
              display: 'flex',
              justifyContent: 'center',
              padding: '8px 0 4px',
              pointerEvents: 'none',
            }}
          >
            <div
              style={{
                width: '40px',
                height: '4px',
                borderRadius: '999px',
                background: 'rgba(255,255,255,0.25)',
              }}
            />
          </div>
        )}

        {/* Loading */}
        {loading && (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 1 }}>
            <div style={{
              width: '48px', height: '48px', borderRadius: '50%',
              border: '3px solid rgba(255,255,255,0.15)',
              borderTopColor: 'rgba(255,255,255,0.7)',
              animation: 'personModalSpin 0.8s linear infinite',
            }} />
          </div>
        )}

        {/* Content — flex column, everything fits in viewport */}
        {!loading && (
          <div
            ref={scrollRef}
            onTouchStart={isCompact ? swipeHandlers.onTouchStart : undefined}
            onTouchMove={isCompact ? swipeHandlers.onTouchMove : undefined}
            onTouchEnd={isCompact ? swipeHandlers.onTouchEnd : undefined}
            data-scroll-container={isCompact ? true : undefined}
            className={isCompact ? 'luma-scroll-y' : undefined}
            style={{
              flex: 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              overflowY: isCompact ? 'auto' : 'hidden',
              overflowX: 'hidden',
              WebkitOverflowScrolling: 'touch',
              overscrollBehavior: isCompact ? 'contain' : undefined,
              touchAction: isCompact ? 'pan-y' : undefined,
            }}
          >
            {/* Top section: centered name + date + collapsible bio */}
            <div style={{
              flexShrink: 0,
              padding: `${isCompact ? '16px' : '24px'} ${pad} 0`,
              textAlign: 'center',
            }}>
              {/* Name + bio toggle */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px' }}>
                <h1 style={{
                  fontSize: isCompact ? '24px' : '32px',
                  fontWeight: 700,
                  color: '#fff',
                  margin: 0,
                  lineHeight: 1.2,
                }}>
                  {displayName}
                </h1>
                {person?.biography && (
                  <button
                    type="button"
                    onClick={() => setBioOpen(true)}
                    aria-label="Show biography"
                    style={{
                      width: '32px',
                      height: '32px',
                      borderRadius: '50%',
                      border: 'none',
                      background: 'rgba(255,255,255,0.08)',
                      color: 'rgba(255,255,255,0.5)',
                      cursor: 'pointer',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      flexShrink: 0,
                      transition: 'all 0.15s',
                    }}
                  >
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <circle cx="12" cy="12" r="10" />
                      <line x1="12" y1="16" x2="12" y2="12" />
                      <line x1="12" y1="8" x2="12.01" y2="8" />
                    </svg>
                  </button>
                )}
              </div>
              {/* Birth/death + birthplace */}
              {person?.birthday && (
                <div style={{
                  display: 'flex',
                  flexWrap: 'wrap',
                  gap: '8px',
                  marginTop: '6px',
                  fontSize: isCompact ? '14px' : '16px',
                  color: 'rgba(255,255,255,0.6)',
                  justifyContent: 'center',
                }}>
                  <span>
                    {formatDate(person.birthday)}
                    {person?.deathday && ` — ${formatDate(person.deathday)}`}
                  </span>
                  {person?.birthplace && (
                    <>
                      <span style={{ color: 'rgba(255,255,255,0.3)' }}>•</span>
                      <span>{person.birthplace}</span>
                    </>
                  )}
                </div>
              )}

            </div>

            {/* Filter pills — fit in one row */}
            <div style={{
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: isCompact ? '6px' : '8px',
              padding: `${isCompact ? '12px' : '16px'} ${pad} ${isCompact ? '10px' : '14px'}`,
              flexWrap: 'nowrap',
            }}>
              {availableTabs.length > 1 && availableTabs.map((tab) => {
                const isActive = activeTab === tab;
                const count = tab === 'movies' ? credits.filter(c => c.media_type === 'movie' && c.credit_type !== 'crew').length
                  : tab === 'tv' ? credits.filter(c => c.media_type === 'tv' && c.credit_type !== 'crew').length
                  : credits.filter(c => c.department === 'Directing').length;
                return (
                  <button
                    key={tab}
                    type="button"
                    onClick={() => setActiveTab(tab)}
                    style={{
                      flex: isCompact ? '1 1 0' : '0 1 auto',
                      minWidth: 0,
                      padding: isCompact ? '10px 12px' : '11px 24px',
                      borderRadius: '50px',
                      border: isActive ? '1px solid rgba(59,130,246,0.4)' : '1px solid rgba(255,255,255,0.12)',
                      background: isActive ? 'rgba(59,130,246,0.2)' : 'rgba(255,255,255,0.06)',
                      color: isActive ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.6)',
                      fontSize: isCompact ? '14px' : '16px',
                      fontWeight: isActive ? 600 : 500,
                      cursor: 'pointer',
                      transition: 'all 0.15s',
                      display: 'inline-flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      gap: '5px',
                      whiteSpace: 'nowrap',
                    }}
                  >
                    {tabLabels[tab]}
                    <span style={{ color: 'rgba(255,255,255,0.35)', fontWeight: 400 }}>{count}</span>
                  </button>
                );
              })}
            </div>

            {/* Subtle sorter — Unowned acts as an independent filter chip that
                can combine with any rating/year/title sort. */}
            {(filteredCredits.length > 0 || unownedOnly) && (
              <div
                style={{
                  flexShrink: 0,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  gap: '8px',
                  padding: isCompact ? `4px ${pad} 6px` : `4px ${pad} 10px`,
                }}
              >
                <span
                  style={{
                    fontSize: isCompact ? '11px' : '12px',
                    fontWeight: 500,
                    color: 'rgba(255,255,255,0.35)',
                  }}
                >
                  Sort
                </span>
                <div style={{ display: 'flex', gap: isCompact ? '2px' : '4px', flexWrap: 'nowrap', alignItems: 'center' }}>
                  {(Object.keys(SORT_LABELS) as SortBy[]).map((opt) => {
                    const isActive = sortBy === opt;
                    return (
                      <button
                        key={opt}
                        type="button"
                        onClick={() => setSortBy(opt)}
                        style={{
                          padding: isCompact ? '4px 8px' : '6px 14px',
                          borderRadius: isCompact ? '6px' : '999px',
                          border: 'none',
                          background: isActive ? 'rgba(255,255,255,0.08)' : 'transparent',
                          color: isActive ? 'rgba(255,255,255,0.9)' : 'rgba(255,255,255,0.5)',
                          fontSize: isCompact ? '12px' : '14px',
                          fontWeight: isActive ? 600 : 500,
                          cursor: 'pointer',
                          transition: 'color 0.15s, background 0.15s',
                        }}
                      >
                        {SORT_LABELS[opt]}
                      </button>
                    );
                  })}
                  {/* Divider between sort + filter so Unowned reads as a filter */}
                  <span
                    aria-hidden
                    style={{
                      width: '1px',
                      height: isCompact ? '12px' : '14px',
                      margin: isCompact ? '0 4px' : '0 6px',
                      background: 'rgba(255,255,255,0.12)',
                    }}
                  />
                  <button
                    type="button"
                    onClick={() => setUnownedOnly(v => !v)}
                    aria-pressed={unownedOnly}
                    style={{
                      padding: isCompact ? '4px 8px' : '6px 14px',
                      borderRadius: isCompact ? '6px' : '999px',
                      border: unownedOnly ? '1px solid rgba(59,130,246,0.4)' : '1px solid transparent',
                      background: unownedOnly ? 'rgba(59,130,246,0.2)' : 'transparent',
                      color: unownedOnly ? 'rgba(255,255,255,0.95)' : 'rgba(255,255,255,0.5)',
                      fontSize: isCompact ? '12px' : '14px',
                      fontWeight: unownedOnly ? 600 : 500,
                      cursor: 'pointer',
                      transition: 'color 0.15s, background 0.15s, border-color 0.15s',
                    }}
                  >
                    Unowned
                  </button>
                </div>
              </div>
            )}

            {/* Filmography grid + show more — fills remaining space (non-compact)
                Expanded always scrolls; compact relies on the outer wrapper. */}
            <div style={{
              flex: isCompact ? 'none' : 1,
              minHeight: 0,
              display: 'flex',
              flexDirection: 'column',
              padding: `0 ${pad} ${isCompact ? '10px' : '24px'}`,
              overflowY: isCompact ? 'visible' : 'auto',
              overflowX: 'hidden',
              WebkitOverflowScrolling: 'touch',
              overscrollBehavior: 'contain',
              transform: isCompact ? undefined : 'translateZ(0)',
              willChange: isCompact ? undefined : 'scroll-position',
              contain: isCompact ? undefined : 'layout style paint',
            }}>
              {unownedPending ? (
                <div style={{
                  display: 'flex', flexDirection: 'column',
                  alignItems: 'center', justifyContent: 'center',
                  padding: '40px 0', gap: '12px',
                  color: 'rgba(255,255,255,0.5)',
                }}>
                  <div style={{
                    width: '28px', height: '28px', borderRadius: '50%',
                    border: '2px solid rgba(255,255,255,0.12)',
                    borderTopColor: 'rgba(255,255,255,0.6)',
                    animation: 'personModalSpin 0.8s linear infinite',
                  }} />
                  <span style={{ fontSize: '13px' }}>Checking library…</span>
                </div>
              ) : filteredCredits.length === 0 ? (
                <p style={{ color: 'rgba(255,255,255,0.5)', textAlign: 'center', fontSize: '15px', marginTop: '24px' }}>
                  {unownedOnly ? 'Nothing unowned in this category.' : 'No credits found for this filter.'}
                </p>
              ) : isCompact ? (
                /* Compact: list view \u2014 small poster thumbnail + title/year */
                <div style={{
                  display: 'flex',
                  flexDirection: 'column',
                }}>
                  {displayCredits.map((credit) => (
                    <div
                      key={`${credit.media_type}-${credit.tmdb_id}`}
                      onClick={() => handleCreditClick(credit.tmdb_id, credit.media_type)}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '12px',
                        padding: '10px 0',
                        cursor: 'pointer',
                        borderBottom: '1px solid rgba(255,255,255,0.06)',
                      }}
                    >
                      <div style={{
                        width: '46px',
                        height: '69px',
                        flexShrink: 0,
                        borderRadius: '6px',
                        overflow: 'hidden',
                        background: 'rgba(255,255,255,0.06)',
                      }}>
                        {credit.poster_url ? (
                          <img
                            src={credit.poster_url}
                            alt={credit.title}
                            loading="lazy"
                            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                          />
                        ) : (
                          <div style={{
                            width: '100%', height: '100%',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            color: 'rgba(255,255,255,0.3)', fontSize: '10px', padding: '4px',
                            wordBreak: 'break-word', textAlign: 'center',
                          }}>
                            {credit.title}
                          </div>
                        )}
                      </div>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '3px' }}>
                          {isCreditOwned(credit) && (
                            <span
                              aria-label="In library"
                              style={{
                                width: '7px',
                                height: '7px',
                                borderRadius: '50%',
                                background: '#4ade80',
                                flexShrink: 0,
                                boxShadow: '0 0 6px rgba(74,222,128,0.5)',
                              }}
                            />
                          )}
                          <p style={{
                            fontSize: '15px',
                            fontWeight: 500,
                            color: 'rgba(255,255,255,0.95)',
                            margin: 0,
                            lineHeight: 1.3,
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                            display: '-webkit-box',
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: 'vertical',
                          }}>
                            {credit.title}
                          </p>
                        </div>
                        <p style={{
                          fontSize: '13px',
                          color: 'rgba(255,255,255,0.55)',
                          margin: 0,
                          lineHeight: 1.3,
                        }}>
                          {credit.year ?? ''}
                          {credit.year && credit.media_type === 'tv' ? ' \u00B7 ' : ''}
                          {credit.media_type === 'tv' ? 'TV' : ''}
                          {credit.rating && credit.rating > 0 ? (
                            <>
                              {(credit.year || credit.media_type === 'tv') ? ' \u00B7 ' : ''}
                              <span style={{ color: 'rgba(255,255,255,0.7)' }}>{credit.rating.toFixed(1)}</span>
                            </>
                          ) : ''}
                        </p>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                /* Expanded: poster grid. Rows size naturally — gridAutoRows: 1fr
                   was forcing equal-height rows which thrashed layout on scroll. */
                <div style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(6, minmax(0, 1fr))',
                  gap: '16px',
                  alignItems: 'start',
                }}>
                  {displayCredits.map((credit) => (
                    <div
                      key={`${credit.media_type}-${credit.tmdb_id}`}
                      onClick={() => handleCreditClick(credit.tmdb_id, credit.media_type)}
                      style={{
                        cursor: 'pointer',
                        textAlign: 'center',
                        contain: 'layout style paint',
                      }}
                    >
                      <div style={{
                        width: '100%',
                        aspectRatio: '2 / 3',
                        position: 'relative',
                        borderRadius: '8px',
                        overflow: 'hidden',
                        background: 'rgba(255,255,255,0.06)',
                        marginBottom: '6px',
                      }}>
                        {credit.poster_url ? (
                          <img
                            src={credit.poster_url}
                            alt={credit.title}
                            loading="lazy"
                            decoding="async"
                            style={{
                              width: '100%',
                              height: '100%',
                              objectFit: 'cover',
                              display: 'block',
                            }}
                          />
                        ) : (
                          <div style={{
                            position: 'absolute',
                            top: 0, left: 0,
                            width: '100%', height: '100%',
                            display: 'flex', alignItems: 'center', justifyContent: 'center',
                            color: 'rgba(255,255,255,0.3)', fontSize: '11px', padding: '6px',
                            wordBreak: 'break-word',
                          }}>
                            {credit.title}
                          </div>
                        )}
                      </div>
                      <p style={{
                        fontSize: '15px', fontWeight: 500,
                        color: 'rgba(255,255,255,0.9)',
                        margin: '0 0 2px', lineHeight: 1.3,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {credit.title}
                      </p>
                      <p style={{ fontSize: '14px', color: 'rgba(255,255,255,0.5)', margin: 0 }}>
                        {credit.year ?? '\u00A0'}
                      </p>
                    </div>
                  ))}
                </div>
              )}

            </div>

            {/* Show more — minimal, no container */}
            {hasMore && (
              <button
                type="button"
                onClick={() => setShowAll(v => !v)}
                style={{
                  flexShrink: 0,
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'center',
                  gap: '4px',
                  padding: `6px 0 ${isCompact ? '14px' : '18px'}`,
                  background: 'none',
                  border: 'none',
                  color: 'rgba(255,255,255,0.7)',
                  fontSize: isCompact ? '16px' : '18px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  width: '100%',
                  transition: 'color 0.15s',
                }}
              >
                {showAll ? 'Show less' : 'Show more'}
                <svg
                  width="20" height="20" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" strokeWidth="2"
                  strokeLinecap="round" strokeLinejoin="round"
                  style={{
                    transition: 'transform 0.3s',
                    transform: showAll ? 'rotate(180deg)' : 'rotate(0deg)',
                    opacity: 0.6,
                  }}
                >
                  <polyline points="6 9 12 15 18 9" />
                </svg>
              </button>
            )}
          </div>
        )}
      </div>

      <style>{`@keyframes personModalSpin { to { transform: rotate(360deg); } }`}</style>

      {/* Bio modal overlay */}
      {bioOpen && person?.biography && (
        <div className="luma-bleed"
          onClick={() => setBioOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            zIndex: 10001,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: 'rgba(0,0,0,0.8)',
            padding: isCompact ? '20px' : '40px',
          }}
        >
          <div
            onClick={(e) => e.stopPropagation()}
            data-scroll-container
            className="luma-scroll-y"
            style={{
              maxWidth: '700px',
              width: '100%',
              maxHeight: '80vh',
              overflowY: 'auto',
              WebkitOverflowScrolling: 'touch',
              overscrollBehavior: 'contain',
              touchAction: 'pan-y',
              backgroundColor: 'var(--color-kiosk-black)',
              borderRadius: '20px',
              padding: isCompact ? '28px 24px' : '40px 44px',
              boxShadow: '0 32px 100px rgba(0,0,0,0.6)',
            }}
          >
            <h2 style={{
              fontSize: isCompact ? '22px' : '26px',
              fontWeight: 700,
              color: '#fff',
              margin: '0 0 20px',
            }}>
              {displayName}
            </h2>
            <p style={{
              fontSize: isCompact ? '18px' : '20px',
              lineHeight: 1.7,
              color: 'rgba(255,255,255,0.8)',
              margin: 0,
            }}>
              {person.biography}
            </p>
          </div>
        </div>
      )}
    </div>,
    document.body,
  );
}

/**
 * ManualMatchPicker — shared full-screen picker for confirming a media identity
 * before a grab-to-library. Used by BOTH the movie and TV sources panels when a
 * release has no confident TMDb match (e.g. an ungated indexer row, or a modal
 * that opened without a tmdbId).
 *
 * Flow: search box (seeded with the release title) → TMDb match-search →
 * grid of poster / title / year / type-chip results → select → onSelect(match).
 * Cancel blocks the grab (onCancel).
 *
 * Touch-first: >=44px targets, works mobile / tablet / kiosk. Matches the
 * glassmorphism visual language of the sources panels (dark surfaces, blurred
 * backdrop, rounded cards).
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import { createPortal } from 'react-dom';
import { tmdbMatchSearch, type TmdbMatchResult, type MediaType } from '@/services/allSourcesApi';
import { posterUrl } from '@/utils/image';
import { apiUrl } from '@/urlHelpers';

interface ManualMatchPickerProps {
  /** Initial query — usually the release title. */
  initialQuery: string;
  /** Optional media-type hint to narrow the search. */
  mediaType?: MediaType;
  onSelect: (match: { tmdb_id: number; media_type: MediaType; title: string; year: number | null }) => void;
  onCancel: () => void;
}

export const MATCH_PICKER_Z = 10100;

export function ManualMatchPicker({
  initialQuery,
  mediaType,
  onSelect,
  onCancel,
}: ManualMatchPickerProps) {
  const [query, setQuery] = useState(initialQuery);
  const [results, setResults] = useState<TmdbMatchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const runSearch = useCallback(async (q: string) => {
    if (!q.trim()) {
      setResults([]);
      setSearched(false);
      return;
    }
    setLoading(true);
    const res = await tmdbMatchSearch(q, mediaType);
    setResults(res);
    setSearched(true);
    setLoading(false);
  }, [mediaType]);

  // Auto-search on mount with the seeded query.
  useEffect(() => {
    runSearch(initialQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Debounced search as the user types.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => runSearch(query), 400);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query, runSearch]);

  // ESC cancels (capture phase so nested modals don't also handle it).
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCancel();
      }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [onCancel]);

  // Portaled to <body> above every Sources screen (z 10001-10005) and the
  // desktop modal, so it is never stuck behind them.
  return createPortal(
    <div className="luma-fullscreen luma-bleed"
      role="dialog"
      aria-label="Match title"
      data-match-picker=""
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: MATCH_PICKER_Z,
        background: 'rgba(0,0,0,0.75)',
        backdropFilter: 'blur(20px)',
        WebkitBackdropFilter: 'blur(20px)',
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        padding: '24px clamp(16px, 4vw, 48px)',
      }}
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div
        style={{
          width: '100%',
          maxWidth: '840px',
          display: 'flex',
          flexDirection: 'column',
          gap: '16px',
          height: '100%',
          minHeight: 0,
        }}
      >
        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexShrink: 0 }}>
          <button
            type="button"
            onClick={onCancel}
            aria-label="Cancel"
            style={{
              width: '48px', height: '48px', flexShrink: 0, borderRadius: '50%',
              background: 'rgba(255,255,255,0.1)', border: '1px solid rgba(255,255,255,0.15)',
              color: '#fff', cursor: 'pointer',
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}
          >
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
          <div style={{ display: 'flex', flexDirection: 'column' }}>
            <h2 style={{ margin: 0, fontSize: '20px', fontWeight: 700, color: '#fff' }}>Match this title</h2>
            <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.55)', lineHeight: 1.4 }}>
              Pick the correct title so the download can be named &amp; filed.
            </span>
          </div>
        </div>

        {/* Search box */}
        <div style={{ position: 'relative', flexShrink: 0 }}>
          <svg
            width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.5)"
            strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ position: 'absolute', left: '16px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
          >
            <circle cx="11" cy="11" r="8" />
            <line x1="21" y1="21" x2="16.65" y2="16.65" />
          </svg>
          <input
            type="text"
            value={query}
            autoFocus
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search movies & shows…"
            style={{
              width: '100%', height: '52px', boxSizing: 'border-box',
              padding: '0 16px 0 46px', borderRadius: '14px',
              background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.15)',
              color: '#fff', fontSize: '16px', outline: 'none',
            }}
          />
        </div>

        {/* Results */}
        <div
          style={{
            flex: 1, minHeight: 0, overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y',
          }}
        >
          {loading && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '16px', padding: '48px 0', color: 'rgba(255,255,255,0.5)' }}>
              <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)', borderRadius: '50%', animation: 'mmpSpin 0.8s linear infinite' }} />
              <span style={{ fontSize: '15px' }}>Searching…</span>
            </div>
          )}

          {!loading && searched && results.length === 0 && (
            <div style={{ textAlign: 'center', padding: '48px 16px', color: 'rgba(255,255,255,0.5)', fontSize: '15px' }}>
              No matches. Try a different search.
            </div>
          )}

          {!loading && results.length > 0 && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(140px, 1fr))',
                gap: '14px',
                paddingBottom: '16px',
              }}
            >
              {results.map((r) => {
                const poster = posterUrl(r.poster_path, 'w342');
                return (
                  <button
                    key={`${r.media_type}-${r.tmdb_id}`}
                    type="button"
                    onClick={() => onSelect({ tmdb_id: r.tmdb_id, media_type: r.media_type, title: r.title, year: r.year })}
                    style={{
                      display: 'flex', flexDirection: 'column', textAlign: 'left',
                      padding: 0, border: '1px solid rgba(255,255,255,0.1)', borderRadius: '14px',
                      background: 'rgba(255,255,255,0.05)', color: '#fff', cursor: 'pointer',
                      overflow: 'hidden', minHeight: '44px',
                    }}
                  >
                    <div style={{ position: 'relative', width: '100%', aspectRatio: '2 / 3', background: 'rgba(255,255,255,0.06)' }}>
                      {poster ? (
                        <img
                          src={apiUrl(`/api/img/tmdb?path=${encodeURIComponent(r.poster_path ?? '')}&size=w200`)}
                          alt={r.title}
                          loading="lazy"
                          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
                        />
                      ) : (
                        <div style={{ width: '100%', height: '100%', display: 'flex', alignItems: 'center', justifyContent: 'center', color: 'rgba(255,255,255,0.3)', fontSize: '13px', padding: '8px', textAlign: 'center' }}>
                          No image
                        </div>
                      )}
                      <span
                        style={{
                          position: 'absolute', top: '8px', left: '8px',
                          padding: '3px 8px', borderRadius: '8px', fontSize: '11px', fontWeight: 700,
                          background: r.media_type === 'tv' ? 'rgba(96,165,250,0.85)' : 'rgba(187,134,252,0.85)',
                          color: '#fff',
                        }}
                      >
                        {r.media_type === 'tv' ? 'TV' : 'Movie'}
                      </span>
                    </div>
                    <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      <span style={{ fontSize: '14px', fontWeight: 600, lineHeight: 1.3, display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}>
                        {r.title}
                      </span>
                      <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.5)' }}>
                        {r.year ?? '—'}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>
      <style>{`@keyframes mmpSpin { to { transform: rotate(360deg); } }`}</style>
    </div>,
    document.body,
  );
}

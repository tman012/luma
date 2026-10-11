import { useState, useEffect, useMemo } from 'react';
import { addToSonarr, type TvSeason } from '@/services/sonarrApi';
import { FocusZone } from '@/components/tv/FocusZone';
import { useFocusable } from '@/hooks/useFocusable';
import type { TvShowDetail } from '@/utils/modalParsers';
import { countAiredEpisodes, formatAirDate, findNextEpisode } from './tvSourcesUtils';

interface OngoingShowPromptProps {
  show: TvShowDetail;
  seasons: TvSeason[];
  tvdbId: number;
  tmdbId?: number;
  onClose: () => void;
  /** Called after the show is successfully added + monitored in Sonarr. */
  onAdded: () => void;
}

/**
 * Shown when the user opens Sources for an in-progress show that isn't in
 * Sonarr yet. Browsing individual releases isn't offered for a still-airing
 * show — the single action is to add it, monitor it, and grab everything that
 * has already aired (future episodes are then picked up automatically).
 */
export function OngoingShowPrompt({
  show,
  seasons,
  tvdbId,
  tmdbId: _tmdbId,
  onClose,
  onAdded,
}: OngoingShowPromptProps) {
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState(false);

  const airedCount = useMemo(() => countAiredEpisodes(seasons), [seasons]);

  // Prefer TMDb's next_episode_to_air; fall back to the earliest unaired
  // episode derived from per-episode air dates (covers between-seasons breaks
  // where TMDb leaves next_episode_to_air null).
  const next = useMemo(() => {
    if (show.nextEpisodeToAir?.airDate) {
      return {
        airDate: show.nextEpisodeToAir.airDate,
        seasonNumber: show.nextEpisodeToAir.seasonNumber,
        episodeNumber: show.nextEpisodeToAir.episodeNumber,
      };
    }
    return findNextEpisode(seasons);
  }, [show.nextEpisodeToAir, seasons]);
  const nextAir = formatAirDate(next?.airDate);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.stopPropagation(); onClose(); }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose]);

  const handleAdd = async () => {
    if (adding || added) return;
    setAdding(true);
    setError(false);
    try {
      // Monitor everything + search missing → grabs all aired episodes now,
      // future episodes are picked up automatically as they air.
      const result = await addToSonarr(tvdbId, show.title, 'all', true);
      if (result.success || (result.message && result.message.includes('already'))) {
        setAdded(true);
        setTimeout(() => { onAdded(); onClose(); }, 1500);
      } else {
        setAdding(false);
        setError(true);
        setTimeout(() => setError(false), 3000);
      }
    } catch {
      setAdding(false);
      setError(true);
      setTimeout(() => setError(false), 3000);
    }
  };

  const addButtonFocus = useFocusable({
    focusKey: 'ongoing-add',
    zoneId: 'ongoing-show-prompt',
    onEnter: handleAdd,
  });

  const buttonLabel = added
    ? 'Added & Monitoring!'
    : adding
      ? 'Adding…'
      : error
        ? 'Failed — Try Again'
        : airedCount > 0
          ? `Monitor and Fetch ${airedCount} latest Episode${airedCount === 1 ? '' : 's'}`
          : 'Monitor & Fetch Latest Episodes';

  return (
    <div
      style={{ position: 'fixed', inset: 0, background: '#05050a', zIndex: 100000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Show backdrop — fills the whole screen (visible inside the card too via
          the card's frosted glass). pointerEvents none so clicking it closes. */}
      {show.backdropUrl && (
        <div
          aria-hidden
          style={{
            position: 'absolute',
            inset: 0,
            backgroundImage: `url(${show.backdropUrl})`,
            backgroundSize: 'cover',
            backgroundPosition: 'center',
            opacity: 0.6,
            transform: 'scale(1.04)',
            pointerEvents: 'none',
          }}
        />
      )}
      {/* Dimming scrim over the backdrop (keeps it readable, dims in + out). */}
      <div
        aria-hidden
        style={{
          position: 'absolute',
          inset: 0,
          background: 'radial-gradient(ellipse at center, rgba(5,5,10,0.30) 0%, rgba(5,5,10,0.62) 100%)',
          pointerEvents: 'none',
        }}
      />

      <FocusZone zoneId="ongoing-show-prompt" orientation="vertical" trapFocus onEscape={onClose}>
        <div
          style={{
            position: 'relative',
            background: 'rgba(12,10,20,0.55)',
            backdropFilter: 'blur(18px)',
            WebkitBackdropFilter: 'blur(18px)',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: '20px',
            padding: '32px 28px',
            maxWidth: '460px',
            width: '100%',
            boxShadow: '0 24px 60px rgba(0,0,0,0.55)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Currently airing badge */}
          <div style={{ display: 'flex', justifyContent: 'center', marginBottom: '14px' }}>
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '7px',
              padding: '6px 14px',
              borderRadius: '999px',
              background: 'rgba(74,222,128,0.12)',
              border: '1px solid rgba(74,222,128,0.3)',
              color: '#4ade80',
              fontSize: '12px',
              fontWeight: 700,
              textTransform: 'uppercase',
              letterSpacing: '0.6px',
            }}>
              <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: '#4ade80', boxShadow: '0 0 8px #4ade80' }} />
              Currently Airing
            </span>
          </div>

          <h2 style={{ marginBottom: '6px', fontSize: '24px', fontWeight: 700, textAlign: 'center', color: 'white', textShadow: '0 2px 8px rgba(0,0,0,0.5)' }}>
            {show.title}
          </h2>
          <p style={{ marginBottom: '24px', fontSize: '14px', textAlign: 'center', color: 'rgba(255,255,255,0.55)', fontWeight: 500, letterSpacing: '0.3px' }}>
            In Progress
          </p>

          {/* Stat rows */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1px', marginBottom: '24px', borderRadius: '14px', overflow: 'hidden', border: '1px solid rgba(255,255,255,0.08)' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px', background: 'rgba(255,255,255,0.05)' }}>
              <span style={{ fontSize: '14px', color: 'rgba(255,255,255,0.6)' }}>Episodes aired</span>
              <span style={{ fontSize: '15px', fontWeight: 700, color: '#fff' }}>
                {airedCount > 0 ? airedCount : '—'}
                {show.numberOfEpisodes ? <span style={{ color: 'rgba(255,255,255,0.4)', fontWeight: 500 }}> / {show.numberOfEpisodes}</span> : null}
              </span>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '14px 16px', background: 'rgba(255,255,255,0.05)' }}>
              <span style={{ fontSize: '14px', color: 'rgba(255,255,255,0.6)' }}>Next episode</span>
              <span style={{ fontSize: '15px', fontWeight: 700, color: '#fff', textAlign: 'right' }}>
                {nextAir ? (
                  <>
                    {next?.seasonNumber != null && next?.episodeNumber != null && (
                      <span style={{ color: 'rgba(255,255,255,0.4)', fontWeight: 500 }}>
                        S{next.seasonNumber}E{next.episodeNumber}{'  '}
                      </span>
                    )}
                    {nextAir}
                  </>
                ) : 'TBA'}
              </span>
            </div>
          </div>

          {/* Single action */}
          <button
            ref={addButtonFocus.ref}
            {...addButtonFocus.focusProps}
            type="button"
            onClick={handleAdd}
            disabled={adding || added}
            style={{
              width: '100%',
              padding: '16px',
              borderRadius: '50px',
              border: 'none',
              background: added ? '#4ade80' : error ? '#ef4444' : '#ffffff',
              color: added ? '#000' : error ? '#fff' : '#000',
              fontSize: '15px',
              fontWeight: 700,
              cursor: adding || added ? 'default' : 'pointer',
              transition: 'all 0.2s',
              opacity: adding ? 0.7 : 1,
            }}
          >
            {buttonLabel}
          </button>
          <p style={{ marginTop: '12px', fontSize: '12px', textAlign: 'center', color: 'rgba(255,255,255,0.35)' }}>
            Grabs aired episodes now and downloads new ones as they air.
          </p>
        </div>
      </FocusZone>
    </div>
  );
}

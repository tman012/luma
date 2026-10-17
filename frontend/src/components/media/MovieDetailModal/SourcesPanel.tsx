import { useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef } from 'react';
import { srcAbove, srcCancelBtn, SRC_HEADING_COLOR, NO_SOURCES, sourcesCount } from '../sourcesTheme';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { useMovieSourcesData } from './useMovieSourcesData';
import { FocusZone } from '@/components/tv/FocusZone';
import { useFocusable } from '@/hooks/useFocusable';
import { ManualMatchPicker } from '@/components/media/ManualMatchPicker';
import { grabToLibrary } from '@/services/allSourcesApi';
import { useIndexerConfigured } from '@/services/sourcesAvailability';
import { START_GLOW } from '@/components/start/StartUI';
import {
  TrackerSeedPills,
  SegmentRow,
  orderSourceTypes,
  orderResolutions,
  needsLibraryGrab,
  grabDisabledReason,
  COMPACT_SOURCES_WIZARD_HEIGHT,
  COMPACT_SOURCES_SHEET_MAX_HEIGHT,
  COMPACT_SOURCES_RESULTS_HEIGHT,
  scrollChildIntoContainer,
  type MovieGrabContext,
  IndexerQueryOverride,
} from '@/components/media/sourcesShared';
import {
  displayResolution,
  pinRecommendedFirst,
  pickBestInGroup,
  isSameRelease,
} from '@/components/media/releaseRecommend';
import {
  getBadgeColor,
  buildQualityGroups,
  type ProcessedRelease,
  type SourceTypeGroup,
  type QualitySegment,
} from './sourcesUtils';
import { apiFetch } from '../../../apiClient';

/** Stable per-release identity for grab/expand UI state. Indexer rows can have
 *  an empty guid, so fall back to downloadUrl/infoHash/title — otherwise every
 *  empty-guid row would share one "grabbing/grabbed/expanded" state. */
function releaseKey(r: ProcessedRelease): string {
  return r.guid || r.downloadUrl || r.infoHash || r.title;
}

// ---------------------------------------------------------------------------
// Expanded-mode sub-components (FocusZone / DPAD)
// ---------------------------------------------------------------------------

function SourcesBackButton({ onClick, large, focusKey, zoneId }: {
  onClick: () => void;
  large?: boolean;
  focusKey: string;
  zoneId: string;
}) {
  const { ref, focusProps } = useFocusable({ focusKey, zoneId, onEnter: onClick });
  const iconSz = large ? 36 : 30;
  return (
    <button
      ref={ref}
      {...focusProps}
      type="button"
      onClick={onClick}
      style={{
        position: 'absolute', top: '16px', left: '12px', zIndex: 10,
        background: 'transparent', border: 'none', padding: '8px',
        color: 'white', cursor: 'pointer',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        transition: 'opacity 0.15s ease',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.7'; }}
      onMouseLeave={(e) => { e.currentTarget.style.opacity = '1'; }}
      aria-label="Back"
    >
      <svg width={iconSz} height={iconSz} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>
  );
}

/** Compact sheet back control — large chevron, no box/border chrome. */
function CompactSourcesBack({ onClick }: { onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-label="Back"
      style={{
        flexShrink: 0,
        background: 'transparent',
        border: 'none',
        color: 'rgba(255,255,255,0.85)',
        cursor: 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: '6px 2px',
      }}
    >
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
        <polyline points="15 18 9 12 15 6" />
      </svg>
    </button>
  );
}

function SourcesScreen({ children, zIndex, onBack, backdropUrl, clearLogoUrl, movieTitle, large, backFocusKey, backZoneId, isWeb }: {
  children: React.ReactNode;
  zIndex: number;
  onBack: () => void;
  backdropUrl?: string | null;
  clearLogoUrl?: string | null;
  movieTitle?: string;
  large?: boolean;
  backFocusKey: string;
  backZoneId: string;
  isWeb?: boolean;
}) {
  return (
    <div
      style={{
        position: 'fixed', inset: 0, backgroundColor: 'var(--color-kiosk-black)', backgroundImage: START_GLOW, zIndex,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        padding: '40px clamp(20px, 5vw, 60px)',
      }}
    >
      {backdropUrl && (
        <img
          src={backdropUrl} alt=""
          style={{
            position: 'absolute', inset: 0, width: '100%', height: '100%',
            objectFit: 'cover', objectPosition: 'center 20%', opacity: isWeb ? 0.18 : 0.35, pointerEvents: 'none',
            zIndex: 0,
          }}
        />
      )}
      {isWeb && (
        <div
          style={{
            position: 'absolute', inset: 0,
            background: 'linear-gradient(rgba(0,0,0,0.4) 0%, rgba(0,0,0,0.6) 100%)',
            pointerEvents: 'none', zIndex: 0,
          }}
        />
      )}
      <SourcesBackButton onClick={onBack} large={large} focusKey={backFocusKey} zoneId={backZoneId} />
      <div
        style={{
          position: 'absolute', top: large ? '86px' : '74px', left: '20px', right: '20px',
          display: 'flex', justifyContent: 'center', pointerEvents: 'none',
          zIndex: 2,
        }}
      >
        {clearLogoUrl ? (
          <img
            src={clearLogoUrl} alt={movieTitle ?? ''}
            style={{
              maxHeight: '80px', maxWidth: '400px', width: 'auto', height: 'auto',
              objectFit: 'contain', filter: 'drop-shadow(0 2px 12px rgba(0,0,0,0.6))',
            }}
          />
        ) : movieTitle ? (
          <h3
            style={{
              margin: 0, textAlign: 'center', fontSize: '36px', fontWeight: 700,
              color: 'rgba(255,255,255,0.9)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}
          >
            {movieTitle}
          </h3>
        ) : null}
      </div>
      {children}
    </div>
  );
}

function FocusableQualityTile({ group, onClick, tileStyle, isTablet, isWeb, zoneId }: {
  group: QualitySegment;
  onClick: () => void;
  tileStyle: React.CSSProperties;
  isTablet: boolean;
  isWeb?: boolean;
  zoneId: string;
}) {
  const { ref, focusProps } = useFocusable({
    focusKey: `src-q-${group.quality}`,
    zoneId,
    onEnter: onClick,
  });
  return (
    <button ref={ref} {...focusProps} type="button" onClick={onClick} style={tileStyle} data-source-tile>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <span style={{ fontSize: (isTablet || isWeb) ? '24px' : '20px', fontWeight: 700, whiteSpace: 'nowrap' }}>{displayResolution(group.quality)}</span>
        <span style={{ fontSize: (isTablet || isWeb) ? '16px' : '14px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>{sourcesCount(group.totalCount)}</span>
      </div>
    </button>
  );
}


function FocusableSourceTile({ st, onClick, tileStyle, isTablet, isWeb, zoneId }: {
  st: SourceTypeGroup;
  onClick: () => void;
  tileStyle: React.CSSProperties;
  isTablet: boolean;
  isWeb?: boolean;
  zoneId: string;
}) {
  const { ref, focusProps } = useFocusable({
    focusKey: `src-st-${st.type}`,
    zoneId,
    onEnter: onClick,
  });
  return (
    <button ref={ref} {...focusProps} type="button" onClick={onClick} style={tileStyle} data-source-tile>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '12px' }}>
        <span style={{ fontSize: (isTablet || isWeb) ? '24px' : '20px', fontWeight: 700, whiteSpace: 'nowrap' }}>{st.type}</span>
        <span style={{ fontSize: (isTablet || isWeb) ? '16px' : '14px', color: 'rgba(255,255,255,0.5)', whiteSpace: 'nowrap' }}>{sourcesCount(st.items.length)}</span>
      </div>
      <TrackerSeedPills releases={st.items} colorFor={getBadgeColor} max={6} />
    </button>
  );
}

function FocusableReleaseRow({ release, onClick, index, zoneId, isWeb, recommended }: {
  release: ProcessedRelease;
  onClick: () => void;
  index: number;
  zoneId: string;
  isWeb?: boolean;
  recommended?: boolean;
}) {
  const { ref, focusProps } = useFocusable({
    focusKey: `src-rel-${index}`,
    zoneId,
    onEnter: onClick,
  });
  const displayName = release.title;
  const ageDays = release.age != null ? Math.round(release.age) : null;
  return (
    <div
      ref={ref}
      {...focusProps}
      role="button"
      onClick={onClick}
      style={{
        display: 'flex', flexDirection: 'column', gap: '10px',
        padding: isWeb ? '22px 28px' : '18px 22px',
        background: release.rejected ? '#2a2520' : 'rgba(255,255,255,0.04)',
        border: `2px solid ${recommended ? 'rgba(74, 222, 128, 0.45)' : (release.rejected ? 'rgba(245, 158, 11, 0.25)' : 'rgba(255, 255, 255, 0.15)')}`,
        borderLeft: `3px solid ${recommended ? '#4ade80' : (release.rejected ? 'rgba(245, 158, 11, 0.5)' : 'rgba(255,255,255,0.15)')}`,
        borderRadius: '16px',
        cursor: 'pointer', minHeight: '72px', transition: 'background 0.2s ease, border-color 0.2s ease',
        position: 'relative', zIndex: 1, outline: 'none',
        WebkitTapHighlightColor: 'transparent',
      }}
      data-source-row
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
          <span style={{
            display: 'inline-block', padding: isWeb ? '5px 14px' : '4px 12px', borderRadius: '10px', fontSize: isWeb ? '15px' : '13px', fontWeight: 700,
            background: getBadgeColor(release.indexer).bg, color: getBadgeColor(release.indexer).fg,
            border: `1px solid ${getBadgeColor(release.indexer).border}`,
          }}>
            {release.indexer}
          </span>
        </div>
        <span style={{ fontSize: isWeb ? '17px' : '14px', fontWeight: 500, color: '#fff', wordBreak: 'break-word', lineHeight: 1.4 }}>
          {displayName}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', fontSize: isWeb ? '17px' : '15px', paddingLeft: '2px' }}>
        <span style={{ color: '#fff', fontWeight: 600, textAlign: 'left' }}>
          {release.sizeGb ? `${release.sizeGb} GB` : '\u2014'}
        </span>
        <span style={{ color: 'rgba(255,255,255,0.7)', textAlign: 'center' }}>
          {ageDays != null ? `${ageDays}d ago` : '\u2014'}
        </span>
        <span style={{ color: '#4ade80', textAlign: 'right', fontWeight: 600 }}>
          {release.seeders} seeders
        </span>
      </div>
      {release.rejected && release.rejectionReasons && release.rejectionReasons.length > 0 && (
        <div style={{ fontSize: isWeb ? '13px' : '11px', color: '#f59e0b', opacity: 0.85, lineHeight: 1.4 }}>
          {release.rejectionReasons.join(' \u2022 ')}
        </div>
      )}
    </div>
  );
}

function ConfirmPanel({ target, confirmLoading, onCancel, onDownload, zoneId, error }: {
  target: ProcessedRelease;
  confirmLoading: boolean;
  onCancel: () => void;
  onDownload: () => void;
  zoneId: string;
  error?: string | null;
}) {
  const disabledReason = grabDisabledReason(target);
  const cancelFocus = useFocusable({
    focusKey: 'src-confirm-cancel',
    zoneId,
    onEnter: onCancel,
  });
  const downloadFocus = useFocusable({
    focusKey: 'src-confirm-download',
    zoneId,
    onEnter: onDownload,
  });
  return (
    <>
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', marginBottom: '16px', position: 'relative', zIndex: 1 }}>
        <span style={{
          display: 'inline-block', padding: '6px 18px', borderRadius: '12px', fontSize: '15px', fontWeight: 700,
          background: getBadgeColor(target.indexer).bg, color: getBadgeColor(target.indexer).fg,
          border: `1px solid ${getBadgeColor(target.indexer).border}`,
        }}>
          {target.indexer}
        </span>
      </div>
      <div
        style={{
          background: 'linear-gradient(rgba(255,255,255,0.06), rgba(255,255,255,0.06)), #15102a', borderRadius: '20px', padding: '36px',
          width: '520px', maxWidth: '90%', border: '1px solid rgba(255,255,255,0.08)',
          position: 'relative', zIndex: 1,
        }}
      >
        <p style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 500, color: '#fff', lineHeight: 1.5, wordBreak: 'break-word', textAlign: 'center' }}>
          {target.title}
        </p>
        {disabledReason && (
          <div style={{
            background: 'rgba(148,163,184,0.12)', border: '1px solid rgba(148,163,184,0.3)',
            borderRadius: '12px', padding: '12px 16px', marginBottom: '16px',
            fontSize: '13px', color: '#cbd5e1', lineHeight: 1.5, textAlign: 'center',
          }}>
            {disabledReason}
          </div>
        )}
        <div style={{ display: 'flex', gap: '20px', fontSize: '15px', justifyContent: 'center', flexWrap: 'wrap', marginBottom: error ? '16px' : '28px' }}>
          <span style={{ color: 'rgba(255,255,255,0.7)', fontWeight: 600 }}>
            {target.sizeGb ? `${target.sizeGb} GB` : '\u2014'}
          </span>
          <span style={{ color: 'rgba(255,255,255,0.55)' }}>
            {target.seeders} seeders
          </span>
        </div>
        {error && (
          <div style={{
            background: 'rgba(239, 68, 68, 0.15)',
            border: '1px solid rgba(239, 68, 68, 0.3)',
            borderRadius: '12px',
            padding: '12px 16px',
            marginBottom: '16px',
            fontSize: '13px',
            color: '#fca5a5',
            lineHeight: 1.5,
            textAlign: 'center',
          }}>
            {error}
          </div>
        )}
        <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
          <button
            ref={cancelFocus.ref}
            {...cancelFocus.focusProps}
            type="button" onClick={onCancel} disabled={confirmLoading}
            style={{
              padding: '16px 36px', borderRadius: '50px',
              ...srcCancelBtn,
              color: '#fff', fontSize: '18px', fontWeight: 600, minHeight: '56px',
              cursor: confirmLoading ? 'default' : 'pointer', transition: 'background 0.2s ease, border-color 0.2s ease',
            }}
          >
            Cancel
          </button>
          <button
            ref={downloadFocus.ref}
            {...downloadFocus.focusProps}
            type="button" onClick={onDownload} disabled={confirmLoading || !!disabledReason}
            style={{
              padding: '16px 40px', borderRadius: '50px',
              border: '1px solid #3b82f6', background: '#3b82f6',
              color: '#fff', fontSize: '18px', fontWeight: 600, minHeight: '56px',
              cursor: (confirmLoading || disabledReason) ? 'default' : 'pointer', transition: 'background 0.2s ease, border-color 0.2s ease',
              opacity: (confirmLoading || disabledReason) ? 0.5 : 1,
            }}
          >
            {confirmLoading ? 'Sending...' : 'Download'}
          </button>
        </div>
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// ExpandedSourcesView — full-screen overlay with FocusZone for DPAD
// ---------------------------------------------------------------------------

function ExpandedSourcesView({
  releases,
  loading,
  error,
  timedOut,
  slow: _slow,
  movieId,
  grabContext,
  title,
  indexerQuery,
  seedQuery = null,
  onIndexerQueryApply,
  onIndexerQueryClear,
  backdropUrl,
  clearLogoUrl,
  onClose,
  onGrabbed,
  isTablet,
  isWeb,
}: {
  releases: ProcessedRelease[];
  loading: boolean;
  error: boolean;
  timedOut: boolean;
  slow: boolean;
  movieId: number;
  grabContext: MovieGrabContext;
  title: string;
  indexerQuery: string | null;
  seedQuery?: string | null;
  onIndexerQueryApply: (query: string) => void;
  onIndexerQueryClear: () => void;
  backdropUrl?: string | null;
  clearLogoUrl?: string | null;
  onClose: () => void;
  onGrabbed?: () => void;
  isTablet: boolean;
  isWeb: boolean;
}) {
  const [selectedQuality, setSelectedQuality] = useState<string | null>(null);
  const [selectedSourceType, setSelectedSourceType] = useState<string | null>(null);
  const [seeingAll, setSeeingAll] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<ProcessedRelease | null>(null);
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [grabError, setGrabError] = useState<string | null>(null);
  const [matchPickerFor, setMatchPickerFor] = useState<ProcessedRelease | null>(null);

  // Stable callbacks for FocusZone onEscape (avoid re-registering zone every render)
  const clearQuality = useCallback(() => {
    setSelectedQuality(null);
    setSelectedSourceType(null);
    setSeeingAll(false);
  }, []);
  const clearConfirm = useCallback(() => setConfirmTarget(null), []);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        if (confirmTarget) setConfirmTarget(null);
        else if (seeingAll) setSeeingAll(false);
        else if (selectedSourceType) { setSelectedSourceType(null); setSeeingAll(false); }
        else if (selectedQuality) { setSelectedQuality(null); setSeeingAll(false); }
        else onClose();
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, confirmTarget, seeingAll, selectedSourceType, selectedQuality]);

  // Grab-to-library with an explicit tmdbId (used directly, or after the picker).
  const runLibraryGrab = useCallback(async (
    release: ProcessedRelease,
    identity: { tmdbId?: number; title: string; year: number | null },
  ) => {
    setConfirmLoading(true);
    setGrabError(null);
    const result = await grabToLibrary({
      downloadUrl: release.downloadUrl ?? '',
      torrentName: release.title,
      tmdbId: identity.tmdbId,
      mediaType: 'movie',
      title: identity.title,
      year: identity.year,
    });
    setConfirmLoading(false);
    if (result.success) {
      setConfirmTarget(null);
      setMatchPickerFor(null);
      window.dispatchEvent(new Event('download-grabbed'));
      if (onGrabbed) onGrabbed();
      return;
    }
    setGrabError(result.error || 'Failed to add download');
  }, [onGrabbed]);

  const handleConfirmDownload = useCallback(async () => {
    if (!confirmTarget) return;
    const disabled = grabDisabledReason(confirmTarget);
    if (disabled) { setGrabError(disabled); return; }

    // Rejected Arr row OR raw indexer row → grab-to-library pipeline.
    if (needsLibraryGrab(confirmTarget)) {
      // No confident identity → confirm the match first.
      // Keep modal media identity for rename — only open picker when missing.
      if (!grabContext.tmdbId) {
        setMatchPickerFor(confirmTarget);
        return;
      }
      await runLibraryGrab(confirmTarget, grabContext);
      return;
    }

    // Normal Radarr grab (unchanged path).
    setConfirmLoading(true);
    setGrabError(null);
    try {
      const resp = await apiFetch('/api/radarr/movie/grab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          guid: confirmTarget.guid,
          indexer_id: confirmTarget.indexerId,
          movie_id: movieId,
        }),
      });
      if (resp.ok) {
        setConfirmLoading(false);
        setConfirmTarget(null);
        window.dispatchEvent(new Event('download-grabbed'));
        if (onGrabbed) onGrabbed();
        return;
      }
      const data = await resp.json().catch(() => ({}));
      setGrabError(data.error || 'Failed to grab release');
    } catch {
      setGrabError('Network error — could not reach server');
    }
    setConfirmLoading(false);
  }, [confirmTarget, movieId, onGrabbed, grabContext, runLibraryGrab]);

  const qualityGroups = buildQualityGroups(releases);
  const selectedGroup = selectedQuality ? qualityGroups.find((g) => g.quality === selectedQuality) : null;
  const selectedReleases = selectedGroup && selectedSourceType
    ? selectedGroup.sourceTypes.find((st) => st.type === selectedSourceType)?.items ?? []
    : [];

  // Normalized wide rows (identical size) for the quality/type segment
  // selection — label + count on top, tracker top-seeder pills below (type
  // level). Mirrors the compact SegmentRow so labels like "WEB-DL" never wrap.
  const tileStyle: React.CSSProperties = {
    width: '100%',
    minHeight: '68px',
    padding: '16px 22px',
    borderRadius: '16px',
    border: '2px solid rgba(255, 255, 255, 0.15)',
    background: 'rgba(255,255,255,0.04)',
    color: '#fff', cursor: 'pointer',
    display: 'flex', flexDirection: 'column', alignItems: 'stretch', justifyContent: 'center',
    gap: '12px', transition: 'background 0.2s ease, border-color 0.2s ease',
    position: 'relative', zIndex: 1, outline: 'none',
    WebkitTapHighlightColor: 'transparent',
  };

  return (
    <>
      {/* Level 1: Quality selection */}
      <FocusZone zoneId="sources-level-1" orientation="grid" trapFocus onEscape={onClose}>
        <SourcesScreen zIndex={99} onBack={onClose} backdropUrl={backdropUrl} clearLogoUrl={clearLogoUrl} movieTitle={title} large={isTablet} backFocusKey="src-back-1" backZoneId="sources-level-1" isWeb={isWeb}>
          <div style={{ position: 'absolute', top: isTablet ? '88px' : '72px', left: 0, right: 0, zIndex: 2 }}>
            <IndexerQueryOverride
              mediaTitle={title}
              defaultQuery={seedQuery}
              appliedQuery={indexerQuery}
              onApply={onIndexerQueryApply}
              onClear={onIndexerQueryClear}
              disabled={loading}
            />
          </div>
          {loading && (
            <div data-sources-state="loading" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px', color: SRC_HEADING_COLOR, fontSize: '22px', ...srcAbove }}>
              <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)', borderRadius: '50%', animation: 'spin 0.8s linear infinite' }} />
              Searching for sources...
            </div>
          )}
          {!loading && error && (
            <div data-sources-state="error" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '8px', ...srcAbove }}>
              <span style={{ color: SRC_HEADING_COLOR, fontSize: '22px' }}>
                {timedOut ? 'Search timed out' : 'Failed to load sources'}
              </span>
            </div>
          )}
          {!loading && !error && releases.length === 0 && (
            <div data-sources-state="empty" style={{ color: SRC_HEADING_COLOR, fontSize: '22px', ...srcAbove }}>{NO_SOURCES}</div>
          )}
          {!loading && !error && qualityGroups.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px', position: 'relative', zIndex: 1 }}>
              {qualityGroups.map((group) => (
                <FocusableQualityTile
                  key={group.quality}
                  group={group}
                  onClick={() => { setSelectedQuality(group.quality); setSelectedSourceType(null); setSeeingAll(false); }}
                  tileStyle={tileStyle}
                  isTablet={isTablet}
                  isWeb={isWeb}
                  zoneId="sources-level-1"
                />
              ))}
            </div>
          )}
          <style>{`
            @keyframes spin { to { transform: rotate(360deg); } }
            @media (hover: hover) {
              [data-source-tile]:hover { background: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.25) !important; }
              [data-source-row]:hover { background: rgba(255,255,255,0.08) !important; border-color: rgba(255,255,255,0.25) !important; }
            }
          `}</style>
        </SourcesScreen>
      </FocusZone>

      {/* Level 2: Source type pills with inline recommended expand */}
      {selectedQuality && selectedGroup && !seeingAll && (
        <FocusZone zoneId="sources-level-2" orientation="grid" trapFocus onEscape={clearQuality}>
          <SourcesScreen zIndex={110} onBack={clearQuality} backdropUrl={backdropUrl} clearLogoUrl={clearLogoUrl} movieTitle={title} large={isTablet} backFocusKey="src-back-2" backZoneId="sources-level-2" isWeb={isWeb}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px', position: 'relative', zIndex: 1 }}>
              {selectedGroup.sourceTypes.map((st) => {
                const open = selectedSourceType === st.type;
                const recommended = open ? pickBestInGroup(st.items) : null;
                return (
                  <div key={st.type}>
                    <FocusableSourceTile
                      st={st}
                      onClick={() => {
                        setSelectedSourceType((prev) => prev === st.type ? null : st.type);
                        setSeeingAll(false);
                      }}
                      tileStyle={tileStyle}
                      isTablet={isTablet}
                      isWeb={isWeb}
                      zoneId="sources-level-2"
                    />
                    {open && recommended && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                        <p style={{ fontSize: '14px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: 0 }}>
                          Recommended
                        </p>
                        <FocusableReleaseRow
                          release={recommended}
                          onClick={() => { setConfirmTarget(recommended); setGrabError(null); }}
                          index={0}
                          zoneId="sources-level-2"
                          isWeb={isWeb}
                          recommended
                        />
                        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0 4px' }}>
                          {st.items.length > 1 ? (
                            <button
                              type="button"
                              onClick={() => setSeeingAll(true)}
                              style={{
                                display: 'inline-flex', alignItems: 'center', gap: '4px',
                                padding: 0, border: 'none', background: 'transparent',
                                color: 'rgba(255,255,255,0.4)', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                              }}
                            >
                              See all {st.items.length}
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 18 15 12 9 6" />
                              </svg>
                            </button>
                          ) : (
                            <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.35)' }}>{sourcesCount(st.items.length)}</span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </SourcesScreen>
        </FocusZone>
      )}

      {/* Level 4: See-all list for one source type */}
      {selectedQuality && selectedSourceType && seeingAll && (() => {
        const bucket = selectedReleases;
        const recommended = pickBestInGroup(bucket);
        const rows = pinRecommendedFirst(
          [...bucket].sort((a, b) => b.seeders - a.seeders),
          recommended,
        );
        return (
          <FocusZone zoneId="sources-level-4" orientation="vertical" trapFocus onEscape={() => setSeeingAll(false)}>
            <SourcesScreen zIndex={125} onBack={() => setSeeingAll(false)} backFocusKey="src-back-4" backZoneId="sources-level-4" isWeb={isWeb}>
              <div data-scroll-container className="luma-scroll-y" style={{ flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y', width: '100%', maxWidth: '900px', paddingTop: '40px', position: 'relative', zIndex: 1 }}>
                <h2 style={{ fontSize: '22px', fontWeight: 600, color: 'rgba(255,255,255,0.85)', textAlign: 'center', margin: '0 0 16px' }}>
                  {displayResolution(selectedQuality)} · {selectedSourceType}
                </h2>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                  {rows.map((release, i) => (
                    <FocusableReleaseRow
                      key={release.guid || releaseKey(release)}
                      release={release}
                      onClick={() => { setConfirmTarget(release); setGrabError(null); }}
                      index={i}
                      zoneId="sources-level-4"
                      isWeb={isWeb}
                      recommended={!!recommended && isSameRelease(release, recommended)}
                    />
                  ))}
                </div>
              </div>
            </SourcesScreen>
          </FocusZone>
        );
      })()}

      {/* Level 5: Confirm download */}
      {confirmTarget && (
        <FocusZone zoneId="sources-level-5" orientation="horizontal" trapFocus onEscape={clearConfirm}>
          <SourcesScreen zIndex={130} onBack={clearConfirm} backFocusKey="src-back-5" backZoneId="sources-level-5" isWeb={isWeb}>
            <ConfirmPanel
              target={confirmTarget}
              confirmLoading={confirmLoading}
              onCancel={clearConfirm}
              onDownload={handleConfirmDownload}
              zoneId="sources-level-5"
              error={grabError}
            />
          </SourcesScreen>
        </FocusZone>
      )}

      {/* Manual match picker — blocks grab until an identity is confirmed */}
      {matchPickerFor && (
        <ManualMatchPicker
          initialQuery={grabContext.title || matchPickerFor.title}
          mediaType="movie"
          onCancel={() => setMatchPickerFor(null)}
          onSelect={(m) => runLibraryGrab(matchPickerFor, { tmdbId: m.tmdb_id, title: m.title, year: m.year })}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Compact-mode sub-components (bottom sheet / wizard)
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;

type CompactStep = 'resolution' | 'type' | 'results';

function ReleaseCard({
  release,
  index,
  expanded,
  grabbing,
  grabbed,
  onToggle,
  onGrab,
  recommended,
}: {
  release: ProcessedRelease;
  index: number;
  expanded: boolean;
  grabbing: boolean;
  grabbed: boolean;
  onToggle: (guid: string) => void;
  onGrab: (release: ProcessedRelease) => void;
  recommended?: boolean;
}) {
  const badge = getBadgeColor(release.indexer);
  const ageDays = release.age != null ? Math.round(release.age) : null;
  const staggerDelay = index < 15 ? index * 30 : 0;
  const disabledReason = grabDisabledReason(release);

  return (
    <div
      role="button"
      onClick={() => onToggle(releaseKey(release))}
      style={{
        background: expanded ? 'rgba(255,255,255,0.08)' : 'rgba(255,255,255,0.04)',
        borderRadius: '14px',
        padding: '14px 16px',
        marginBottom: '8px',
        cursor: 'pointer',
        border: `1px solid ${recommended ? 'rgba(74, 222, 128, 0.45)' : (expanded ? badge.border : badge.border.replace('0.3)', '0.15)'))}`,
        borderLeft: `3px solid ${recommended ? '#4ade80' : badge.fg}`,
        transition: 'background 0.15s, border-color 0.15s',
        animation: `srcCardEnter 250ms ease-out ${staggerDelay}ms both`,
      }}
    >
      {/* Row 0: tracker name + rejection badge (name always shown) */}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginBottom: '8px', alignItems: 'center' }}>
        <span style={{
          display: 'inline-block', padding: '2px 9px', borderRadius: '8px', fontSize: '11px', fontWeight: 700,
          background: badge.bg, color: badge.fg, border: `1px solid ${badge.border}`,
        }}>
          {release.indexer}
        </span>
      </div>

      {/* Row 1: Title */}
      <div style={{ marginBottom: '8px' }}>
        <span style={{
          fontSize: '15px',
          fontWeight: 600,
          color: 'rgba(255,255,255,0.9)',
          lineHeight: 1.4,
          wordBreak: 'break-word',
        }}>
          {release.title}
        </span>
      </div>

      {/* Row 2: Size + Seeds + Age + CF score */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <span style={{
          fontSize: '13px',
          fontWeight: 600,
          color: 'rgba(255,255,255,0.5)',
          fontVariantNumeric: 'tabular-nums',
          flexShrink: 0,
        }}>
          {release.sizeGb ? `${release.sizeGb} GB` : '\u2014'}
        </span>

        <span style={{ display: 'flex', alignItems: 'center', gap: '3px', fontSize: '12px', fontWeight: 600, color: '#4ade80' }}>
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M12 19V5M5 12l7-7 7 7" />
          </svg>
          {release.seeders}
        </span>

        {ageDays != null && (
          <span style={{ fontSize: '11px', color: 'rgba(255,255,255,0.3)' }}>
            {ageDays === 0 ? 'Today' : `${ageDays}d`}
          </span>
        )}

        {release.customFormatScore > 0 && (
          <span style={{ fontSize: '11px', fontWeight: 600, color: '#fbbf24', marginLeft: 'auto' }}>
            CF +{release.customFormatScore}
          </span>
        )}
      </div>

      {/* Expanded: full title + download */}
      {expanded && (
        <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid rgba(255,255,255,0.08)' }}>
          {disabledReason && (
            <div style={{
              background: 'rgba(148,163,184,0.12)', border: '1px solid rgba(148,163,184,0.3)',
              borderRadius: '10px', padding: '10px 12px', marginBottom: '12px',
              fontSize: '12px', color: '#cbd5e1', lineHeight: 1.5, textAlign: 'center',
            }}>
              {disabledReason}
            </div>
          )}
          <button
            type="button"
            disabled={grabbing || grabbed || !!disabledReason}
            onClick={(e) => {
              e.stopPropagation();
              if (!grabbing && !grabbed && !disabledReason) onGrab(release);
            }}
            style={{
              width: '100%',
              height: '48px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              borderRadius: '12px',
              fontSize: '15px',
              fontWeight: 600,
              cursor: grabbing || grabbed || disabledReason ? 'default' : 'pointer',
              transition: 'all 0.2s',
              border: 'none',
              background: grabbed
                ? '#166534'
                : grabbing
                  ? '#1e3a5f'
                  : '#1d4ed8',
              color: '#fff',
              opacity: grabbing ? 0.8 : disabledReason ? 0.4 : 1,
            }}
          >
            {grabbed ? (
              <>
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="20 6 9 17 4 12" />
                </svg>
                Sent
              </>
            ) : grabbing ? (
              <>
                <div style={{
                  width: '16px', height: '16px',
                  border: '2px solid rgba(255,255,255,0.3)', borderTopColor: '#fff',
                  borderRadius: '50%', animation: 'srcSpin 0.8s linear infinite',
                }} />
                Sending...
              </>
            ) : (
              <>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M21 15v4a2 2 0 01-2 2H5a2 2 0 01-2-2v-4" />
                  <polyline points="7 10 12 15 17 10" />
                  <line x1="12" y1="15" x2="12" y2="3" />
                </svg>
                Download
              </>
            )}
          </button>
        </div>
      )}

      {/* Keyframe for stagger entrance */}
      {index === 0 && (
        <style>{`
          @keyframes srcCardEnter {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
          }
        `}</style>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CompactSourcesView — bottom sheet with wizard steps
// ---------------------------------------------------------------------------

function CompactSourcesView({
  releases,
  loading,
  error,
  timedOut,
  slow: _slow,
  movieId,
  grabContext,
  indexerQuery,
  seedQuery = null,
  onIndexerQueryApply,
  onIndexerQueryClear,
  onClose,
  onGrabbed,
  onAddToWatchlist,
}: {
  releases: ProcessedRelease[];
  loading: boolean;
  error: boolean;
  timedOut: boolean;
  slow: boolean;
  movieId: number;
  grabContext: MovieGrabContext;
  indexerQuery: string | null;
  seedQuery?: string | null;
  onIndexerQueryApply: (query: string) => void;
  onIndexerQueryClear: () => void;
  onClose: () => void;
  onGrabbed?: () => void;
  onAddToWatchlist?: () => void;
}) {
  // Wizard state
  const [step, setStep] = useState<CompactStep>('resolution');
  const [selectedResolution, setSelectedResolution] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<string | null>(null);

  // Results state
  const [sortMode, setSortMode] = useState<'seeders' | 'date'>('seeders');
  const [expandedGuid, setExpandedGuid] = useState<string | null>(null);
  const [grabbingGuid, setGrabbingGuid] = useState<string | null>(null);
  const [grabbedGuids, setGrabbedGuids] = useState<Set<string>>(() => new Set());
  const [grabErrorMsg, setGrabErrorMsg] = useState<string | null>(null);
  const [matchPickerFor, setMatchPickerFor] = useState<ProcessedRelease | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  /** Anchor for type-step expand scroll-into-view (SegmentRow + recommended). */
  const typeExpandAnchorRef = useRef<HTMLDivElement>(null);
  /** Anchor for expanded ReleaseCard (Download actions) scroll-into-view. */
  const releaseExpandAnchorRef = useRef<HTMLDivElement>(null);
  /** scrollTop of type list before expand; restored on collapse. */
  const typeListScrollTopRef = useRef<number | null>(null);

  // Sheet animation
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  // ESC to close
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        handleDismiss();
      }
    }
    window.addEventListener('keydown', handleKey, true);
    return () => window.removeEventListener('keydown', handleKey, true);
  }, [onClose]);

  // All results are shown by default (rejected already filtered upstream by the
  // Show-rejected toggle). Every resolution/source type present is reachable.
  const visibleReleases = releases;

  // Plain-text Override search: skip resolution→type wizard; one flat list.
  const flatOverride = !!indexerQuery;

  // When override toggles, reset wizard selection (flat list owns the UI).
  useEffect(() => {
    setSelectedResolution(null);
    setSelectedType(null);
    setExpandedGuid(null);
    if (flatOverride) {
      setSortMode('date');
      setStep('results');
    } else {
      setSortMode('seeders');
      setStep('resolution');
    }
  }, [flatOverride]);

  // Override flat list — fixed newest-first (age), then seeders.
  const overrideFlatReleases = useMemo(() => {
    const result = [...visibleReleases];
    result.sort((a, b) => (a.age ?? Infinity) - (b.age ?? Infinity) || b.seeders - a.seeders);
    return result;
  }, [visibleReleases]);

  // Available resolutions, highest → lowest (Unknown last).
  const availableQualities = useMemo(
    () => orderResolutions(visibleReleases.map(r => r.resolution)),
    [visibleReleases],
  );

  // Available types for selected resolution (curated order).
  const availableSourceTypes = useMemo(() => {
    if (!selectedResolution) return [];
    const filtered = visibleReleases.filter(r => r.resolution === selectedResolution);
    return orderSourceTypes(filtered.map(r => r.sourceType));
  }, [visibleReleases, selectedResolution]);

  // Auto-advance: if only 1 resolution, skip step 1 (not in override flat mode)
  useEffect(() => {
    if (flatOverride) return;
    if (!loading && !error && visibleReleases.length > 0 && step === 'resolution') {
      if (availableQualities.length === 1) {
        setSelectedResolution(availableQualities[0]!);
        setStep('type');
      }
    }
  }, [loading, error, visibleReleases, availableQualities, step]);

  // Auto-expand: if only 1 type, expand recommended under that pill (stay on type step)
  useEffect(() => {
    if (flatOverride) return;
    if (step === 'type' && availableSourceTypes.length === 1 && !selectedType) {
      setSelectedType(availableSourceTypes[0]!);
    }
  }, [step, availableSourceTypes, selectedType]);

  // See-all list for one type — recommended pinned, seeders desc (no tracker-pill chrome).
  const filteredReleases = useMemo(() => {
    if (!selectedResolution || !selectedType) return [];
    let result = visibleReleases.filter(
      (r) => r.resolution === selectedResolution && r.sourceType === selectedType,
    );
    if (sortMode === 'date') {
      result = [...result].sort((a, b) => (a.age ?? Infinity) - (b.age ?? Infinity) || b.seeders - a.seeders);
    } else {
      result = [...result].sort((a, b) => b.seeders - a.seeders);
    }
    const recommended = pickBestInGroup(result);
    return pinRecommendedFirst(result, recommended);
  }, [visibleReleases, selectedResolution, selectedType, sortMode]);

  const seeAllRecommended = useMemo(
    () => pickBestInGroup(filteredReleases),
    [filteredReleases],
  );

  // Counts per resolution
  const qualityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of visibleReleases) {
      counts[r.resolution] = (counts[r.resolution] ?? 0) + 1;
    }
    return counts;
  }, [visibleReleases]);

  // Counts per type (for selected resolution)
  const sourceTypeCounts = useMemo(() => {
    const filtered = selectedResolution
      ? visibleReleases.filter(r => r.resolution === selectedResolution)
      : visibleReleases;
    const counts: Record<string, number> = {};
    for (const r of filtered) {
      counts[r.sourceType] = (counts[r.sourceType] ?? 0) + 1;
    }
    return counts;
  }, [visibleReleases, selectedResolution]);

  // Guards against a rapid double-tap firing two grab POSTs before re-render.
  const grabInFlightRef = useRef(false);

  // Grab-to-library with an explicit identity (used directly, or after the picker).
  const runLibraryGrab = useCallback(async (
    release: ProcessedRelease,
    identity: { tmdbId?: number; title: string; year: number | null },
  ) => {
    if (grabInFlightRef.current) return;
    grabInFlightRef.current = true;
    const rk = releaseKey(release);
    setGrabbingGuid(rk);
    setGrabErrorMsg(null);
    try {
      const result = await grabToLibrary({
        downloadUrl: release.downloadUrl ?? '',
        torrentName: release.title,
        tmdbId: identity.tmdbId,
        mediaType: 'movie',
        title: identity.title,
        year: identity.year,
      });
      if (result.success) {
        setGrabbedGuids(prev => new Set(prev).add(rk));
        setMatchPickerFor(null);
        window.dispatchEvent(new Event('download-grabbed'));
        if (onGrabbed) setTimeout(() => onGrabbed(), 600);
        return;
      }
      setGrabErrorMsg(result.error || 'Failed to add download');
    } finally {
      setGrabbingGuid(null);
      grabInFlightRef.current = false;
    }
  }, [onGrabbed]);

  // Grab handler — routes rejected / indexer rows through grab-to-library.
  const handleGrab = useCallback(async (release: ProcessedRelease) => {
    if (grabInFlightRef.current) return;
    const disabled = grabDisabledReason(release);
    if (disabled) { setGrabErrorMsg(disabled); return; }

    // Rejected Arr row OR raw indexer row → grab-to-library pipeline.
    if (needsLibraryGrab(release)) {
      // No confident identity → confirm the match first (no POST yet).
      // Keep modal media identity for rename — only open picker when missing.
      if (!grabContext.tmdbId) {
        setGrabErrorMsg(null);
        setMatchPickerFor(release);
        return;
      }
      await runLibraryGrab(release, grabContext);
      return;
    }

    // Normal Radarr grab (unchanged path).
    grabInFlightRef.current = true;
    const rk = releaseKey(release);
    setGrabbingGuid(rk);
    setGrabErrorMsg(null);
    try {
      const resp = await apiFetch('/api/radarr/movie/grab', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          guid: release.guid,
          indexer_id: release.indexerId,
          movie_id: movieId,
        }),
      });
      if (resp.ok) {
        setGrabbedGuids(prev => new Set(prev).add(rk));
        window.dispatchEvent(new Event('download-grabbed'));
        if (onGrabbed) setTimeout(() => onGrabbed(), 600);
        return;
      }
      const data = await resp.json().catch(() => ({}));
      setGrabErrorMsg(data.error || 'Failed to grab release');
    } catch {
      setGrabErrorMsg('Network error — could not reach server');
    } finally {
      setGrabbingGuid(null);
      grabInFlightRef.current = false;
    }
  }, [movieId, onGrabbed, grabContext, runLibraryGrab]);

  const handleSelectType = useCallback((t: string) => {
    setSelectedType(prev => {
      if (prev === t) return null; // collapse — restore scroll in layout effect
      // Save list orientation only when expanding from collapsed (not type→type switch).
      if (prev == null && scrollRef.current) {
        typeListScrollTopRef.current = scrollRef.current.scrollTop;
      }
      return t;
    });
  }, []);

  // Type-step: pin expanded type block to top of inner scroller (recommended + See all
  // fully below); restore saved scrollTop on collapse. Sheet chrome stays fixed.
  useLayoutEffect(() => {
    if (step !== 'type') {
      // Leaving type step — drop pending restore so it cannot apply on a later visit.
      typeListScrollTopRef.current = null;
      return;
    }
    const container = scrollRef.current;
    if (!container) return;
    if (selectedType) {
      const anchor = typeExpandAnchorRef.current;
      if (anchor) scrollChildIntoContainer(container, anchor, 'start', 20);
    } else if (typeListScrollTopRef.current != null) {
      container.scrollTop = typeListScrollTopRef.current;
      typeListScrollTopRef.current = null;
    }
  }, [selectedType, step]);

  // When recommended/results ReleaseCard expands, jump so Download clears the clip.
  useLayoutEffect(() => {
    if (!expandedGuid) return;
    if (!flatOverride && step !== 'type' && step !== 'results') return;
    const container = scrollRef.current;
    if (!container) return;
    if (!flatOverride && step === 'type') {
      const anchor = typeExpandAnchorRef.current;
      if (anchor) scrollChildIntoContainer(container, anchor, 'start', 20);
    } else {
      const anchor = releaseExpandAnchorRef.current;
      if (anchor) scrollChildIntoContainer(container, anchor, 'nearest', 20);
    }
  }, [expandedGuid, step, flatOverride]);

  const handleToggleExpand = useCallback((guid: string) => {
    setExpandedGuid(prev => prev === guid ? null : guid);
  }, []);

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  // Drag-to-dismiss (touch)
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    e.stopPropagation();
    const target = e.target as HTMLElement;
    const isHandle = target.closest('[data-sheet-handle]');
    const scrollEl = scrollRef.current;
    const isAtTop = !scrollEl || scrollEl.scrollTop <= 0;
    if ((isHandle || isAtTop) && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    e.stopPropagation();
    const y = e.touches[0]!.clientY;
    const dy = y - dragRef.current.startY;
    if (dy > 0) {
      dragRef.current.currentY = y;
      setDragOffset(dy);
    }
  }, []);

  const onTouchEnd = useCallback((e?: React.TouchEvent) => {
    e?.stopPropagation();
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) {
      handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss]);

  const noSources = !loading && !error && visibleReleases.length === 0;
  const indexerConfigured = useIndexerConfigured();
  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  return (
    <>
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 200,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
        background: entered && !dismissing ? 'rgba(0,0,0,0.4)' : 'transparent',
        transition: 'background 0.25s ease',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) handleDismiss();
      }}
    >
      <div
        ref={sheetRef}
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
        style={{
          // resolution+type(+expand) share fixed footprint so bottom sheet never jumps
          height: (flatOverride || step === 'results') ? COMPACT_SOURCES_RESULTS_HEIGHT : COMPACT_SOURCES_WIZARD_HEIGHT,
          maxHeight: COMPACT_SOURCES_SHEET_MAX_HEIGHT,
          display: 'flex',
          flexDirection: 'column',
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          overflow: 'hidden',
          transform: `translateY(${sheetTranslate})`,
          transition: dragOffset > 0 ? 'none' : 'transform 0.3s cubic-bezier(0.32, 0.72, 0, 1)',
          willChange: 'transform',
        }}
      >
        {/* Sheet top chrome: type keeps back by pull tab; extra bottom pad vs resolution */}
        {!flatOverride && step === 'type' ? (
          <div
            data-sheet-handle
            style={{
              position: 'relative',
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '12px 0 32px',
              cursor: 'grab',
            }}
          >
            {availableQualities.length > 1 && (
              <div
                style={{ position: 'absolute', left: 4, top: '50%', transform: 'translateY(-50%)', zIndex: 1 }}
                onTouchStart={(e) => e.stopPropagation()}
              >
                <CompactSourcesBack onClick={() => { setSelectedResolution(null); setSelectedType(null); setStep('resolution'); }} />
              </div>
            )}
            <div style={{
              width: '40px',
              height: '5px',
              borderRadius: '3px',
              background: 'rgba(255,255,255,0.3)',
            }} />
          </div>
        ) : (
          <div
            data-sheet-handle
            style={{
              flexShrink: 0,
              display: 'flex',
              justifyContent: 'center',
              padding: '12px 0 20px',
              cursor: 'grab',
            }}
          >
            <div style={{
              width: '40px',
              height: '5px',
              borderRadius: '3px',
              background: 'rgba(255,255,255,0.3)',
            }} />
          </div>
        )}

        {/* Override chrome: enter via link when idle; hide entirely once active */}
        {!flatOverride && (
          <IndexerQueryOverride
            mediaTitle={grabContext.title}
            defaultQuery={seedQuery}
            appliedQuery={indexerQuery}
            onApply={onIndexerQueryApply}
            onClear={onIndexerQueryClear}
            disabled={loading}
          />
        )}

        {/* Loading */}
        {loading && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '24px', padding: '60px 0 80px' }}>
            <div style={{
              width: '56px', height: '56px',
              border: '3px solid #2c2c2e', borderTopColor: 'rgba(255,255,255,0.7)',
              borderRadius: '50%', animation: 'srcSpin 0.8s linear infinite',
            }} />
            <span style={{ fontSize: '16px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>
              Searching<span style={{ display: 'inline-block', width: '24px', textAlign: 'left' }}>
                <span style={{ animation: 'srcDot1 1.4s infinite' }}>.</span>
                <span style={{ animation: 'srcDot2 1.4s infinite' }}>.</span>
                <span style={{ animation: 'srcDot3 1.4s infinite' }}>.</span>
              </span>
            </span>
            <style>{`
              @keyframes srcSpin { to { transform: rotate(360deg) } }
              @keyframes srcDot1 { 0%, 20% { opacity: 0 } 40%, 100% { opacity: 1 } }
              @keyframes srcDot2 { 0%, 40% { opacity: 0 } 60%, 100% { opacity: 1 } }
              @keyframes srcDot3 { 0%, 60% { opacity: 0 } 80%, 100% { opacity: 1 } }
            `}</style>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '60px 0 80px' }}>
            <span style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>
              {timedOut ? 'Search timed out' : 'Failed to load sources'}
            </span>
          </div>
        )}

        {/* No trackers (and no Prowlarr): a clean empty state */}
        {noSources && indexerConfigured === false && (
          <div data-sources-empty="" style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 24px 60px' }}>
            <span style={{ fontSize: '17px', fontWeight: 600, color: 'rgba(255,255,255,0.7)', textAlign: 'center' }}>
              {NO_SOURCES}
            </span>
          </div>
        )}

        {/* No sources — prompt to add to watchlist */}
        {noSources && indexerConfigured === true && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 24px 60px', gap: '20px' }}>
            <span style={{ fontSize: '17px', fontWeight: 600, color: 'rgba(255,255,255,0.7)', textAlign: 'center' }}>
              {NO_SOURCES}
            </span>
            {indexerQuery && (
              <button
                type="button"
                onClick={onIndexerQueryClear}
                style={{
                  background: 'none', border: 'none', padding: '8px 12px',
                  color: 'rgba(255,255,255,0.45)', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                }}
              >
                Clear override
              </button>
            )}
            {onAddToWatchlist && (
              <button
                type="button"
                onClick={() => {
                  handleDismiss();
                  setTimeout(() => onAddToWatchlist(), 280);
                }}
                style={{
                  width: '100%',
                  maxWidth: '300px',
                  height: '56px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: '10px',
                  borderRadius: '16px',
                  fontSize: '16px',
                  fontWeight: 600,
                  cursor: 'pointer',
                  border: 'none',
                  background: '#ffffff',
                  color: '#000',
                }}
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <line x1="12" y1="5" x2="12" y2="19" />
                  <line x1="5" y1="12" x2="19" y2="12" />
                </svg>
                Add to Watchlist
              </button>
            )}
          </div>
        )}

        {/* === STEP 1: Resolution === */}
        {!flatOverride && !loading && !error && visibleReleases.length > 0 && step === 'resolution' && (
          <div
            data-sources-scrollable
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: 'auto',
              overflowX: 'hidden',
              WebkitOverflowScrolling: 'touch',
              touchAction: 'pan-y',
              padding: '8px 20px 60px',
            }}
          >
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select resolution
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {availableQualities.map(q => (
                <SegmentRow
                  key={q}
                  label={displayResolution(q)}
                  count={qualityCounts[q] ?? 0}
                  releases={[]}
                  colorFor={getBadgeColor}
                  showPills={false}
                  onClick={() => { setSelectedResolution(q); setSelectedType(null); setStep('type'); }}
                />
              ))}
            </div>
          </div>
        )}

        {/* === STEP 2: Type pills with inline recommended expand === */}
        {!flatOverride && !loading && !error && visibleReleases.length > 0 && step === 'type' && (
          <>
          <div
            ref={scrollRef}
            data-sources-scrollable
            style={{
              flex: 1,
              minHeight: 0,
              overflowY: 'auto',
              overflowX: 'hidden',
              WebkitOverflowScrolling: 'touch',
              touchAction: 'pan-y',
              padding: '16px 20px 60px',
            }}
          >
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 20px' }}>
              Select Type
            </p>
            {grabErrorMsg && (
              <div style={{
                background: 'rgba(239, 68, 68, 0.15)',
                border: '1px solid rgba(239, 68, 68, 0.3)',
                borderRadius: '12px',
                padding: '12px 16px',
                marginBottom: '12px',
                fontSize: '13px',
                color: '#fca5a5',
                lineHeight: 1.5,
                textAlign: 'center',
              }}>
                {grabErrorMsg}
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {availableSourceTypes.map(t => {
                const open = selectedType === t;
                const bucket = visibleReleases.filter(r => r.resolution === selectedResolution && r.sourceType === t);
                const rec = open ? pickBestInGroup(bucket) : null;
                return (
                  <div key={t} ref={open ? typeExpandAnchorRef : undefined}>
                    <SegmentRow
                      label={t}
                      count={sourceTypeCounts[t] ?? 0}
                      releases={bucket}
                      colorFor={getBadgeColor}
                      showPills={false}
                      onClick={() => handleSelectType(t)}
                    />
                    {open && rec && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px', padding: '0 2px' }}>
                        <div ref={expandedGuid === releaseKey(rec) ? releaseExpandAnchorRef : undefined}>
                        <ReleaseCard
                          release={rec}
                          index={0}
                          expanded={expandedGuid === releaseKey(rec)}
                          grabbing={grabbingGuid === releaseKey(rec)}
                          grabbed={grabbedGuids.has(releaseKey(rec))}
                          onToggle={handleToggleExpand}
                          onGrab={handleGrab}
                          recommended
                        />
                        </div>
                        <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '0 4px' }}>
                          {bucket.length > 1 ? (
                            <button
                              type="button"
                              onClick={() => setStep('results')}
                              style={{
                                display: 'inline-flex', alignItems: 'center', gap: '4px',
                                padding: 0, border: 'none', background: 'transparent',
                                color: 'rgba(255,255,255,0.4)', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
                              }}
                            >
                              See all {bucket.length}
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                                <polyline points="9 18 15 12 9 6" />
                              </svg>
                            </button>
                          ) : (
                            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.35)' }}>{sourcesCount(bucket.length)}</span>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
          </>
        )}

        {/* === STEP 4: See-all results for one type === */}
        {!flatOverride && !loading && !error && visibleReleases.length > 0 && step === 'results' && (
          <>
            <div style={{ flexShrink: 0, padding: '4px 12px 0', background: 'var(--color-kiosk-black)' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', paddingBottom: '8px' }}>
                <CompactSourcesBack onClick={() => setStep('type')} />
                <span style={{ flex: 1, fontSize: '13px', fontWeight: 600, color: 'rgba(255,255,255,0.55)', textAlign: 'center' }}>
                  {displayResolution(selectedResolution || '')} · {selectedType}
                </span>
                <div style={{ position: 'relative', flexShrink: 0 }}>
                  <select
                    value={sortMode}
                    onChange={(e) => setSortMode(e.target.value as 'seeders' | 'date')}
                    style={{
                      appearance: 'none',
                      WebkitAppearance: 'none',
                      height: '42px',
                      padding: '0 34px 0 14px',
                      borderRadius: '10px',
                      fontSize: '12px',
                      fontWeight: 600,
                      background: '#1c1c1e',
                      border: '1px solid #2c2c2e',
                      color: '#fff',
                      cursor: 'pointer',
                      outline: 'none',
                    }}
                  >
                    <option value="seeders">Seeders</option>
                    <option value="date">Date</option>
                  </select>
                  <svg
                    width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="rgba(255,255,255,0.4)"
                    strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round"
                    style={{ position: 'absolute', right: '10px', top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}
                  >
                    <path d="M6 9l6 6 6-6" />
                  </svg>
                </div>
              </div>
            </div>

            <div
              ref={scrollRef}
              data-sources-scrollable
              style={{
                flex: 1,
                overflowY: 'auto',
                overflowX: 'hidden',
                WebkitOverflowScrolling: 'touch',
                touchAction: 'pan-y',
                padding: '4px 12px 32px',
              }}
            >
              {grabErrorMsg && (
                <div style={{
                  background: 'rgba(239, 68, 68, 0.15)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  borderRadius: '12px',
                  padding: '12px 16px',
                  marginBottom: '12px',
                  fontSize: '13px',
                  color: '#fca5a5',
                  lineHeight: 1.5,
                  textAlign: 'center',
                }}>
                  {grabErrorMsg}
                </div>
              )}
              {filteredReleases.length === 0 && (
                <div style={{ textAlign: 'center', padding: '40px 0', color: 'rgba(255,255,255,0.3)', fontSize: '14px' }}>
                  No matches for current filters
                </div>
              )}
              {filteredReleases.map((release, i) => {
                const rk = releaseKey(release);
                return (
                <div key={rk || i} ref={expandedGuid === rk ? releaseExpandAnchorRef : undefined}>
                <ReleaseCard
                  release={release}
                  index={i}
                  expanded={expandedGuid === rk}
                  grabbing={grabbingGuid === rk}
                  grabbed={grabbedGuids.has(rk)}
                  onToggle={handleToggleExpand}
                  onGrab={handleGrab}
                  recommended={!!seeAllRecommended && isSameRelease(release, seeAllRecommended)}
                />
                </div>
                );
              })}
            </div>
          </>
        )}

        {/* === Override: results-only flat list (date newest first; no chrome) === */}
        {flatOverride && !loading && !error && visibleReleases.length > 0 && (
          <>
            <div
              ref={scrollRef}
              data-sources-scrollable
              style={{
                flex: 1,
                overflowY: 'auto',
                overflowX: 'hidden',
                WebkitOverflowScrolling: 'touch',
                touchAction: 'pan-y',
                padding: '4px 12px 32px',
              }}
            >
              {grabErrorMsg && (
                <div style={{
                  background: 'rgba(239, 68, 68, 0.15)',
                  border: '1px solid rgba(239, 68, 68, 0.3)',
                  borderRadius: '12px',
                  padding: '12px 16px',
                  marginBottom: '12px',
                  fontSize: '13px',
                  color: '#fca5a5',
                  lineHeight: 1.5,
                  textAlign: 'center',
                }}>
                  {grabErrorMsg}
                </div>
              )}
              {overrideFlatReleases.map((release, i) => {
                const rk = releaseKey(release);
                return (
                <div key={rk || i} ref={expandedGuid === rk ? releaseExpandAnchorRef : undefined}>
                <ReleaseCard
                  release={release}
                  index={i}
                  expanded={expandedGuid === rk}
                  grabbing={grabbingGuid === rk}
                  grabbed={grabbedGuids.has(rk)}
                  onToggle={handleToggleExpand}
                  onGrab={handleGrab}
                />
                </div>
                );
              })}
            </div>
          </>
        )}
      </div>
    </div>

    {/* Manual match picker — blocks grab until an identity is confirmed */}
    {matchPickerFor && (
      <ManualMatchPicker
        initialQuery={grabContext.title || matchPickerFor.title}
        mediaType="movie"
        onCancel={() => setMatchPickerFor(null)}
        onSelect={(m) => runLibraryGrab(matchPickerFor, { tmdbId: m.tmdb_id, title: m.title, year: m.year })}
      />
    )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Main SourcesPanel component
// ---------------------------------------------------------------------------

interface SourcesPanelProps {
  tmdbId?: number;
  title: string;
  year: number | null;
  backdropUrl?: string | null;
  clearLogoUrl?: string | null;
  onClose: () => void;
  onGrabbed?: () => void;
  onAddToWatchlist?: () => void;
  /** Original app-search query — default plain-text override draft. */
  seedQuery?: string | null;
}

export function SourcesPanel({
  tmdbId,
  title,
  year,
  backdropUrl,
  clearLogoUrl,
  onClose,
  onGrabbed,
  onAddToWatchlist,
  seedQuery = null,
}: SourcesPanelProps) {
  const { mode, isTablet } = useLayoutMode();
  // Plain-text override for indexer/Prowlarr only — never replaces modal identity.
  // seedQuery only prefills the Override draft (defaultQuery); do NOT auto-apply
  // it as indexerQuery. Auto-apply forced flatOverride (no resolution→type
  // segmentation) whenever Search opened a movie whose query text differed from
  // the title — Home path stayed segmented. User must Apply Override explicitly.
  const [indexerQuery, setIndexerQuery] = useState<string | null>(null);
  // All results are fetched up front (rejected + indexer rows included). We show
  // everything, segmented by resolution → type and sorted by seeders within each
  // segment — no filtering, no toggle.
  const { loading, error, timedOut, slow, releases, movieId } = useMovieSourcesData({
    tmdbId,
    title,
    year,
    indexerQuery,
  });
  const grabContext: MovieGrabContext = { tmdbId, title, year };

  if (mode === 'compact') {
    return (
      <CompactSourcesView
        releases={releases}
        loading={loading}
        error={error}
        timedOut={timedOut}
        slow={slow}
        movieId={movieId}
        grabContext={grabContext}
        indexerQuery={indexerQuery}
        seedQuery={seedQuery}
        onIndexerQueryApply={setIndexerQuery}
        onIndexerQueryClear={() => setIndexerQuery(null)}
        onClose={onClose}
        onGrabbed={onGrabbed}
        onAddToWatchlist={onAddToWatchlist}
      />
    );
  }

  const isWeb = !isTablet;

  return (
    <ExpandedSourcesView
      releases={releases}
      loading={loading}
      error={error}
      timedOut={timedOut}
      slow={slow}
      movieId={movieId}
      grabContext={grabContext}
      title={title}
      indexerQuery={indexerQuery}
      seedQuery={seedQuery}
      onIndexerQueryApply={setIndexerQuery}
      onIndexerQueryClear={() => setIndexerQuery(null)}
      backdropUrl={backdropUrl}
      clearLogoUrl={clearLogoUrl}
      onClose={onClose}
      onGrabbed={onGrabbed}
      isTablet={isTablet}
      isWeb={isWeb}
    />
  );
}

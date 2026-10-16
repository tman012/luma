import { useState, useEffect, useLayoutEffect, useMemo, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { useTvSourcesData, type TvProcessedRelease } from './useTvSourcesData';
import { sourcesTargetLabel } from './seasonLabels';
import { getBadgeColor, buildQualityGroups, buildScopeOptions, filterByScope } from './tvSourcesUtils';
import { ManualMatchPicker } from '@/components/media/ManualMatchPicker';
import { grabToLibrary } from '@/services/allSourcesApi';
import {
  SegmentRow,
  orderSourceTypes,
  orderResolutions,
  needsLibraryGrab,
  grabDisabledReason,
  COMPACT_SOURCES_WIZARD_HEIGHT,
  COMPACT_SOURCES_SHEET_MAX_HEIGHT,
  COMPACT_SOURCES_RESULTS_HEIGHT,
  scrollChildIntoContainer,
  IndexerQueryOverride,
  type MediaGrabContext,
} from '@/components/media/sourcesShared';

/**
 * TV: every Sonarr row (accepted or rejected) is grabbed through Sonarr so it
 * handles download + import; only raw indexer rows use grab-to-library.
 */
function tvNeedsLibraryGrab(release: TvProcessedRelease): boolean {
  return release.origin === 'indexer' && needsLibraryGrab(release);
}
function tvGrabDisabledReason(release: TvProcessedRelease): string | null {
  return tvNeedsLibraryGrab(release) ? grabDisabledReason(release) : null;
}
import {
  displayResolution,
  pinRecommendedFirst,
  pickBestInGroup,
  isSameTflixRelease,
} from '@/components/media/tflixRecommend';
import type { SourcesTarget } from './types';

/** Stable per-release identity for grab/expand UI state. Indexer rows can have
 *  an empty guid, so fall back to downloadUrl/infoHash/title — otherwise every
 *  empty-guid row would share one "grabbing/grabbed/expanded" state. */
function releaseKey(r: TvProcessedRelease): string {
  return r.guid || r.downloadUrl || r.infoHash || r.title;
}

// ---------------------------------------------------------------------------
// Expanded-mode sub-components
// ---------------------------------------------------------------------------

function BackButton({ onClick, large }: { onClick: () => void; large?: boolean }) {
  const iconSz = large ? 36 : 30;
  return (
    <button
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

function SourcesScreen({ children, zIndex, onBack, headerText, large }: {
  children: React.ReactNode;
  zIndex: number;
  onBack: () => void;
  headerText?: string;
  large?: boolean;
}) {
  return (
    <div
      style={{
        position: 'fixed', inset: 0, background: '#000', zIndex,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
        padding: '40px clamp(20px, 5vw, 60px)',
      }}
    >
      <BackButton onClick={onBack} large={large} />
      {headerText && (
        <div style={{ position: 'absolute', top: '28px', left: '80px', right: '80px', display: 'flex', justifyContent: 'center', pointerEvents: 'none' }}>
          <h3 data-sources-title style={{ margin: 0, textAlign: 'center', fontSize: '22px', fontWeight: 700, color: 'rgba(255,255,255,0.9)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
            {headerText}
          </h3>
        </div>
      )}
      {children}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ExpandedTvSourcesView — 4-level drill-down (quality → type → list → confirm)
// ---------------------------------------------------------------------------

function ExpandedTvSourcesView({
  target,
  tvdbId,
  tmdbId,
  title,
  year,
  onClose,
  onGrabbed,
  isTablet,
  indexerQuery = null,
  seedQuery = null,
  onIndexerQueryApply,
  onIndexerQueryClear,
  targetName = null,
}: TvSourcesPanelProps & { isTablet: boolean }) {
  // All results are fetched up front (rejected + indexer rows included). We show
  // everything, segmented by resolution → type and sorted by seeders within each
  // segment — no filtering, no toggle.
  const { loading, error, releases, handleGrab } = useTvSourcesData({
    target, tvdbId, tmdbId, title, year, indexerQuery,
  });

  const grabContext: MediaGrabContext = { tmdbId, title: title ?? '', year: year ?? null };

  const [selectedScope, setSelectedScope] = useState<string | null>(null);
  const [selectedQuality, setSelectedQuality] = useState<string | null>(null);
  const [selectedSourceType, setSelectedSourceType] = useState<string | null>(null);
  const [seeingAll, setSeeingAll] = useState(false);
  const [confirmTarget, setConfirmTarget] = useState<TvProcessedRelease | null>(null);

  // Season / episode scope segmentation. Only season browses offer a choice;
  // when there's a single scope it's applied implicitly (no extra screen).
  const availableScopes = useMemo(
    () => (target.type === 'season' ? buildScopeOptions(releases) : []),
    [releases, target.type],
  );
  const scopedReleases = useMemo(
    () => (target.type === 'season' ? filterByScope(releases, selectedScope) : releases),
    [releases, selectedScope, target.type],
  );
  const showScopeStep = target.type === 'season' && availableScopes.length > 1 && !selectedScope;
  const [confirmLoading, setConfirmLoading] = useState(false);
  const [grabError, setGrabError] = useState<string | null>(null);
  const [matchPickerFor, setMatchPickerFor] = useState<TvProcessedRelease | null>(null);

  const targetLabel = sourcesTargetLabel(target, targetName);
  const headerText = loading ? `Searching for ${targetLabel}` : targetLabel;

  // Escape key navigation
  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        if (confirmTarget) setConfirmTarget(null);
        else if (seeingAll) setSeeingAll(false);
        else if (selectedSourceType) { setSelectedSourceType(null); setSeeingAll(false); }
        else if (selectedQuality) { setSelectedQuality(null); setSeeingAll(false); }
        else if (selectedScope && availableScopes.length > 1) setSelectedScope(null);
        else onClose();
      }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, confirmTarget, seeingAll, selectedSourceType, selectedQuality, selectedScope, availableScopes]);

  // Grab-to-library with an explicit identity (used directly, or after the picker).
  const runLibraryGrab = useCallback(async (
    release: TvProcessedRelease,
    identity: { tmdbId?: number; title: string; year: number | null },
  ) => {
    setConfirmLoading(true);
    setGrabError(null);
    const result = await grabToLibrary({
      downloadUrl: release.downloadUrl ?? '',
      torrentName: release.title,
      tmdbId: identity.tmdbId,
      mediaType: 'tv',
      title: identity.title,
      year: identity.year,
    });
    setConfirmLoading(false);
    if (result.success) {
      setConfirmTarget(null);
      setMatchPickerFor(null);
      window.dispatchEvent(new Event('download-grabbed'));
      onGrabbed();
      return;
    }
    setGrabError(result.error || 'Failed to add download');
  }, [onGrabbed]);

  const handleConfirmDownload = useCallback(async () => {
    if (!confirmTarget) return;
    const disabled = tvGrabDisabledReason(confirmTarget);
    if (disabled) { setGrabError(disabled); return; }

    // Rejected Arr row OR raw indexer row → grab-to-library pipeline.
    if (tvNeedsLibraryGrab(confirmTarget)) {
      // Keep modal media identity for rename — only open picker when missing.
      if (!grabContext.tmdbId) {
        setMatchPickerFor(confirmTarget);
        return;
      }
      await runLibraryGrab(confirmTarget, grabContext);
      return;
    }

    // Normal Sonarr grab (unchanged path).
    setConfirmLoading(true);
    setGrabError(null);
    try {
      const success = await handleGrab(confirmTarget.guid, confirmTarget.indexerId);
      if (success) {
        setConfirmLoading(false);
        setConfirmTarget(null);
        window.dispatchEvent(new Event('download-grabbed'));
        onGrabbed();
        return;
      }
      setGrabError('Failed to grab release');
    } catch {
      setGrabError('Network error — could not reach server');
    }
    setConfirmLoading(false);
  }, [confirmTarget, handleGrab, onGrabbed, grabContext, runLibraryGrab]);

  const qualityGroups = buildQualityGroups(scopedReleases);
  const selectedGroup = selectedQuality ? qualityGroups.find((g) => g.quality === selectedQuality) : null;
  const selectedReleases = selectedGroup && selectedSourceType
    ? selectedGroup.sourceTypes.find((st) => st.type === selectedSourceType)?.items ?? []
    : [];

  return (
    <>
      {/* Level 1: Scope (season/episode) or quality selection */}
      <SourcesScreen
        zIndex={10001}
        onBack={() => {
          if (target.type === 'season' && selectedScope && availableScopes.length > 1) setSelectedScope(null);
          else onClose();
        }}
        headerText={headerText}
        large={isTablet}
      >
        <div style={{ position: 'absolute', top: isTablet ? '88px' : '72px', left: 0, right: 0, zIndex: 2 }}>
          <IndexerQueryOverride
            mediaTitle={title ?? ''}
            defaultQuery={seedQuery}
            appliedQuery={indexerQuery ?? null}
            onApply={(q) => onIndexerQueryApply?.(q)}
            onClear={() => onIndexerQueryClear?.()}
            disabled={loading}
          />
        </div>
        {loading && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '20px', color: 'rgba(255,255,255,0.5)', fontSize: '22px' }}>
            <div style={{ width: '40px', height: '40px', border: '3px solid rgba(255,255,255,0.15)', borderTopColor: 'rgba(255,255,255,0.7)', borderRadius: '50%', animation: 'tvSourcesSpin 0.8s linear infinite' }} />
            Searching for sources...
          </div>
        )}
        {!loading && error && (
          <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: '22px' }}>
            Show not found in Sonarr. Add it first.
          </div>
        )}
        {/* Scope rows — pick Full Season vs an individual episode first. */}
        {!loading && !error && showScopeStep && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px' }}>
            {availableScopes.map((s) => (
              <SegmentRow
                key={s.key}
                label={s.label}
                count={s.count}
                releases={filterByScope(releases, s.key)}
                colorFor={getBadgeColor}
                showPills={false}
                onClick={() => setSelectedScope(s.key)}
              />
            ))}
          </div>
        )}
        {!loading && !error && !showScopeStep && scopedReleases.length === 0 && (
          <div style={{ color: 'rgba(255,255,255,0.5)', fontSize: '22px' }}>No sources found</div>
        )}
        {!loading && !error && !showScopeStep && qualityGroups.length > 0 && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px' }}>
            {qualityGroups.map((group) => (
              <SegmentRow
                key={group.quality}
                label={displayResolution(group.quality)}
                count={group.totalCount}
                releases={[]}
                colorFor={getBadgeColor}
                showPills={false}
                onClick={() => { setSelectedQuality(group.quality); setSelectedSourceType(null); setSeeingAll(false); }}
              />
            ))}
          </div>
        )}
        <style>{`
          @keyframes tvSourcesSpin { to { transform: rotate(360deg); } }
          @media (hover: hover) {
            [data-source-tile]:hover { background: #444 !important; border-color: rgba(255,255,255,0.25) !important; }
            [data-source-row]:hover { background: #444 !important; border-color: rgba(255,255,255,0.25) !important; }
          }
        `}</style>
      </SourcesScreen>

      {/* Level 2: Source type pills with inline recommended expand */}
      {selectedQuality && selectedGroup && !seeingAll && (
        <SourcesScreen zIndex={10002} onBack={() => { setSelectedQuality(null); setSelectedSourceType(null); setSeeingAll(false); }} headerText={`${displayResolution(selectedQuality)} · ${headerText}`} large={isTablet}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', width: '100%', maxWidth: '560px' }}>
            {selectedGroup.sourceTypes.map((st) => {
              const open = selectedSourceType === st.type;
              const recommended = open ? pickBestInGroup(st.items) : null;
              const ageDays = recommended && recommended.age != null ? Math.round(recommended.age) : null;
              return (
                <div key={st.type}>
                  <SegmentRow
                    label={st.type}
                    count={st.items.length}
                    releases={st.items}
                    colorFor={getBadgeColor}
                    onClick={() => setSelectedSourceType(prev => prev === st.type ? null : st.type)}
                  />
                  {open && recommended && (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginTop: '8px' }}>
                      <p style={{ fontSize: '14px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: 0 }}>Recommended</p>
                      <div
                        onClick={() => { setConfirmTarget(recommended); setGrabError(null); }}
                        style={{
                          display: 'flex', flexDirection: 'column', gap: '10px',
                          padding: '18px 22px',
                          background: recommended.rejected ? '#2a2520' : '#333',
                          border: '2px solid rgba(74, 222, 128, 0.45)',
                          borderLeft: '3px solid #4ade80',
                          borderRadius: '16px',
                          cursor: 'pointer', minHeight: '72px',
                        }}
                        data-source-row
                      >
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
                          <span style={{
                            display: 'inline-block', padding: '4px 12px', borderRadius: '10px', fontSize: '13px', fontWeight: 700,
                            background: getBadgeColor(recommended.indexer).bg, color: getBadgeColor(recommended.indexer).fg,
                            border: `1px solid ${getBadgeColor(recommended.indexer).border}`,
                          }}>
                            {recommended.indexer}
                          </span>
                        </div>
                        <span style={{ fontSize: '14px', fontWeight: 500, color: '#fff', wordBreak: 'break-word', lineHeight: 1.4 }}>
                          {recommended.title}
                        </span>
                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', fontSize: '15px' }}>
                          <span style={{ color: '#fff', fontWeight: 600 }}>{recommended.sizeGb ? `${recommended.sizeGb} GB` : '—'}</span>
                          <span style={{ color: 'rgba(255,255,255,0.7)', textAlign: 'center' }}>{ageDays != null ? `${ageDays}d ago` : '—'}</span>
                          <span style={{ color: '#4ade80', textAlign: 'right', fontWeight: 600 }}>{recommended.seeders} seeders</span>
                        </div>
                      </div>
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
                          <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.35)' }}>{st.items.length} sources</span>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </SourcesScreen>
      )}

      {/* Level 4: See-all for one source type */}
      {selectedQuality && selectedSourceType && seeingAll && (() => {
        const recommended = pickBestInGroup(selectedReleases);
        const rows = pinRecommendedFirst(
          [...selectedReleases].sort((a, b) => b.seeders - a.seeders),
          recommended,
        );
        return (
          <SourcesScreen zIndex={10004} onBack={() => setSeeingAll(false)} headerText={`${displayResolution(selectedQuality)} · ${selectedSourceType}`}>
            <div data-scroll-container className="luma-scroll-y" style={{ flex: 1, overflowY: 'auto', WebkitOverflowScrolling: 'touch', overscrollBehavior: 'contain', touchAction: 'pan-y', width: '100%', maxWidth: '900px', paddingTop: '40px' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
                {rows.map((release) => {
                  const ageDays = release.age != null ? Math.round(release.age) : null;
                  const isRec = !!recommended && isSameTflixRelease(release, recommended);
                  return (
                    <div
                      key={release.guid || releaseKey(release)}
                      onClick={() => { setConfirmTarget(release); setGrabError(null); }}
                      style={{
                        display: 'flex', flexDirection: 'column', gap: '10px',
                        padding: '18px 22px',
                        background: release.rejected ? '#2a2520' : '#333',
                        border: `2px solid ${isRec ? 'rgba(74, 222, 128, 0.45)' : (release.rejected ? 'rgba(245, 158, 11, 0.25)' : 'rgba(255, 255, 255, 0.15)')}`,
                        borderLeft: `3px solid ${isRec ? '#4ade80' : (release.rejected ? 'rgba(245, 158, 11, 0.5)' : 'rgba(255,255,255,0.15)')}`,
                        borderRadius: '16px',
                        cursor: 'pointer', minHeight: '72px',
                        outline: 'none', WebkitTapHighlightColor: 'transparent',
                      }}
                      data-source-row
                    >
                      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                        <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', alignItems: 'center' }}>
                          <span style={{
                            display: 'inline-block', padding: '4px 12px', borderRadius: '10px', fontSize: '13px', fontWeight: 700,
                            background: getBadgeColor(release.indexer).bg, color: getBadgeColor(release.indexer).fg,
                            border: `1px solid ${getBadgeColor(release.indexer).border}`,
                          }}>
                            {release.indexer}
                          </span>
                        </div>
                        <span style={{ fontSize: '14px', fontWeight: 500, color: '#fff', wordBreak: 'break-word', lineHeight: 1.4 }}>
                          {release.title}
                        </span>
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', fontSize: '15px', paddingLeft: '2px' }}>
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
                        <div style={{ fontSize: '11px', color: '#f59e0b', opacity: 0.85, lineHeight: 1.4 }}>
                          {release.rejectionReasons.join(' \u2022 ')}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          </SourcesScreen>
        );
      })()}

      {/* Level 5: Confirm download */}
      {confirmTarget && (() => {
        const disabledReason = tvGrabDisabledReason(confirmTarget);
        return (
        <SourcesScreen zIndex={10005} onBack={() => setConfirmTarget(null)}>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center', marginBottom: '16px' }}>
            <span style={{
              display: 'inline-block', padding: '6px 18px', borderRadius: '12px', fontSize: '15px', fontWeight: 700,
              background: getBadgeColor(confirmTarget.indexer).bg, color: getBadgeColor(confirmTarget.indexer).fg,
              border: `1px solid ${getBadgeColor(confirmTarget.indexer).border}`,
            }}>
              {confirmTarget.indexer}
            </span>
          </div>
          <div style={{ background: '#222', borderRadius: '20px', padding: '36px', width: '520px', maxWidth: '90%', border: '1px solid rgba(255,255,255,0.1)' }}>
            <p style={{ margin: '0 0 16px', fontSize: '15px', fontWeight: 500, color: '#fff', lineHeight: 1.5, wordBreak: 'break-word', textAlign: 'center' }}>
              {confirmTarget.title}
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
            <div style={{ display: 'flex', gap: '20px', fontSize: '15px', justifyContent: 'center', flexWrap: 'wrap', marginBottom: grabError ? '16px' : '28px' }}>
              <span style={{ color: 'rgba(255,255,255,0.7)', fontWeight: 600 }}>
                {confirmTarget.sizeGb ? `${confirmTarget.sizeGb} GB` : '\u2014'}
              </span>
              <span style={{ color: 'rgba(255,255,255,0.55)' }}>
                {confirmTarget.seeders} seeders
              </span>
            </div>
            {grabError && (
              <div style={{
                background: 'rgba(239, 68, 68, 0.15)', border: '1px solid rgba(239, 68, 68, 0.3)',
                borderRadius: '12px', padding: '12px 16px', marginBottom: '16px',
                fontSize: '13px', color: '#fca5a5', lineHeight: 1.5, textAlign: 'center',
              }}>
                {grabError}
              </div>
            )}
            <div style={{ display: 'flex', gap: '16px', justifyContent: 'center' }}>
              <button
                type="button" onClick={() => setConfirmTarget(null)} disabled={confirmLoading}
                style={{
                  padding: '16px 36px', borderRadius: '50px',
                  border: '1px solid #777', background: '#444',
                  color: '#fff', fontSize: '18px', fontWeight: 600, minHeight: '56px',
                  cursor: confirmLoading ? 'default' : 'pointer', transition: 'background 0.2s ease, border-color 0.2s ease',
                }}
              >
                Cancel
              </button>
              <button
                type="button" onClick={handleConfirmDownload} disabled={confirmLoading || !!disabledReason}
                style={{
                  padding: '16px 40px', borderRadius: '50px',
                  border: '1px solid #999', background: '#555',
                  color: '#fff', fontSize: '18px', fontWeight: 600, minHeight: '56px',
                  cursor: (confirmLoading || disabledReason) ? 'default' : 'pointer', transition: 'background 0.2s ease, border-color 0.2s ease',
                  opacity: (confirmLoading || disabledReason) ? 0.5 : 1,
                }}
              >
                {confirmLoading ? 'Sending...' : 'Download'}
              </button>
            </div>
          </div>
        </SourcesScreen>
        );
      })()}

      {/* Manual match picker \u2014 blocks grab until an identity is confirmed */}
      {matchPickerFor && (
        <div style={{ position: 'relative', zIndex: 10100 }}>
        <ManualMatchPicker
          initialQuery={grabContext.title || matchPickerFor.title}
          mediaType="tv"
          onCancel={() => setMatchPickerFor(null)}
          onSelect={(m) => runLibraryGrab(matchPickerFor, { tmdbId: m.tmdb_id, title: m.title, year: m.year })}
        />
        </div>
      )}
    </>
  );
}

// ---------------------------------------------------------------------------
// Compact-mode sub-components
// ---------------------------------------------------------------------------

const DISMISS_THRESHOLD = 120;

type Step = 'scope' | 'resolution' | 'type' | 'results';

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
  release: TvProcessedRelease;
  index: number;
  expanded: boolean;
  grabbing: boolean;
  grabbed: boolean;
  onToggle: (guid: string) => void;
  onGrab: (release: TvProcessedRelease) => void;
  recommended?: boolean;
}) {
  const badge = getBadgeColor(release.indexer);
  const ageDays = release.age != null ? Math.round(release.age) : null;
  const staggerDelay = index < 15 ? index * 30 : 0;
  const disabledReason = tvGrabDisabledReason(release);

  return (
    <div
      role="button"
      onClick={() => onToggle(releaseKey(release))}
      style={{
        background: expanded ? '#1a1a1c' : '#111112',
        borderRadius: '14px',
        padding: '14px 16px',
        marginBottom: '8px',
        cursor: 'pointer',
        border: `1px solid ${recommended ? 'rgba(74, 222, 128, 0.45)' : (expanded ? badge.border : badge.border.replace('0.3)', '0.15)'))}`,
        borderLeft: `3px solid ${recommended ? '#4ade80' : badge.fg}`,
        transition: 'background 0.15s, border-color 0.15s',
        animation: `tvSrcCardEnter 250ms ease-out ${staggerDelay}ms both`,
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

      {/* Row 2: Size + Seeds + Age */}
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
                  borderRadius: '50%', animation: 'tvSrcSpin 0.8s linear infinite',
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
          @keyframes tvSrcCardEnter {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
          }
        `}</style>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// CompactTvSourcesView — bottom sheet with 3-step wizard
// ---------------------------------------------------------------------------

function CompactTvSourcesView({
  target,
  tvdbId,
  tmdbId,
  title,
  year,
  onClose,
  onGrabbed,
  indexerQuery = null,
  seedQuery = null,
  onIndexerQueryApply,
  onIndexerQueryClear,
  targetName = null,
}: TvSourcesPanelProps) {
  const { loading, error, releases, handleGrab } = useTvSourcesData({
    target, tvdbId, tmdbId, title, year, indexerQuery,
  });

  const grabContext: MediaGrabContext = { tmdbId, title: title ?? '', year: year ?? null };

  // Wizard state. Season browses start at the scope step (full-season vs
  // individual episodes); episode browses skip straight to resolution.
  const [step, setStep] = useState<Step>(target.type === 'season' ? 'scope' : 'resolution');
  const [selectedScope, setSelectedScope] = useState<string | null>(null);
  const [selectedResolution, setSelectedResolution] = useState<string | null>(null);
  const [selectedType, setSelectedType] = useState<string | null>(null);

  // Results state
  const [sortMode, setSortMode] = useState<'seeders' | 'date'>('seeders');
  const [expandedGuid, setExpandedGuid] = useState<string | null>(null);
  const [grabbingGuid, setGrabbingGuid] = useState<string | null>(null);
  const [grabbedGuids, setGrabbedGuids] = useState<Set<string>>(() => new Set());
  const [grabErrorMsg, setGrabErrorMsg] = useState<string | null>(null);
  const [matchPickerFor, setMatchPickerFor] = useState<TvProcessedRelease | null>(null);
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

  // Show everything the fetch returned (rejected + indexer rows included).
  // Every resolution/source type present stays reachable.
  const visibleReleases = releases;

  // Plain-text Override search: skip scope/resolution→type wizard; one flat list.
  const flatOverride = !!indexerQuery;

  // When override toggles, reset wizard selection (flat list owns the UI).
  useEffect(() => {
    setSelectedScope(null);
    setSelectedResolution(null);
    setSelectedType(null);
    setExpandedGuid(null);
    if (flatOverride) {
      setSortMode('date');
      setStep('results');
    } else {
      setSortMode('seeders');
      setStep(target.type === 'season' ? 'scope' : 'resolution');
    }
  }, [flatOverride, target.type]);

  // Override flat list — fixed newest-first (age), then seeders.
  // Uses full visible set (no season/episode scope filter) so override stays one list.
  const overrideFlatReleases = useMemo(() => {
    const result = [...visibleReleases];
    result.sort((a, b) => (a.age ?? Infinity) - (b.age ?? Infinity) || b.seeders - a.seeders);
    return result;
  }, [visibleReleases]);

  // Season / episode scope segmentation (only meaningful for season browses).
  const availableScopes = useMemo(
    () => (target.type === 'season' ? buildScopeOptions(visibleReleases) : []),
    [visibleReleases, target.type],
  );
  // Everything downstream (resolution/type/results) works on the scoped subset.
  const scopedReleases = useMemo(
    () => (target.type === 'season' ? filterByScope(visibleReleases, selectedScope) : visibleReleases),
    [visibleReleases, selectedScope, target.type],
  );

  // Auto-advance: if there's only one scope (or none to choose), skip the step.
  useEffect(() => {
    if (flatOverride) return;
    if (!loading && !error && step === 'scope') {
      if (availableScopes.length <= 1) {
        if (availableScopes.length === 1) setSelectedScope(availableScopes[0]!.key);
        setStep('resolution');
      }
    }
  }, [loading, error, step, availableScopes]);

  // Available resolutions, highest → lowest (Unknown last).
  const availableQualities = useMemo(
    () => orderResolutions(scopedReleases.map(r => r.resolution)),
    [scopedReleases],
  );

  // Auto-advance: if only 1 resolution, skip to type (not in override flat mode)
  useEffect(() => {
    if (flatOverride) return;
    if (!loading && !error && scopedReleases.length > 0 && step === 'resolution') {
      if (availableQualities.length === 1) {
        setSelectedResolution(availableQualities[0]!);
        setStep('type');
      }
    }
  }, [loading, error, scopedReleases, availableQualities, step]);

  // Available types for selected resolution.
  const availableSourceTypes = useMemo(() => {
    if (!selectedResolution) return [];
    const filtered = scopedReleases.filter(r => r.resolution === selectedResolution);
    return orderSourceTypes(filtered.map(r => r.sourceType));
  }, [scopedReleases, selectedResolution]);

  // Auto-expand: if only 1 type, expand recommended under that pill (stay on type step)
  useEffect(() => {
    if (flatOverride) return;
    if (step === 'type' && availableSourceTypes.length === 1 && !selectedType) {
      setSelectedType(availableSourceTypes[0]!);
    }
  }, [step, availableSourceTypes, selectedType]);

  // See-all list for one type
  const filteredReleases = useMemo(() => {
    if (!selectedResolution || !selectedType) return [];
    let result = scopedReleases.filter(
      (r) => r.resolution === selectedResolution && r.sourceType === selectedType,
    );
    if (sortMode === 'date') {
      result = [...result].sort((a, b) => (a.age ?? Infinity) - (b.age ?? Infinity) || b.seeders - a.seeders);
    } else {
      result = [...result].sort((a, b) => b.seeders - a.seeders);
    }
    const recommended = pickBestInGroup(result);
    return pinRecommendedFirst(result, recommended);
  }, [scopedReleases, selectedResolution, selectedType, sortMode]);

  const seeAllRecommended = useMemo(
    () => pickBestInGroup(filteredReleases),
    [filteredReleases],
  );

  // Counts per resolution
  const qualityCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const r of scopedReleases) {
      counts[r.resolution] = (counts[r.resolution] ?? 0) + 1;
    }
    return counts;
  }, [scopedReleases]);

  const sourceTypeCounts = useMemo(() => {
    const filtered = selectedResolution
      ? scopedReleases.filter(r => r.resolution === selectedResolution)
      : scopedReleases;
    const counts: Record<string, number> = {};
    for (const r of filtered) {
      counts[r.sourceType] = (counts[r.sourceType] ?? 0) + 1;
    }
    return counts;
  }, [scopedReleases, selectedResolution]);

  // Guards against a rapid double-tap firing two grab POSTs before re-render.
  const grabInFlightRef = useRef(false);

  // Grab-to-library with an explicit identity (used directly, or after the picker).
  const runLibraryGrab = useCallback(async (
    release: TvProcessedRelease,
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
        mediaType: 'tv',
        title: identity.title,
        year: identity.year,
      });
      if (result.success) {
        setGrabbedGuids(prev => new Set(prev).add(rk));
        setMatchPickerFor(null);
        window.dispatchEvent(new Event('download-grabbed'));
        setTimeout(() => onGrabbed(), 600);
        return;
      }
      setGrabErrorMsg(result.error || 'Failed to add download');
    } finally {
      setGrabbingGuid(null);
      grabInFlightRef.current = false;
    }
  }, [onGrabbed]);

  // Grab handler — routes rejected / indexer rows through grab-to-library.
  const onGrabRelease = useCallback(async (release: TvProcessedRelease) => {
    if (grabInFlightRef.current) return;
    const disabled = tvGrabDisabledReason(release);
    if (disabled) { setGrabErrorMsg(disabled); return; }

    // Rejected Arr row OR raw indexer row → grab-to-library pipeline.
    if (tvNeedsLibraryGrab(release)) {
      // Keep modal media identity for rename — only open picker when missing.
      if (!grabContext.tmdbId) {
        setGrabErrorMsg(null);
        setMatchPickerFor(release);
        return;
      }
      await runLibraryGrab(release, grabContext);
      return;
    }

    // Normal Sonarr grab (unchanged path).
    grabInFlightRef.current = true;
    const rk = releaseKey(release);
    setGrabbingGuid(rk);
    setGrabErrorMsg(null);
    try {
      const success = await handleGrab(release.guid, release.indexerId);
      if (success) {
        setGrabbedGuids(prev => new Set(prev).add(rk));
        window.dispatchEvent(new Event('download-grabbed'));
        setTimeout(() => onGrabbed(), 600);
        return;
      }
      setGrabErrorMsg('Failed to grab release');
    } catch { setGrabErrorMsg('Network error — could not reach server'); }
    finally {
      setGrabbingGuid(null);
      grabInFlightRef.current = false;
    }
  }, [handleGrab, onGrabbed, grabContext, runLibraryGrab]);

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

  // Handle-only drag-to-dismiss — never bind dismiss to scrollTop===0.
  // isAtTop + downward drag was fighting native list scroll and intermittently
  // trapped users at the bottom (could not scroll back up).
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    e.stopPropagation();
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    } else {
      dragRef.current = { active: false, startY: 0, currentY: 0 };
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

  const onTouchCancel = useCallback(() => {
    dragRef.current.active = false;
    setDragOffset(0);
  }, []);

  const noSources = !loading && !error && visibleReleases.length === 0;
  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  const targetLabel = sourcesTargetLabel(target, targetName);
  const headerLabel = loading ? `Searching for ${targetLabel}` : targetLabel;

  return (
    <>
    <div
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 10020,
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
        onTouchCancel={onTouchCancel}
        style={{
          // scope+resolution+type(+expand) share fixed footprint so bottom sheet never jumps
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
          <>
            <div
              data-sheet-handle
              style={{
                flexShrink: 0,
                display: 'flex',
                justifyContent: 'center',
                padding: '12px 0 8px',
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
            <div style={{
              flexShrink: 0,
              padding: '0 20px 16px',
              textAlign: 'center',
            }}>
              <span data-sources-title style={{
                display: 'block',
                maxWidth: '100%',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                fontSize: '15px',
                fontWeight: 600,
                color: 'rgba(255,255,255,0.5)',
              }}>
                {headerLabel}
              </span>
            </div>
          </>
        )}

        {/* Override chrome: enter via link when idle; hide entirely once active */}
        {!flatOverride && (
          <IndexerQueryOverride
            mediaTitle={title ?? ''}
            defaultQuery={seedQuery}
            appliedQuery={indexerQuery ?? null}
            onApply={(q) => onIndexerQueryApply?.(q)}
            onClear={() => onIndexerQueryClear?.()}
            disabled={loading}
          />
        )}

        {/* Loading */}
        {loading && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '24px', padding: '60px 0 80px' }}>
            <div style={{
              width: '56px', height: '56px',
              border: '3px solid #2c2c2e', borderTopColor: 'rgba(255,255,255,0.7)',
              borderRadius: '50%', animation: 'tvSrcSpin 0.8s linear infinite',
            }} />
            <span style={{ fontSize: '16px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>
              Searching<span style={{ display: 'inline-block', width: '24px', textAlign: 'left' }}>
                <span style={{ animation: 'tvSrcDot1 1.4s infinite' }}>.</span>
                <span style={{ animation: 'tvSrcDot2 1.4s infinite' }}>.</span>
                <span style={{ animation: 'tvSrcDot3 1.4s infinite' }}>.</span>
              </span>
            </span>
            <style>{`
              @keyframes tvSrcSpin { to { transform: rotate(360deg) } }
              @keyframes tvSrcDot1 { 0%, 20% { opacity: 0 } 40%, 100% { opacity: 1 } }
              @keyframes tvSrcDot2 { 0%, 40% { opacity: 0 } 60%, 100% { opacity: 1 } }
              @keyframes tvSrcDot3 { 0%, 60% { opacity: 0 } 80%, 100% { opacity: 1 } }
            `}</style>
          </div>
        )}

        {/* Error */}
        {!loading && error && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: '8px', padding: '60px 0 80px' }}>
            <span style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.5)' }}>Failed to load sources</span>
            <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.3)' }}>Check Sonarr connection</span>
          </div>
        )}

        {/* No sources */}
        {noSources && (
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '40px 24px 60px', gap: '20px' }}>
            <span style={{ fontSize: '17px', fontWeight: 600, color: 'rgba(255,255,255,0.7)', textAlign: 'center' }}>
              Sources not found
            </span>
            <span style={{ fontSize: '13px', color: 'rgba(255,255,255,0.35)', textAlign: 'center', maxWidth: '300px', lineHeight: 1.4 }}>
              {indexerQuery
                ? 'No indexer hits for that plain-text query.'
                : 'Fancy titles often miss on trackers — try Override search with a plain query.'}
            </span>
            {indexerQuery && (
              <button
                type="button"
                onClick={() => onIndexerQueryClear?.()}
                style={{
                  background: 'none', border: 'none', padding: '8px 12px',
                  color: 'rgba(255,255,255,0.45)', fontSize: '13px', fontWeight: 600, cursor: 'pointer',
                }}
              >
                Clear override
              </button>
            )}
          </div>
        )}

        {/* === STEP 0: Scope (full season vs individual episode) === */}
        {!flatOverride && !loading && !error && visibleReleases.length > 0 && step === 'scope' && (
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
              Full Season or Episode
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
              {availableScopes.map(s => (
                <SegmentRow
                  key={s.key}
                  label={s.label}
                  count={s.count}
                  releases={filterByScope(visibleReleases, s.key)}
                  colorFor={getBadgeColor}
                  showPills={false}
                  onClick={() => { setSelectedScope(s.key); setStep('resolution'); }}
                />
              ))}
            </div>
          </div>
        )}

        {/* === STEP 1: Resolution === */}
        {!flatOverride && !loading && !error && visibleReleases.length > 0 && step === 'resolution' && (
          <>
            {target.type === 'season' && (
              <div style={{ flexShrink: 0, padding: '4px 12px 0', background: 'var(--color-kiosk-black)' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', paddingBottom: '8px' }}>
                <CompactSourcesBack onClick={() => { setSelectedScope(null); setStep('scope'); }} />
                  <span style={{ flex: 1, fontSize: '13px', fontWeight: 600, color: 'rgba(255,255,255,0.55)', textAlign: 'center' }}>
                    Select Resolution
                  </span>
                  <div style={{ width: '32px', flexShrink: 0 }} />
                </div>
              </div>
            )}
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
            {target.type !== 'season' && (
            <p style={{ fontSize: '15px', fontWeight: 500, color: 'rgba(255,255,255,0.4)', textAlign: 'center', margin: '0 0 16px' }}>
              Select Resolution
            </p>
            )}
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
          </>
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
                const bucket = scopedReleases.filter(r => r.resolution === selectedResolution && r.sourceType === t);
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
                          onGrab={onGrabRelease}
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
                            <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.35)' }}>{bucket.length} sources</span>
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
                  onGrab={onGrabRelease}
                  recommended={!!seeAllRecommended && isSameTflixRelease(release, seeAllRecommended)}
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
                  onGrab={onGrabRelease}
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
      <div style={{ position: 'relative', zIndex: 10100 }}>
      <ManualMatchPicker
        initialQuery={grabContext.title || matchPickerFor.title}
        mediaType="tv"
        onCancel={() => setMatchPickerFor(null)}
        onSelect={(m) => runLibraryGrab(matchPickerFor, { tmdbId: m.tmdb_id, title: m.title, year: m.year })}
      />
      </div>
    )}
    </>
  );
}

// ---------------------------------------------------------------------------
// TvSourcesPanel — unified entry point, switches on layout mode
// ---------------------------------------------------------------------------

interface TvSourcesPanelProps {
  target: SourcesTarget;
  tvdbId: number;
  tmdbId?: number;
  /** Show title — feeds the ungated indexer sweep + grab-to-library identity. */
  title?: string;
  /** Show first-air year. */
  year?: number | null;
  onClose: () => void;
  onGrabbed: () => void;
  /** Plain-text indexer override (owned by TvSourcesPanel entry). */
  indexerQuery?: string | null;
  onIndexerQueryApply?: (query: string) => void;
  onIndexerQueryClear?: () => void;
  /** Original app-search query — default plain-text override draft. */
  seedQuery?: string | null;
  /** Episode or season name for the sheet title. */
  targetName?: string | null;
}

export function TvSourcesPanel(props: TvSourcesPanelProps) {
  const { mode, isTablet } = useLayoutMode();
  // Plain-text override for indexer/Prowlarr only — never replaces modal identity.
  // seedQuery only prefills the Override draft; do NOT auto-apply as indexerQuery
  // (same Search vs Home segmentation fix as movie SourcesPanel).
  const [indexerQuery, setIndexerQuery] = useState<string | null>(null);
  const overrideProps = {
    indexerQuery,
    seedQuery: props.seedQuery,
    onIndexerQueryApply: setIndexerQuery,
    onIndexerQueryClear: () => setIndexerQuery(null),
  };

  // Portal to body so Sources always layers above whichever sheet/grid opened it.
  if (mode === 'compact') {
    return createPortal(<CompactTvSourcesView {...props} {...overrideProps} />, document.body);
  }

  // Portal so it layers above the season/episode grids (fixed overlays on body).
  return createPortal(<ExpandedTvSourcesView {...props} {...overrideProps} isTablet={isTablet} />, document.body);
}

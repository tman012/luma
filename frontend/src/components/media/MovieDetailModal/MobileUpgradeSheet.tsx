import { useState, useEffect, useCallback, useRef } from 'react';
import { apiFetch } from '../../../apiClient';
import { isMinimal } from '@/minimal';
import { addToNativeWatchlist } from '@/services/watchlistApi';

const DISMISS_THRESHOLD = 120;

interface QualityProfile {
  id: number;
  name: string;
}

interface MobileUpgradeSheetProps {
  isOwned: boolean;
  tmdbId: number;
  title: string;
  year: number | null;
  onClose: () => void;
  onAdded: () => void;
}

export function MobileUpgradeSheet({
  isOwned,
  tmdbId,
  title,
  year,
  onClose,
  onAdded,
}: MobileUpgradeSheetProps) {
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef({ active: false, startY: 0, currentY: 0 });
  const [dragOffset, setDragOffset] = useState(0);
  const [dismissing, setDismissing] = useState(false);
  const [entered, setEntered] = useState(false);

  const [adding, setAdding] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [profiles, setProfiles] = useState<QualityProfile[] | null>(null);

  useEffect(() => {
    requestAnimationFrame(() => setEntered(true));
  }, []);

  useEffect(() => {
    let cancelled = false;
    // v204 (test install): Luma's own movie profiles (movies.db, Settings > Movies); never Radarr
    apiFetch(isMinimal() ? '/api/movies/watchlist/profiles' : '/api/radarr/upgrade-profiles')
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then((data: QualityProfile[]) => { if (!cancelled) setProfiles(data); })
      .catch(() => { if (!cancelled) setProfiles([]); });
    return () => { cancelled = true; };
  }, []);

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

  const handleDismiss = useCallback(() => {
    setDismissing(true);
    setTimeout(onClose, 250);
  }, [onClose]);

  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-sheet-handle]') && e.touches.length > 0) {
      dragRef.current = { active: true, startY: e.touches[0]!.clientY, currentY: e.touches[0]!.clientY };
    }
  }, []);

  const onTouchMove = useCallback((e: React.TouchEvent) => {
    if (!dragRef.current.active || e.touches.length === 0) return;
    const y = e.touches[0]!.clientY;
    const dy = y - dragRef.current.startY;
    if (dy > 0) {
      dragRef.current.currentY = y;
      setDragOffset(dy);
    }
  }, []);

  const onTouchEnd = useCallback(() => {
    if (!dragRef.current.active) return;
    const dy = dragRef.current.currentY - dragRef.current.startY;
    dragRef.current.active = false;
    if (dy > DISMISS_THRESHOLD) {
      handleDismiss();
    } else {
      setDragOffset(0);
    }
  }, [handleDismiss]);

  const handleSelect = useCallback(async (qualityProfile: string) => {
    if (adding) return;
    setAdding(qualityProfile);
    setError(null);
    setErrorMsg(null);
    if (isMinimal()) {
      // v204: the native watchlist on the test install, with the picked profile's id
      const opt = (profiles ?? []).find(p => p.name === qualityProfile);
      const r = await addToNativeWatchlist({ tmdbId, title, year, profileId: opt?.id ?? null });
      if (r.ok) { handleDismiss(); setTimeout(() => onAdded(), 280); return; }
      setAdding(null); setError(qualityProfile); setErrorMsg(r.error ?? 'Failed');
      setTimeout(() => { setError(null); setErrorMsg(null); }, 4000);
      return;
    }
    try {
      const resp = await apiFetch('/api/watchlist', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          tmdb_id: tmdbId,
          media_type: 'movie',
          title,
          year,
          quality_profile: qualityProfile,
        }),
      });
      const result = (await resp.json()) as Record<string, unknown>;
      if (resp.ok && result.success) {
        handleDismiss();
        setTimeout(() => onAdded(), 280);
      } else {
        setAdding(null);
        setError(qualityProfile);
        setErrorMsg((result.error as string) ?? 'Failed');
        setTimeout(() => { setError(null); setErrorMsg(null); }, 4000);
      }
    } catch {
      setAdding(null);
      setError(qualityProfile);
      setErrorMsg('Network error');
      setTimeout(() => { setError(null); setErrorMsg(null); }, 4000);
    }
  }, [adding, profiles, tmdbId, title, year, handleDismiss, onAdded]);

  const sheetTranslate = dismissing ? '100%' : entered ? `${dragOffset}px` : '100%';

  return (
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
        {/* Grab handle */}
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

        {/* Title */}
        <p style={{
          fontSize: '15px',
          fontWeight: 500,
          color: 'rgba(255,255,255,0.4)',
          textAlign: 'center',
          margin: '0 0 16px',
        }}>
          {isOwned ? 'Upgrade Quality' : 'Select Quality'}
        </p>

        {/* Quality options */}
        <div style={{ padding: '0 20px 60px', display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {profiles === null ? (
            <div style={{ textAlign: 'center', color: 'rgba(255,255,255,0.3)', padding: '16px', fontSize: '14px' }}>
              Loading profiles...
            </div>
          ) : profiles.length === 0 ? (
            <div style={{ textAlign: 'center', color: 'rgba(255,255,255,0.3)', padding: '16px', fontSize: '14px' }}>
              No profiles available
            </div>
          ) : profiles.map((opt) => {
            const isAdding = adding === opt.name;
            const isError = error === opt.name;
            return (
              <button
                key={opt.id}
                type="button"
                disabled={!!adding}
                onClick={() => handleSelect(opt.name)}
                style={{
                  width: '100%',
                  height: '64px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: '0 24px',
                  borderRadius: '16px',
                  fontSize: '18px',
                  fontWeight: 700,
                  cursor: adding ? 'default' : 'pointer',
                  border: isError
                    ? '2px solid rgba(244, 67, 54, 0.6)'
                    : '1px solid rgba(255,255,255,0.12)',
                  background: isError
                    ? 'rgba(244, 67, 54, 0.2)'
                    : 'rgba(255,255,255,0.06)',
                  color: isError ? '#ef4444' : '#fff',
                  transition: 'all 0.15s',
                }}
              >
                <span>{isAdding ? 'Adding...' : isError ? 'Failed' : opt.name}</span>
                {isError && errorMsg && (
                  <span style={{ fontSize: '12px', color: '#f87171', marginLeft: '8px' }}>
                    {errorMsg}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

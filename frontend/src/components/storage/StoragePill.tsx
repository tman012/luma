/**
 * StoragePill — compact-home nav pill showing free space in the downloads
 * folder. Tapping it opens a sheet whose only action permanently deletes the
 * oldest 100 GB of downloads; the pill re-reads usage after a run so the number
 * it shows is always the real post-cleanup figure.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { useLocation, useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';
import { useNotificationsContext } from '@/contexts/NotificationsContext';
import {
  fetchDownloadsStorage,
  cleanupDownloads,
  formatBytes,
  type StorageUsage,
  type CleanupResult,
} from '@/services/storageApi';

const ENTER_DURATION = 500;
const EXIT_DURATION = 320;
const SPRING = 'cubic-bezier(0.16, 1, 0.3, 1)';

/** Repoll cadence while the page is visible and nothing is downloading. */
const USAGE_POLL_IDLE_MS = 60_000;
/** Faster cadence while torrents are writing into the downloads folder. */
const USAGE_POLL_ACTIVE_MS = 15_000;

type SheetPhase = 'entering' | 'visible' | 'exiting' | 'gone';
type Step = 'idle' | 'confirm' | 'running' | 'done' | 'error' | 'signin';
/** Label of the sign-in prompt for the cleanup (sentence case, no helper text). */
export const SIGN_IN_TO_FREE = 'Sign in to free up space';

// ---------------------------------------------------------------------------
// Pill
// ---------------------------------------------------------------------------

export function StoragePill() {
  const [usage, setUsage] = useState<StorageUsage | null>(null);
  const [open, setOpen] = useState(false);
  const [pressed, setPressed] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setUsage(await fetchDownloadsStorage());
    } catch {
      setUsage(null);
    }
  }, []);

  // Piggyback on the shared download-queue poller rather than adding another.
  const { downloads } = useNotificationsContext();
  const hasActive = downloads.length > 0;
  // Stable primitive dep: only changes when a torrent joins or leaves the queue.
  const queueKey = downloads.map((d) => d.name).join('|');
  const prevQueueRef = useRef<string[]>([]);

  // A torrent leaving the queue means its last bytes just hit the downloads
  // folder — refresh right then instead of waiting out the interval.
  useEffect(() => {
    const names = queueKey ? queueKey.split('|') : [];
    const finished = prevQueueRef.current.some((n) => !names.includes(n));
    prevQueueRef.current = names;
    if (finished) void refresh();
  }, [queueKey, refresh]);

  // Poll while visible, pause while hidden, and refresh the moment the tab or
  // kiosk comes back — otherwise an app left open overnight shows yesterday's
  // free space after downloads have landed. Space drains *during* a download
  // (qBittorrent writes as it goes), so an active queue polls faster.
  useEffect(() => {
    void refresh();

    const tick = () => {
      if (typeof document !== 'undefined' && document.hidden) return;
      void refresh();
    };
    const timer = setInterval(tick, hasActive ? USAGE_POLL_ACTIVE_MS : USAGE_POLL_IDLE_MS);

    const onVisible = () => {
      if (typeof document !== 'undefined' && !document.hidden) void refresh();
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onVisible);

    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onVisible);
    };
  }, [refresh, hasActive]);

  const label = usage?.available ? `${formatBytes(usage.freeBytes)} free` : 'Storage';

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        onTouchStart={() => setPressed(true)}
        onTouchEnd={() => setPressed(false)}
        onTouchCancel={() => setPressed(false)}
        style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '10px 18px', borderRadius: 999,
          background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
          color: 'rgba(255,255,255,0.85)', fontSize: 14, fontWeight: 500,
          cursor: 'pointer', flexShrink: 0, fontFamily: 'inherit',
          touchAction: 'manipulation',
          transform: pressed ? 'scale(0.95)' : 'scale(1)',
          transition: pressed ? 'transform 0.1s ease-out' : 'transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1)',
          outline: 'none',
        }}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5v14c0 1.66 4.03 3 9 3s9-1.34 9-3V5" />
          <path d="M3 12c0 1.66 4.03 3 9 3s9-1.34 9-3" />
        </svg>
        {label}
      </button>

      <StorageSheet
        open={open}
        usage={usage}
        onClose={() => setOpen(false)}
        onUsageChange={setUsage}
      />
    </>
  );
}

// ---------------------------------------------------------------------------
// Sheet
// ---------------------------------------------------------------------------

function StorageSheet({ open, usage, onClose, onUsageChange }: {
  open: boolean;
  usage: StorageUsage | null;
  onClose: () => void;
  onUsageChange: (u: StorageUsage) => void;
}) {
  const [phase, setPhase] = useState<SheetPhase>('gone');
  const [step, setStep] = useState<Step>('idle');
  const [result, setResult] = useState<CleanupResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Deleting files needs the owner signed in with a session; LAN access without
  // sign-in (implicit) gets a sign-in prompt instead of the delete.
  const { implicit } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const signIn = useCallback(() => {
    navigate('/login', { state: { from: location.pathname + location.search, signIn: true } });
  }, [navigate, location.pathname, location.search]);

  useEffect(() => {
    if (!open) return;
    setPhase('entering');
    setStep('idle');
    setResult(null);
    setError(null);
    // Re-read on open so the sheet never shows a stale figure.
    fetchDownloadsStorage().then(onUsageChange).catch(() => {});
    requestAnimationFrame(() => requestAnimationFrame(() => setPhase('visible')));
  }, [open, onUsageChange]);

  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  const dismiss = useCallback(() => {
    if (step === 'running') return;   // don't close mid-delete
    setPhase('exiting');
    setTimeout(() => { setPhase('gone'); onClose(); }, EXIT_DURATION);
  }, [onClose, step]);

  const runCleanup = useCallback(async () => {
    setStep('running');
    setError(null);
    try {
      const res = await cleanupDownloads(usage?.cleanupTargetBytes);
      if (res.signIn) { setStep('signin'); return; }
      setResult(res);
      if (res.after) onUsageChange(res.after);
      else await fetchDownloadsStorage().then(onUsageChange).catch(() => {});
      if (res.success) {
        setStep('done');
      } else {
        setError(res.error ?? 'Cleanup failed');
        setStep('error');
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Cleanup failed');
      setStep('error');
    }
  }, [onUsageChange, usage?.cleanupTargetBytes]);

  if (phase === 'gone' && !open) return null;

  const targetLabel = formatBytes(usage?.cleanupTargetBytes ?? 100 * 1024 ** 3);
  const usedPct = usage?.available && usage.totalBytes > 0
    ? Math.min(100, (usage.usedBytes / usage.totalBytes) * 100)
    : 0;

  return createPortal(
    <div className="luma-bleed"
      style={{
        position: 'fixed', inset: 0, zIndex: 10001,
        display: 'flex', flexDirection: 'column', justifyContent: 'flex-end',
        backgroundColor: phase === 'visible' ? 'rgba(0,0,0,0.5)' : 'rgba(0,0,0,0)',
        transition: `background-color ${phase === 'exiting' ? EXIT_DURATION : ENTER_DURATION}ms ease`,
      }}
      onClick={(e) => { if (e.target === e.currentTarget) dismiss(); }}
    >
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      <div className="luma-bleed-panel"
        style={{
          background: 'var(--color-kiosk-black)',
          borderRadius: '20px 20px 0 0',
          borderTop: '1px solid rgba(255,255,255,0.08)',
          paddingBottom: 'calc(env(safe-area-inset-bottom, 0px) + 16px)',
          transform: phase === 'visible' ? 'translate3d(0,0,0)' : 'translate3d(0,100%,0)',
          transition: `transform ${phase === 'exiting' ? EXIT_DURATION : ENTER_DURATION}ms ${SPRING}`,
          willChange: 'transform',
        }}
      >
        {/* Grab handle */}
        <div style={{ display: 'flex', justifyContent: 'center', padding: '12px 0 8px' }}>
          <div style={{ width: 40, height: 5, borderRadius: 3, background: 'rgba(255,255,255,0.3)' }} />
        </div>

        <div style={{ padding: '0 20px' }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700, color: '#fff' }}>Downloads storage</h2>
          {usage && !usage.available && (
            <p style={{ margin: '4px 0 0', fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>Downloads folder unavailable</p>
          )}

          {/* Usage bar */}
          {usage?.available && (
            <div style={{ marginTop: 18 }}>
              <div style={{ height: 8, borderRadius: 4, background: 'rgba(255,255,255,0.1)', overflow: 'hidden' }}>
                <div
                  style={{
                    height: '100%', width: `${usedPct}%`, borderRadius: 4,
                    background: usedPct > 90 ? '#f87171' : usedPct > 75 ? '#fbbf24' : '#4ade80',
                    transition: 'width 600ms cubic-bezier(0.16, 1, 0.3, 1), background 400ms ease',
                  }}
                />
              </div>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
                <span style={{ fontSize: 15, fontWeight: 600, color: '#fff' }}>
                  {formatBytes(usage.freeBytes)} free
                </span>
                <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.4)' }}>
                  {formatBytes(usage.usedBytes)} of {formatBytes(usage.totalBytes)} used
                </span>
              </div>
            </div>
          )}

          {/* Action area */}
          <div style={{ marginTop: 22 }}>
            {(step === 'signin' || (step === 'idle' && implicit)) && (
              <SheetButton onClick={signIn} label={SIGN_IN_TO_FREE} testId="storage-sign-in" />
            )}

            {step === 'idle' && !implicit && (
              <SheetButton
                onClick={() => setStep('confirm')}
                disabled={!usage?.available}
                label={`Free up ${targetLabel}`}
              />
            )}

            {step === 'confirm' && (
              <>
                <div style={{ display: 'flex', gap: 10 }}>
                  <button
                    type="button"
                    onClick={() => setStep('idle')}
                    style={{
                      flex: 1, padding: '14px 0', borderRadius: 12, fontFamily: 'inherit',
                      background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
                      color: 'rgba(255,255,255,0.85)', fontSize: 15, fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    Cancel
                  </button>
                  <button
                    type="button"
                    onClick={() => void runCleanup()}
                    style={{
                      flex: 1, padding: '14px 0', borderRadius: 12, fontFamily: 'inherit',
                      background: 'rgba(248,113,113,0.16)', border: '1px solid rgba(248,113,113,0.4)',
                      color: '#fca5a5', fontSize: 15, fontWeight: 600, cursor: 'pointer',
                    }}
                  >
                    Delete {targetLabel}
                  </button>
                </div>
              </>
            )}

            {step === 'running' && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '14px 0' }}>
                <span
                  style={{
                    width: 16, height: 16, borderRadius: '50%',
                    border: '2px solid rgba(255,255,255,0.2)', borderTopColor: '#fff',
                    animation: 'spin 0.8s linear infinite',
                  }}
                />
                <span style={{ fontSize: 14, color: 'rgba(255,255,255,0.75)' }}>Freeing space…</span>
              </div>
            )}

            {step === 'done' && result && (
              <>
                <p style={{ margin: '0 0 14px', fontSize: 14, lineHeight: 1.5, color: '#4ade80' }}>
                  Freed {formatBytes(result.freedBytes)} — {result.deletedCount}{' '}
                  {result.deletedCount === 1 ? 'item' : 'items'} deleted.
                  {result.failed.length > 0 && ` ${result.failed.length} could not be deleted.`}
                </p>
                <SheetButton onClick={dismiss} label="Done" />
              </>
            )}

            {step === 'error' && (
              <>
                <p style={{ margin: '0 0 14px', fontSize: 13, lineHeight: 1.5, color: '#fca5a5' }}>
                  {error}
                </p>
                <SheetButton onClick={() => setStep('idle')} label="Try again" />
              </>
            )}
          </div>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function SheetButton({ onClick, label, sublabel, disabled, testId }: {
  onClick: () => void;
  label: string;
  sublabel?: string;
  disabled?: boolean;
  testId?: string;
}) {
  return (
    <button
      type="button"
      data-testid={testId}
      onClick={onClick}
      disabled={disabled}
      style={{
        width: '100%', padding: '14px 16px', borderRadius: 12, textAlign: 'left',
        background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
        color: disabled ? 'rgba(255,255,255,0.3)' : 'rgba(255,255,255,0.9)',
        fontSize: 15, fontWeight: 600, fontFamily: 'inherit',
        cursor: disabled ? 'default' : 'pointer', touchAction: 'manipulation',
      }}
    >
      {label}
      {sublabel && (
        <span style={{ display: 'block', marginTop: 3, fontSize: 12, fontWeight: 400, color: 'rgba(255,255,255,0.4)' }}>
          {sublabel}
        </span>
      )}
    </button>
  );
}

/**
 * v175 (PM): a real run (dry_run false) of Movie night, Genre roulette or Franchise completer asks first.
 * Designer's glass dialog (settings-page-spec.md "Dialogs"): 400px centered on desktop, a bottom sheet on
 * phones, #15102a + glass, radius 20, 44px buttons; the legacy Settings navy scrim (no black). Title only,
 * no helper text. Cancel (or Escape, or the scrim) sends nothing; Run calls onRun once.
 */
import { useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { LEGACY } from './legacyTokens';

export const REAL_RUN_TITLE = 'Start a real run?';

const BTN = 'inline-flex flex-1 sm:flex-none items-center justify-center rounded-[10px] px-5 min-h-[44px] text-[14px] font-semibold transition-colors';
export const RUN_BTN = `${BTN} bg-[#3b82f6] text-white hover:bg-[#60a5fa]`;
export const CANCEL_BTN = `${BTN} border border-white/[0.12] bg-white/[0.08] text-white/90 hover:bg-white/[0.12]`;

function useWide(): boolean {
  const q = '(min-width: 640px)';
  const [wide, setWide] = useState(() => (typeof window === 'undefined' || !window.matchMedia ? true : window.matchMedia(q).matches));
  useEffect(() => {
    if (!window.matchMedia) return;
    const m = window.matchMedia(q);
    const on = () => setWide(m.matches);
    m.addEventListener?.('change', on);
    return () => m.removeEventListener?.('change', on);
  }, []);
  return wide;
}

export function RealRunConfirm({ open, onRun, onCancel }: { open: boolean; onRun: () => void; onCancel: () => void }) {
  const wide = useWide();
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    cancelRef.current?.focus();
    const key = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onCancel(); } };
    window.addEventListener('keydown', key, true);
    return () => window.removeEventListener('keydown', key, true);
  }, [open, onCancel]);
  if (!open) return null;
  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-testid="real-run-scrim" onClick={onCancel}
      style={{ position: 'fixed', inset: 0, zIndex: 400, display: 'flex', justifyContent: 'center',
        alignItems: wide ? 'center' : 'flex-end', padding: wide ? 20 : 0, background: LEGACY.scrim,
        backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }}>
      <div role="alertdialog" aria-modal="true" aria-labelledby="real-run-title" data-testid="real-run-confirm" className={wide ? undefined : 'luma-bleed-panel'}
        onClick={e => e.stopPropagation()}
        style={{ width: '100%', maxWidth: wide ? 400 : 'none', boxSizing: 'border-box', padding: 24,
          paddingBottom: wide ? 24 : 'calc(24px + env(safe-area-inset-bottom))',
          backgroundColor: '#15102a', backgroundImage: `linear-gradient(${LEGACY.card}, ${LEGACY.card})`,
          border: `1px solid ${LEGACY.cardBorder}`, borderRadius: wide ? 20 : '20px 20px 0 0',
          backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', boxShadow: '0 24px 64px rgba(21,16,42,0.6)' }}>
        <h2 id="real-run-title" className="m-0 mb-5 text-[17px] font-semibold text-white/95">{REAL_RUN_TITLE}</h2>
        <div className={wide ? 'flex justify-end gap-3' : 'flex flex-col-reverse gap-3'}>
          <button ref={cancelRef} type="button" className={CANCEL_BTN} onClick={onCancel}>Cancel</button>
          <button type="button" className={RUN_BTN} data-testid="real-run-go" onClick={onRun}>Run</button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

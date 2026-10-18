import { useCallback, useEffect, useState } from 'react';
import type { NormalizedMediaItem } from '@/types/media';

/**
 * Light confirm sheet for permanently dismissing a What You're Missing title.
 */
export function DismissMissingSheet({
  item,
  onConfirm,
  onClose,
}: {
  item: NormalizedMediaItem;
  onConfirm: () => void;
  onClose: () => void;
}) {
  const [entered, setEntered] = useState(false);
  const [dismissing, setDismissing] = useState(false);

  useEffect(() => {
    const id = requestAnimationFrame(() => setEntered(true));
    return () => cancelAnimationFrame(id);
  }, []);

  const handleClose = useCallback(() => {
    setDismissing(true);
    window.setTimeout(onClose, 200);
  }, [onClose]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        handleClose();
      }
    }
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [handleClose]);

  const translate = dismissing ? '100%' : entered ? '0%' : '100%';

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Remove from what you're missing"
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 80,
        display: 'flex',
        flexDirection: 'column',
        justifyContent: 'flex-end',
      }}
    >
      <button
        type="button"
        aria-label="Close"
        onClick={handleClose}
        style={{
          position: 'absolute',
          inset: 0,
          border: 'none',
          background: dismissing ? 'rgba(0,0,0,0)' : 'rgba(0,0,0,0.45)',
          transition: 'background 0.2s ease',
          cursor: 'pointer',
        }}
      />
      <div
        style={{
          position: 'relative',
          background: 'rgba(22, 18, 28, 0.98)',
          borderTopLeftRadius: 18,
          borderTopRightRadius: 18,
          border: '1px solid rgba(255,255,255,0.08)',
          borderBottom: 'none',
          padding: '10px 16px calc(16px + env(safe-area-inset-bottom, 0px))',
          transform: `translateY(${translate})`,
          transition: 'transform 0.22s cubic-bezier(0.22, 1, 0.36, 1)',
          boxShadow: '0 -12px 40px rgba(0,0,0,0.45)',
        }}
      >
        <div
          style={{
            width: 36,
            height: 4,
            borderRadius: 2,
            background: 'rgba(255,255,255,0.18)',
            margin: '0 auto 14px',
          }}
        />
        <p style={{ margin: '0 0 4px', fontSize: 13, color: 'rgba(255,255,255,0.45)' }}>
          What You&apos;re Missing
        </p>
        <p
          style={{
            margin: '0 0 16px',
            fontSize: 16,
            fontWeight: 600,
            color: 'rgba(255,255,255,0.92)',
            lineHeight: 1.3,
          }}
        >
          {item.title}
          {item.year ? (
            <span style={{ fontWeight: 500, color: 'rgba(255,255,255,0.45)' }}> ({item.year})</span>
          ) : null}
        </p>
        <button
          type="button"
          onClick={() => {
            onConfirm();
          }}
          style={{
            width: '100%',
            border: 'none',
            borderRadius: 12,
            padding: '14px 16px',
            marginBottom: 8,
            background: 'hsla(280, 70%, 42%, 0.95)',
            color: 'white',
            fontSize: 15,
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Don&apos;t show again
        </button>
        <button
          type="button"
          onClick={handleClose}
          style={{
            width: '100%',
            border: '1px solid rgba(255,255,255,0.12)',
            borderRadius: 12,
            padding: '14px 16px',
            background: 'rgba(255,255,255,0.06)',
            color: 'rgba(255,255,255,0.82)',
            fontSize: 15,
            fontWeight: 500,
            cursor: 'pointer',
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

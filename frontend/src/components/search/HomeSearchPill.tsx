/** v215 C2: desktop Home search pill (480x52, a third of the way down the slideshow). Opens the palette. */
import { useEffect, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { openSearch, SearchIcon } from './MinimalSearchHost';

export const PILL_TOP = 'clamp(184px, 31vh, 372px)';

export function HomeSearchPill() {
  const [hidden, setHidden] = useState(false);
  useEffect(() => {
    const on = (e: Event) => setHidden(!!(e as CustomEvent).detail?.open);
    window.addEventListener('search-palette', on);
    return () => window.removeEventListener('search-palette', on);
  }, []);
  return (
    <>
      <style>{pillCss}</style>
      <button type="button" className="luma-home-pill" aria-label="Search" data-testid="home-search-pill"
        data-hidden={hidden ? 'true' : 'false'} onClick={openSearch} style={{ fontFamily: FONT }}>
        <span className="luma-home-pill-icon"><SearchIcon size={20} /></span>
        <span className="luma-home-pill-label">Search</span>
      </button>
    </>
  );
}

export const pillCss = `
.luma-home-pill{line-height:normal;position:fixed;left:50%;top:${PILL_TOP};transform:translateX(-50%);z-index:50;
  width:480px;height:52px;box-sizing:border-box;padding:0 20px;display:flex;align-items:center;gap:12px;
  border-radius:999px;background:rgba(21,16,42,.62);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);
  border:1px solid rgba(255,255,255,.14);box-shadow:0 8px 28px rgba(21,16,42,.45);color:rgba(255,255,255,.70);
  cursor:pointer;outline:none;transition:background 150ms,border-color 150ms,color 150ms,opacity 120ms,transform 80ms}
.luma-home-pill-icon{display:inline-flex}
.luma-home-pill-label{font-size:16px;font-weight:400}
.luma-home-pill-key{font-size:12px;font-weight:500;color:rgba(255,255,255,.70);padding:1px 6px;border-radius:6px;border:1px solid rgba(255,255,255,.10)}
.luma-home-pill:hover{background:rgba(21,16,42,.72);border-color:rgba(255,255,255,.22);color:rgba(255,255,255,.95)}
.luma-home-pill:focus-visible{border-color:var(--color-accent);box-shadow:0 8px 28px rgba(21,16,42,.45),0 0 0 3px rgba(59,130,246,.28)}
.luma-home-pill:active{transform:translateX(-50%) scale(.98)}
.luma-home-pill[data-hidden="true"]{opacity:0;visibility:hidden;transition:opacity 120ms,visibility 0s 120ms}
@media (prefers-reduced-motion: reduce){.luma-home-pill{transition:opacity 100ms}.luma-home-pill:active{transform:translateX(-50%)}}
`;

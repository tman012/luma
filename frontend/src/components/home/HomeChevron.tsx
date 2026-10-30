/** v326 (Taylor): Home's chevron (fixed, centered at the top, 48px, #e2e2e9, no circle or background) as one
 * shared component. Home's menu bar (HomeSearchR3) and the desktop TV Calendar's close-to-Home both render
 * this, so the glyph, size, color and spot are the same by construction. */
import type { ButtonHTMLAttributes } from 'react';

export const HOME_CHEV_UP = 'M7.4 15.4 12 10.8l4.6 4.6L18 14l-6-6-6 6z';
export const HOME_CHEV_DOWN = 'M7.4 8.6 12 13.2l4.6-4.6L18 10l-6 6-6-6z';
export const HOME_CHEV_SIZE = 48;

export const homeChevCss = `.a1-chev{position:fixed;z-index:56;top:28px;left:50%;transform:translateX(-50%);width:48px;height:48px;padding:0;border:0;background:none;box-shadow:none;
  color:#e2e2e9;display:grid;place-items:center;cursor:pointer;border-radius:8px}
.a1-chev:focus-visible{outline:2px solid var(--color-accent, #3b82f6);outline-offset:2px}`;

type Props = { up: boolean; withCss?: boolean } & Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'className' | 'type' | 'children'>;

export function HomeChevron({ up, withCss = false, ...rest }: Props) {
  return (
    <>
      {withCss && <style>{homeChevCss}</style>}
      <button type="button" className="a1-chev" data-home-chevron="" {...rest}>
        <svg width={HOME_CHEV_SIZE} height={HOME_CHEV_SIZE} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
          <path d={up ? HOME_CHEV_UP : HOME_CHEV_DOWN} />
        </svg>
      </button>
    </>
  );
}

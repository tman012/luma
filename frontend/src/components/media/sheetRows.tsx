/** Phone detail sheet rows shared by the real Movie/TV CompactLayouts and their seed (MOTION.md §1.2-1.3,
 *  Designer 8:53 PM: the details' boxes are reserved from frame 0). The real rows and the seed's invisible
 *  reserves are built from these same constants, so the boxes match exactly; the content fades in inside them. */
import type { CSSProperties, ReactNode } from 'react';

/** Under the 140px title block: the title block's own bottom margin (title `margin: 0 auto 24px`). */
export const TITLE_BLOCK_H = 140;
export const TITLE_BLOCK_MB = 24;

/** Meta line: year • runtime • rating • genres. */
export const META_ROW: CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px', marginTop: '8px', flexWrap: 'nowrap', fontSize: '14px',
};
/** Primary pill (Play / Add to Watchlist / Request ...). Layout only; each variant adds its colors. */
export const PILL: CSSProperties = {
  width: 'calc(100% - 32px)', margin: '20px 16px 0', padding: '14px 20px', borderRadius: '50px', border: 'none',
  fontSize: '16px', fontWeight: 600, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '10px',
};
export const PILL_ICON = 16;
/** Action button box: the circle's slot is the largest circle (5-6 buttons only shrink the circle inside it) and
 *  the label line is the 11px label's, so the row's height never depends on how many buttons it holds. */
export const ACTION_CIRCLE_SLOT = '50px';
export const ACTION_LABEL_LINE = 'calc(11px * 1.15)';
/** Overview: wrapper and clamped text (3 lines of 16px at 1.6). */
export const OVERVIEW_WRAP: CSSProperties = { padding: '0 16px', marginBottom: '24px', textAlign: 'left' };
export const OVERVIEW_LINES = 3;
export const OVERVIEW_TEXT: CSSProperties = { fontSize: '16px', lineHeight: 1.6, margin: 0 };
export const OVERVIEW_CLAMP: CSSProperties = {
  display: '-webkit-box', WebkitLineClamp: OVERVIEW_LINES, WebkitBoxOrient: 'vertical', overflow: 'hidden',
};

/** The seed's reserved rows: the same boxes as the real meta row, primary pill, action row and overview, empty
 *  and invisible (opacity 0, no fill). `actions` is the layout's own action row with one invisible button. */
export function ReservedRows({ actions, pillKept = false, ratingsH = 0 }: { actions: ReactNode; pillKept?: boolean; ratingsH?: number }) {
  return (
    <div data-seed-rows="" aria-hidden="true" style={{ opacity: 0, margin: `${TITLE_BLOCK_MB}px -16px 0`, textAlign: 'center', pointerEvents: 'none' }}>
      <div data-box="meta" style={META_ROW}><span>{'\u00a0'}</span></div>
      {ratingsH > 0 && <div style={{ minHeight: `${ratingsH}px` }} />}
      {/* Same wrapper as the real pill, so the build's display rules (html.luma-minimal [data-modal-actions])
          treat the reserve exactly like the real row. */}
      <div data-modal-actions="">
        <div data-box="watchlist" data-modal-keep={pillKept ? '' : undefined} style={{ ...PILL, background: 'none' }}>
          <svg width={PILL_ICON} height={PILL_ICON} aria-hidden="true" /><span>{'\u00a0'}</span>
        </div>
      </div>
      {actions}
      <div style={OVERVIEW_WRAP}>
        <p data-box="overview" style={{ ...OVERVIEW_TEXT, height: `calc(${OVERVIEW_TEXT.lineHeight}em * ${OVERVIEW_LINES})` }} />
      </div>
    </div>
  );
}

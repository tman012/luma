/** v321 (PM, Taylor 11:49 PM ET; final layout 12:03 AM, Designer r4c-title-v9 / build_v9.py): the series detail header.
 * No poster. The card takes the banner's natural aspect ratio (758/140 until the image loads) and the art fills it
 * 100% x 100%, so it's never cropped or stretched; it's clean art (no fade), with only Edit (dark fill) and the
 * monitored bookmark at the top right. Below it, centered: the title, the meta line, then the fields row.
 * No banner: the backdrop (cover, center 22%), else plain #111318. The art fades in on load (SPEC-fade-in: 240ms,
 * instant on a cache hit or reduced motion). */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { FONT } from '@/components/start/StartUI';
import { backdropUrl, tvdbImg } from '@/utils/image';

/** v5: a long path ends in an ellipsis */
export const PATH_MAX = 260;
export const HERO_BG = '#111318';
/** the fields stack's cap (v9) */
export const BOTTOM_MAX = 760;
/** the title's cap (v9) */
export const TITLE_MAX = 900;
/** Designer v9 spacing below the card: card to title, title to meta, meta to the fields, fields to the seasons */
export const GAP_TITLE = 20;
export const GAP_META = 6;
export const GAP_FIELDS = 16;
export const GAP_SEASONS = 28;
/** TvSeriesDetail's column gap (the hero adds the rest of GAP_SEASONS) */
export const PAGE_GAP = 16;
/** TheTVDB's banner size (v9), the box until the image tells its own ratio */
export const BANNER_RATIO = 758 / 140;
export const HERO_FONT = "'Google Sans Flex', " + FONT;

export function reducedMotion(): boolean {
  try { return !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}

export function TvSeriesHero({ title, meta, facts, bannerUrl, backdropPath, phone, edit, flag }: {
  title: string; meta: string[]; facts: [string, string, string][]; bannerUrl?: string | null; backdropPath?: string | null;
  phone: boolean; edit: ReactNode; flag: ReactNode;
}) {
  const banner = tvdbImg(bannerUrl);
  const [failed, setFailed] = useState(false);
  const useBanner = !!banner && !failed;
  const src = useBanner ? banner : (backdropPath ? backdropUrl(backdropPath, 'w1280') : null);
  const [ratio, setRatio] = useState(BANNER_RATIO);
  const [shown, setShown] = useState(false);
  const [animate, setAnimate] = useState(false);
  const img = useRef<HTMLImageElement>(null);
  useEffect(() => { setShown(false); setAnimate(false); setRatio(BANNER_RATIO); }, [src]);
  // a cache hit is complete before the first effect: show it at once, no fade
  useEffect(() => {
    const el = img.current;
    if (el && el.complete && el.naturalWidth > 0) { onLoad(el, false); }
  }, [src]); // eslint-disable-line react-hooks/exhaustive-deps
  const onLoad = (el: HTMLImageElement, fresh: boolean) => {
    if (useBanner && el.naturalWidth > 0 && el.naturalHeight > 0) setRatio(el.naturalWidth / el.naturalHeight);
    setAnimate(fresh && !reducedMotion());
    setShown(true);
  };
  const fade = animate ? 'opacity var(--motion-fade-dur, 240ms) var(--motion-fade-ease, cubic-bezier(0.2,0,0,1))' : 'none';
  const lab = { color: '#aeb0b8', fontSize: 11.5, lineHeight: '14px', marginBottom: 3, whiteSpace: 'nowrap' as const };
  const val = { color: '#fff', fontSize: 13, lineHeight: '16px', whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' };
  const fields = (
    <dl data-testid="tv-detail-facts" style={phone
      ? { margin: 0, display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0, 1fr))', columnGap: 24, rowGap: 12, width: '100%' }
      : { margin: 0, display: 'flex', justifyContent: 'center', gap: 40, textAlign: 'center', maxWidth: '100%' }}>
      {facts.map(([k, v, id]) => (
        <div key={k} data-testid={`tv-detail-fact-${id}`} style={{ minWidth: 0, flex: 'none', textAlign: 'center', maxWidth: id === 'path' && !phone ? PATH_MAX : undefined }}>
          <dt style={lab}>{k}</dt>
          <dd style={{ ...val, margin: 0, fontVariantNumeric: 'tabular-nums' }} title={v}>{v}</dd>
        </div>
      ))}
    </dl>
  );
  return (
    <div data-testid="tv-detail-hero" data-seasons-gap={GAP_SEASONS} style={{ display: 'flex', flexDirection: 'column', minWidth: 0, marginBottom: GAP_SEASONS - PAGE_GAP }}>
      <section data-testid="tv-detail-header" aria-label={title} data-art={useBanner ? 'banner' : src ? 'backdrop' : 'none'} data-ratio={ratio}
        style={{ position: 'relative', width: '100%', aspectRatio: String(ratio), borderRadius: phone ? 16 : 20, overflow: 'hidden',
          background: HERO_BG, border: '1px solid rgba(255,255,255,.08)', boxSizing: 'border-box', fontFamily: FONT }}>
        {src && (
          <img ref={img} src={src} alt="" data-testid="tv-detail-art" data-shown={shown || undefined} decoding="async"
            onLoad={e => onLoad(e.currentTarget, true)} onError={() => { if (useBanner) setFailed(true); }}
            style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', display: 'block',
              objectFit: useBanner ? 'fill' : 'cover', objectPosition: useBanner ? 'center' : 'center 22%',
              opacity: shown ? 1 : 0, transition: fade }} />
        )}
        <div style={{ position: 'absolute', top: phone ? 8 : 20, right: phone ? 8 : 20, display: 'flex', alignItems: 'center', gap: 8 }}>
          {flag}{!phone && edit}
        </div>
      </section>
      {/* below the card, centered: the title, the meta line, then the fields row, then the seasons */}
      <h1 data-testid="tv-detail-title" style={{ margin: `${GAP_TITLE}px auto 0`, width: 'fit-content', maxWidth: `min(${TITLE_MAX}px, 100%)`, textAlign: 'center',
        fontFamily: HERO_FONT, fontSize: phone ? 28 : 36, fontWeight: 700, lineHeight: 1.05, letterSpacing: -0.4, color: '#fff',
        whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{title}</h1>
      {meta.length > 0 && (
        <div data-testid="tv-detail-meta" style={{ margin: `${GAP_META}px auto 0`, color: '#c9cad2', fontSize: 14, lineHeight: '18px', textAlign: 'center',
          width: 'fit-content', maxWidth: '100%', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{meta.join(' \u00b7 ')}</div>
      )}
      <div data-testid="tv-detail-text" style={{ margin: `${GAP_FIELDS}px auto 0`, display: 'flex', flexDirection: 'column', alignItems: 'center',
        width: phone ? '100%' : undefined, maxWidth: `min(${BOTTOM_MAX}px, 100%)`, minWidth: 0 }}>
        {fields}
      </div>
    </div>
  );
}

/** Edit's token style (dark fill) */
export const heroEditBtn = {
  background: 'rgba(17,19,24,.94)', backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
  border: '1px solid rgba(255,255,255,.14)', borderRadius: 10, padding: '9px 18px', fontSize: 13, fontWeight: 600, color: '#fff',
  boxShadow: '0 6px 24px rgba(0,0,0,.45)', cursor: 'pointer', lineHeight: 'normal',
} as const;

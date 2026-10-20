/** v201 (Taylor's pick E2): the phone episode page (poster, title, air time, Monitored, Search automatically,
 * Interactive search), the automatic search dialog (searching, grabbed, none) and the interactive search
 * screen. Reuses the detail page's API calls; phone only (the caller decides). */
import { useEffect, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Poster } from './TvImport';
import { glassBtn, primaryBtn } from './TvEdit';
import { MonitorFlag, monitorFlagCss } from './TvMonitorFlag';
import { airET, bytes } from './TvSeriesDetail';
import {
  getDetail, grabRelease, interactiveSearch, searchOutcome, searchShow, setEpisodeMonitored,
  type DetailEpisode, type DetailView, type InteractiveResult, type Release,
} from './tvCalendarApi';

const W95 = 'rgba(255,255,255,0.95)';
const W70 = 'rgba(255,255,255,0.7)';
const GLASS = 'rgba(255,255,255,0.06)';
const LINE = '1px solid rgba(255,255,255,0.08)';
const GREEN_BG = 'rgba(34,197,94,0.14)', GREEN_B = 'rgba(34,197,94,0.5)', GREEN_T = '#86efac';
const SRC: Record<string, string> = { web: 'WEB', bluray: 'Bluray', hdtv: 'HDTV', remux: 'Remux' };

export const shortCode = (s: number, e: number) => `S${s}E${e}`;
/** '1080p-web' -> '1080p WEB' (labels pass through). */
export const qualityText = (q: string | null | undefined) => {
  if (!q) return '';
  const [res, src] = q.split('-');
  return src && SRC[src] ? `${res === 'sd' ? 'SD' : res} ${SRC[src]}` : q;
};
/** The release group: the name's last '-GROUP'. */
export const groupOf = (title: string) => title.replace(/\.(mkv|mp4|avi)$/i, '').match(/-([A-Za-z0-9]+)$/)?.[1] ?? '';

type Auto = { k: 'searching' } | { k: 'grabbed'; title: string; quality: string; size: number | null } | { k: 'none' } | { k: 'msg'; text: string };

const iconBack = (
  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="m15 18-6-6 6-6" /></svg>
);
const backBtn: React.CSSProperties = { width: 44, height: 44, flex: 'none', padding: 0, border: 'none', background: 'none', color: W95, cursor: 'pointer',
  display: 'inline-flex', alignItems: 'center', justifyContent: 'flex-start' };

export function TvEpisodePage({ seriesId, episodeId, onBack, onChanged }: {
  seriesId: number; episodeId: number; onBack: () => void; onChanged?: () => void;
}) {
  const [view, setView] = useState<DetailView | null>(null);
  const [failed, setFailed] = useState(false);
  const [auto, setAuto] = useState<Auto | null>(null);
  const [inter, setInter] = useState(false);
  const run = useRef(0);
  const load = () => getDetail(seriesId).then(r => { if (r.ok) { setView(r.data); setFailed(false); } else setFailed(true); });
  useEffect(() => { void load(); }, [seriesId]);   // eslint-disable-line react-hooks/exhaustive-deps
  const e = view?.episodes.find(x => x.id === episodeId);
  if (failed) return <div role="alert" style={{ fontFamily: FONT, fontSize: 15, color: W70 }}>{"Couldn't load this series"}</div>;
  if (!view) return <div data-testid="tv-episode-loading" aria-busy="true" style={{ height: 200, borderRadius: 16, background: GLASS }} />;
  if (!e) return <div style={{ fontFamily: FONT, fontSize: 15, color: W70 }}>No episodes</div>;
  const changed = () => { void load(); onChanged?.(); };
  const label = `${view.title} \u00b7 ${shortCode(e.season, e.episode)}`;
  const name = e.title || `Episode ${e.episode}`;

  const searchAuto = async () => {
    const my = ++run.current;
    setAuto({ k: 'searching' });
    const r = await searchShow(seriesId, { episode_id: e.id });
    if (run.current !== my) return;                       // cancelled
    const pick = r.ok ? r.data.picks.find(p => p.grabbed) : undefined;
    if (pick) {
      const p = pick as typeof pick & { size?: number | null };
      setAuto({ k: 'grabbed', title: p.title, quality: qualityText(p.quality), size: p.size ?? null });
      changed();
      return;
    }
    const text = searchOutcome(r);
    setAuto(text === 'No releases found' ? { k: 'none' } : { k: 'msg', text });
  };

  if (inter) {
    return (
      <InteractiveScreen seriesId={seriesId} e={e} show={view.title} onBack={() => setInter(false)}
        onGrabbed={(r) => { setInter(false); setAuto({ k: 'grabbed', title: r.title, quality: r.quality_label || qualityText(r.quality), size: r.size }); changed(); }} />
    );
  }
  const meta = [airET(e), e.runtime ? `${e.runtime} min` : ''].filter(Boolean).join(' \u00b7 ');
  return (
    <div data-testid="tv-episode-page" style={{ fontFamily: FONT, display: 'flex', flexDirection: 'column', gap: 12 }}>
      <style>{monitorFlagCss}</style>
      <button type="button" className="lp-focus" data-testid="tv-episode-back" aria-label="Back" onClick={onBack} style={backBtn}>{iconBack}</button>
      <div style={{ display: 'flex', gap: 16, alignItems: 'flex-start' }}>
        <Poster path={view.poster_path} w={116} h={174} tmdbLogo />
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
          <div style={{ fontSize: 13, color: W70 }}>{label}</div>
          <h2 data-testid="tv-episode-title" style={{ margin: 0, fontSize: 22, fontWeight: 700, lineHeight: 1.15, color: W95, overflowWrap: 'anywhere' }}>{name}</h2>
          {meta && <div data-testid="tv-episode-meta" style={{ fontSize: 13, color: W70, marginTop: 4 }}>{meta}</div>}
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 12, minHeight: 44 }}>
            <MonitorFlag on={e.monitored} title={name} testid="tv-episode-monitored" onChange={on => void setEpisodeMonitored(e.id, on).then(changed)} />
            <span style={{ fontSize: 14, color: W95 }}>{e.monitored ? 'Monitored' : 'Unmonitored'}</span>
          </div>
        </div>
      </div>
      <button type="button" className="lp-focus tve-primary" data-testid="tv-episode-auto" onClick={() => void searchAuto()}
        style={{ ...primaryBtn(true), height: 52, width: '100%', borderRadius: 14, fontSize: 16, marginTop: 16, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
        Search automatically
      </button>
      <button type="button" className="lp-focus tve-glass" data-testid="tv-episode-interactive" onClick={() => setInter(true)}
        style={{ ...glassBtn, height: 52, width: '100%', borderRadius: 14, fontSize: 16, display: 'inline-flex', alignItems: 'center', justifyContent: 'center', gap: 8 }}>
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01" /></svg>
        Interactive search
      </button>
      {auto && (
        <AutoDialog auto={auto} sub={label} onClose={() => { run.current++; setAuto(null); }}
          onInteractive={() => { setAuto(null); setInter(true); }} />
      )}
    </div>
  );
}

function AutoDialog({ auto, sub, onClose, onInteractive }: { auto: Auto; sub: string; onClose: () => void; onInteractive: () => void }) {
  const big = (t: string) => <div data-testid="tv-auto-title" style={{ fontSize: 17, fontWeight: 600, color: W95 }}>{t}</div>;
  const subEl = <div style={{ fontSize: 13, color: W70, marginTop: 4 }}>{sub}</div>;
  const full: React.CSSProperties = { width: '100%', height: 44, marginTop: 8 };
  let inner: React.ReactNode;
  if (auto.k === 'searching') {
    inner = (<>
      <style>{'@keyframes tvspin{to{transform:rotate(360deg)}}'}</style>
      <div aria-hidden="true" style={{ width: 32, height: 32, margin: '4px auto 12px', borderRadius: '50%', border: '3px solid rgba(255,255,255,0.14)', borderTopColor: '#60a5fa', animation: 'tvspin 0.9s linear infinite' }} />
      {big('Searching indexers\u2026')}{subEl}
      <button type="button" className="lp-focus" data-testid="tv-auto-cancel" onClick={onClose} style={{ ...full, border: 'none', background: 'none', color: '#60a5fa', fontFamily: FONT, fontSize: 15, fontWeight: 600, cursor: 'pointer' }}>Cancel</button>
    </>);
  } else if (auto.k === 'grabbed') {
    inner = (<>
      <div aria-hidden="true" style={{ width: 44, height: 44, margin: '0 auto 10px', borderRadius: '50%', display: 'grid', placeItems: 'center', background: GREEN_BG, color: GREEN_T }}>
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7" /></svg>
      </div>
      {big('Grabbed')}
      <div data-testid="tv-auto-release" style={{ fontSize: 13, color: W70, marginTop: 8, wordBreak: 'break-all' }}>{auto.title}</div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'center', alignItems: 'center', marginTop: 10 }}>
        {auto.quality && <span style={{ fontSize: 12, height: 24, display: 'inline-flex', alignItems: 'center', padding: '0 8px', borderRadius: 6, background: GREEN_BG, border: `1px solid ${GREEN_B}`, color: GREEN_T }}>{auto.quality}</span>}
        {auto.size ? <span style={{ fontSize: 13, color: W70 }}>{bytes(auto.size)}</span> : null}
      </div>
      <button type="button" className="lp-focus tve-primary" data-testid="tv-auto-ok" onClick={onClose} style={{ ...primaryBtn(true), ...full, marginTop: 16 }}>OK</button>
    </>);
  } else {
    inner = (<>
      <div style={{ marginTop: 4 }}>{big(auto.k === 'none' ? 'No releases found' : auto.text)}</div>{subEl}
      <button type="button" className="lp-focus tve-primary" data-testid="tv-auto-interactive" onClick={onInteractive} style={{ ...primaryBtn(true), ...full, marginTop: 16 }}>Interactive search</button>
      <button type="button" className="lp-focus tve-glass" data-testid="tv-auto-close" onClick={onClose} style={{ ...glassBtn, ...full }}>Close</button>
    </>);
  }
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(21,16,42,0.72)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
      display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 24 }}>
      <div role="dialog" aria-modal="true" data-testid="tv-auto-dialog" data-state={auto.k}
        style={{ width: '100%', maxWidth: 360, boxSizing: 'border-box', borderRadius: 20, padding: 20, textAlign: 'center', fontFamily: FONT,
          background: 'rgba(21,16,42,0.92)', backdropFilter: 'blur(40px)', WebkitBackdropFilter: 'blur(40px)', border: LINE }}>
        {inner}
      </div>
    </div>
  );
}

type SortKey = 'seeders' | 'size' | 'quality';
const qrank = (r: Release) => { const q = (r.quality || '').toLowerCase(); return (parseInt(q, 10) || 0) * 10 + (/remux|bluray/.test(q) ? 2 : q.includes('web') ? 1 : 0); };

export function InteractiveScreen({ seriesId, e, show, onBack, onGrabbed }: {
  seriesId: number; e: DetailEpisode; show: string; onBack: () => void; onGrabbed: (r: Release) => void;
}) {
  const [res, setRes] = useState<InteractiveResult | null>(null);
  const [error, setError] = useState('');
  const [sort, setSort] = useState<SortKey>('seeders');
  const [busy, setBusy] = useState<string | null>(null);
  const [errs, setErrs] = useState<Record<string, string>>({});
  useEffect(() => {
    let live = true;
    void interactiveSearch(seriesId, { episode_id: e.id }).then(r => {
      if (!live) return;
      if (r.ok) { setRes(r.data); setError(r.data.error || ''); } else setError(r.error);
    });
    return () => { live = false; };
  }, [seriesId, e.id]);
  const list = [...(res?.releases ?? [])].sort((a, b) =>
    sort === 'size' ? b.size - a.size : sort === 'quality' ? qrank(b) - qrank(a) || b.seeders - a.seeders : b.seeders - a.seeders);
  const off = res ? !res.grabs_allowed : false;
  const grab = async (r: Release, k: string) => {
    setBusy(k);
    const out = await grabRelease(seriesId, r);
    setBusy(null);
    if (out.ok && out.data.ok) { onGrabbed(r); return; }
    setErrs(x => ({ ...x, [k]: out.ok ? out.data.error || 'Not grabbed' : out.error }));
  };
  const seg = (on: boolean): React.CSSProperties => ({ flex: 1, height: 44, borderRadius: 10, border: 'none', fontFamily: FONT, fontSize: 15, fontWeight: 600, cursor: 'pointer',
    background: on ? '#3b82f6' : 'transparent', color: on ? '#fff' : W70 });
  return (
    <div data-testid="tv-interactive-screen" style={{ fontFamily: FONT, display: 'flex', flexDirection: 'column', gap: 12, margin: '0 -16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '0 16px' }}>
        <button type="button" className="lp-focus" data-testid="tv-interactive-back" aria-label="Back" onClick={onBack} style={backBtn}>{iconBack}</button>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 17, fontWeight: 700, color: W95, overflowWrap: 'anywhere' }}>{show}</div>
          <div style={{ fontSize: 13, color: W70 }}>{`${shortCode(e.season, e.episode)} \u00b7 ${e.title || `Episode ${e.episode}`}`}</div>
        </div>
        {res && <span data-testid="tv-interactive-count" style={{ fontSize: 13, color: W70, whiteSpace: 'nowrap' }}>{`${list.length} ${list.length === 1 ? 'release' : 'releases'}`}</span>}
      </div>
      <div role="group" aria-label="Sort" style={{ display: 'flex', gap: 4, padding: 4, margin: '0 16px', borderRadius: 14, background: GLASS, border: LINE }}>
        {(['seeders', 'size', 'quality'] as const).map(k => (
          <button key={k} type="button" data-testid={`tv-interactive-sort-${k}`} aria-pressed={sort === k} onClick={() => setSort(k)} style={seg(sort === k)}>
            {k === 'seeders' ? 'Seeders' : k === 'size' ? 'Size' : 'Quality'}
          </button>
        ))}
      </div>
      {error && !list.length ? (
        <div role="alert" style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>{error}</div>
      ) : !res ? (
        <div data-testid="tv-interactive-loading" aria-busy="true">
          {[0, 1, 2, 3].map(i => <div key={i} style={{ height: 96, borderTop: LINE, background: GLASS, opacity: 0.6 }} />)}
        </div>
      ) : !list.length ? (
        <div style={{ minHeight: 160, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15, color: W70 }}>No releases</div>
      ) : (
        <div role="list" aria-label="Releases" style={{ borderBottom: LINE }}>
          {list.map(r => {
            const k = `${r.tracker}|${r.title}|${r.size}`;
            const grp = groupOf(r.title);
            const line = [bytes(r.size), `${r.seeders} seeders`, r.tracker || '', grp].filter(Boolean).join(' \u00b7 ');
            return (
              <div key={k} role="listitem" data-testid="tv-interactive-row" style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px 10px 16px', borderTop: LINE }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: W95, wordBreak: 'break-all', lineHeight: 1.3 }}>{r.title}</div>
                  <div style={{ display: 'flex', gap: '6px 8px', alignItems: 'center', marginTop: 6, flexWrap: 'wrap' }}>
                    {(r.quality_label || r.quality) && (
                      <span style={{ fontSize: 12, height: 24, display: 'inline-flex', alignItems: 'center', padding: '0 8px', borderRadius: 6, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.10)', color: W95 }}>
                        {r.quality_label || qualityText(r.quality)}
                      </span>
                    )}
                  </div>
                  <div data-testid="tv-interactive-line" style={{ fontSize: 12, color: W70, marginTop: 6 }}>{line}</div>
                  {r.rejections.length > 0 && <div style={{ fontSize: 12, fontWeight: 500, marginTop: 4, color: r.grabbable ? '#f59e0b' : '#ef4444' }}>{r.rejections.join(' \u00b7 ')}</div>}
                  {errs[k] && <div role="alert" style={{ fontSize: 12, marginTop: 4, color: '#ef4444' }}>{errs[k]}</div>}
                </div>
                {!off && (
                  <button type="button" className="lp-focus tve-primary" data-testid="tv-interactive-grab" aria-label={`Grab ${r.title}`}
                    disabled={!r.grabbable || busy !== null} onClick={() => void grab(r, k)}
                    style={{ ...primaryBtn(r.grabbable && busy === null), height: 52, padding: '0 14px', borderRadius: 14, fontSize: 16, flex: 'none' }}>Grab</button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/** Settings > TV > Show detail (docs/design/tv.md §5): the show's seasons and
 * episodes with their linked files, "Wrong show?", and the deep-linked episode
 * highlighted for a moment. */
import { useCallback, useEffect, useRef, useState } from 'react';
import { FONT } from '@/components/start/StartUI';
import { ChevronLeftIcon } from '@/components/nav/AppNav';
import { Poster, ShowPicker } from './TvImport';
import { glassCard } from './TvCalendar';
import { rematchShow, type Candidate } from './tvLibraryApi';
import {
  STATE_COLOR, STATE_WORD, episodeState, haveText, loadShow, longDate, metaText, openingSeason, seasonName,
  type Day, type ShowDetail,
} from './tvCalendarApi';

export const HIGHLIGHT_MS = 1600;
const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';

export function TvShowDetail({ wide, id, season, episode, today, onBack, onSeason, say }: {
  wide: boolean; id: number; season: number | null; episode: number | null; today: Day;
  onBack: () => void; onSeason: (n: number) => void; say: (msg: string) => void;
}) {
  const [show, setShow] = useState<ShowDetail | null>(null);
  const [failed, setFailed] = useState(false);
  const [picker, setPicker] = useState(false);
  const [lit, setLit] = useState<number | null>(episode);
  const anchor = useRef<HTMLButtonElement>(null);
  const load = useCallback(() => {
    setFailed(false);
    void loadShow(id).then(r => { if (r.ok) setShow(r.data); else { setShow(null); setFailed(true); } });
  }, [id]);
  useEffect(() => { setShow(null); load(); }, [load]);
  useEffect(() => {
    setLit(episode);
    if (episode == null) return undefined;
    const t = window.setTimeout(() => setLit(null), HIGHLIGHT_MS);
    return () => window.clearTimeout(t);
  }, [id, season, episode]);
  const litRow = useRef<HTMLDivElement | null>(null);
  useEffect(() => { litRow.current?.scrollIntoView?.({ block: 'center' }); }, [show]);

  const back = wide && (
    <button type="button" data-testid="tv-detail-back" className="lp-focus" onClick={onBack} style={backBtn}>
      <ChevronLeftIcon size={16} />TV
    </button>
  );
  if (failed) {
    return (
      <div data-testid="tv-detail" style={{ fontFamily: FONT }}>
        {back}
        <div role="alert" style={{ minHeight: 200, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
          <span style={{ fontSize: 15, fontWeight: 500, color: W70 }}>{"Couldn't load show"}</span>
          <button type="button" data-testid="tv-detail-retry" className="lp-focus tvc-glass" onClick={load} style={glassBtn}>Retry</button>
        </div>
      </div>
    );
  }
  if (!show) {
    return (
      <div data-testid="tv-detail" aria-busy="true" style={{ fontFamily: FONT }}>
        {back}
        <div style={{ display: 'flex', gap: 16 }}>
          <span className="tvc-pulse" style={{ width: wide ? 120 : 80, height: wide ? 180 : 120, borderRadius: 8, background: 'rgba(255,255,255,0.04)' }} />
          <span style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: 10, paddingTop: 4 }}>
            <span className="tvc-pulse" style={{ height: 22, width: '50%', borderRadius: 4, background: 'rgba(255,255,255,0.04)' }} />
            <span className="tvc-pulse" style={{ height: 13, width: '30%', borderRadius: 4, background: 'rgba(255,255,255,0.04)' }} />
          </span>
        </div>
      </div>
    );
  }
  const current = openingSeason(show.seasons, season);
  const eps = show.seasons.find(s => s.number === current)?.episodes || [];
  const pick = (c: Candidate) => {
    setPicker(false);
    if (c.tmdb_id === show.tmdb_id) return;
    void rematchShow(show.id, c.tmdb_id).then(r => { if (r.ok) { say('Matched'); load(); } else say(r.error); });
  };
  const pw = wide ? 120 : 80;
  return (
    <div data-testid="tv-detail" style={{ fontFamily: FONT }}>
      {back}
      <header style={{ display: 'flex', gap: wide ? 20 : 16, alignItems: 'flex-start' }}>
        <Poster path={show.poster_path} w={pw} h={pw * 1.5} tmdbLogo />
        <div style={{ position: 'relative', flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 6 }}>
          <h3 data-testid="tv-detail-title" style={{ margin: 0, fontSize: 24, fontWeight: 600, lineHeight: '30px', color: W95, overflowWrap: 'anywhere' }}>{show.title}</h3>
          {metaText(show) && <span style={{ fontSize: 13, color: W50 }}>{metaText(show)}</span>}
          <span data-testid="tv-detail-have" style={{ fontSize: 13, color: W70 }}>{haveText(show.have, show.aired)}</span>
          <span>
            <button ref={anchor} type="button" data-testid="tv-wrong-show" className="lp-focus" aria-haspopup="dialog" aria-expanded={picker}
              onClick={() => setPicker(p => !p)}
              style={{ minHeight: 32, padding: 0, border: 'none', background: 'none', color: '#3b82f6', fontSize: 14, fontWeight: 500, fontFamily: FONT, cursor: 'pointer' }}>
              Wrong show?
            </button>
          </span>
          {picker && (
            <ShowPicker wide={wide} anchor={anchor} initial={show.title} year={show.year} current={show.tmdb_id}
              suggestions={[]} onPick={pick} onClose={() => setPicker(false)} />
          )}
        </div>
      </header>

      {show.seasons.length > 0 && (
        <div role="tablist" aria-label="Seasons" data-testid="tv-seasons" style={chipRow}>
          {show.seasons.map(s => {
            const on = s.number === current;
            return (
              <button key={s.number} type="button" role="tab" aria-selected={on} data-testid="tv-season-chip" className="lp-focus"
                onClick={() => onSeason(s.number)}
                style={{ ...chip, ...(on ? { background: '#3b82f6', borderColor: '#3b82f6', color: '#fff' } : null) }}>
                {seasonName(s.number)}
              </button>
            );
          })}
        </div>
      )}

      {eps.length > 0 && (
        <div data-testid="tv-episodes" style={{ ...glassCard, marginTop: 16 }}>
          {eps.map((e, i) => {
            const st = episodeState(e, today);
            const hl = lit != null && e.episode === lit && e.season === current;
            return (
              <div key={e.id} ref={hl ? litRow : undefined} data-testid="tv-episode" data-state={st} data-highlight={hl || undefined}
                title={e.files.join('\n') || undefined}
                className={hl ? 'tvd-hl' : undefined}
                style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12, height: 56, padding: '0 16px 0 12px',
                  boxSizing: 'border-box', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none' }}>
                <span style={{ width: 40, flex: 'none', fontSize: 13, fontWeight: 600, color: W50 }}>E{String(e.episode).padStart(2, '0')}</span>
                <span style={{ flex: 1, minWidth: 0, fontSize: 14, fontWeight: 500, color: W95, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {e.title || `Episode ${e.episode}`}
                </span>
                {wide && e.air_date && <span style={{ flex: 'none', fontSize: 13, color: W50 }}>{longDate(e.air_date)}</span>}
                <span style={{ flex: 'none', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 2, minWidth: wide ? 84 : 0 }}>
                  {!wide && e.air_date && <span style={{ fontSize: 12, color: W50 }}>{longDate(e.air_date)}</span>}
                  <span style={{ fontSize: 12, fontWeight: 600, color: STATE_COLOR[st] }}>{STATE_WORD[st]}</span>
                </span>
                <span aria-hidden="true" style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 3, background: STATE_COLOR[st] }} />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

export const tvDetailCss = `
@keyframes tvd-hl{0%,40%{background:rgba(255,255,255,.12)}100%{background:rgba(255,255,255,0)}}
.tvd-hl{animation:tvd-hl ${HIGHLIGHT_MS}ms ease-out forwards}
@media (prefers-reduced-motion: reduce){.tvd-hl{animation-timing-function:steps(1,end)}}
`;

const backBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: 2, height: 32, margin: '0 0 12px -4px', padding: '0 4px', border: 'none',
  background: 'none', color: '#3b82f6', fontSize: 15, fontWeight: 500, fontFamily: FONT, cursor: 'pointer',
};
const glassBtn: React.CSSProperties = {
  height: 40, padding: '0 20px', borderRadius: 10, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.12)',
  color: '#fff', fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
};
const chipRow: React.CSSProperties = {
  display: 'flex', gap: 8, marginTop: 20, overflowX: 'auto', scrollbarWidth: 'none', paddingBottom: 2,
};
const chip: React.CSSProperties = {
  height: 32, padding: '0 14px', borderRadius: 999, flex: 'none', background: 'rgba(255,255,255,0.06)',
  border: '1px solid rgba(255,255,255,0.08)', color: W95, fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', whiteSpace: 'nowrap',
};

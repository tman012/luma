/** v224 where Play (movie popup) and Shuffle (TV show popup) play: one room plays at once with a
 * 'Playing in <Room> · Change' toast; several open the 'Play in' picker with the default preselected.
 * No play buttons here; the popups call choosePlayRoom and render these. */
import { useCallback, useEffect, useState, type ReactNode, useRef } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import type { Room } from './roomsApi';
import { badge, loadRooms, roomOnline } from './roomsApi';
import { PLAYBACK_STARTED, stopRoom } from '@/services/playbackApi';

export type PlayChoice = { mode: 'none' } | { mode: 'auto'; room: Room } | { mode: 'pick'; rooms: Room[]; preselect: string };

/** Rooms with a player; exactly one plays at once, several pick (default room first choice). */
export function choosePlayRoom(rooms: Room[] | null | undefined, defaultId?: string): PlayChoice {
  const usable = (Array.isArray(rooms) ? rooms : []).filter(r => r && r.player);
  if (!usable.length) return { mode: 'none' };
  if (usable.length === 1) return { mode: 'auto', room: usable[0]! };
  const def = usable.find(r => r.id === defaultId) || usable.find(r => r.default) || usable[0]!;
  return { mode: 'pick', rooms: usable, preselect: def.id };
}

const W50 = 'rgba(255,255,255,0.5)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const GREEN = '#22c55e';
const btn = { height: 40, flex: 1, borderRadius: 10, fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer' } as const;

export function StatusDot({ online }: { online: boolean }) {
  return <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: 3, background: online ? GREEN : 'rgba(255,255,255,0.35)', display: 'inline-block', flex: 'none' }} />;
}
export function Badge({ text }: { text: string }) {
  return <span aria-hidden="true" style={{ width: 32, height: 32, borderRadius: 8, flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center',
    background: 'rgba(59,130,246,0.18)', color: '#bfdbfe', fontSize: 11, fontWeight: 700, fontFamily: FONT }}>{text}</span>;
}

/** The 'Play in' / 'Shuffle in' picker: a radio list of rooms, Cancel and the action. */
export function PlayInPicker({ rooms, preselect, action = 'Play', onPick, onCancel }: {
  rooms: Room[]; preselect: string; action?: 'Play' | 'Shuffle'; onPick: (room: Room) => void; onCancel: () => void;
}) {
  const [sel, setSel] = useState(preselect);
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    document.addEventListener('keydown', k);
    return () => document.removeEventListener('keydown', k);
  }, [onCancel]);
  const room = rooms.find(r => r.id === sel);
  return (
    <div className="luma-fullscreen luma-bleed" data-testid="play-in-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onCancel(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 10010, background: 'rgba(10,8,24,.6)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: FONT, padding: 16 }}>
      <div role="dialog" aria-modal="true" aria-label={`${action} in`} data-testid="play-in"
        style={{ width: 360, maxWidth: '100%', boxSizing: 'border-box', padding: 16, borderRadius: 16, background: 'rgba(21,16,42,0.96)',
          border: '1px solid rgba(255,255,255,0.08)', color: W95, boxShadow: '0 24px 64px rgba(0,0,0,.5)' }}>
        <h2 style={{ margin: '4px 4px 12px', fontSize: 16, fontWeight: 600 }}>{action} in</h2>
        <div role="radiogroup" aria-label={`${action} in`} style={{ borderRadius: 12, border: '1px solid rgba(255,255,255,0.08)', background: 'rgba(255,255,255,0.04)', overflow: 'hidden' }}>
          {rooms.map((r, i) => {
            const on = r.id === sel;
            return (
              <button key={r.id} type="button" role="radio" aria-checked={on} data-testid="play-in-room" onClick={() => setSel(r.id)}
                className="lp-focus"
                style={{ width: '100%', minHeight: 56, display: 'flex', alignItems: 'center', gap: 12, padding: '0 12px', background: 'none', border: 'none',
                  borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', color: W95, cursor: 'pointer', textAlign: 'left', fontFamily: FONT }}>
                <span aria-hidden="true" style={{ width: 18, height: 18, borderRadius: 9, boxSizing: 'border-box', flex: 'none',
                  border: on ? `5px solid ${ACCENT}` : '2px solid rgba(255,255,255,0.35)' }} />
                <Badge text={badge(r.player)} />
                <span style={{ display: 'flex', flexDirection: 'column', minWidth: 0 }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{r.name}</span>
                  <span style={{ fontSize: 12, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <StatusDot online={roomOnline(r)} />{roomOnline(r) ? 'Online' : 'Offline'}
                  </span>
                </span>
              </button>
            );
          })}
        </div>
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <button type="button" data-testid="play-in-cancel" onClick={onCancel} className="lp-focus"
            style={{ ...btn, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', color: W95 }}>Cancel</button>
          <button type="button" data-testid="play-in-go" disabled={!room} onClick={() => room && onPick(room)} className="lp-focus"
            style={{ ...btn, background: ACCENT, border: 'none', color: '#fff' }}>{action}</button>
        </div>
      </div>
    </div>
  );
}

/** 'Playing in <Room> · Change' at the top of the popup; Change opens the picker. */
export function PlayingToast({ room, onChange, onDone, ms = 6000 }: { room: Room; onChange: () => void; onDone?: () => void; ms?: number }) {
  useEffect(() => { if (!onDone) return; const t = window.setTimeout(onDone, ms); return () => window.clearTimeout(t); }, [onDone, ms]);
  return (
    <div role="status" data-testid="playing-toast"
      style={{ position: 'fixed', top: 'calc(env(safe-area-inset-top, 0px) + 12px)', left: '50%', transform: 'translateX(-50%)', zIndex: 10010,
        width: 'min(420px, calc(100vw - 24px))', boxSizing: 'border-box', display: 'flex', alignItems: 'center', gap: 10, padding: '6px 6px 6px 14px',
        borderRadius: 12, background: 'rgba(21,16,42,0.96)', border: '1px solid rgba(255,255,255,0.1)', color: W95, fontFamily: FONT, fontSize: 13,
        boxShadow: '0 12px 32px rgba(0,0,0,.4)' }}>
      <StatusDot online />
      <span style={{ flex: 1, minWidth: 0 }}>Playing in <b>{room.name}</b></span>
      <button type="button" data-testid="playing-toast-change" onClick={onChange} className="lp-focus"
        style={{ height: 32, padding: '0 12px', borderRadius: 8, background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
          color: '#93c5fd', fontSize: 13, fontWeight: 600, fontFamily: FONT, cursor: 'pointer' }}>Change</button>
    </div>
  );
}

/** v225 the playback popups (prod's PlaybackModal / MobilePlaybackSheet) start room first: with their usable rooms
 * (a device with a host), one room is selected at once and the toast shows; several open the picker with the
 * default room preselected (names, players and online state from /api/rooms when it answers); none returns false
 * so prod's own room step shows. The popups keep everything after the room (device, resume, play). */
export function useRoomFirst<T extends { id: string; name: string }>({ action, select, onCancel }: {
  action: 'Play' | 'Shuffle'; select: (room: T) => void; onCancel: () => void;
}): { start: (usable: T[], defaultId?: string) => boolean; closeAfter: (ms: number) => void; failed: () => void; ui: ReactNode } {
  const [pick, setPick] = useState<{ rooms: Room[]; preselect: string; byId: Map<string, T>; fromToast: boolean } | null>(null);
  const [toast, setToast] = useState<Room | null>(null);
  const [all, setAll] = useState<T[]>([]);
  const [current, setCurrent] = useState('');
  const [closeWanted, setCloseWanted] = useState(false);
  // prod closes the popup 2s after playback starts; while the toast or the picker shows, it waits for them
  useEffect(() => { if (closeWanted && !toast && !pick) { setCloseWanted(false); onCancel(); } }, [closeWanted, toast, pick, onCancel]);
  const closeAfter = useCallback((ms: number) => { window.setTimeout(() => setCloseWanted(true), ms); }, []);
  const [pendingStop, setPendingStop] = useState<{ from: string; to: string; fromRoom: Room } | null>(null);
  const changedFrom = useRef<Room | null>(null);
  const pendingRef = useRef(pendingStop);
  pendingRef.current = pendingStop;
  // v231 (Tester): play failed in the room just picked. After Change the first room still plays, so the toast
  // goes back to it; on a first play nothing plays, so it hides. The popup keeps showing its error.
  const failed = useCallback(() => {
    const p = pendingRef.current;
    setPendingStop(null);
    if (p) { setCurrent(p.from); setToast(p.fromRoom); } else setToast(null);
  }, []);
  // Change: once playback has started in the new room, stop the first one (never before, so nothing is left silent)
  useEffect(() => {
    if (!pendingStop) return;
    const on = (e: Event) => {
      const id = (e as CustomEvent<{ roomId?: string }>).detail?.roomId;
      if (id !== pendingStop.to) return;
      setPendingStop(null);
      void stopRoom(pendingStop.from);
    };
    window.addEventListener(PLAYBACK_STARTED, on);
    return () => window.removeEventListener(PLAYBACK_STARTED, on);
  }, [pendingStop]);
  const [def, setDef] = useState('');
  const view = (rooms: T[], known: Room[]): Room[] => rooms.map(r => known.find(k => k.id === r.id) || { id: r.id, name: r.name, default: false, harmony_hub: '', player: null });
  const openPicker = useCallback((rooms: T[], defaultId: string, fromToast: boolean) => {
    const byId = new Map(rooms.map(r => [r.id, r]));
    const preselect = rooms.find(r => r.id === defaultId)?.id || rooms[0]!.id;
    setPick({ rooms: view(rooms, []), preselect, byId, fromToast });
    void loadRooms().then(r => { if (r.ok) setPick(p => (p && p.byId === byId ? { ...p, rooms: view(rooms, r.data.rooms) } : p)); });
  }, []);
  const start = useCallback((usable: T[], defaultId?: string) => {
    setAll(usable); setDef(defaultId || '');
    const c = choosePlayRoom(usable.map(r => ({ id: r.id, name: r.name, default: r.id === defaultId, harmony_hub: '', player: {} as Room['player'] })), defaultId);
    if (c.mode === 'none') return false;
    if (c.mode === 'auto') {
      select(usable[0]!);
      setCurrent(usable[0]!.id);
      setToast({ id: usable[0]!.id, name: usable[0]!.name, default: true, harmony_hub: '', player: null });
      return true;
    }
    openPicker(usable, c.preselect, false);
    return true;
  }, [select, openPicker]);
  const ui = (toast || pick) && typeof document !== 'undefined' && createPortal(
    <>
      {toast && !pick && <PlayingToast room={toast} onDone={() => setToast(null)}
        onChange={() => { changedFrom.current = toast; setToast(null); openPicker(all, def || toast.id, true); }} />}
      {pick && <PlayInPicker rooms={pick.rooms} preselect={pick.preselect} action={action}
        onCancel={() => { const t = pick.fromToast; setPick(null); if (!t) onCancel(); }}
        onPick={r => {
          const room = pick.byId.get(r.id); setPick(null);
          if (!room) return;
          setCloseWanted(false);
          const was = changedFrom.current;
          setPendingStop(pick.fromToast && current && current !== room.id && was ? { from: current, to: room.id, fromRoom: was } : null);
          setCurrent(room.id); select(room); setToast(r);
        }} />}
    </>, document.body,
  );
  return { start, closeAfter, failed, ui: ui || null };
}

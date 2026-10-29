/** v224 Settings > Rooms (Taylor's Option A, room first; docs: /workspace/design/rooms/option-A-*).
 * v299: Add Room wizard — Name → Network debugging → IP Pair → smart home (HA | Harmony).
 * Pair is part of Add: empty room on create; devices only after Pair success (v272). Cancel/Back
 * before Pair deletes the empty room; after Pair, Back/close never DELETE.
 * Settings premium pass (Designer rooms-v2): centered "Rooms" with bubbly tiles (icon bubble +
 * pencil → icon picker, TV chip, dashed Add room tile); the wizard is one centered 620px card
 * (Back · progress dots · close, hero, title, content, one big primary); the name step is an empty
 * field only. The room page is a centered column: TV hero, Name + Default room, Power chips,
 * pickers for the system (no raw entity/activity text), Advanced (Port, Navigation, Wake-on-LAN). */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { FONT } from '@/components/start/StartUI';
import { assetUrl } from '@/urlHelpers';
import { Modal, glassBtn, primaryBtn, field, tvEditCss } from '@/components/library/TvEdit';
import {
  badge, createRoom, detectMac, loadHaConnector, loadHaEntities, loadHarmonyOptions, loadPlayers, loadRooms, pairFailLabel,
  pairRoom, removeRoom, saveHaConnector, updateRoom, WAKE_LABEL,
  type FoundPlayer, type HaEntity, type Room, type RoomPatch, type RoomsView, type Wake,
} from './roomsApi';
import { Badge, StatusDot } from './PlayIn';
import { Bubble, Glyph, IconPicker, roomIconOf } from './roomIcons';

const W50 = 'rgba(255,255,255,0.5)';
const W70 = 'rgba(255,255,255,0.7)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RED = '#ef4444';
const card: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };
const row = (i: number): CSSProperties => ({ minHeight: 56, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px',
  borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', fontFamily: FONT, color: W95, boxSizing: 'border-box' });
const label: CSSProperties = { margin: '0 4px 8px', fontSize: 12, fontWeight: 600, color: W50 };
/** Power chips (premium pass): TV = ADB, Home Assistant, Harmony, Off. Wake-on-LAN is the Advanced toggle. */
type Power = 'adb' | 'homeassistant' | 'harmony' | 'none';
const POWERS: { id: Power; label: string }[] = [
  { id: 'adb', label: 'TV' }, { id: 'homeassistant', label: 'Home Assistant' }, { id: 'harmony', label: 'Harmony' }, { id: 'none', label: 'Off' },
];
const IP_RE = /^(\d{1,3}\.){3}\d{1,3}$/;

type View =
  | { k: 'list' }
  | { k: 'wiz-name' }
  | { k: 'wiz-adb'; id: string }
  | { k: 'wiz-player'; id: string }
  | { k: 'wiz-smarthome'; id: string }
  | { k: 'wiz-power'; id: string }
  | { k: 'wiz-ha'; id: string }
  | { k: 'wiz-harmony'; id: string }
  | { k: 'edit'; id: string }
  | { k: 'add'; id: string };
const STEP: Partial<Record<View['k'], number>> = {
  'wiz-name': 0, 'wiz-adb': 1, 'wiz-player': 2, 'wiz-smarthome': 3, 'wiz-power': 4, 'wiz-ha': 4, 'wiz-harmony': 4,
};

export function playerLine(r: Room, long = false): string {
  const p = r.player;
  if (!p) return 'No player';
  const state = p.online ? 'Online' : 'Offline';
  if (long) return `${state} · ${p.name}`;
  const w = WAKE_LABEL[p.wake];
  return p.online && w ? `${state} · ${w}` : state;
}

export function RoomsSettings({ wide = true, onExit }: { wide?: boolean; onExit?: () => void }) {
  const [data, setData] = useState<RoomsView | null>(null);
  const [failed, setFailed] = useState(false);
  const [view, setView] = useState<View>({ k: 'list' });
  const [error, setError] = useState('');
  const [iconFor, setIconFor] = useState<string | null>(null);
  const [editSave, setEditSave] = useState<{ dirty: boolean; save: () => void }>({ dirty: false, save: () => {} });
  const onSaveState = useCallback((dirty: boolean, save: () => void) => setEditSave({ dirty, save }), []);
  const load = useCallback(() => void loadRooms().then(r => { if (r.ok) { setData(r.data); setFailed(false); } else setFailed(true); }), []);
  useEffect(load, [load]);
  const take = (r: Awaited<ReturnType<typeof loadRooms>>, then?: () => void) => {
    if (r.ok) { setData(r.data); setError(''); then?.(); } else setError(Object.values(r.errors)[0] || "Couldn't save");
    return r.ok;
  };
  const rooms = Array.isArray(data?.rooms) ? data!.rooms : [];
  const room = (id: string) => rooms.find(r => r.id === id);
  const toList = () => { setView({ k: 'list' }); setError(''); };

  /** Cancel/Back before Pair OK: delete the empty room shell (PM LOCKED).
   * Never DELETE once a player is present (Pair success) — defense in depth. */
  const abandonEmpty = (id: string) => {
    const r = room(id);
    if (r?.player) { toList(); return; }
    void removeRoom(id).then(res => {
      if (res.ok) setData(res.data);
      toList();
    });
  };

  if (failed) return <div role="alert" style={{ ...card, height: 120, display: 'flex', alignItems: 'center', justifyContent: 'center', color: W50, fontFamily: FONT }}>{"Couldn't load"}</div>;
  if (!data) return <div aria-busy="true" style={{ ...card, height: 120 }} />;

  let flow: { title: string; body: ReactNode; action?: ReactNode; back: () => void; step?: number } | null = null;

  if (view.k === 'wiz-name') {
    flow = {
      title: 'Add room', back: toList, body: (
        <WizNameStep error={error} onNext={name => {
          void createRoom(name).then(r => {
            if (!r.ok) { setError(Object.values(r.errors)[0] || "Couldn't save"); return; }
            setData(r.data); setError('');
            const created = r.data.rooms.find(x => x.name === name) || r.data.rooms[r.data.rooms.length - 1];
            if (created) setView({ k: 'wiz-adb', id: created.id });
            else toList();
          });
        }} />),
    };
  } else if (view.k === 'wiz-adb' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Network debugging', back: () => {
        const r = room(id);
        if (r && !r.player) abandonEmpty(id);
        else toList();
      }, body: (
        <WizAdbHowto onContinue={() => { setError(''); setView({ k: 'wiz-player', id }); }} />),
    };
  } else if (view.k === 'wiz-player' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Pair with your TV', back: () => { setError(''); setView({ k: 'wiz-adb', id }); }, body: (
        <WizPlayerStep roomId={id} error={error} playerName={room(id)?.player?.name || ''}
          onPairing={() => setError('')}
          onFail={hint => setError(pairFailLabel(hint))}
          onPaired={async () => { setError(''); await load(); }}
          onContinue={() => { setError(''); setView({ k: 'wiz-smarthome', id }); }} />),
    };
  } else if (view.k === 'wiz-smarthome' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Smart home features?', back: () => setView({ k: 'wiz-player', id }), body: (
        <WizSmartHome
          onNo={() => void updateRoom(id, { wake: 'adb' }).then(x => take(x, toList))}
          onYes={() => setView({ k: 'wiz-power', id })} />),
    };
  } else if (view.k === 'wiz-power' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Power-on', back: () => setView({ k: 'wiz-smarthome', id }), body: (
        <WizPowerChooser
          onHa={() => setView({ k: 'wiz-ha', id })}
          onHarmony={() => setView({ k: 'wiz-harmony', id })} />),
    };
  } else if (view.k === 'wiz-ha' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Home Assistant', back: () => setView({ k: 'wiz-power', id }), body: (
        <WizHaStep error={error}
          onError={setError}
          onDone={async entity => {
            setError('');
            const x = await updateRoom(id, { wake: 'homeassistant', ha_power_entity: entity });
            take(x, toList);
          }} />),
    };
  } else if (view.k === 'wiz-harmony' && room(view.id)) {
    const id = view.id;
    const saveHub = hubSaver(id);
    flow = {
      title: 'Harmony Hub', back: () => setView({ k: 'wiz-power', id }), body: (
        <WizHarmonyStep error={error} saveHub={saveHub}
          onDone={patch => void updateRoom(id, { wake: 'harmony', ...patch }).then(x => take(x, toList))} />),
    };
  } else if (view.k === 'add' && room(view.id)) {
    const r = room(view.id)!;
    flow = { title: r.name, back: () => setView({ k: 'edit', id: r.id }), body: (
      <PickStep cta="Add player" current={r.player?.machine_id || r.player?.host} error={error}
        onPick={async p => {
          setError('');
          const x = await pairRoom(r.id, p);
          if (!x.ok) { setError(pairFailLabel(x.hint)); return; }
          await load(); setView({ k: 'edit', id: r.id });
        }} />) };
  }
  if (flow && STEP[view.k] !== undefined) flow.step = STEP[view.k];

  const editing = view.k === 'edit' ? room(view.id) : undefined;
  const detail = editing ? (
    <EditRoom key={editing.id} room={editing} error={error} wide={wide} onSaveState={onSaveState} onPaired={load}
      onBack={toList} saveDirty={editSave.dirty} onSaveClick={editSave.save}
      onChangePlayer={() => setView({ k: 'add', id: editing.id })}
      onSave={patch => void updateRoom(editing.id, patch).then(x => take(x, toList))}
      onRemove={() => void removeRoom(editing.id).then(x => take(x, toList))} />
  ) : null;

  const startWizard = () => { setError(''); setView({ k: 'wiz-name' }); };
  const closeFlow = () => {
    if (view.k === 'wiz-adb' || view.k === 'wiz-player') {
      const id = 'id' in view ? view.id : '';
      const r = id ? room(id) : undefined;
      if (r && !r.player) { abandonEmpty(id); return; }
    }
    toList();
  };
  const iconRoom = iconFor ? room(iconFor) : undefined;
  const picker = iconRoom ? (
    <IconPicker name={iconRoom.name} value={roomIconOf(iconRoom.icon)} onCancel={() => setIconFor(null)}
      onDone={icon => { setIconFor(null); if (icon !== roomIconOf(iconRoom.icon)) void updateRoom(iconRoom.id, { icon }).then(x => take(x)); }} />
  ) : null;

  if (!wide) {
    if (detail) {
      return (
        <div data-testid="rooms-settings" data-view="edit" style={{ fontFamily: FONT }}>
          <style>{tvEditCss}{roomsCss}</style>
          <PageHeader title={editing!.name} onBack={toList} action={<HeaderSave dirty={editSave.dirty} onSave={editSave.save} />} />
          <div style={{ padding: '8px 16px 24px' }}>{detail}</div>
        </div>
      );
    }
    if (flow) {
      return (
        <div data-testid="rooms-settings" data-view={view.k} style={{ fontFamily: FONT }}>
          <style>{tvEditCss}{roomsCss}</style>
          <PageHeader title="" onBack={flow.back} action={flow.action} />
          <div style={{ padding: '8px 16px 24px' }}>{flow.body}</div>
        </div>
      );
    }
    return (
      <div data-testid="rooms-settings" data-view="list" style={{ fontFamily: FONT }}>
        <style>{tvEditCss}{roomsCss}</style>
        <PageHeader title="Rooms" onBack={onExit} action={rooms.length ? <IconBtn label="Add room" onClick={startWizard}>+</IconBtn> : undefined} />
        <div style={{ padding: '8px 16px 0' }}>
          {rooms.length === 0 ? <Empty onAdd={startWizard} /> : (
            <div style={card} data-testid="rooms-list">
              {rooms.map((r, i) => (
                <button key={r.id} type="button" data-testid="room-row" className="lp-focus tve-row" onClick={() => setView({ k: 'edit', id: r.id })}
                  style={{ ...row(i), width: '100%', background: 'none', border: 'none', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', cursor: 'pointer', textAlign: 'left' }}>
                  <Bubble size={36}><Glyph id={roomIconOf(r.icon)} size={18} /></Bubble>
                  <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                    <span style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, fontWeight: 600 }}>{r.name}{r.default && <DefaultPill />}</span>
                    <span style={{ fontSize: 12, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}><StatusDot online={!!r.player?.online} />{playerLine(r, true)}</span>
                  </span>
                  <span aria-hidden="true" style={{ color: W50, fontSize: 18 }}>›</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  if (detail) {
    return (
      <div data-testid="rooms-settings" data-view="edit" style={{ fontFamily: FONT, display: 'flex', justifyContent: 'center' }}>
        <style>{tvEditCss}{roomsCss}</style>
        {detail}
      </div>
    );
  }

  return (
    <div data-testid="rooms-settings" data-view="list" style={{ fontFamily: FONT, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
      <style>{tvEditCss}{roomsCss}</style>
      <h2 style={{ margin: '0 0 40px', fontSize: 30, fontWeight: 650, letterSpacing: -0.4, color: W95, textAlign: 'center' }}>Rooms</h2>
      {error && view.k === 'list' && <div role="alert" style={{ color: '#fca5a5', fontSize: 13, marginBottom: 12 }}>{error}</div>}
      <div data-testid="rooms-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, 236px)', gap: 20, justifyContent: 'center', width: '100%', maxWidth: 748 }}>
        {rooms.map(r => (
          <RoomTile key={r.id} room={r} onOpen={() => { setError(''); setView({ k: 'edit', id: r.id }); }} onIcon={() => setIconFor(r.id)} />
        ))}
        <button type="button" data-testid="rooms-add" className="lp-focus rooms-add" onClick={startWizard} style={addTile}>
          <Bubble size={46} style={{ background: 'rgba(59,130,246,0.16)' }}>
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#adc7ff" strokeWidth="1.8" strokeLinecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg>
          </Bubble>
          <span style={{ fontSize: 16, fontWeight: 600, color: '#adc7ff' }}>Add room</span>
        </button>
      </div>
      {flow && view.k === 'add' && <Modal title={flow.title} testid="room-flow" onClose={() => setView({ k: 'edit', id: view.id })} footer={null}>{flow.body}</Modal>}
      {flow && view.k !== 'add' && <WizardCard step={flow.step ?? 0} onBack={flow.step ? flow.back : undefined} onClose={closeFlow} title={flow.title}>{flow.body}</WizardCard>}
      {picker}
    </div>
  );
}

const roomsCss = `
.room-tile:hover{border-color:rgba(173,199,255,.22)}
.rooms-add:hover{border-color:rgba(173,199,255,.45)}
.room-pick:focus-visible,.room-tile-open:focus-visible{outline:2px solid #60a5fa;outline-offset:2px}
@keyframes room-pulse{0%{transform:scale(.9);opacity:.8}100%{transform:scale(1.35);opacity:0}}
@keyframes room-dot{0%,80%,100%{opacity:.25}40%{opacity:1}}
`;

const addTile: CSSProperties = {
  height: 216, borderRadius: 26, background: 'none', border: '2px dashed rgba(173,199,255,0.28)', display: 'flex', flexDirection: 'column',
  alignItems: 'center', justifyContent: 'center', gap: 14, cursor: 'pointer', fontFamily: FONT, boxSizing: 'border-box',
};

function RoomTile({ room: r, onOpen, onIcon }: { room: Room; onOpen: () => void; onIcon: () => void }) {
  const icon = roomIconOf(r.icon);
  return (
    <div data-testid="room-card" data-icon={icon} className="room-tile"
      style={{ position: 'relative', height: 216, borderRadius: 26, border: '1px solid rgba(255,255,255,0.09)', boxSizing: 'border-box',
        background: 'linear-gradient(160deg, rgba(255,255,255,0.10), rgba(255,255,255,0.04))', boxShadow: '0 10px 30px rgba(0,0,0,0.25)' }}>
      <button type="button" aria-label={r.name} data-testid="room-open" className="room-tile-open" onClick={onOpen}
        style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 'none', background: 'none', borderRadius: 26, cursor: 'pointer',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14, fontFamily: FONT, color: W95, padding: 12, boxSizing: 'border-box' }}>
        <Bubble size={96}><Glyph id={icon} size={46} /></Bubble>
        <span style={{ fontSize: 18, fontWeight: 600, maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</span>
        <span data-testid="room-tv-chip" style={{ height: 28, padding: '0 12px', borderRadius: 999, background: 'rgba(255,255,255,0.08)', fontSize: 13, display: 'inline-flex',
          alignItems: 'center', gap: 7, color: r.player ? 'rgba(255,255,255,0.75)' : W50, maxWidth: '100%' }}>
          {r.player && <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 4, background: r.player.online ? '#22c55e' : 'rgba(255,255,255,0.35)', flex: 'none' }} />}
          <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.player ? r.player.name : 'No TV'}</span>
        </span>
      </button>
      <button type="button" aria-label={`Change icon for ${r.name}`} data-testid="room-icon-edit" onClick={onIcon} className="room-pick"
        style={{ position: 'absolute', top: 84, left: 'calc(50% + 14px)', width: 30, height: 30, borderRadius: 15, padding: 0,
          border: '3px solid #2a2450', background: ACCENT, display: 'grid', placeItems: 'center', cursor: 'pointer', boxSizing: 'content-box' }}>
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M4 20h4L19 9l-4-4L4 16z" /><path d="M13.5 6.5l4 4" />
        </svg>
      </button>
    </div>
  );
}

/** Centered 620px wizard card: round Back (hidden on step 1) · pill-dot progress · round close. */
function WizardCard({ step, onBack, onClose, title, children }: { step: number; onBack?: () => void; onClose: () => void; title: string; children: ReactNode }) {
  useEffect(() => {
    const k = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    document.addEventListener('keydown', k, true);
    return () => document.removeEventListener('keydown', k, true);
  }, [onClose]);
  return createPortal(
    <div className="luma-fullscreen luma-bleed" data-testid="room-flow-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onClose(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1000, background: 'rgba(10,8,24,.62)', backdropFilter: 'blur(10px)', WebkitBackdropFilter: 'blur(10px)',
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: FONT }}>
      <style>{tvEditCss}{roomsCss}</style>
      <div role="dialog" aria-modal="true" aria-label={title} data-testid="room-flow" data-step={step + 1}
        style={{ width: 620, maxWidth: '100%', maxHeight: 'calc(100vh - 32px)', overflowY: 'auto', boxSizing: 'border-box', padding: '28px 40px 36px',
          borderRadius: 28, background: 'linear-gradient(180deg, rgba(34,28,70,.98), rgba(24,19,50,.98))', border: '1px solid rgba(255,255,255,0.1)',
          boxShadow: '0 40px 100px rgba(0,0,0,.6)', color: W95, display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center' }}>
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 22 }}>
          {onBack
            ? <button type="button" aria-label="Back" data-testid="wiz-back" onClick={onBack} style={circleBtn}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
              </button>
            : <span style={{ width: 36 }} />}
          <span aria-hidden="true" data-testid="wiz-dots" style={{ display: 'flex', gap: 8 }}>
            {[0, 1, 2, 3, 4].map(i => (
              <i key={i} data-on={i === step ? 'true' : undefined}
                style={{ width: i === step ? 24 : 8, height: 8, borderRadius: 4, background: i === step ? ACCENT : i < step ? 'rgba(59,130,246,0.6)' : 'rgba(255,255,255,0.18)', transition: 'width .2s' }} />
            ))}
          </span>
          <button type="button" aria-label="Close" data-testid="room-flow-close" onClick={onClose} style={circleBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </div>
        <div style={{ width: '100%' }}>{children}</div>
      </div>
    </div>,
    document.body,
  );
}

const circleBtn: CSSProperties = {
  width: 36, height: 36, borderRadius: 18, border: 'none', background: 'rgba(255,255,255,0.08)', color: 'rgba(255,255,255,0.8)',
  display: 'grid', placeItems: 'center', cursor: 'pointer', padding: 0, flex: 'none',
};
/** One big centered 52px primary pill (min 240px), as in rooms-v2. */
const bigBtn = (on: boolean): CSSProperties => ({
  height: 52, minWidth: 240, padding: '0 32px', borderRadius: 999, border: 'none', background: ACCENT, color: '#fff',
  fontSize: 16, fontWeight: 600, fontFamily: FONT, cursor: on ? 'pointer' : 'default', marginTop: 32,
  opacity: on ? 1 : 0.4, boxShadow: on ? '0 8px 24px rgba(59,130,246,.35)' : 'none',
});
const stepTitle: CSSProperties = { margin: '20px 0 24px', fontSize: 26, fontWeight: 650, letterSpacing: -0.3, color: W95, textAlign: 'center' };
const bigInput: CSSProperties = {
  width: '100%', height: 60, boxSizing: 'border-box', borderRadius: 20, background: 'rgba(255,255,255,0.07)', border: '1.5px solid rgba(255,255,255,0.12)',
  color: W95, fontSize: 22, fontWeight: 500, fontFamily: FONT, textAlign: 'center', outline: 'none', padding: '0 20px',
};
const Center = ({ children, testid }: { children: ReactNode; testid: string }) => (
  <div data-testid={testid} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', textAlign: 'center', width: '100%' }}>{children}</div>
);
const ErrorChip = ({ text, testid }: { text: string; testid?: string }) => (
  <span role="alert" data-testid={testid} style={{ marginTop: 16, height: 30, padding: '0 14px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 7,
    background: 'rgba(239,68,68,0.14)', color: '#f87171', fontSize: 13, fontWeight: 600 }}>{text}</span>
);
const Chip = ({ text, tone, testid }: { text: string; tone: 'ok' | 'wait' | 'muted'; testid?: string }) => (
  <span data-testid={testid} style={{ height: 30, padding: '0 14px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 7, fontSize: 13, fontWeight: 600,
    background: tone === 'ok' ? 'rgba(34,197,94,0.14)' : tone === 'wait' ? 'rgba(59,130,246,0.16)' : 'rgba(255,255,255,0.08)',
    color: tone === 'ok' ? '#86efac' : tone === 'wait' ? '#adc7ff' : W70 }}>
    {tone !== 'muted' && <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: 4, background: tone === 'ok' ? '#22c55e' : '#adc7ff' }} />}
    {text}
  </span>
);

function PageHeader({ title, onBack, action }: { title: string; onBack?: () => void; action?: ReactNode }) {
  return (
    <header style={{ height: 56, display: 'flex', alignItems: 'center', gap: 8, padding: '0 12px 0 8px' }}>
      {onBack && <IconBtn label="Back" onClick={onBack}>‹</IconBtn>}
      <h1 style={{ flex: 1, margin: 0, fontSize: 18, fontWeight: 700, color: W95, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{title}</h1>
      {action}
    </header>
  );
}

function HeaderSave({ dirty, onSave }: { dirty: boolean; onSave: () => void }) {
  return (
    <button type="button" data-testid="room-save" disabled={!dirty} onClick={onSave} className="lp-focus"
      style={{ minWidth: 44, height: 44, padding: '0 8px', background: 'none', border: 'none', borderRadius: 10, color: ACCENT,
        fontSize: 16, fontWeight: 600, fontFamily: FONT, opacity: dirty ? 1 : 0.4, cursor: dirty ? 'pointer' : 'default', flex: 'none' }}>Save</button>
  );
}

function IconBtn({ label: l, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return <button type="button" aria-label={l} onClick={onClick} className="lp-focus tve-glass"
    style={{ ...glassBtn, width: 36, height: 36, padding: 0, fontSize: 20, lineHeight: 1 }}>{children}</button>;
}

const DefaultPill = () => <span data-testid="room-default" style={{ height: 20, padding: '0 8px', borderRadius: 6, display: 'inline-flex', alignItems: 'center',
  background: 'rgba(59,130,246,0.2)', color: '#bfdbfe', fontSize: 11, fontWeight: 600 }}>Default</span>;

function Empty({ onAdd }: { onAdd: () => void }) {
  return (
    <div data-testid="rooms-empty" style={{ minHeight: 320, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16 }}>
      <Bubble size={72}><Glyph id="home" size={34} /></Bubble>
      <span style={{ fontSize: 16, fontWeight: 600, color: W95 }}>No rooms yet</span>
      <button type="button" data-testid="rooms-empty-add" className="lp-focus tve-primary" onClick={onAdd} style={primaryBtn(true)}>Add room</button>
    </div>
  );
}

function ConfirmRemove({ name, onCancel, onRemove }: { name: string; onCancel: () => void; onRemove: () => void }) {
  return (
    <div className="luma-fullscreen luma-bleed" data-testid="room-remove-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onCancel(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(10,8,24,.6)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: FONT }}>
      <div role="alertdialog" aria-label={`Remove ${name}?`} data-testid="room-remove" style={{ width: 360, maxWidth: '100%', boxSizing: 'border-box', padding: 20, borderRadius: 20, background: 'rgba(21,16,42,0.96)', border: '1px solid rgba(255,255,255,0.08)', textAlign: 'center' }}>
        <h2 style={{ margin: '0 0 18px', fontSize: 17, fontWeight: 600, color: W95 }}>Remove {name}?</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" onClick={onCancel} className="lp-focus tve-glass" style={{ ...glassBtn, flex: 1, height: 44, borderRadius: 999 }}>Cancel</button>
          <button type="button" data-testid="room-remove-go" onClick={onRemove} className="lp-focus" style={{ ...primaryBtn(true), flex: 1, height: 44, borderRadius: 999, background: RED }}>Remove</button>
        </div>
      </div>
    </div>
  );
}

/** Wizard step 1 — one empty field only: no prefill, placeholder, suggestions or icon picker.
 * Continue stays disabled until a name is typed. New rooms get the home icon (change it with the pencil). */
export function WizNameStep({ error, onNext }: { error?: string; onNext: (name: string) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const ok = !busy && !!name.trim() && name.trim().length <= 40;
  const go = () => { setBusy(true); onNext(name.trim()); };
  useEffect(() => { if (error) setBusy(false); }, [error]);
  return (
    <Center testid="room-name-step">
      <h3 style={stepTitle}>Name this room</h3>
      <input aria-label="Name" data-testid="room-name" className="tve-in" value={name} maxLength={40} autoFocus
        autoComplete="off" spellCheck={false}
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && ok) go(); }} style={bigInput} />
      {error && <ErrorChip text={error} />}
      <button type="button" data-testid="room-next" disabled={!ok} onClick={go} className="lp-focus tve-primary" style={bigBtn(ok)}>Continue</button>
    </Center>
  );
}

/** Wizard step 2 — the only screen with instructional copy (AmEng). */
function WizAdbHowto({ onContinue }: { onContinue: () => void }) {
  const B = ({ children }: { children: ReactNode }) => <b style={{ fontWeight: 650, color: '#fff' }}>{children}</b>;
  const steps: ReactNode[] = [
    <>Open <B>Settings</B> on the TV</>,
    <>Go to <B>Device Preferences</B> › <B>About</B></>,
    <>Select <B>Build</B> 7 times</>,
    <>Open <B>Developer options</B></>,
    <>Turn on <B>Network debugging</B></>,
  ];
  return (
    <Center testid="wiz-adb-howto">
      <Bubble size={96}>
        <svg data-testid="wiz-adb-hero" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#adc7ff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M8.5 8L4.5 12l4 4M15.5 8l4 4-4 4M13.2 6.5l-2.4 11" />
        </svg>
      </Bubble>
      <h3 style={stepTitle}>Turn on Network debugging</h3>
      <ol data-testid="wiz-adb-steps" style={{ listStyle: 'none', margin: 0, padding: '0 16px', width: '100%', boxSizing: 'border-box', textAlign: 'left',
        borderRadius: 20, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)' }}>
        {steps.map((s, i) => (
          <li key={i} style={{ display: 'flex', alignItems: 'center', gap: 14, minHeight: 54, fontSize: 15, color: 'rgba(255,255,255,0.85)', fontFamily: FONT,
            borderTop: i ? '1px solid rgba(255,255,255,0.08)' : 'none' }}>
            <span aria-hidden="true" style={{ width: 28, height: 28, borderRadius: 14, background: 'rgba(59,130,246,0.2)', color: '#adc7ff', fontSize: 14, fontWeight: 700,
              display: 'grid', placeItems: 'center', flex: 'none' }}>{i + 1}</span>
            <span>{s}</span>
          </li>
        ))}
      </ol>
      <button type="button" data-testid="wiz-adb-continue" onClick={onContinue} className="lp-focus tve-primary" style={bigBtn(true)}>Continue</button>
    </Center>
  );
}

function TvArt({ state }: { state: 'idle' | 'wait' | 'ok' }) {
  return (
    <div aria-hidden="true" style={{ position: 'relative', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
      {state === 'wait' && [0, 1].map(i => (
        <span key={i} style={{ position: 'absolute', inset: -24, borderRadius: 24, border: '2px solid rgba(173,199,255,.25)', animation: `room-pulse 1.8s ${i * 0.9}s infinite ease-out` }} />
      ))}
      <div style={{ width: 156, height: 92, borderRadius: 12, border: '2px solid rgba(255,255,255,0.22)', background: state === 'wait'
        ? 'radial-gradient(circle at 50% 50%, rgba(59,130,246,.45), rgba(20,16,44,1) 75%)' : 'linear-gradient(180deg, #0f0c24, #14112c)',
        boxShadow: state === 'wait' ? '0 0 40px rgba(59,130,246,.35)' : 'inset 0 0 30px rgba(0,0,0,.6)', display: 'grid', placeItems: 'center' }}>
        {state === 'ok' && (
          <span style={{ width: 44, height: 44, borderRadius: 22, background: '#22c55e', display: 'grid', placeItems: 'center' }}>
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
          </span>
        )}
        {state === 'wait' && (
          <span style={{ display: 'flex', gap: 6 }}>
            {[0, 1, 2].map(i => <span key={i} style={{ width: 8, height: 8, borderRadius: 4, background: '#fff', animation: `room-dot 1.2s ${i * 0.2}s infinite` }} />)}
          </span>
        )}
      </div>
      <span style={{ width: 44, height: 4, borderRadius: 2, background: 'rgba(255,255,255,0.22)' }} />
    </div>
  );
}

/** Wizard step 3 — IP-only Pair; the primary button is Pair. Devices persist only on Pair success. */
function WizPlayerStep({ roomId, error, playerName, onPairing, onFail, onPaired, onContinue }: {
  roomId: string; error: string; playerName: string;
  onPairing: () => void; onFail: (hint?: string) => void; onPaired: () => Promise<void>; onContinue: () => void;
}) {
  const [ip, setIp] = useState('');
  const [busy, setBusy] = useState(false);
  const [paired, setPaired] = useState(false);
  const ipOk = IP_RE.test(ip.trim());
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const pair = () => {
    setBusy(true); setPaired(false); onPairing();
    void pairRoom(roomId, { host: ip.trim() }).then(async x => {
      if (!x.ok) { setBusy(false); onFail(x.hint); return; }
      setBusy(false); setPaired(true);
      await onPaired();
    });
  };
  return (
    <Center testid="wiz-player-step">
      <TvArt state={paired ? 'ok' : busy ? 'wait' : 'idle'} />
      <h3 style={stepTitle}>{paired ? (playerName || ip.trim()) : busy ? 'Allow on your TV' : 'Pair with your TV'}</h3>
      {paired ? <Chip text="Paired" tone="ok" testid="wiz-pair-ok" />
        : busy ? <Chip text="Waiting for TV" tone="wait" testid="wiz-pair-busy" />
        : <input aria-label="IP address" data-testid="room-ip" className="tve-in" value={ip} inputMode="decimal" autoFocus
            autoComplete="off" spellCheck={false}
            onChange={e => setIp(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && ipOk) pair(); }}
            style={{ ...bigInput, fontVariantNumeric: 'tabular-nums', letterSpacing: 0.5 }} />}
      {error && !busy && !paired && <ErrorChip text={error} testid="wiz-pair-fail" />}
      {paired
        ? <button type="button" data-testid="wiz-player-continue" onClick={onContinue} className="lp-focus tve-primary" style={bigBtn(true)}>Continue</button>
        : <button type="button" data-testid="room-pair" disabled={busy || !ipOk} onClick={pair} className="lp-focus tve-primary" style={bigBtn(!busy && ipOk)}>
            {busy ? 'Pairing…' : 'Pair'}
          </button>}
    </Center>
  );
}

const Logo = ({ src, bg, size = 44, pad = 9 }: { src: string; bg: string; size?: number; pad?: number }) => (
  <span aria-hidden="true" style={{ width: size, height: size, borderRadius: Math.round(size * 0.26), background: bg, padding: pad, boxSizing: 'border-box',
    display: 'inline-flex', flex: 'none', boxShadow: 'inset 0 0 0 1px rgba(255,255,255,0.08)' }}>
    <img src={assetUrl(`/static/images/connectors/${src}`)} alt="" draggable={false} style={{ width: '100%', height: '100%', objectFit: 'contain' }} />
  </span>
);
const HaLogo = (p: { size?: number }) => <Logo src="homeassistant.svg" bg="#18BCF2" size={p.size} pad={Math.round((p.size ?? 44) * 0.2)} />;
const HarmonyLogo = (p: { size?: number }) => <Logo src="harmony.svg" bg="#00B8FC" size={p.size} pad={Math.round((p.size ?? 44) * 0.2)} />;

function BigCard({ testid, on, onClick, label: l, children }: { testid: string; on: boolean; onClick: () => void; label: string; children: ReactNode }) {
  return (
    <button type="button" role="radio" aria-checked={on} aria-label={l} data-testid={testid} onClick={onClick} className="lp-focus"
      style={{ flex: 1, height: 180, borderRadius: 24, cursor: 'pointer', fontFamily: FONT, color: W95, fontSize: 18, fontWeight: 600,
        background: on ? 'rgba(59,130,246,0.13)' : 'rgba(255,255,255,0.06)', border: `2px solid ${on ? ACCENT : 'rgba(255,255,255,0.08)'}`,
        display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 14 }}>
      {children}
      <span>{l}</span>
    </button>
  );
}

function WizSmartHome({ onNo, onYes }: { onNo: () => void; onYes: () => void }) {
  const [sel, setSel] = useState<'no' | 'yes' | ''>('');
  const [busy, setBusy] = useState(false);
  return (
    <Center testid="wiz-smarthome">
      <h3 style={{ ...stepTitle, marginTop: 4 }}>Use smart home for power?</h3>
      <div role="radiogroup" aria-label="Smart home" style={{ display: 'flex', gap: 16, width: '100%' }}>
        <BigCard testid="wiz-smarthome-no" label="No" on={sel === 'no'} onClick={() => setSel('no')}>
          <Bubble size={64}><Glyph id="tv" size={30} /></Bubble>
        </BigCard>
        <BigCard testid="wiz-smarthome-yes" label="Yes" on={sel === 'yes'} onClick={() => setSel('yes')}>
          <span style={{ display: 'flex', gap: 10 }}><HaLogo /><HarmonyLogo /></span>
        </BigCard>
      </div>
      <button type="button" data-testid="wiz-smarthome-continue" disabled={!sel || busy} className="lp-focus tve-primary" style={bigBtn(!!sel && !busy)}
        onClick={() => { if (sel === 'no') { setBusy(true); onNo(); } else if (sel === 'yes') onYes(); }}>Continue</button>
    </Center>
  );
}

function WizPowerChooser({ onHa, onHarmony }: { onHa: () => void; onHarmony: () => void }) {
  const [sel, setSel] = useState<'ha' | 'harmony' | ''>('');
  return (
    <Center testid="wiz-power">
      <h3 style={{ ...stepTitle, marginTop: 4 }}>Choose your system</h3>
      <div role="radiogroup" aria-label="System" style={{ display: 'flex', gap: 16, width: '100%' }}>
        <BigCard testid="wiz-power-ha" label="Home Assistant" on={sel === 'ha'} onClick={() => setSel('ha')}><HaLogo size={64} /></BigCard>
        <BigCard testid="wiz-power-harmony" label="Harmony" on={sel === 'harmony'} onClick={() => setSel('harmony')}><HarmonyLogo size={64} /></BigCard>
      </div>
      <button type="button" data-testid="wiz-power-continue" disabled={!sel} className="lp-focus tve-primary" style={bigBtn(!!sel)}
        onClick={() => { if (sel === 'ha') onHa(); else if (sel === 'harmony') onHarmony(); }}>Continue</button>
    </Center>
  );
}

/** A grouped-list row: label left, control right (60px rows, inset dividers). */
function SRow({ label: l, children, first }: { label: string; children: ReactNode; first?: boolean }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', minHeight: 60, padding: '0 20px', gap: 12, position: 'relative', fontSize: 16, color: W95,
      borderTop: first ? 'none' : '1px solid rgba(255,255,255,0.07)', textAlign: 'left' }}>
      <span style={{ flex: 1, minWidth: 0 }}>{l}</span>
      {children}
    </div>
  );
}
const group: CSSProperties = { width: '100%', borderRadius: 22, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden', boxSizing: 'border-box' };
const inlineInput: CSSProperties = {
  width: 190, height: 36, padding: '0 4px', boxSizing: 'border-box', background: 'transparent', border: '1px solid transparent', borderRadius: 8,
  color: W70, fontSize: 16, fontFamily: FONT, textAlign: 'right', outline: 'none',
};

/** A picker (native select, styled): right-aligned value + chevron. A saved value missing from the
 * options stays listed so nothing is lost. */
export function Picker({ label: l, testid, value, options, onChange, empty = '' }: {
  label: string; testid: string; value: string; options: { value: string; label: string }[]; onChange: (v: string) => void; empty?: string;
}) {
  const opts = value && !options.some(o => o.value === value) ? [{ value, label: value }, ...options] : options;
  return (
    <span style={{ position: 'relative', display: 'inline-flex', alignItems: 'center', maxWidth: 260 }}>
      <select aria-label={l} data-testid={testid} value={value} onChange={e => onChange(e.target.value)} className="lp-focus"
        style={{ appearance: 'none', WebkitAppearance: 'none', background: 'transparent', border: 'none', color: W70, fontSize: 16, fontFamily: FONT,
          textAlign: 'right', textAlignLast: 'right', padding: '8px 26px 8px 8px', cursor: 'pointer', maxWidth: 260, borderRadius: 8 }}>
        {(!value || empty) && <option value="">{empty}</option>}
        {opts.map(o => <option key={o.value} value={o.value} style={{ background: '#1d1840', color: '#fff' }}>{o.label}</option>)}
      </select>
      <span aria-hidden="true" style={{ position: 'absolute', right: 6, top: '50%', width: 7, height: 7, borderRight: '2px solid rgba(255,255,255,0.4)',
        borderBottom: '2px solid rgba(255,255,255,0.4)', transform: 'translateY(-70%) rotate(45deg)', pointerEvents: 'none' }} />
    </span>
  );
}

/** Wizard 5a — one TV entity picker (HA). Asks for the URL and access token first when the
 * Home Assistant connector isn't set up yet. */
function WizHaStep({ error, onError, onDone }: { error: string; onError: (e: string) => void; onDone: (entity: string) => void }) {
  const [ready, setReady] = useState(false);
  const [needConnectors, setNeedConnectors] = useState(false);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [entities, setEntities] = useState<HaEntity[] | null>(null);
  const [entity, setEntity] = useState('');
  const [busy, setBusy] = useState(false);
  const fetchEntities = useCallback(() => { setEntities(null); void loadHaEntities().then(setEntities); }, []);
  useEffect(() => {
    void loadHaConnector().then(r => {
      const set = r.ok && !!r.data.url && r.data.access_token.set;
      setNeedConnectors(!set);
      if (r.ok && r.data.url) setUrl(r.data.url);
      setReady(true);
      if (set) fetchEntities();
    });
  }, [fetchEntities]);
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const connect = async () => {
    setBusy(true);
    const s = await saveHaConnector(url.trim(), token.trim());
    setBusy(false);
    if (!s.ok) { onError(Object.values(s.errors)[0] || "Couldn't save"); return; }
    onError('');
    setNeedConnectors(false);
    fetchEntities();
  };
  if (!ready) return <div aria-busy="true" data-testid="wiz-ha" style={{ height: 80 }} />;
  const connOk = !!url.trim() && !!token.trim();
  const ok = !busy && !!entity;
  return (
    <Center testid="wiz-ha">
      <HaLogo size={72} />
      <h3 style={stepTitle}>Home Assistant</h3>
      {needConnectors ? (
        <div style={group} data-testid="wiz-ha-connectors">
          <SRow label="URL" first>
            <input aria-label="URL" data-testid="ha-url" className="tve-in" value={url} onChange={e => setUrl(e.target.value)} autoComplete="off" spellCheck={false} style={inlineInput} />
          </SRow>
          <SRow label="Access Token">
            <input aria-label="Access Token" data-testid="ha-access-token" data-secret="true" type="password" className="tve-in" autoComplete="off"
              value={token} onChange={e => setToken(e.target.value)} style={inlineInput} />
          </SRow>
        </div>
      ) : (
        <div style={group}>
          <SRow label="TV" first>
            <Picker label="TV" testid="room-ha-entity" value={entity} onChange={setEntity}
              options={(entities || []).map(e => ({ value: e.id, label: e.name }))} />
          </SRow>
        </div>
      )}
      {error && <ErrorChip text={error} />}
      {needConnectors
        ? <button type="button" data-testid="wiz-ha-connect" disabled={!connOk || busy} onClick={() => void connect()} className="lp-focus tve-primary" style={bigBtn(connOk && !busy)}>Continue</button>
        : <button type="button" data-testid="wiz-ha-done" disabled={!ok} onClick={() => { setBusy(true); onDone(entity); }} className="lp-focus tve-primary" style={bigBtn(ok)}>Done</button>}
    </Center>
  );
}

/** Loads a hub's activities and devices once its address looks complete. The server only reads a hub
 * that is already saved on a room, so the wizard saves the typed hub first (`save`); the room page reads
 * only its saved hub (a new one is read after Save). */
function useHarmony(hub: string, save?: (hub: string) => Promise<boolean>, saved?: string) {
  const [opts, setOpts] = useState<{ hub: string; activities: string[]; devices: string[] } | null>(null);
  useEffect(() => {
    const h = hub.trim();
    if (!IP_RE.test(h) && !/^[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/.test(h)) return undefined;
    if (!save && h !== (saved || '').trim()) return undefined;
    let alive = true;
    const t = window.setTimeout(() => {
      void (save ? save(h) : Promise.resolve(true)).then(ok => {
        if (!alive || !ok) return;
        void loadHarmonyOptions(h).then(o => { if (alive) setOpts({ hub: h, activities: o.activities, devices: o.devices }); });
      });
    }, 400);
    return () => { alive = false; window.clearTimeout(t); };
  }, [hub, save, saved]);
  const live = opts && opts.hub === hub.trim() ? opts : null;
  return { activities: live?.activities || [], devices: live?.devices || [] };
}
const hubSavers = new Map<string, (hub: string) => Promise<boolean>>();
/** One stable saver per room (keeps useHarmony's effect from re-running every render). */
function hubSaver(id: string) {
  let f = hubSavers.get(id);
  if (!f) { f = (hub: string) => updateRoom(id, { harmony_hub: hub }).then(x => x.ok); hubSavers.set(id, f); }
  return f;
}
const asOptions = (xs: string[]) => xs.map(x => ({ value: x, label: x }));

/** Wizard 5b — Hub, Activity and Volume pickers (Harmony). */
function WizHarmonyStep({ error, onDone, saveHub }: {
  error: string; saveHub: (hub: string) => Promise<boolean>;
  onDone: (p: { harmony_hub: string; harmony_activity: string; harmony_volume_device: string; harmony_nav_device: string }) => void;
}) {
  const [hub, setHub] = useState('');
  const [act, setAct] = useState('');
  const [vol, setVol] = useState('');
  const [busy, setBusy] = useState(false);
  const { activities, devices } = useHarmony(hub, saveHub);
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const ok = !busy && !!hub.trim();
  return (
    <Center testid="wiz-harmony">
      <HarmonyLogo size={72} />
      <h3 style={stepTitle}>Harmony</h3>
      <div style={group}>
        <SRow label="Hub" first>
          <input aria-label="Hub" data-testid="room-hub" className="tve-in" value={hub} inputMode="decimal" autoComplete="off" spellCheck={false}
            onChange={e => setHub(e.target.value)} style={inlineInput} />
        </SRow>
        <SRow label="Activity"><Picker label="Activity" testid="room-activity" value={act} onChange={setAct} options={asOptions(activities)} /></SRow>
        <SRow label="Volume"><Picker label="Volume" testid="room-volume-device" value={vol} onChange={setVol} options={asOptions(devices)} /></SRow>
      </div>
      {error && <ErrorChip text={error} />}
      <button type="button" data-testid="wiz-harmony-done" disabled={!ok} onClick={() => {
        setBusy(true);
        onDone({ harmony_hub: hub.trim(), harmony_activity: act, harmony_volume_device: vol, harmony_nav_device: '' });
      }} className="lp-focus tve-primary" style={bigBtn(ok)}>Done</button>
    </Center>
  );
}

function PickStep({ cta, current, error, onPick }: { cta: string; current?: string; error: string; onPick: (p: Partial<FoundPlayer>) => void }) {
  const [list, setList] = useState<FoundPlayer[] | null>(null);
  const [sel, setSel] = useState<string>('');
  const [byIp, setByIp] = useState(false);
  const [ip, setIp] = useState('');
  const [busy, setBusy] = useState(false);
  const key = (p: FoundPlayer) => p.machine_id || `host:${p.host}`;
  const refresh = useCallback(() => {
    setList(null);
    void loadPlayers().then(r => {
      const ps = r.ok ? r.data.players : [];
      setList(ps);
      const first = ps.find(p => !!current && (p.machine_id === current || p.host === current)) || ps.find(p => p.online);
      setSel(s => s || (first ? key(first) : ''));
    });
  }, [current]);
  useEffect(refresh, [refresh]);
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const chosen = list?.find(p => key(p) === sel);
  const ipOk = /^(\d{1,3}\.){3}\d{1,3}$/.test(ip.trim()) || /^[A-Za-z0-9.-]{1,253}$/.test(ip.trim()) && ip.includes('.');
  const go = () => {
    setBusy(true);
    if (byIp) onPick({ host: ip.trim() });
    else if (chosen) onPick({ machine_id: chosen.machine_id, name: chosen.name, product: chosen.product, platform: chosen.platform, host: chosen.host });
  };
  const can = !busy && (byIp ? ipOk : !!chosen);
  return (
    <div data-testid="room-pick-step">
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={label}>Players</div>
        <IconBtn label="Refresh" onClick={refresh}>↻</IconBtn>
      </div>
      {byIp ? (
        <div style={{ marginTop: 8 }}>
          <div style={label}>IP address</div>
          <input aria-label="IP address" data-testid="room-ip" className="tve-in" value={ip} inputMode="decimal" autoFocus onChange={e => setIp(e.target.value)} style={field} />
        </div>
      ) : (
        <div role="radiogroup" aria-label="Players" data-testid="room-players" style={{ ...card, marginTop: 8 }}>
          {list === null ? <div aria-busy="true" style={{ height: 112 }} /> : list.length === 0 ? (
            <div style={{ ...row(0), justifyContent: 'center', color: W50, fontSize: 14 }}>No players found</div>
          ) : list.map((p, i) => {
            const on = key(p) === sel;
            return (
              <button key={key(p)} type="button" role="radio" aria-checked={on} data-testid="room-player" onClick={() => setSel(key(p))} className="lp-focus tve-row"
                style={{ ...row(i), width: '100%', background: 'none', border: 'none', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', cursor: 'pointer', textAlign: 'left' }}>
                <Badge text={badge(p)} />
                <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <span style={{ fontSize: 14, fontWeight: 600 }}>{p.name}</span>
                  <span style={{ fontSize: 12, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                    <StatusDot online={p.online} />{[p.online ? 'Online' : 'Offline', p.product].filter(Boolean).join(' · ')}
                  </span>
                </span>
                <span aria-hidden="true" style={{ width: 18, height: 18, borderRadius: 9, boxSizing: 'border-box', flex: 'none', border: on ? `5px solid ${ACCENT}` : '2px solid rgba(255,255,255,0.35)' }} />
              </button>
            );
          })}
        </div>
      )}
      <button type="button" data-testid="room-by-ip" onClick={() => setByIp(b => !b)} className="lp-focus tve-glass" style={{ ...glassBtn, width: '100%', marginTop: 12 }}>
        {byIp ? 'Choose a player' : 'Add by IP'}
      </button>
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13, marginTop: 8 }}>{error}</div>}
      <button type="button" data-testid="room-pick-go" disabled={!can} onClick={go} className="lp-focus tve-primary" style={{ ...primaryBtn(can), width: '100%', marginTop: 12 }}>{cta}</button>
    </div>
  );
}

function Switch({ id, on, onChange, label: l }: { id: string; on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={l} data-testid={`${id}-toggle`} onClick={() => onChange(!on)} className="lp-focus"
      style={{ width: 51, height: 31, padding: 0, border: 'none', borderRadius: 16, position: 'relative', cursor: 'pointer', flex: 'none',
        background: on ? ACCENT : 'rgba(255,255,255,0.18)', transition: 'background .2s' }}>
      <span aria-hidden="true" style={{ position: 'absolute', top: 2, left: on ? 22 : 2, width: 27, height: 27, borderRadius: 14, background: '#fff',
        transition: 'left .2s', boxShadow: '0 1px 3px rgba(0,0,0,.3)' }} />
    </button>
  );
}

/** Room page (premium pass): centered 600px column — TV hero, Name + Default room, Power chips,
 * the system's pickers, Advanced (Port, Navigation, Wake-on-LAN with the MAC auto-detected), Remove room. */
function EditRoom({ room: r, error, wide, onSave, onRemove, onChangePlayer, onSaveState, onPaired, onBack, saveDirty, onSaveClick }: {
  room: Room; error: string; wide: boolean; onSave: (p: RoomPatch) => void; onRemove: () => void; onChangePlayer: () => void; onPaired?: () => void;
  onSaveState: (dirty: boolean, save: () => void) => void; onBack: () => void; saveDirty: boolean; onSaveClick: () => void;
}) {
  const p = r.player;
  const wake0: Wake = p?.wake || 'none';
  const [name, setName] = useState(r.name);
  const [power, setPower] = useState<Power>(wake0 === 'wol' ? 'adb' : wake0);
  const [wol, setWol] = useState(wake0 === 'wol');
  const [host, setHost] = useState(p?.host || '');
  const [port, setPort] = useState(p?.port != null ? String(p.port) : '5555');
  const [mac, setMac] = useState(p?.mac || '');
  const [macBusy, setMacBusy] = useState(false);
  const [hub, setHub] = useState(r.harmony_hub || '');
  const [act, setAct] = useState(p?.harmony_activity || '');
  const [vol, setVol] = useState(r.harmony_volume_device || '');
  const [nav, setNav] = useState(p?.harmony_nav_device || '');
  const [haEntity, setHaEntity] = useState(p?.ha_power_entity || '');
  const [haList, setHaList] = useState<HaEntity[]>([]);
  const [def, setDef] = useState(r.default);
  const [advanced, setAdvanced] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const { activities, devices } = useHarmony(power === 'harmony' ? hub : '', undefined, r.harmony_hub);
  useEffect(() => { if (power === 'homeassistant') void loadHaEntities().then(setHaList); }, [power]);
  const wake: Wake = power === 'adb' && wol ? 'wol' : power;
  const patch: RoomPatch = {};
  if (name.trim() !== r.name) patch.name = name.trim();
  if (wake !== wake0) patch.wake = wake;
  if (host.trim() !== (p?.host || '')) patch.host = host.trim();
  if ((wake === 'adb' || wake === 'wol') && port !== (p?.port != null ? String(p.port) : '5555')) patch.port = port.trim();
  if (wake === 'wol' && mac.trim() !== (p?.mac || '')) patch.mac = mac.trim();
  if (wake === 'harmony' && hub.trim() !== (r.harmony_hub || '')) patch.harmony_hub = hub.trim();
  if (wake === 'harmony' && act !== (p?.harmony_activity || '')) patch.harmony_activity = act;
  if (wake === 'harmony' && vol !== (r.harmony_volume_device || '')) patch.harmony_volume_device = vol;
  if (wake === 'harmony' && nav !== (p?.harmony_nav_device || '')) patch.harmony_nav_device = nav;
  if (wake === 'homeassistant' && haEntity !== (p?.ha_power_entity || '')) patch.ha_power_entity = haEntity;
  if (def !== r.default) patch.default = def;
  const dirty = Object.keys(patch).length > 0 && !!name.trim();
  const patchKey = JSON.stringify(patch);
  const [pairMsg, setPairMsg] = useState<'' | 'busy' | 'ok' | 'fail'>('');
  const [pairHint, setPairHint] = useState('');
  const pair = () => {
    setPairMsg('busy'); setPairHint('');
    void pairRoom(r.id).then(x => {
      setPairMsg(x.ok ? 'ok' : 'fail');
      setPairHint(x.ok ? '' : pairFailLabel(x.hint));
      if (x.ok) onPaired?.();
    });
  };
  const toggleWol = (on: boolean) => {
    setWol(on);
    if (on && power !== 'adb') setPower('adb');
    if (on && !mac.trim()) {
      setMacBusy(true);
      void detectMac(r.id).then(m => { setMacBusy(false); if (m) setMac(m); });
    }
  };
  const saveRef = useRef(onSave);
  saveRef.current = onSave;
  useEffect(() => { onSaveState(dirty, () => saveRef.current(JSON.parse(patchKey) as RoomPatch)); }, [dirty, patchKey, onSaveState]);
  const paired = pairMsg === 'ok' || (!pairMsg && !!p?.paired);

  return (
    <div data-testid="room-edit" style={{ width: '100%', maxWidth: 600, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, fontFamily: FONT }}>
      {wide && (
        <div style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, marginBottom: 4 }}>
          <button type="button" aria-label="Back" data-testid="room-back" onClick={onBack} style={circleBtn}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6" /></svg>
          </button>
          <h2 style={{ flex: 1, margin: 0, textAlign: 'center', fontSize: 24, fontWeight: 650, color: W95, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.name}</h2>
          <span style={{ minWidth: 80, display: 'flex', justifyContent: 'flex-end' }}>
            {saveDirty && (
              <button type="button" data-testid="room-save" onClick={onSaveClick} className="lp-focus"
                style={{ height: 36, padding: '0 18px', borderRadius: 999, border: 'none', background: ACCENT, color: '#fff', fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer' }}>Save</button>
            )}
          </span>
        </div>
      )}
      <div data-testid="room-hero" style={{ width: '100%', boxSizing: 'border-box', borderRadius: 28, padding: '24px 20px', textAlign: 'center',
        background: 'linear-gradient(160deg, rgba(59,130,246,0.22), rgba(255,255,255,0.05))', border: '1px solid rgba(173,199,255,0.18)',
        display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12 }}>
        {p ? (
          <>
            <button type="button" aria-label={`Change ${p.name}`} data-testid="room-change-player" onClick={onChangePlayer}
              style={{ background: 'none', border: 'none', padding: 0, cursor: 'pointer', display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: W95, fontFamily: FONT }}>
              <Bubble size={92}><Glyph id="tv" size={42} /></Bubble>
              <span style={{ fontSize: 20, fontWeight: 650 }}>{p.name}</span>
            </button>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', justifyContent: 'center' }}>
              <span data-testid="room-paired">
                {pairMsg === 'busy' ? <Chip text="Pairing…" tone="wait" /> : pairMsg === 'fail' ? <Chip text={pairHint || 'Pairing failed'} tone="muted" />
                  : <Chip text={paired ? 'Paired' : 'Not paired'} tone={paired ? 'ok' : 'muted'} />}
              </span>
              <input aria-label="IP address" data-testid="room-host" className="tve-in" value={host} inputMode="decimal" onChange={e => setHost(e.target.value)}
                autoComplete="off" spellCheck={false}
                style={{ height: 30, width: Math.max(110, host.length * 9 + 28), padding: '0 12px', borderRadius: 999, border: '1px solid transparent', boxSizing: 'border-box',
                  background: 'rgba(255,255,255,0.08)', color: W70, fontSize: 13, fontWeight: 600, fontFamily: FONT, textAlign: 'center', outline: 'none' }} />
              {!paired && pairMsg !== 'busy' && (
                <button type="button" data-testid="room-pair" disabled={dirty || !p.host} onClick={pair} className="lp-focus"
                  style={{ height: 30, padding: '0 16px', borderRadius: 999, border: 'none', background: ACCENT, color: '#fff', fontSize: 13, fontWeight: 600,
                    fontFamily: FONT, cursor: dirty || !p.host ? 'default' : 'pointer', opacity: dirty || !p.host ? 0.4 : 1 }}>Pair</button>
              )}
            </div>
          </>
        ) : (
          <>
            <Bubble size={92}><Glyph id="tv" size={42} color="rgba(255,255,255,0.5)" /></Bubble>
            <span style={{ fontSize: 20, fontWeight: 650, color: W70 }}>No TV</span>
            <button type="button" data-testid="room-add-player" onClick={onChangePlayer} className="lp-focus tve-primary"
              style={{ height: 40, padding: '0 22px', borderRadius: 999, border: 'none', background: ACCENT, color: '#fff', fontSize: 15, fontWeight: 600, fontFamily: FONT, cursor: 'pointer' }}>Add TV</button>
          </>
        )}
      </div>

      <div style={group}>
        <SRow label="Name" first>
          <input aria-label="Name" data-testid="room-name" className="tve-in" value={name} maxLength={40} onChange={e => setName(e.target.value)} autoComplete="off" style={inlineInput} />
        </SRow>
        <SRow label="Default room"><Switch id="room-default" label="Default room" on={def} onChange={setDef} /></SRow>
      </div>

      {p && (
        <>
          <div style={{ fontSize: 13, fontWeight: 600, color: W50, marginTop: 6 }}>Power</div>
          <div role="radiogroup" aria-label="Power" style={{ display: 'flex', gap: 8, justifyContent: 'center', flexWrap: 'wrap' }}>
            {POWERS.map(w => {
              const on = power === w.id;
              return (
                <button key={w.id} type="button" role="radio" aria-checked={on} aria-label={w.label} data-testid={`room-wake-${w.id}`}
                  onClick={() => { setPower(w.id); if (w.id !== 'adb') setWol(false); }} className="lp-focus"
                  style={{ height: 44, padding: w.id === 'none' ? '0 16px' : '0 16px 0 8px', borderRadius: 999, display: 'inline-flex', alignItems: 'center', gap: 10,
                    fontSize: 14, fontWeight: 600, fontFamily: FONT, cursor: 'pointer', color: W95,
                    background: on ? 'rgba(59,130,246,0.16)' : 'rgba(255,255,255,0.07)', border: `1.5px solid ${on ? ACCENT : 'transparent'}` }}>
                  {w.id === 'adb' && <Bubble size={30}><Glyph id="tv" size={16} /></Bubble>}
                  {w.id === 'homeassistant' && <HaLogo size={30} />}
                  {w.id === 'harmony' && <HarmonyLogo size={30} />}
                  {w.label}
                </button>
              );
            })}
          </div>
          {power === 'homeassistant' && (
            <div style={group} data-testid="room-wake-fields">
              <SRow label="TV" first>
                <Picker label="TV" testid="room-ha-entity" value={haEntity} onChange={setHaEntity} options={haList.map(e => ({ value: e.id, label: e.name }))} />
              </SRow>
            </div>
          )}
          {power === 'harmony' && (
            <div style={group} data-testid="room-wake-fields">
              <SRow label="Hub" first>
                <input aria-label="Hub" data-testid="room-hub" className="tve-in" value={hub} inputMode="decimal" onChange={e => setHub(e.target.value)} autoComplete="off" style={inlineInput} />
              </SRow>
              <SRow label="Activity"><Picker label="Activity" testid="room-activity" value={act} onChange={setAct} options={asOptions(activities)} /></SRow>
              <SRow label="Volume"><Picker label="Volume" testid="room-volume-device" value={vol} onChange={setVol} options={asOptions(devices)} /></SRow>
            </div>
          )}
          <div style={group} data-testid="room-advanced">
            <button type="button" aria-expanded={advanced} data-testid="room-advanced-toggle" onClick={() => setAdvanced(a => !a)}
              style={{ width: '100%', minHeight: 56, padding: '0 20px', display: 'flex', alignItems: 'center', background: 'none', border: 'none', cursor: 'pointer',
                fontSize: 16, fontFamily: FONT, color: '#adc7ff', textAlign: 'left' }}>
              <span style={{ flex: 1 }}>Advanced</span>
              <span aria-hidden="true" style={{ color: W50, transition: 'transform .15s', transform: advanced ? 'rotate(90deg)' : 'none' }}>›</span>
            </button>
            {advanced && (
              <>
                <SRow label="Port">
                  <input aria-label="Port" data-testid="room-port" className="tve-in" value={port} inputMode="numeric" onChange={e => setPort(e.target.value)} style={{ ...inlineInput, width: 100 }} />
                </SRow>
                {power === 'harmony' && (
                  <SRow label="Navigation">
                    <Picker label="Navigation" testid="room-nav-device" value={nav} onChange={setNav} empty="Same as TV" options={asOptions(devices)} />
                  </SRow>
                )}
                <SRow label="Wake-on-LAN"><Switch id="room-wol" label="Wake-on-LAN" on={wol} onChange={toggleWol} /></SRow>
                {wol && (
                  <SRow label="MAC address">
                    {macBusy ? <span style={{ color: W50, fontSize: 15 }}>…</span>
                      : <input aria-label="MAC address" data-testid="room-mac" className="tve-in" value={mac} onChange={e => setMac(e.target.value)} autoComplete="off" spellCheck={false} style={inlineInput} />}
                  </SRow>
                )}
              </>
            )}
          </div>
        </>
      )}
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13 }}>{error}</div>}
      <button type="button" data-testid="room-remove-open" onClick={() => setConfirm(true)} className="lp-focus"
        style={{ marginTop: 8, height: 44, padding: '0 16px', background: 'none', border: 'none', color: '#f87171', fontSize: 15, fontFamily: FONT, cursor: 'pointer' }}>Remove room</button>
      {confirm && <ConfirmRemove name={r.name} onCancel={() => setConfirm(false)} onRemove={() => { setConfirm(false); onRemove(); }} />}
    </div>
  );
}

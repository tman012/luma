/** v224 Settings > Rooms (Taylor's Option A, room first; docs: /workspace/design/rooms/option-A-*).
 * v299: Add Room wizard — Name → Network debugging → IP Pair → smart home (HA | Harmony).
 * Pair is part of Add: empty room on create; devices only after Pair success (v272). Cancel/Back
 * before Pair deletes the empty room; after Pair, Back/close never DELETE. Desktop uses the same
 * wizard steps in a modal. */
import { useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { FONT } from '@/components/start/StartUI';
import { Modal, Toggle, field, glassBtn, primaryBtn, tvEditCss } from '@/components/library/TvEdit';
import {
  badge, createRoom, loadHaConnector, loadPlayers, loadRooms, pairFailLabel, pairRoom, removeRoom,
  saveHaConnector, updateRoom, WAKE_LABEL,
  type FoundPlayer, type Room, type RoomPatch, type RoomsView, type Wake,
} from './roomsApi';
import { Badge, StatusDot } from './PlayIn';

const W50 = 'rgba(255,255,255,0.5)';
const W95 = 'rgba(255,255,255,0.95)';
const ACCENT = '#3b82f6';
const RED = '#ef4444';
const card: CSSProperties = { borderRadius: 16, background: 'rgba(255,255,255,0.06)', border: '1px solid rgba(255,255,255,0.08)', overflow: 'hidden' };
const row = (i: number): CSSProperties => ({ minHeight: 56, display: 'flex', alignItems: 'center', gap: 12, padding: '0 14px',
  borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', fontFamily: FONT, color: W95, boxSizing: 'border-box' });
const label: CSSProperties = { margin: '0 4px 8px', fontSize: 12, fontWeight: 600, color: W50 };
const WAKES: { id: Wake; label: string }[] = [
  { id: 'adb', label: 'ADB' }, { id: 'wol', label: 'Wake-on-LAN' }, { id: 'harmony', label: 'Harmony' },
  { id: 'homeassistant', label: 'HA' }, { id: 'none', label: 'None' },
];
const TAP: CSSProperties = { minHeight: 44 };

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

  let flow: { title: string; body: ReactNode; action?: ReactNode; back: () => void } | null = null;

  if (view.k === 'wiz-name') {
    flow = {
      title: 'Add Room', back: toList, body: (
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
      title: 'Add Media Player', back: () => { setError(''); setView({ k: 'wiz-adb', id }); }, body: (
        <WizPlayerStep roomId={id} error={error}
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
          onDone={async (entity, connectors) => {
            setError('');
            if (connectors) {
              const s = await saveHaConnector(connectors.url, connectors.token);
              if (!s.ok) { setError(Object.values(s.errors)[0] || "Couldn't save"); return; }
            }
            const x = await updateRoom(id, { wake: 'homeassistant', ha_power_entity: entity });
            take(x, toList);
          }} />),
    };
  } else if (view.k === 'wiz-harmony' && room(view.id)) {
    const id = view.id;
    flow = {
      title: 'Harmony Hub', back: () => setView({ k: 'wiz-power', id }), body: (
        <WizHarmonyStep error={error}
          onDone={patch => void updateRoom(id, { wake: 'harmony', ...patch }).then(x => take(x, toList))} />),
    };
  } else if (view.k === 'edit' && room(view.id)) {
    const r = room(view.id)!;
    flow = { title: r.name, back: toList, action: <HeaderSave dirty={editSave.dirty} onSave={editSave.save} />, body: (
      <EditRoom key={r.id} room={r} error={error} onSaveState={onSaveState} onPaired={load}
        onChangePlayer={() => setView({ k: 'add', id: r.id })}
        onSave={patch => void updateRoom(r.id, patch).then(x => take(x, toList))}
        onRemove={() => void removeRoom(r.id).then(x => take(x, toList))} />) };
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

  const startWizard = () => { setError(''); setView({ k: 'wiz-name' }); };
  const closeFlow = () => {
    if (view.k === 'wiz-adb' || view.k === 'wiz-player') {
      const id = 'id' in view ? view.id : '';
      const r = id ? room(id) : undefined;
      if (r && !r.player) { abandonEmpty(id); return; }
    }
    toList();
  };

  if (!wide) {
    if (flow) {
      return (
        <div data-testid="rooms-settings" data-view={view.k} style={{ fontFamily: FONT }}>
          <style>{tvEditCss}</style>
          <PageHeader title={flow.title} onBack={flow.back} action={flow.action} />
          <div style={{ padding: '8px 16px 0' }}>{flow.body}</div>
        </div>
      );
    }
    return (
      <div data-testid="rooms-settings" data-view="list" style={{ fontFamily: FONT }}>
        <style>{tvEditCss}</style>
        <PageHeader title="Rooms" onBack={onExit} action={rooms.length ? <IconBtn label="Add room" onClick={startWizard}>+</IconBtn> : undefined} />
        <div style={{ padding: '8px 16px 0' }}>
          {rooms.length === 0 ? <Empty onAdd={startWizard} /> : (
            <div style={card} data-testid="rooms-list">
              {rooms.map((r, i) => (
                <button key={r.id} type="button" data-testid="room-row" className="lp-focus tve-row" onClick={() => setView({ k: 'edit', id: r.id })}
                  style={{ ...row(i), width: '100%', background: 'none', border: 'none', borderTop: i ? '1px solid rgba(255,255,255,0.06)' : 'none', cursor: 'pointer', textAlign: 'left' }}>
                  <Badge text={badge(r.player)} />
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

  return (
    <div data-testid="rooms-settings" data-view="list" style={{ fontFamily: FONT }}>
      <style>{tvEditCss}</style>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: W95 }}>Rooms</h2>
        {rooms.length > 0 && <button type="button" data-testid="rooms-add" className="lp-focus tve-primary" onClick={startWizard} style={primaryBtn(true)}>+ Add room</button>}
      </div>
      {error && view.k === 'list' && <div role="alert" style={{ color: '#fca5a5', fontSize: 13, marginBottom: 12 }}>{error}</div>}
      {rooms.length === 0 ? <Empty onAdd={startWizard} /> : (
        <div data-testid="rooms-grid" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))', gap: 12 }}>
          {rooms.map(r => (
            <RoomCard key={r.id} room={r} onEdit={() => setView({ k: 'edit', id: r.id })}
              onDefault={() => void updateRoom(r.id, { default: true }).then(x => take(x))}
              onRemove={() => void removeRoom(r.id).then(x => take(x))} />
          ))}
        </div>
      )}
      {flow && <Modal title={flow.title} testid="room-flow" onClose={closeFlow} footer={null} actions={flow.action}>{flow.body}</Modal>}
    </div>
  );
}

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
      <span aria-hidden="true" style={{ width: 48, height: 48, borderRadius: 12, background: 'rgba(255,255,255,0.08)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ width: 18, height: 12, border: `2px solid ${W95}`, borderRadius: 2 }} />
      </span>
      <span style={{ fontSize: 16, fontWeight: 600, color: W95 }}>No rooms yet</span>
      <button type="button" data-testid="rooms-empty-add" className="lp-focus tve-primary" onClick={onAdd} style={primaryBtn(true)}>+ Add room</button>
    </div>
  );
}

function RoomCard({ room: r, onEdit, onDefault, onRemove }: { room: Room; onEdit: () => void; onDefault: () => void; onRemove: () => void }) {
  const [menu, setMenu] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const item: CSSProperties = { display: 'block', width: '100%', textAlign: 'left', padding: '10px 14px', background: 'none', border: 'none', color: W95, fontSize: 14, fontFamily: FONT, cursor: 'pointer' };
  return (
    <div data-testid="room-card" style={{ ...card, padding: 14, position: 'relative', overflow: 'visible' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <span style={{ flex: 1, fontSize: 16, fontWeight: 600, color: W95 }}>{r.name}</span>
        {r.default && <DefaultPill />}
        <button type="button" aria-label={`${r.name} options`} data-testid="room-menu" onClick={() => setMenu(m => !m)} className="lp-focus tve-glass"
          style={{ ...glassBtn, width: 32, height: 32, padding: 0, background: 'none', border: 'none' }}>⋯</button>
      </div>
      {menu && (
        <div role="menu" style={{ position: 'absolute', right: 14, top: 48, zIndex: 5, minWidth: 160, borderRadius: 12, background: 'rgba(21,16,42,0.98)', border: '1px solid rgba(255,255,255,0.1)', overflow: 'hidden' }}>
          <button type="button" role="menuitem" className="tve-opt" style={item} onClick={() => { setMenu(false); onEdit(); }}>Edit</button>
          {!r.default && <button type="button" role="menuitem" className="tve-opt" style={item} onClick={() => { setMenu(false); onDefault(); }}>Make default</button>}
          <button type="button" role="menuitem" className="tve-opt" style={{ ...item, color: RED }} onClick={() => { setMenu(false); setConfirm(true); }}>Remove</button>
        </div>
      )}
      <button type="button" onClick={onEdit} className="lp-focus tve-row" style={{ ...row(0), width: '100%', borderRadius: 12, background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)', cursor: 'pointer', textAlign: 'left' }}>
        <Badge text={badge(r.player)} />
        <span style={{ display: 'flex', flexDirection: 'column', gap: 2, minWidth: 0 }}>
          <span style={{ fontSize: 13, fontWeight: 600 }}>{r.player?.name || 'No player'}</span>
          <span style={{ fontSize: 12, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}><StatusDot online={!!r.player?.online} />{playerLine(r)}</span>
        </span>
      </button>
      {confirm && <ConfirmRemove name={r.name} onCancel={() => setConfirm(false)} onRemove={() => { setConfirm(false); onRemove(); }} />}
    </div>
  );
}

function ConfirmRemove({ name, onCancel, onRemove }: { name: string; onCancel: () => void; onRemove: () => void }) {
  return (
    <div className="luma-fullscreen luma-bleed" data-testid="room-remove-scrim" onPointerDown={e => { if (e.target === e.currentTarget) onCancel(); }}
      style={{ position: 'fixed', inset: 0, zIndex: 1200, background: 'rgba(10,8,24,.6)', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16, fontFamily: FONT }}>
      <div role="alertdialog" aria-label={`Remove ${name}?`} data-testid="room-remove" style={{ width: 360, maxWidth: '100%', boxSizing: 'border-box', padding: 16, borderRadius: 16, background: 'rgba(21,16,42,0.96)', border: '1px solid rgba(255,255,255,0.08)' }}>
        <h2 style={{ margin: '0 0 14px', fontSize: 16, fontWeight: 600, color: W95 }}>Remove {name}?</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <button type="button" onClick={onCancel} className="lp-focus tve-glass" style={{ ...glassBtn, flex: 1 }}>Cancel</button>
          <button type="button" data-testid="room-remove-go" onClick={onRemove} className="lp-focus" style={{ ...primaryBtn(true), flex: 1, background: RED }}>Remove</button>
        </div>
      </div>
    </div>
  );
}

/** Wizard step 1 — blank Name, Next (no chips). Creates empty room shell on Next. */
function WizNameStep({ error, onNext }: { error?: string; onNext: (name: string) => void }) {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const ok = !busy && !!name.trim() && name.trim().length <= 40;
  const go = () => { setBusy(true); onNext(name.trim()); };
  useEffect(() => { if (error) setBusy(false); }, [error]);
  return (
    <div data-testid="room-name-step">
      <div style={label}>Name</div>
      <input aria-label="Name" data-testid="room-name" className="tve-in" value={name} maxLength={40} autoFocus
        onChange={e => setName(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter' && ok) go(); }} style={field} />
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13, marginTop: 8 }}>{error}</div>}
      <button type="button" data-testid="room-next" disabled={!ok} onClick={go} className="lp-focus tve-primary"
        style={{ ...primaryBtn(ok), width: '100%', marginTop: 16, ...TAP }}>Next</button>
    </div>
  );
}

/** Wizard step 2 — only screen with instructional copy (AmEng). */
function WizAdbHowto({ onContinue }: { onContinue: () => void }) {
  const steps = [
    'Open Settings on the TV.',
    'Go to Device Preferences → About.',
    'Tap Build number seven times.',
    'Open Developer options.',
    'Turn on Network debugging.',
  ];
  return (
    <div data-testid="wiz-adb-howto">
      <ol data-testid="wiz-adb-steps" style={{ margin: '0 0 16px', padding: '0 0 0 20px', color: W95, fontSize: 14, lineHeight: 1.55, fontFamily: FONT }}>
        {steps.map(s => <li key={s} style={{ marginBottom: 8 }}>{s}</li>)}
      </ol>
      <button type="button" data-testid="wiz-adb-continue" onClick={onContinue} className="lp-focus tve-primary"
        style={{ ...primaryBtn(true), width: '100%', ...TAP }}>Continue</button>
    </div>
  );
}

/** Wizard step 3 — IP-only Pair. Devices persist only on Pair success. */
function WizPlayerStep({ roomId, error, onPairing, onFail, onPaired, onContinue }: {
  roomId: string; error: string;
  onPairing: () => void; onFail: (hint?: string) => void; onPaired: () => Promise<void>; onContinue: () => void;
}) {
  const [ip, setIp] = useState('');
  const [busy, setBusy] = useState(false);
  const [paired, setPaired] = useState(false);
  const ipOk = /^(\d{1,3}\.){3}\d{1,3}$/.test(ip.trim());
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
    <div data-testid="wiz-player-step">
      <div style={label}>IP address</div>
      <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
        <input aria-label="IP address" data-testid="room-ip" className="tve-in" value={ip} inputMode="decimal" autoFocus
          disabled={busy || paired} onChange={e => setIp(e.target.value)} style={{ ...field, flex: 1 }} />
        <button type="button" data-testid="room-pair" disabled={busy || paired || !ipOk} onClick={pair} className="lp-focus tve-glass"
          style={{ ...glassBtn, height: 44, padding: '0 16px', fontSize: 14, opacity: busy || paired || !ipOk ? 0.4 : 1 }}>Pair</button>
      </div>
      {busy && <div data-testid="wiz-pair-busy" style={{ marginTop: 12, fontSize: 14, color: W95 }}>Check the TV and tap Allow</div>}
      {paired && <div data-testid="wiz-pair-ok" style={{ marginTop: 12, fontSize: 14, color: W95, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
        <StatusDot online />Paired and connected
      </div>}
      {error && !busy && <div role="alert" data-testid="wiz-pair-fail" style={{ color: '#fca5a5', fontSize: 13, marginTop: 8 }}>{error}</div>}
      <button type="button" data-testid="wiz-player-continue" disabled={!paired} onClick={onContinue} className="lp-focus tve-primary"
        style={{ ...primaryBtn(paired), width: '100%', marginTop: 16, ...TAP }}>Continue</button>
    </div>
  );
}

function ChoiceRow({ testid, label: l, onClick }: { testid: string; label: string; onClick: () => void }) {
  return (
    <button type="button" data-testid={testid} onClick={onClick} className="lp-focus tve-row"
      style={{ ...row(0), ...TAP, width: '100%', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)',
        borderRadius: 12, cursor: 'pointer', textAlign: 'left', marginBottom: 10, fontSize: 15, fontWeight: 600 }}>
      {l}
    </button>
  );
}

function WizSmartHome({ onNo, onYes }: { onNo: () => void; onYes: () => void }) {
  return (
    <div data-testid="wiz-smarthome">
      <ChoiceRow testid="wiz-smarthome-no" label="No" onClick={onNo} />
      <ChoiceRow testid="wiz-smarthome-yes" label="Yes" onClick={onYes} />
    </div>
  );
}

function WizPowerChooser({ onHa, onHarmony }: { onHa: () => void; onHarmony: () => void }) {
  return (
    <div data-testid="wiz-power">
      <ChoiceRow testid="wiz-power-ha" label="Home Assistant" onClick={onHa} />
      <ChoiceRow testid="wiz-power-harmony" label="Harmony Hub" onClick={onHarmony} />
    </div>
  );
}

function WizHaStep({ error, onDone }: {
  error: string;
  onDone: (entity: string, connectors?: { url: string; token: string }) => void;
}) {
  const [ready, setReady] = useState(false);
  const [needConnectors, setNeedConnectors] = useState(false);
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [entity, setEntity] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    void loadHaConnector().then(r => {
      const set = r.ok && !!r.data.url && r.data.access_token.set;
      setNeedConnectors(!set);
      if (r.ok && r.data.url) setUrl(r.data.url);
      setReady(true);
    });
  }, []);
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const entityOk = !!entity.trim();
  const connOk = !needConnectors || (!!url.trim() && !!token.trim());
  const ok = ready && !busy && entityOk && connOk;
  const go = () => {
    setBusy(true);
    onDone(entity.trim(), needConnectors ? { url: url.trim(), token: token.trim() } : undefined);
  };
  if (!ready) return <div aria-busy="true" data-testid="wiz-ha" style={{ height: 80 }} />;
  return (
    <div data-testid="wiz-ha" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {needConnectors && (
        <div style={card} data-testid="wiz-ha-connectors">
          <label style={{ ...row(0), justifyContent: 'space-between' }}>
            <span style={{ fontSize: 14 }}>URL</span>
            <input aria-label="URL" data-testid="ha-url" className="tve-in" value={url} onChange={e => setUrl(e.target.value)}
              style={{ ...field, width: 200, height: 36, textAlign: 'right', background: 'transparent', border: '1px solid transparent' }} />
          </label>
          <label style={{ ...row(1), justifyContent: 'space-between' }}>
            <span style={{ fontSize: 14 }}>Access Token</span>
            <input aria-label="Access Token" data-testid="ha-access-token" data-secret="true" type="password" className="tve-in"
              value={token} onChange={e => setToken(e.target.value)}
              style={{ ...field, width: 200, height: 36, textAlign: 'right', background: 'transparent', border: '1px solid transparent' }} />
          </label>
        </div>
      )}
      <div>
        <div style={label}>HA power entity</div>
        <input aria-label="HA power entity" data-testid="room-ha-entity" className="tve-in" value={entity}
          onChange={e => setEntity(e.target.value)} style={field} />
      </div>
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13 }}>{error}</div>}
      <button type="button" data-testid="wiz-ha-done" disabled={!ok} onClick={go} className="lp-focus tve-primary"
        style={{ ...primaryBtn(ok), width: '100%', ...TAP }}>Done</button>
    </div>
  );
}

function WizHarmonyStep({ error, onDone }: {
  error: string;
  onDone: (p: { harmony_hub: string; harmony_activity: string; harmony_volume_device: string; harmony_nav_device: string }) => void;
}) {
  const [hub, setHub] = useState('');
  const [act, setAct] = useState('');
  const [vol, setVol] = useState('');
  const [nav, setNav] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (error) setBusy(false); }, [error]);
  const ok = !busy && !!hub.trim();
  const fieldRow = (l: string, id: string, v: string, set: (s: string) => void, mode?: 'decimal') => (
    <label key={id} style={{ ...row(id === 'hub' ? 0 : 1), justifyContent: 'space-between' }}>
      <span style={{ fontSize: 14 }}>{l}</span>
      <input aria-label={l} data-testid={`room-${id}`} className="tve-in" value={v} inputMode={mode} onChange={e => set(e.target.value)}
        style={{ ...field, width: 170, height: 36, textAlign: 'right', background: 'transparent', border: '1px solid transparent' }} />
    </label>
  );
  return (
    <div data-testid="wiz-harmony">
      <div style={card}>
        {fieldRow('Hub IP address', 'hub', hub, setHub, 'decimal')}
        {fieldRow('Activity', 'activity', act, setAct)}
        {fieldRow('Volume device', 'volume-device', vol, setVol)}
        {fieldRow('Harmony nav device', 'nav-device', nav, setNav)}
      </div>
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13, marginTop: 8 }}>{error}</div>}
      <button type="button" data-testid="wiz-harmony-done" disabled={!ok} onClick={() => {
        setBusy(true);
        onDone({ harmony_hub: hub.trim(), harmony_activity: act.trim(), harmony_volume_device: vol.trim(), harmony_nav_device: nav.trim() });
      }} className="lp-focus tve-primary" style={{ ...primaryBtn(ok), width: '100%', marginTop: 16, ...TAP }}>Done</button>
    </div>
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

function EditRoom({ room: r, error, onSave, onRemove, onChangePlayer, onSaveState, onPaired }: {
  room: Room; error: string; onSave: (p: RoomPatch) => void; onRemove: () => void; onChangePlayer: () => void; onPaired?: () => void;
  onSaveState: (dirty: boolean, save: () => void) => void;
}) {
  const p = r.player;
  const [name, setName] = useState(r.name);
  const [wake, setWake] = useState<Wake>(p?.wake || 'none');
  const [host, setHost] = useState(p?.host || '');
  const [port, setPort] = useState(p?.port != null ? String(p.port) : '5555');
  const [mac, setMac] = useState(p?.mac || '');
  const [hub, setHub] = useState(r.harmony_hub || '');
  const [act, setAct] = useState(p?.harmony_activity || '');
  const [vol, setVol] = useState(r.harmony_volume_device || '');
  const [nav, setNav] = useState(p?.harmony_nav_device || '');
  const [haEntity, setHaEntity] = useState(p?.ha_power_entity || '');
  const [def, setDef] = useState(r.default);
  const [confirm, setConfirm] = useState(false);
  const patch: RoomPatch = {};
  if (name.trim() !== r.name) patch.name = name.trim();
  if (wake !== (p?.wake || 'none')) patch.wake = wake;
  if (host.trim() !== (p?.host || '')) patch.host = host.trim();
  if (wake === 'adb' && port !== (p?.port != null ? String(p.port) : '5555')) patch.port = port.trim();
  if (wake === 'wol' && mac.trim() !== (p?.mac || '')) patch.mac = mac.trim();
  if (wake === 'harmony' && hub.trim() !== (r.harmony_hub || '')) patch.harmony_hub = hub.trim();
  if (wake === 'harmony' && act.trim() !== (p?.harmony_activity || '')) patch.harmony_activity = act.trim();
  if (wake === 'harmony' && vol.trim() !== (r.harmony_volume_device || '')) patch.harmony_volume_device = vol.trim();
  if (wake === 'harmony' && nav.trim() !== (p?.harmony_nav_device || '')) patch.harmony_nav_device = nav.trim();
  if (wake === 'homeassistant' && haEntity.trim() !== (p?.ha_power_entity || '')) patch.ha_power_entity = haEntity.trim();
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
  const saveRef = useRef(onSave);
  saveRef.current = onSave;
  useEffect(() => { onSaveState(dirty, () => saveRef.current(JSON.parse(patchKey) as RoomPatch)); }, [dirty, patchKey, onSaveState]);
  const fieldRow = (l: string, id: string, v: string, set: (s: string) => void, mode?: 'decimal' | 'numeric') => (
    <label key={id} style={{ ...row(id === 'host' ? 0 : 1), justifyContent: 'space-between' }}>
      <span style={{ fontSize: 14 }}>{l}</span>
      <input aria-label={l} data-testid={`room-${id}`} className="tve-in" value={v} inputMode={mode} onChange={e => set(e.target.value)}
        style={{ ...field, width: 170, height: 36, textAlign: 'right', background: 'transparent', border: '1px solid transparent' }} />
    </label>
  );
  return (
    <div data-testid="room-edit" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <div style={label}>Name</div>
        <input aria-label="Name" data-testid="room-name" className="tve-in" value={name} maxLength={40} onChange={e => setName(e.target.value)} style={field} />
      </div>
      <div>
        <div style={label}>Player</div>
        {p ? (
          <div style={{ ...card, ...row(0), padding: '0 8px 0 12px' }}>
            <Badge text={badge(p)} />
            <span style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 2 }}>
              <span style={{ fontSize: 14, fontWeight: 600 }}>{p.name}</span>
              <span style={{ fontSize: 12, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}><StatusDot online={!!p.online} />{[p.online ? 'Online' : 'Offline', p.host].filter(Boolean).join(' · ')}</span>
            </span>
            <button type="button" data-testid="room-change-player" onClick={onChangePlayer} className="lp-focus tve-glass" style={{ ...glassBtn, height: 36, padding: '0 14px', fontSize: 13 }}>Change</button>
          </div>
        ) : (
          <button type="button" data-testid="room-add-player" onClick={onChangePlayer} className="lp-focus tve-primary"
            style={{ ...primaryBtn(true), width: '100%' }}>Add player</button>
        )}
      </div>
{p && (
      <>
      <div>
        <div style={label}>Wake with</div>
        <div role="radiogroup" aria-label="Wake with" style={{ ...card, display: 'flex', flexWrap: 'wrap', padding: 4, gap: 4 }}>
          {WAKES.map(w => (
            <button key={w.id} type="button" role="radio" aria-checked={wake === w.id} aria-label={w.id === 'homeassistant' ? 'Home Assistant' : w.label}
              data-testid={`room-wake-${w.id}`} onClick={() => setWake(w.id)} className="lp-focus"
              style={{ flex: '1 1 18%', minWidth: 52, height: 32, borderRadius: 8, border: 'none', fontSize: 11, fontWeight: 600, fontFamily: FONT, cursor: 'pointer',
                background: wake === w.id ? ACCENT : 'transparent', color: wake === w.id ? '#fff' : 'rgba(255,255,255,0.75)' }}>{w.label}</button>
          ))}
        </div>
      </div>
      {wake !== 'none' && (
        <div style={card} data-testid="room-wake-fields">
          {fieldRow('IP address', 'host', host, setHost, 'decimal')}
          {wake === 'adb' && fieldRow('Port', 'port', port, setPort, 'numeric')}
          {wake === 'adb' && (
            <div style={{ ...row(1), justifyContent: 'space-between' }}>
              <span style={{ fontSize: 14 }}>Pairing</span>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 10 }}>
                <span data-testid="room-paired" style={{ fontSize: 13, color: W50, display: 'inline-flex', alignItems: 'center', gap: 6 }}>
                  <StatusDot online={pairMsg === 'ok' || (!pairMsg && !!p?.paired)} />
                  {pairMsg === 'busy' ? 'Pairing…' : pairMsg === 'ok' ? 'Paired and connected' : pairMsg === 'fail' ? (pairHint || 'Pairing failed') : p?.paired ? 'Paired' : 'Not paired'}
                </span>
                <button type="button" data-testid="room-pair" disabled={pairMsg === 'busy' || dirty || !r.player?.host} onClick={pair} className="lp-focus tve-glass"
                  style={{ ...glassBtn, height: 32, padding: '0 14px', fontSize: 13, opacity: pairMsg === 'busy' || dirty || !r.player?.host ? 0.4 : 1 }}>Pair</button>
              </span>
            </div>
          )}
          {wake === 'wol' && fieldRow('MAC address', 'mac', mac, setMac)}
          {wake === 'homeassistant' && fieldRow('HA power entity', 'ha-entity', haEntity, setHaEntity)}
          {wake === 'harmony' && fieldRow('Hub IP address', 'hub', hub, setHub, 'decimal')}
          {wake === 'harmony' && fieldRow('Activity', 'activity', act, setAct)}
          {wake === 'harmony' && fieldRow('Volume device', 'volume-device', vol, setVol)}
          {wake === 'harmony' && fieldRow('Harmony nav device', 'nav-device', nav, setNav)}
        </div>
      )}
      </>
      )}
            <div style={{ ...card, ...row(0), justifyContent: 'space-between' }}>
        <span style={{ fontSize: 14 }}>Default room</span>
        <Toggle id="room-default-toggle" label="Default room" on={def} onChange={setDef} />
      </div>
      {error && <div role="alert" style={{ color: '#fca5a5', fontSize: 13 }}>{error}</div>}
      <button type="button" data-testid="room-remove-open" onClick={() => setConfirm(true)} className="lp-focus tve-glass"
        style={{ ...glassBtn, width: '100%', color: '#f87171', border: '1px solid rgba(239,68,68,0.3)' }}>Remove room</button>
      {confirm && <ConfirmRemove name={r.name} onCancel={() => setConfirm(false)} onRemove={() => { setConfirm(false); onRemove(); }} />}
    </div>
  );
}

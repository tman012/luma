/**
 * SmartHomeModal — full-screen control panel for Home Assistant entities.
 *
 * Tapping the kiosk background opens this modal pre-selected to the device's
 * configured default area. Other areas are reachable via a chip row at the
 * top so the user can control any room from any device.
 *
 * Data flow:
 *   /api/ha/areas                        — once on mount
 *   /api/ha/area/<area_id>/entities      — on area change + after each control
 *   /api/ha/service   (POST)             — for every toggle/slider action
 *
 * After a control fires we refetch the active area so sliders and toggles
 * reflect HA's authoritative state rather than our optimistic guess. HA
 * typically settles in <500ms, so we refetch after a short delay.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { apiFetch } from '../../apiClient';

// ────────────────────────────────────────────────────────────────────
// Types
// ────────────────────────────────────────────────────────────────────

interface HaArea {
  id: string;
  name: string;
}

interface HaEntity {
  entity_id: string;
  domain: string;
  name: string;
  state: string | null;
  attributes: Record<string, unknown>;
}

interface SmartHomeModalProps {
  open: boolean;
  onClose: () => void;
  defaultAreaId?: string;
}

// ────────────────────────────────────────────────────────────────────
// HA helpers
// ────────────────────────────────────────────────────────────────────

async function callService(domain: string, service: string, data: Record<string, unknown>): Promise<boolean> {
  try {
    const resp = await apiFetch('/api/ha/service', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain, service, data }),
    });
    const body = await resp.json();
    return Boolean(body?.ok);
  } catch {
    return false;
  }
}

// A click should only be treated as "inside" the sheet when it lands on a
// real interactive element (tile, button, slider). Anything else — the gap
// between tiles, the padding around the slider, the empty space next to
// the power toggle — is considered backdrop and closes the sheet.
function isInteractiveTarget(target: EventTarget | null): boolean {
  if (!(target instanceof Element)) return false;
  return !!target.closest('button, a, [role="button"], [role="slider"], input, select, textarea');
}

function isOn(state: string | null | undefined): boolean {
  if (!state) return false;
  return state === 'on' || state === 'open' || state === 'unlocked' || state === 'playing' || state === 'heat' || state === 'cool';
}

function domainLabel(domain: string): string {
  switch (domain) {
    case 'light': return 'Lights';
    case 'fan': return 'Fans';
    case 'cover': return 'Shades';
    case 'climate': return 'Climate';
    case 'scene': return 'Scenes';
    case 'script': return 'Scripts';
    case 'input_boolean': return 'Helpers';
    case 'lock': return 'Locks';
    default: return domain;
  }
}

function groupByDomain(entities: HaEntity[]): { domain: string; items: HaEntity[] }[] {
  const map = new Map<string, HaEntity[]>();
  for (const e of entities) {
    if (!map.has(e.domain)) map.set(e.domain, []);
    map.get(e.domain)!.push(e);
  }
  const order = ['light', 'fan', 'cover', 'climate', 'scene', 'script', 'input_boolean', 'lock'];
  return order
    .filter((d) => map.has(d))
    .map((d) => ({ domain: d, items: map.get(d)! }));
}

// ────────────────────────────────────────────────────────────────────
// Icons (inline SVG — one per domain, stroke-based to match kiosk theme)
// ────────────────────────────────────────────────────────────────────

function DomainIcon({ domain, size = 24 }: { domain: string; size?: number }) {
  const props = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.5, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const };
  switch (domain) {
    case 'light':
      // Incandescent bulb with filament loops — reads as a light even at small sizes
      return (
        <svg {...props}>
          <path d="M9 18h6" />
          <path d="M10 21h4" />
          <path d="M12 3a6 6 0 0 0-4 10.5c.6.6 1 1.3 1.2 2L9.5 18h5l.3-2.5c.2-.7.6-1.4 1.2-2A6 6 0 0 0 12 3z" />
          <path d="M10 12c.7-.8 1.3-.8 2 0s1.3.8 2 0" />
        </svg>
      );
    case 'switch':
      return (<svg {...props}><rect x="3" y="8" width="18" height="8" rx="4" /><circle cx="16" cy="12" r="2.5" fill="currentColor" /></svg>);
    case 'fan':
      // Four-blade ceiling fan — blades curve out from a central hub
      return (
        <svg {...props}>
          <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
          <path d="M12 10.4c0-3 -1.5-5 -4-5.5 -0.6 2 0 4.5 4 5.5z" />
          <path d="M13.6 12c3 0 5-1.5 5.5-4 -2-.6-4.5 0-5.5 4z" />
          <path d="M12 13.6c0 3 1.5 5 4 5.5 .6-2 0-4.5-4-5.5z" />
          <path d="M10.4 12c-3 0-5 1.5-5.5 4 2 .6 4.5 0 5.5-4z" />
        </svg>
      );
    case 'cover':
      return (<svg {...props}><rect x="4" y="3" width="16" height="18" rx="1" /><path d="M4 9h16M4 15h16" /></svg>);
    case 'climate':
      return (<svg {...props}><path d="M12 3v18" /><circle cx="12" cy="17" r="3" /><path d="M9 7l3-3 3 3" /></svg>);
    case 'media_player':
      return (<svg {...props}><rect x="3" y="5" width="18" height="12" rx="2" /><path d="M10 9l4 3-4 3z" fill="currentColor" stroke="none" /></svg>);
    case 'scene':
      return (<svg {...props}><path d="M12 2l2.4 5 5.6.6-4 4 1 5.6L12 14.8 7 17l1-5.6-4-4 5.6-.6z" /></svg>);
    case 'script':
      return (<svg {...props}><path d="M8 4h10a2 2 0 0 1 2 2v14H8z" /><path d="M4 4h4v16" /><path d="M11 9h6M11 13h6M11 17h4" /></svg>);
    case 'lock':
      return (<svg {...props}><rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" /></svg>);
    default:
      return (<svg {...props}><circle cx="12" cy="12" r="9" /></svg>);
  }
}

// ────────────────────────────────────────────────────────────────────
// Entity card — per-domain control surface
// ────────────────────────────────────────────────────────────────────

function EntityCard({
  entity,
  onChanged,
  onOpenBrightness,
  onOpenFan,
}: {
  entity: HaEntity;
  onChanged: () => void;
  onOpenBrightness?: (entity: HaEntity) => void;
  onOpenFan?: (entity: HaEntity) => void;
}) {
  const on = isOn(entity.state);

  const brightnessFromAttrs = (() => {
    const raw = entity.attributes['brightness'];
    if (typeof raw === 'number') return Math.max(1, Math.round((raw / 255) * 100));
    return 0;
  })();

  const tempUnit = (entity.attributes['temperature_unit'] as string) || '°F';
  const currentTemp = entity.attributes['current_temperature'] as number | undefined;
  const targetTemp = entity.attributes['temperature'] as number | undefined;
  const hvacMode = entity.state || '';

  const coverPosition = (() => {
    const raw = entity.attributes['current_position'];
    if (typeof raw === 'number') return raw;
    return on ? 100 : 0;
  })();

  const fire = useCallback(async (domain: string, service: string, data: Record<string, unknown>) => {
    const ok = await callService(domain, service, { entity_id: entity.entity_id, ...data });
    if (ok) {
      // HA needs a beat to settle before the new state reads back.
      setTimeout(onChanged, 400);
    }
  }, [entity.entity_id, onChanged]);

  // ── Light ──
  // Card is a centered icon+name with a toggle underneath. The card
  // background itself fills from the bottom with a yellow gradient whose
  // height equals the current brightness — the gradient IS the readout, so
  // we don't show a percentage number. Tap the card body to open the
  // dimmer sheet; tap the toggle to flip power without entering the sheet.
  if (entity.domain === 'light') {
    // Tiles are always openable as long as the light supports dimming — the
    // sheet's slider doubles as both the on/off paddle and the dim control,
    // so tapping an off-but-dimmable tile still makes sense.
    const supportsDimming = typeof entity.attributes['brightness'] === 'number' || entity.attributes['supported_color_modes'] !== undefined;
    const canOpen = supportsDimming && !!onOpenBrightness;
    const openCard = () => { if (canOpen) onOpenBrightness!(entity); };
    const fillPct = on ? brightnessFromAttrs : 0;
    return (
      <div
        className={`sh-tile sh-tile--light ${on ? 'sh-tile--on' : ''} ${canOpen ? 'sh-tile--openable' : ''}`}
        role={canOpen ? 'button' : undefined}
        tabIndex={canOpen ? 0 : -1}
        aria-label={canOpen ? `${entity.name} — open light controls` : entity.name}
        aria-disabled={!canOpen}
        onClick={openCard}
        onKeyDown={(e) => {
          if (!canOpen) return;
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          openCard();
        }}
      >
        <div className="sh-tile__fill" style={{ height: `${fillPct}%` }} />
        <div className="sh-tile__inner">
          <DomainIcon domain="light" size={90} />
          <span className="sh-tile__name">{entity.name}</span>
        </div>
      </div>
    );
  }

  // ── Fan ──
  // Same pattern as lights: tapping the card opens a detail sheet with the
  // HA-advertised controls (speed, presets, direction, oscillate); the
  // corner pill is a dedicated on/off toggle. Fans with no controllable
  // features collapse to a plain tap-to-toggle card.
  if (entity.domain === 'fan') {
    // Always openable — the sheet's slider acts as the paddle (top = on,
    // bottom = off) and shows speed/modes once on.
    const canOpen = !!onOpenFan;

    const speedPct = (() => {
      const raw = entity.attributes['percentage'];
      return typeof raw === 'number' ? Math.round(raw) : null;
    })();

    const subState = (() => {
      if (!on) return 'Off';
      const preset = entity.attributes['preset_mode'] as string | undefined;
      if (preset) return preset;
      if (speedPct !== null) return `${speedPct}%`;
      return 'On';
    })();

    const fillPct = on ? (speedPct ?? 50) : 0;
    const openCard = () => { if (canOpen) onOpenFan!(entity); };
    return (
      <div
        className={`sh-tile sh-tile--fan ${on ? 'sh-tile--on' : ''} ${canOpen ? 'sh-tile--openable' : ''}`}
        role={canOpen ? 'button' : undefined}
        tabIndex={canOpen ? 0 : -1}
        aria-label={canOpen ? `${entity.name} — open fan controls` : entity.name}
        aria-disabled={!canOpen}
        onClick={openCard}
        onKeyDown={(e) => {
          if (!canOpen) return;
          if (e.key !== 'Enter' && e.key !== ' ') return;
          e.preventDefault();
          openCard();
        }}
      >
        <div className="sh-tile__fill" style={{ height: `${fillPct}%` }} />
        <div className="sh-tile__inner">
          <DomainIcon domain="fan" size={90} />
          <span className="sh-tile__name">{entity.name}</span>
        </div>
        {/* keep subState out of view — the gradient communicates intensity */}
        <span className="sh-sr-only">{subState}</span>
      </div>
    );
  }

  // ── Input Boolean (plain on/off helper) ──
  if (entity.domain === 'input_boolean') {
    return (
      <div className={`sh-card ${on ? 'sh-card--on' : ''}`}>
        <div className="sh-card__head">
          <button
            type="button"
            className="sh-card__icon"
            onClick={() => fire(entity.domain, on ? 'turn_off' : 'turn_on', {})}
          >
            <DomainIcon domain={entity.domain} size={28} />
          </button>
          <div className="sh-card__meta">
            <span className="sh-card__name">{entity.name}</span>
            <span className="sh-card__state">{on ? 'On' : 'Off'}</span>
          </div>
        </div>
      </div>
    );
  }

  // ── Cover ──
  if (entity.domain === 'cover') {
    return (
      <div className={`sh-card ${on ? 'sh-card--on' : ''}`}>
        <div className="sh-card__head">
          <div className="sh-card__icon sh-card__icon--static"><DomainIcon domain="cover" size={28} /></div>
          <div className="sh-card__meta">
            <span className="sh-card__name">{entity.name}</span>
            <span className="sh-card__state">{coverPosition}% open</span>
          </div>
        </div>
        <div className="sh-card__row">
          <button className="sh-btn" onClick={() => fire('cover', 'open_cover', {})}>Open</button>
          <button className="sh-btn" onClick={() => fire('cover', 'close_cover', {})}>Close</button>
          <button className="sh-btn sh-btn--ghost" onClick={() => fire('cover', 'stop_cover', {})}>Stop</button>
        </div>
      </div>
    );
  }

  // ── Climate ──
  if (entity.domain === 'climate') {
    const stepTemp = (delta: number) => {
      if (typeof targetTemp !== 'number') return;
      fire('climate', 'set_temperature', { temperature: targetTemp + delta });
    };
    return (
      <div className="sh-card sh-card--on sh-card--wide">
        <div className="sh-card__head">
          <div className="sh-card__icon sh-card__icon--static"><DomainIcon domain="climate" size={28} /></div>
          <div className="sh-card__meta">
            <span className="sh-card__name">{entity.name}</span>
            <span className="sh-card__state">
              {typeof currentTemp === 'number' ? `${Math.round(currentTemp)}${tempUnit}` : '—'} · {hvacMode}
            </span>
          </div>
        </div>
        <div className="sh-card__row">
          <button className="sh-btn sh-btn--icon" onClick={() => stepTemp(-1)}>−</button>
          <div className="sh-card__bigtemp">
            {typeof targetTemp === 'number' ? `${Math.round(targetTemp)}${tempUnit}` : '—'}
          </div>
          <button className="sh-btn sh-btn--icon" onClick={() => stepTemp(+1)}>+</button>
        </div>
      </div>
    );
  }

  // ── Scene / Script — fire-and-forget ──
  if (entity.domain === 'scene' || entity.domain === 'script') {
    const service = entity.domain === 'scene' ? 'turn_on' : 'turn_on';
    return (
      <button
        type="button"
        className="sh-card sh-card--action"
        onClick={() => fire(entity.domain, service, {})}
      >
        <div className="sh-card__icon sh-card__icon--static"><DomainIcon domain={entity.domain} size={28} /></div>
        <span className="sh-card__name">{entity.name}</span>
      </button>
    );
  }

  // ── Lock ──
  if (entity.domain === 'lock') {
    const locked = entity.state === 'locked';
    return (
      <div className={`sh-card ${!locked ? 'sh-card--on' : ''}`}>
        <div className="sh-card__head">
          <button
            type="button"
            className="sh-card__icon"
            onClick={() => fire('lock', locked ? 'unlock' : 'lock', {})}
          >
            <DomainIcon domain="lock" size={28} />
          </button>
          <div className="sh-card__meta">
            <span className="sh-card__name">{entity.name}</span>
            <span className="sh-card__state">{locked ? 'Locked' : 'Unlocked'}</span>
          </div>
        </div>
      </div>
    );
  }

  return null;
}

// ────────────────────────────────────────────────────────────────────
// Power toggle — pill switch used at the top of detail sheets
// ────────────────────────────────────────────────────────────────────

function PowerToggle({
  on,
  onToggle,
  variant = 'light',
}: {
  on: boolean;
  onToggle: () => void;
  variant?: 'light' | 'fan';
}) {
  return (
    <button
      type="button"
      className={`sh-power sh-power--${variant} ${on ? 'sh-power--on' : ''}`}
      aria-label={on ? 'Turn off' : 'Turn on'}
      aria-pressed={on}
      onClick={(e) => {
        e.stopPropagation();
        onToggle();
      }}
    >
      <span className="sh-power__knob" />
    </button>
  );
}

// ────────────────────────────────────────────────────────────────────
// Fan sheet — paddle slider (top on / bottom off) + preset modes
// ────────────────────────────────────────────────────────────────────

function FanSheet({
  entity,
  onClose,
  onChanged,
}: {
  entity: HaEntity;
  onClose: () => void;
  onChanged: () => void;
}) {
  const on = isOn(entity.state);
  const attrSpeed = (() => {
    if (!on) return 0;
    const raw = entity.attributes['percentage'];
    return typeof raw === 'number' ? Math.max(1, Math.round(raw)) : 100;
  })();
  const presetModes = Array.isArray(entity.attributes['preset_modes'])
    ? (entity.attributes['preset_modes'] as string[])
    : [];
  const currentPreset = (entity.attributes['preset_mode'] as string | undefined) || '';

  const [pct, setPct] = useState<number>(attrSpeed);
  const draggingRef = useRef(false);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!draggingRef.current) setPct(attrSpeed);
  }, [attrSpeed]);

  const pctFromPointer = useCallback((clientY: number): number => {
    const el = barRef.current;
    if (!el) return pct;
    const rect = el.getBoundingClientRect();
    const ratio = 1 - (clientY - rect.top) / rect.height;
    return Math.max(0, Math.min(100, Math.round(ratio * 100)));
  }, [pct]);

  const commitPaddle = useCallback(async (value: number) => {
    if (value <= 0) {
      await callService('fan', 'turn_off', { entity_id: entity.entity_id });
    } else {
      await callService('fan', 'set_percentage', {
        entity_id: entity.entity_id,
        percentage: value,
      });
    }
    setTimeout(onChanged, 400);
  }, [entity.entity_id, onChanged]);

  const commitPreset = useCallback(async (mode: string) => {
    await callService('fan', 'set_preset_mode', {
      entity_id: entity.entity_id,
      preset_mode: mode,
    });
    setTimeout(onChanged, 400);
  }, [entity.entity_id, onChanged]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    draggingRef.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setPct(pctFromPointer(e.clientY));
  }, [pctFromPointer]);
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    setPct(pctFromPointer(e.clientY));
  }, [pctFromPointer]);
  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    const final = pctFromPointer(e.clientY);
    setPct(final);
    commitPaddle(final);
  }, [pctFromPointer, commitPaddle]);

  return (
    <div
      className="sh-bright sh-bright--fan"
      onClick={(e) => {
        e.stopPropagation();
        if (!isInteractiveTarget(e.target)) onClose();
      }}
      onTouchEnd={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
    >
      <div className="sh-bright__panel">
        <h3 className="sh-bright__title">{entity.name}</h3>
        <span className="sh-bright__pct">{pct}%</span>
        <div
          ref={barRef}
          className="sh-bright__bar sh-bright__bar--fan"
          role="slider"
          aria-label="Speed"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <div className="sh-bright__fill sh-bright__fill--fan" style={{ height: `${pct}%` }} />
          <div className="sh-bright__handle" style={{ bottom: `calc(${pct}% - 14px)` }} />
          <svg
            className="sh-bright__sun sh-bright__sun--fan"
            width="24" height="24" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="1.6" fill="currentColor" stroke="none" />
            <path d="M12 10.4c0-3 -1.5-5 -4-5.5 -0.6 2 0 4.5 4 5.5z" />
            <path d="M13.6 12c3 0 5-1.5 5.5-4 -2-.6-4.5 0-5.5 4z" />
            <path d="M12 13.6c0 3 1.5 5 4 5.5 .6-2 0-4.5-4-5.5z" />
            <path d="M10.4 12c-3 0-5 1.5-5.5 4 2 .6 4.5 0 5.5-4z" />
          </svg>
        </div>
        <PowerToggle
          on={on}
          variant="fan"
          onToggle={async () => {
            if (on) {
              await callService('fan', 'turn_off', { entity_id: entity.entity_id });
            } else {
              await callService('fan', 'turn_on', { entity_id: entity.entity_id });
            }
            setTimeout(onChanged, 400);
          }}
        />

        {presetModes.length > 0 && (
          <div className="sh-bright__modes">
            {presetModes.map((mode) => {
              const active = mode === currentPreset;
              return (
                <button
                  key={mode}
                  type="button"
                  className={`sh-bright__mode ${active ? 'sh-bright__mode--active' : ''}`}
                  onClick={() => commitPreset(mode)}
                >
                  {mode}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Brightness sheet — vertical fill bar, Hue-style
// ────────────────────────────────────────────────────────────────────

function BrightnessSheet({
  entity,
  onClose,
  onChanged,
}: {
  entity: HaEntity;
  onClose: () => void;
  onChanged: () => void;
}) {
  const on = isOn(entity.state);
  // When the light is off, the slider reads as a paddle at the bottom. When
  // on, it reflects the current brightness. Dragging to the bottom (0) is
  // the off gesture; anywhere above 0 is the on gesture at that level.
  const attrPct = (() => {
    if (!on) return 0;
    const raw = entity.attributes['brightness'];
    if (typeof raw === 'number') return Math.max(1, Math.round((raw / 255) * 100));
    return 100;
  })();

  const [pct, setPct] = useState<number>(attrPct);
  const draggingRef = useRef(false);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!draggingRef.current) setPct(attrPct);
  }, [attrPct]);

  const pctFromPointer = useCallback((clientY: number): number => {
    const el = barRef.current;
    if (!el) return pct;
    const rect = el.getBoundingClientRect();
    const ratio = 1 - (clientY - rect.top) / rect.height;
    return Math.max(0, Math.min(100, Math.round(ratio * 100)));
  }, [pct]);

  const commit = useCallback(async (value: number) => {
    if (value <= 0) {
      await callService('light', 'turn_off', { entity_id: entity.entity_id });
    } else {
      await callService('light', 'turn_on', {
        entity_id: entity.entity_id,
        brightness_pct: value,
      });
    }
    setTimeout(onChanged, 400);
  }, [entity.entity_id, onChanged]);

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    draggingRef.current = true;
    (e.target as Element).setPointerCapture?.(e.pointerId);
    setPct(pctFromPointer(e.clientY));
  }, [pctFromPointer]);

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    setPct(pctFromPointer(e.clientY));
  }, [pctFromPointer]);

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    const final = pctFromPointer(e.clientY);
    setPct(final);
    commit(final);
  }, [pctFromPointer, commit]);

  return (
    <div
      className="sh-bright"
      onClick={(e) => {
        e.stopPropagation();
        if (!isInteractiveTarget(e.target)) onClose();
      }}
      onTouchEnd={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
    >
      <div className="sh-bright__panel">
        <h3 className="sh-bright__title">{entity.name}</h3>
        <span className="sh-bright__pct">{pct}%</span>
        <div
          ref={barRef}
          className="sh-bright__bar"
          role="slider"
          aria-label="Brightness"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={pct}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={onPointerUp}
          onPointerCancel={onPointerUp}
        >
          <div className="sh-bright__fill" style={{ height: `${pct}%` }} />
          <div className="sh-bright__handle" style={{ bottom: `calc(${pct}% - 14px)` }} />
          {/* Little sunshine glyph anchored at the bottom of the bar — a
              visual "light" icon so the orientation reads instantly. */}
          <svg
            className="sh-bright__sun"
            width="24" height="24" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" strokeWidth="1.8"
            strokeLinecap="round" strokeLinejoin="round"
            aria-hidden="true"
          >
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5.6 5.6l1.4 1.4M17 17l1.4 1.4M5.6 18.4l1.4-1.4M17 7l1.4-1.4" />
          </svg>
        </div>
        <PowerToggle
          on={on}
          variant="light"
          onToggle={async () => {
            if (on) {
              await callService('light', 'turn_off', { entity_id: entity.entity_id });
            } else {
              await callService('light', 'turn_on', { entity_id: entity.entity_id });
            }
            setTimeout(onChanged, 400);
          }}
        />
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Main modal
// ────────────────────────────────────────────────────────────────────

export function SmartHomeModal({ open, onClose, defaultAreaId }: SmartHomeModalProps) {
  const [areas, setAreas] = useState<HaArea[]>([]);
  const [activeAreaId, setActiveAreaId] = useState<string>('');
  const [entities, setEntities] = useState<HaEntity[]>([]);
  const [loading, setLoading] = useState(false);
  const [entered, setEntered] = useState(false);
  const [roomPickerOpen, setRoomPickerOpen] = useState(false);
  const [brightnessEntity, setBrightnessEntity] = useState<HaEntity | null>(null);
  const [fanEntity, setFanEntity] = useState<HaEntity | null>(null);
  // Which category is drilled into (null = category landing view).
  const [activeCategory, setActiveCategory] = useState<string | null>(null);
  const inFlightRef = useRef<AbortController | null>(null);

  // Fetch areas once
  useEffect(() => {
    if (!open) return;
    apiFetch('/api/ha/areas')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return;
        if (Array.isArray(data.areas)) setAreas(data.areas);
      })
      .catch(() => {});
  }, [open]);

  // Pick the active area once per open cycle. Without the didInit guard the
  // effect re-runs every time activeAreaId changes (because it's in the
  // deps), which snaps the user back to defaultAreaId the instant they pick
  // a different room from the picker.
  const didInitAreaRef = useRef(false);
  useEffect(() => {
    if (!open) {
      didInitAreaRef.current = false;
      return;
    }
    if (didInitAreaRef.current) return;
    if (defaultAreaId && defaultAreaId.length > 0) {
      setActiveAreaId(defaultAreaId);
      didInitAreaRef.current = true;
    } else if (areas.length > 0) {
      setActiveAreaId(areas[0]!.id);
      didInitAreaRef.current = true;
    }
  }, [open, defaultAreaId, areas]);

  // Enter animation
  useEffect(() => {
    if (!open) { setEntered(false); return; }
    const id = window.setTimeout(() => setEntered(true), 20);
    return () => window.clearTimeout(id);
  }, [open]);

  // ESC to close — closes the brightness sheet, fan sheet, or room picker
  // first if open, otherwise the modal.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      if (brightnessEntity) setBrightnessEntity(null);
      else if (fanEntity) setFanEntity(null);
      else if (roomPickerOpen) setRoomPickerOpen(false);
      else if (activeCategory) setActiveCategory(null);
      else onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose, roomPickerOpen, brightnessEntity, fanEntity, activeCategory]);

  // Close the secondary sheets automatically when the modal closes.
  useEffect(() => {
    if (!open) {
      setRoomPickerOpen(false);
      setBrightnessEntity(null);
      setFanEntity(null);
      setActiveCategory(null);
    }
  }, [open]);

  // Drilling into a category is per-room — switching rooms should start
  // back at the category landing so the user orients themselves first.
  useEffect(() => {
    setActiveCategory(null);
  }, [activeAreaId]);

  // Keep the brightness / fan sheet entity refs in sync with the latest
  // state from the parent fetch. Without this, sliders and toggles would
  // read the stale snapshot captured when the sheet was opened.
  useEffect(() => {
    if (brightnessEntity) {
      const fresh = entities.find((e) => e.entity_id === brightnessEntity.entity_id);
      if (fresh && fresh !== brightnessEntity) setBrightnessEntity(fresh);
    }
    if (fanEntity) {
      const fresh = entities.find((e) => e.entity_id === fanEntity.entity_id);
      if (fresh && fresh !== fanEntity) setFanEntity(fresh);
    }
  }, [entities, brightnessEntity, fanEntity]);

  const loadEntities = useCallback((areaId: string) => {
    if (!areaId) { setEntities([]); return; }
    inFlightRef.current?.abort();
    const ctrl = new AbortController();
    inFlightRef.current = ctrl;
    setLoading(true);
    apiFetch(`/api/ha/area/${encodeURIComponent(areaId)}/entities`, { signal: ctrl.signal })
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (ctrl.signal.aborted) return;
        setEntities(Array.isArray(data?.entities) ? data.entities : []);
      })
      .catch(() => {})
      .finally(() => {
        if (!ctrl.signal.aborted) setLoading(false);
      });
  }, []);

  useEffect(() => {
    if (!open || !activeAreaId) return;
    loadEntities(activeAreaId);
  }, [open, activeAreaId, loadEntities]);

  // Live state — poll the active area every 2.5s while the modal is open
  // so the UI reflects out-of-band changes (other apps,
  // the HA app, automations, etc.) without requiring the user to close and
  // reopen. The sheets guard their sliders against mid-drag overwrites.
  useEffect(() => {
    if (!open || !activeAreaId) return;
    const id = window.setInterval(() => loadEntities(activeAreaId), 2500);
    // Also refresh when the tab becomes visible again — handles stale
    // state after the kiosk wakes from sleep without waiting for the
    // next interval tick.
    const onVis = () => { if (document.visibilityState === 'visible') loadEntities(activeAreaId); };
    document.addEventListener('visibilitychange', onVis);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVis);
    };
  }, [open, activeAreaId, loadEntities]);

  // Room-level power — "on" if any entity in the active area is on. Tapping
  // the header toggle calls homeassistant.turn_on / turn_off with the area
  // id, which HA expands to every supported entity in that area in one shot.
  const roomOn = entities.some((e) => isOn(e.state));
  const toggleRoom = useCallback(async () => {
    if (!activeAreaId) return;
    const service = roomOn ? 'turn_off' : 'turn_on';
    await callService('homeassistant', service, { area_id: activeAreaId });
    setTimeout(() => loadEntities(activeAreaId), 500);
  }, [activeAreaId, roomOn, loadEntities]);

  if (!open) return null;

  const grouped = groupByDomain(entities);
  const activeArea = areas.find((a) => a.id === activeAreaId);

  return createPortal(
    <>
      <style>{SMART_HOME_STYLES}</style>
      <div
        className={`sh-overlay ${entered ? 'sh-overlay--entered' : ''}`}
        onClick={(e) => {
          // React portal events bubble through the component tree, so a
          // click on the backdrop would also reach DailyKioskPage's root
          // handler and re-open the modal. Stop propagation at the overlay.
          e.stopPropagation();
          onClose();
        }}
        onTouchEnd={(e) => e.stopPropagation()}
        onPointerUp={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="Smart home control panel"
      >
        <div
          className={`sh-panel ${entered ? 'sh-panel--entered' : ''}`}
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header — large room toggle top-left, room name centered, all-
              rooms home icon top-right. Backdrop tap closes the modal. */}
          <div className="sh-header">
            {activeAreaId && activeCategory === null && (
              <button
                type="button"
                className={`sh-room-power ${roomOn ? 'sh-room-power--on' : ''}`}
                aria-label={roomOn ? 'Turn everything off' : 'Turn everything on'}
                aria-pressed={roomOn}
                onClick={toggleRoom}
              >
                <span className="sh-room-power__knob" />
              </button>
            )}
            <h2 className="sh-header__h2">{activeArea?.name || 'Select a room'}</h2>
            {areas.length > 1 && (
              <button
                className="sh-rooms-btn"
                onClick={() => setRoomPickerOpen(true)}
                aria-label="All rooms"
                title="All rooms"
              >
                <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M3 12l9-9 9 9" />
                  <path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10" />
                </svg>
              </button>
            )}
          </div>

          {/* Body — landing shows big category buttons; drilling in shows
              the entities for just that category. */}
          <div className="sh-body">
            {loading && entities.length === 0 && (
              <div className="sh-empty">Loading…</div>
            )}
            {!loading && entities.length === 0 && (
              <div className="sh-empty">
                {areas.length === 0
                  ? 'Home Assistant is not configured.'
                  : 'No controllable entities in this room.'}
              </div>
            )}

            {activeCategory === null && grouped.length > 0 && (
              <div className="sh-cats">
                {grouped.map(({ domain, items }) => {
                  // Stateful domains glow only when something is actually
                  // on — fire-once domains (scenes/scripts) always glow.
                  const stateful = !(domain === 'scene' || domain === 'script');
                  const anyOn = items.some((e) => isOn(e.state));
                  const lit = !stateful || anyOn;
                  return (
                    <button
                      key={domain}
                      type="button"
                      className={`sh-cat sh-cat--${domain} ${lit ? 'sh-cat--on' : 'sh-cat--off'}`}
                      onClick={() => setActiveCategory(domain)}
                      aria-label={domainLabel(domain)}
                    >
                      <DomainIcon domain={domain} size={220} />
                      <span className="sh-cat__label">{domainLabel(domain)}</span>
                    </button>
                  );
                })}
              </div>
            )}

          </div>
        </div>

        {/* Category drill-in sheet — a smaller centered overlay that sits
            above the room panel. Tapping the backdrop closes it and
            returns to the category landing, so no back button is needed. */}
        {activeCategory !== null && (() => {
          const group = grouped.find((g) => g.domain === activeCategory);
          const items = group?.items ?? [];
          return (
            <div
              className="sh-catview"
              onClick={(e) => {
                // Always stop propagation so non-closing clicks (e.g. a tile
                // tap) don't bubble to the main overlay which would close
                // the whole modal. Close the category only when the click
                // missed every interactive target.
                e.stopPropagation();
                if (!isInteractiveTarget(e.target)) setActiveCategory(null);
              }}
              onTouchEnd={(e) => e.stopPropagation()}
              onPointerUp={(e) => e.stopPropagation()}
            >
              <div className="sh-catview__panel">
                <div className={`sh-grid ${activeCategory === 'scene' || activeCategory === 'script' ? 'sh-grid--actions' : ''}`}>
                  {items.map((ent) => (
                    <EntityCard
                      key={ent.entity_id}
                      entity={ent}
                      onChanged={() => loadEntities(activeAreaId)}
                      onOpenBrightness={setBrightnessEntity}
                      onOpenFan={setFanEntity}
                    />
                  ))}
                </div>
              </div>
            </div>
          );
        })()}

        {/* Brightness sheet — overlays the panel with a large vertical fill
            bar. Used by light cards when the user taps the card body. */}
        {brightnessEntity && (
          <BrightnessSheet
            entity={brightnessEntity}
            onClose={() => setBrightnessEntity(null)}
            onChanged={() => loadEntities(activeAreaId)}
          />
        )}

        {/* Fan sheet — exposes speed / preset / direction / oscillate per
            HA's advertised capabilities on the entity. */}
        {fanEntity && (
          <FanSheet
            entity={fanEntity}
            onClose={() => setFanEntity(null)}
            onChanged={() => loadEntities(activeAreaId)}
          />
        )}

        {/* Room picker — lightweight overlay layered on top of the main
            panel. Tapping its backdrop closes it without dismissing the
            main modal. Tapping a room switches and closes the picker. */}
        {roomPickerOpen && (
          <div
            className={`sh-picker ${entered ? 'sh-picker--entered' : ''}`}
            onClick={(e) => {
              // Close when the user taps the backdrop (not the panel).
              if (e.target === e.currentTarget) {
                e.stopPropagation();
                setRoomPickerOpen(false);
              }
            }}
            onTouchEnd={(e) => e.stopPropagation()}
            onPointerUp={(e) => e.stopPropagation()}
          >
            <div className="sh-picker__panel" onClick={(e) => e.stopPropagation()}>
              <h3 className="sh-picker__title">Choose a room</h3>
              <div className="sh-picker__grid">
                {areas.map((a) => {
                  const active = a.id === activeAreaId;
                  return (
                    <button
                      key={a.id}
                      className={`sh-picker__room ${active ? 'sh-picker__room--active' : ''}`}
                      onClick={() => {
                        setActiveAreaId(a.id);
                        setRoomPickerOpen(false);
                      }}
                    >
                      {a.name}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </>,
    document.body,
  );
}

// ────────────────────────────────────────────────────────────────────
// Styles — injected with the modal so it can live anywhere
// ────────────────────────────────────────────────────────────────────

const SMART_HOME_STYLES = `
.sh-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  background: rgba(0, 0, 0, 0.35);
  backdrop-filter: blur(18px);
  -webkit-backdrop-filter: blur(18px);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.22s ease-out;
  font-family: 'Amazon Ember', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
}
.sh-overlay--entered { opacity: 1; }

.sh-panel {
  position: relative;
  width: min(1280px, calc(100% - clamp(32px, 6vw, 96px)));
  /* Always a substantial presence — fills most of the viewport even when the
     room has a single light. Capped just under full-height to reveal the
     blurred wallpaper at the top and bottom. */
  height: min(860px, calc(100dvh - clamp(48px, 8vh, 96px)));
  background: linear-gradient(180deg, rgba(22, 26, 34, 0.72) 0%, rgba(14, 16, 22, 0.72) 100%);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 24px;
  box-shadow: 0 30px 80px rgba(0, 0, 0, 0.55), 0 2px 12px rgba(0, 0, 0, 0.4);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  transform: translateY(16px) scale(0.985);
  transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1);
}
.sh-panel--entered { transform: translateY(0) scale(1); }

.sh-header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: clamp(24px, 3.2vh, 36px) clamp(120px, 12vw, 160px);
  border-bottom: 1px solid rgba(255, 255, 255, 0.06);
  text-align: center;
  flex-shrink: 0;
}
.sh-header__h2 {
  margin: 0;
  font-size: clamp(28px, 3vw, 42px);
  font-weight: 500;
  color: #fff;
  letter-spacing: -0.3px;
}
.sh-rooms-btn {
  position: absolute;
  top: 50%;
  right: clamp(18px, 2vw, 28px);
  transform: translateY(-50%);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
  padding: 0;
}
.sh-rooms-btn:hover {
  background: rgba(255, 255, 255, 0.14);
  color: #fff;
  transform: translateY(-50%) scale(1.04);
}

/* Room-level master power pill — large paddle in the top-left of the
   main header. Reflects whether anything in the current area is on; a
   single tap hits homeassistant.turn_on / turn_off with the area_id. */
.sh-room-power {
  position: absolute;
  top: 50%;
  left: clamp(18px, 2vw, 28px);
  transform: translateY(-50%);
  width: 88px;
  height: 44px;
  padding: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.14);
  cursor: pointer;
  transition: all 0.2s cubic-bezier(0.2, 0.9, 0.25, 1);
  display: inline-flex;
  align-items: center;
  font-family: inherit;
}
.sh-room-power:hover { background: rgba(255, 255, 255, 0.16); }
.sh-room-power__knob {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: #fff;
  margin-left: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.45);
  transition: margin-left 0.2s cubic-bezier(0.2, 0.9, 0.25, 1), background 0.2s ease;
}
.sh-room-power--on {
  background: linear-gradient(180deg, rgba(255, 238, 110, 0.95) 0%, rgba(255, 210, 60, 0.88) 100%);
  border-color: rgba(255, 232, 110, 0.7);
  box-shadow: 0 0 18px rgba(255, 225, 90, 0.5);
}
.sh-room-power--on .sh-room-power__knob {
  margin-left: 48px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.35), 0 0 10px rgba(255, 255, 255, 0.85);
}

/* Category entities view — chromeless. The dimmed backdrop is the only
   surface; the entity tiles float on it, centered. Tap the backdrop
   (anywhere outside the tiles) to close. */
.sh-catview {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.62);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2050;
  animation: sh-bright-fade 0.18s ease-out;
}
.sh-catview__panel {
  width: auto;
  max-width: calc(100% - 64px);
  max-height: calc(100dvh - 96px);
  padding: 0;
  background: none;
  border: none;
  box-shadow: none;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: auto;
}
.sh-catview__panel .sh-grid {
  margin: auto;
  gap: clamp(18px, 2vw, 28px);
}

/* Brightness sheet — chromeless. The slider, percentage, title, and power
   toggle float on a deeply-dimmed backdrop. Tapping outside closes the
   sheet and returns to the category view. */
.sh-bright {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.72);
  backdrop-filter: blur(22px);
  -webkit-backdrop-filter: blur(22px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 2100;
  animation: sh-bright-fade 0.22s ease-out;
}
@keyframes sh-bright-fade { from { opacity: 0; } to { opacity: 1; } }
.sh-bright__panel {
  position: relative;
  width: auto;
  max-width: calc(100% - 32px);
  padding: 0;
  background: none;
  border: none;
  border-radius: 0;
  box-shadow: none;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 20px;
}
.sh-bright__title {
  margin: 0;
  font-size: clamp(22px, 2.6vw, 32px);
  font-weight: 500;
  color: #fff;
  text-align: center;
  max-width: min(520px, 90vw);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.6);
}

/* Power pill — sits in the detail sheet header, inline with the title */
.sh-power {
  position: relative;
  width: 64px;
  height: 34px;
  padding: 0;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.12);
  border: 1px solid rgba(255, 255, 255, 0.14);
  cursor: pointer;
  transition: all 0.18s cubic-bezier(0.2, 0.9, 0.25, 1);
  display: inline-flex;
  align-items: center;
  font-family: inherit;
  flex-shrink: 0;
}
.sh-power:hover { background: rgba(255, 255, 255, 0.18); }
.sh-power__knob {
  width: 26px;
  height: 26px;
  border-radius: 50%;
  background: #fff;
  margin-left: 3px;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
  transition: margin-left 0.18s cubic-bezier(0.2, 0.9, 0.25, 1), background 0.18s ease;
}
.sh-power--light.sh-power--on {
  background: linear-gradient(180deg, rgba(255, 238, 110, 0.95) 0%, rgba(255, 210, 60, 0.85) 100%);
  border-color: rgba(255, 232, 110, 0.7);
  box-shadow: 0 0 14px rgba(255, 225, 90, 0.45);
}
.sh-power--fan.sh-power--on {
  background: linear-gradient(180deg, rgba(120, 195, 255, 0.95) 0%, rgba(60, 140, 230, 0.88) 100%);
  border-color: rgba(130, 195, 255, 0.7);
  box-shadow: 0 0 14px rgba(100, 170, 250, 0.45);
}
.sh-power--on .sh-power__knob {
  margin-left: 33px;
  background: #fff;
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35), 0 0 8px rgba(255, 255, 255, 0.7);
}
.sh-bright__pct {
  font-size: 13px;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.65);
  letter-spacing: 0.08em;
  line-height: 1;
  text-shadow: 0 1px 6px rgba(0, 0, 0, 0.55);
}
.sh-bright__bar {
  position: relative;
  width: 180px;
  height: clamp(260px, 44vh, 380px);
  border-radius: 28px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  overflow: hidden;
  cursor: pointer;
  touch-action: none;
  user-select: none;
  -webkit-user-select: none;
}
.sh-bright__sun {
  position: absolute;
  left: 50%;
  bottom: 16px;
  transform: translateX(-50%);
  color: rgba(255, 248, 210, 0.95);
  pointer-events: none;
  mix-blend-mode: screen;
  filter: drop-shadow(0 0 6px rgba(0, 0, 0, 0.35));
  z-index: 2;
}
.sh-bright__fill {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  background: linear-gradient(180deg, rgba(255, 240, 110, 0.95) 0%, rgba(255, 210, 60, 0.85) 100%);
  box-shadow: 0 -8px 32px rgba(255, 220, 90, 0.55) inset;
  transition: height 0.06s linear;
  pointer-events: none;
}
.sh-bright__handle {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
  width: 96px;
  height: 5px;
  border-radius: 5px;
  background: rgba(255, 255, 255, 0.8);
  box-shadow: 0 0 14px rgba(255, 250, 200, 0.7);
  pointer-events: none;
  transition: bottom 0.06s linear;
}

/* Fan sheet reuses the brightness sheet layout (vertical bar), recolored
   blue. Modes render as a row of chips under the bar. */
.sh-bright__fill--fan {
  background: linear-gradient(180deg, rgba(120, 200, 255, 0.95) 0%, rgba(50, 130, 235, 0.85) 100%);
  box-shadow: 0 -8px 32px rgba(90, 170, 250, 0.55) inset;
}
.sh-bright__sun--fan { color: #eaf4ff; }

.sh-bright__modes {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 8px;
  margin-top: 4px;
}
.sh-bright__mode {
  padding: 10px 18px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.78);
  font-size: 14px;
  font-weight: 500;
  letter-spacing: 0.04em;
  text-transform: capitalize;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
}
.sh-bright__mode:hover {
  background: rgba(255, 255, 255, 0.1);
  color: #fff;
}
.sh-bright__mode--active {
  background: linear-gradient(180deg, rgba(120, 195, 255, 0.28) 0%, rgba(60, 140, 230, 0.14) 100%);
  border-color: rgba(120, 195, 255, 0.55);
  color: #e7f1ff;
  box-shadow: 0 0 14px rgba(100, 170, 250, 0.22);
}

/* Room picker overlay — fixed to the viewport so it eats clicks that
   would otherwise fall through to the main overlay. */
.sh-picker {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.55);
  backdrop-filter: blur(10px);
  -webkit-backdrop-filter: blur(10px);
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 0.18s ease-out;
  z-index: 2100;
}
.sh-picker--entered { opacity: 1; }
.sh-picker__panel {
  width: min(720px, calc(100% - 32px));
  max-height: calc(100% - 64px);
  padding: clamp(20px, 2.6vh, 28px) clamp(22px, 2.6vw, 32px);
  background: linear-gradient(180deg, rgba(28, 32, 42, 0.98) 0%, rgba(18, 20, 28, 0.98) 100%);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  box-shadow: 0 24px 60px rgba(0, 0, 0, 0.55);
  overflow: auto;
}
.sh-picker__title {
  margin: 0 0 18px;
  font-size: 18px;
  font-weight: 500;
  color: rgba(255, 255, 255, 0.75);
  text-align: center;
  letter-spacing: 0.05em;
}
.sh-picker__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.sh-picker__room {
  padding: 16px 12px;
  border-radius: 14px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
  font-family: inherit;
  min-height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
}
.sh-picker__room:hover {
  background: rgba(255, 255, 255, 0.09);
  color: #fff;
}
.sh-picker__room--active {
  background: rgba(120, 200, 255, 0.16);
  border-color: rgba(120, 200, 255, 0.4);
  color: #fff;
}

.sh-body {
  flex: 1;
  padding: clamp(16px, 2.2vh, 24px) clamp(22px, 2.6vw, 32px) clamp(22px, 3vh, 36px);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: clamp(16px, 2.4vh, 26px);
  min-height: 0;
}

.sh-empty {
  padding: 48px 24px;
  text-align: center;
  color: rgba(255, 255, 255, 0.45);
  font-size: 16px;
}

/* ── Category landing ─────────────────────────────────────────────
   No outer container on each button — just a large icon with the
   category name underneath. The landing fills the body vertically and
   horizontally so categories sit at the visual center of the room. */
.sh-cats {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: center;
  align-content: center;
  gap: clamp(60px, 9vw, 140px);
  width: 100%;
  padding: clamp(32px, 5vh, 64px) 0;
}
.sh-cat {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 16px;
  padding: 8px;
  background: none;
  border: none;
  cursor: pointer;
  color: rgba(255, 255, 255, 0.88);
  font: inherit;
  font-family: inherit;
  transition: transform 0.15s ease, color 0.15s ease;
}
.sh-cat:hover { transform: scale(1.04); color: #fff; }
.sh-cat:active { transform: scale(0.97); }
.sh-cat svg {
  color: rgba(255, 255, 255, 0.45);
  filter: drop-shadow(0 2px 10px rgba(0, 0, 0, 0.45));
  transition: filter 0.25s ease, color 0.25s ease;
}
.sh-cat__label {
  font-size: clamp(24px, 2.6vw, 36px);
  font-weight: 500;
  letter-spacing: 0.2px;
  color: rgba(255, 255, 255, 0.7);
  text-shadow: 0 2px 10px rgba(0, 0, 0, 0.5);
  transition: color 0.25s ease;
}
.sh-cat--on .sh-cat__label { color: #fff; }

/* Per-domain accent glow only when the category is active (something on). */
.sh-cat--on.sh-cat--light svg {
  color: #fff0a8;
  filter: drop-shadow(0 0 28px rgba(255, 225, 90, 0.55));
}
.sh-cat--on.sh-cat--fan svg {
  color: #cfe6ff;
  filter: drop-shadow(0 0 28px rgba(110, 180, 255, 0.55));
}
.sh-cat--on.sh-cat--cover svg,
.sh-cat--on.sh-cat--climate svg {
  color: #dfeff6;
  filter: drop-shadow(0 0 20px rgba(160, 200, 220, 0.35));
}
.sh-cat--on.sh-cat--scene svg,
.sh-cat--on.sh-cat--script svg {
  color: #efe2ff;
  filter: drop-shadow(0 0 24px rgba(180, 130, 240, 0.45));
}
.sh-cat--on.sh-cat--input_boolean svg,
.sh-cat--on.sh-cat--lock svg {
  color: rgba(255, 255, 255, 0.95);
  filter: drop-shadow(0 2px 10px rgba(0, 0, 0, 0.45));
}

/* ── Subnav (when drilled into a category) ──────────────────────── */
.sh-subnav {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  gap: 12px;
  width: 100%;
  padding-bottom: 6px;
}
.sh-subnav__title {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  margin: 0;
  font-size: 16px;
  font-weight: 500;
  color: #fff;
  justify-self: center;
}
.sh-subnav__title svg { color: rgba(255, 255, 255, 0.7); }
.sh-subnav__spacer { justify-self: end; }
.sh-back {
  justify-self: start;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 14px 10px 10px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: rgba(255, 255, 255, 0.85);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
  font-family: inherit;
}
.sh-back:hover { background: rgba(255, 255, 255, 0.12); color: #fff; }

.sh-section { display: flex; flex-direction: column; gap: 14px; align-items: center; }
.sh-section__title {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  margin: 0;
  font-size: 13px;
  font-weight: 500;
  letter-spacing: 0.14em;
  text-transform: uppercase;
  color: rgba(255, 255, 255, 0.5);
  text-align: center;
}
.sh-section__title svg { color: rgba(255, 255, 255, 0.4); }
.sh-section__count {
  margin-left: 4px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.05em;
  padding: 2px 8px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.55);
}

/* Tile grid — flex wrap centered horizontally, so sparse rooms feel
   intentional instead of a lone card in the top-left. */
.sh-grid {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  gap: 14px;
  width: 100%;
}
.sh-grid--actions { gap: 12px; }

.sh-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 16px;
  padding: 18px 18px 16px;
  border-radius: 20px;
  background: rgba(255, 255, 255, 0.035);
  border: 1px solid rgba(255, 255, 255, 0.06);
  transition: all 0.2s ease;
  min-height: 140px;
  width: 200px;
}
.sh-card:hover { background: rgba(255, 255, 255, 0.06); }
.sh-card--on {
  background: linear-gradient(180deg, rgba(255, 238, 120, 0.2) 0%, rgba(255, 220, 70, 0.06) 100%);
  border-color: rgba(255, 232, 110, 0.38);
  box-shadow: inset 0 1px 0 rgba(255, 248, 200, 0.12), 0 0 28px rgba(255, 220, 90, 0.08);
}

/* ── Tile (light / fan) — centered icon+name, toggle below, gradient fill ── */
.sh-tile {
  position: relative;
  display: flex;
  align-items: stretch;
  justify-content: center;
  padding: 0;
  min-height: 240px;
  width: 240px;
  border-radius: 24px;
  background: rgba(255, 255, 255, 0.04);
  border: 1px solid rgba(255, 255, 255, 0.08);
  overflow: hidden;
  transition: border-color 0.2s ease, transform 0.1s ease;
  cursor: default;
  text-align: center;
  color: #fff;
  font: inherit;
  font-family: inherit;
}
.sh-tile--openable { cursor: pointer; }
.sh-tile--openable:hover { border-color: rgba(255, 255, 255, 0.14); }
.sh-tile--openable:active { transform: scale(0.99); }

/* Gradient fill layer — absolute, grows from bottom. Height driven by the
   entity percentage so the card itself communicates the level. The mask
   fades the top ~35% of the fill so it melts into the tile background
   instead of showing a hard horizontal line at the fill edge. */
.sh-tile__fill {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 0;
  height: 0;
  pointer-events: none;
  transition: height 0.35s cubic-bezier(0.16, 1, 0.3, 1);
  mask-image: linear-gradient(to top, black 0%, black 55%, rgba(0,0,0,0.55) 80%, transparent 100%);
  -webkit-mask-image: linear-gradient(to top, black 0%, black 55%, rgba(0,0,0,0.55) 80%, transparent 100%);
}
.sh-tile--light .sh-tile__fill {
  background: linear-gradient(
    180deg,
    rgba(255, 238, 110, 0.55) 0%,
    rgba(255, 216, 70, 0.42) 55%,
    rgba(255, 200, 50, 0.35) 100%
  );
  box-shadow: 0 -12px 28px rgba(255, 220, 90, 0.25) inset;
}
.sh-tile--fan .sh-tile__fill {
  background: linear-gradient(
    180deg,
    rgba(110, 190, 255, 0.5) 0%,
    rgba(70, 150, 240, 0.4) 55%,
    rgba(40, 120, 220, 0.32) 100%
  );
  box-shadow: 0 -12px 28px rgba(80, 160, 250, 0.22) inset;
}

/* Soft outer glow when the tile is on, color-coded per domain. */
.sh-tile--light.sh-tile--on {
  border-color: rgba(255, 232, 110, 0.45);
  box-shadow: 0 0 34px rgba(255, 220, 90, 0.16);
}
.sh-tile--fan.sh-tile--on {
  border-color: rgba(110, 180, 255, 0.45);
  box-shadow: 0 0 34px rgba(100, 170, 250, 0.16);
}

/* Inner content stack — icon top, name middle, toggle bottom, all centered */
.sh-tile__inner {
  position: relative;
  z-index: 1;
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 18px 14px 16px;
}
.sh-tile__inner svg {
  color: rgba(255, 255, 255, 0.75);
  transition: color 0.2s ease, filter 0.2s ease;
  filter: drop-shadow(0 2px 6px rgba(0, 0, 0, 0.35));
}
.sh-tile--light.sh-tile--on .sh-tile__inner svg {
  color: #fff8cf;
  filter: drop-shadow(0 0 10px rgba(255, 235, 120, 0.6));
}
.sh-tile--fan.sh-tile--on .sh-tile__inner svg {
  color: #e8f3ff;
  filter: drop-shadow(0 0 10px rgba(130, 190, 255, 0.6));
}
.sh-tile__name {
  font-size: 16px;
  font-weight: 500;
  color: #fff;
  letter-spacing: -0.1px;
  text-align: center;
  line-height: 1.2;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  text-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);
}

/* Visually hidden but still read by screen readers */
.sh-sr-only {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden; clip: rect(0, 0, 0, 0);
  white-space: nowrap; border: 0;
}

.sh-card--wide { width: 416px; max-width: 100%; }

.sh-card--action {
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding: 20px 12px;
  text-align: center;
  cursor: pointer;
  color: #fff;
  font: inherit;
}
.sh-card--action:hover { transform: translateY(-1px); }
.sh-card--action .sh-card__name { text-align: center; }

.sh-card__head {
  display: flex;
  align-items: center;
  gap: 14px;
}

.sh-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 64px;
  height: 64px;
  border-radius: 18px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.06);
  color: rgba(255, 255, 255, 0.85);
  cursor: pointer;
  transition: all 0.15s ease;
  flex-shrink: 0;
  font: inherit;
  padding: 0;
}
.sh-card__icon--static { cursor: default; }
.sh-card__icon:not(.sh-card__icon--static):hover { background: rgba(255, 255, 255, 0.1); }
.sh-card--on .sh-card__icon {
  background: rgba(255, 232, 100, 0.3);
  border-color: rgba(255, 232, 100, 0.55);
  color: #fff8d0;
  box-shadow: 0 0 18px rgba(255, 225, 90, 0.22);
}

.sh-card__meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.sh-card__name {
  font-size: 16px;
  font-weight: 500;
  color: #fff;
  letter-spacing: -0.1px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.sh-card__state {
  font-size: 13px;
  font-weight: 400;
  color: rgba(255, 255, 255, 0.55);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.sh-card__slider {
  width: 100%;
  height: 6px;
  -webkit-appearance: none;
  appearance: none;
  background: rgba(255, 255, 255, 0.1);
  border-radius: 999px;
  outline: none;
  cursor: pointer;
}
.sh-card__slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  border: 2px solid rgba(255, 210, 120, 0.5);
  cursor: pointer;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.4);
}
.sh-card__slider::-moz-range-thumb {
  width: 22px; height: 22px; border-radius: 50%;
  background: #fff; border: 2px solid rgba(255, 210, 120, 0.5);
  cursor: pointer; box-shadow: 0 2px 10px rgba(0, 0, 0, 0.4);
}

.sh-card__row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.sh-card__bigtemp {
  flex: 1;
  text-align: center;
  font-size: clamp(28px, 3vw, 38px);
  font-weight: 300;
  color: #fff;
  letter-spacing: -0.5px;
}

.sh-btn {
  flex: 1;
  min-height: 44px;
  padding: 10px 16px;
  border-radius: 12px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.08);
  color: #fff;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s ease;
  font-family: inherit;
}
.sh-btn:hover { background: rgba(255, 255, 255, 0.1); }
.sh-btn--ghost {
  background: transparent;
  color: rgba(255, 255, 255, 0.55);
}
.sh-btn--icon {
  flex: 0 0 48px;
  width: 48px;
  height: 48px;
  padding: 0;
  font-size: 20px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
`;

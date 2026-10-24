/**
 * v260: prod 1b4ea74's phone Home button row (AuthenticatedHomePage.tsx:296-387), same look and order: the
 * Storage pill first, then prod's buttons whose screen the minimal app has. Latest, Activity, Luma AI,
 * Library, Watchlist, Collections and Upgrades are left out until test has their screens (no
 * placeholders). v265: Rooms (prod's button, opens prod's RoomsSheet). Phones only; the kiosk has no row.
 */
import React, { useState } from 'react';
import { StoragePill } from '@/components/storage/StoragePill';
import { TvCalendarSheet } from '@/components/calendar/TvCalendarSheet';
import { RoomsSheet } from '@/components/rooms/RoomsSheet';

interface PillDef {
  label: string;
  id: string;
  icon: React.ReactNode;
}

/** Prod's NAV_PILLS in prod's order, filtered to the screens test has. */
export const HOME_PILLS: PillDef[] = [
  { label: 'TV Calendar', id: 'tv-calendar', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="18" rx="2" ry="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" />
    </svg>
  )},
  { label: 'Rooms', id: 'rooms', icon: (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
    </svg>
  )},
];

/** after: under a rail. Prod's rails end in a 28 px margin and the row keeps 20 px under it; test's rails
 * open with 28 px instead, so the row adds 28 above and gives 8 back below. */
export function HomePills({ after = false }: { after?: boolean }) {
  const [pressed, setPressed] = useState<string | null>(null);
  const [calendarOpen, setCalendarOpen] = useState(false);
  const [roomsOpen, setRoomsOpen] = useState(false);
  const open = (id: string) => { if (id === 'tv-calendar') setCalendarOpen(true); else if (id === 'rooms') setRoomsOpen(true); };

  return (
    <>
      <div style={after ? { ...row, marginTop: 28, marginBottom: -8 } : row} data-testid="home-pills">
        {/* Downloads free space: leftmost, opens the free-up-space sheet */}
        <StoragePill />
        {HOME_PILLS.map(pill => (
          <button
            key={pill.id}
            type="button"
            data-testid={`pill-${pill.id}`}
            onClick={() => open(pill.id)}
            onTouchStart={() => setPressed(pill.id)}
            onTouchEnd={() => setPressed(null)}
            onTouchCancel={() => setPressed(null)}
            style={{
              ...pillStyle,
              transform: pressed === pill.id ? 'scale(0.95)' : 'scale(1)',
              transition: pressed === pill.id ? 'transform 0.1s ease-out' : 'transform 0.25s cubic-bezier(0.34, 1.56, 0.64, 1)',
            }}
          >
            {pill.icon}
            {pill.label}
          </button>
        ))}
      </div>
      <TvCalendarSheet open={calendarOpen} onClose={() => setCalendarOpen(false)} />
      <RoomsSheet open={roomsOpen} onClose={() => setRoomsOpen(false)} />
    </>
  );
}

const row: React.CSSProperties = {
  display: 'flex', gap: 10, paddingLeft: 12, paddingRight: 12, overflowX: 'auto', paddingBottom: 8, marginBottom: 20,
  scrollbarWidth: 'none', msOverflowStyle: 'none',
};
const pillStyle: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8,
  padding: '10px 18px', borderRadius: 999,
  background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.1)',
  color: 'rgba(255,255,255,0.85)', fontSize: 14, fontWeight: 500,
  cursor: 'pointer', flexShrink: 0, fontFamily: 'inherit',
  touchAction: 'manipulation',
  outline: 'none',
};

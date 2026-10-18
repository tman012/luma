/**
 * RoomsTab — Rooms / Devices / Services accordion with CRUD.
 */

import { RoomAccordion } from './components/RoomAccordion';

export function RoomsTab() {
  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Rooms &amp; devices</h2>
      </div>

      <RoomAccordion />
    </div>
  );
}

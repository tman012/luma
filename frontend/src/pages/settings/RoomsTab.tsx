/**
 * RoomsTab — Rooms / Devices / Services accordion with CRUD.
 */

import { RoomAccordion } from './components/RoomAccordion';

export function RoomsTab() {
  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Rooms / Devices</h2>
        <p className="mt-1 text-[13px] text-white/40">
          Configure playback target devices. Supports Shield/Android TV (ADB) and
          Chromecast with Google TV (Wireless Debugging).
        </p>
      </div>

      <RoomAccordion />
    </div>
  );
}

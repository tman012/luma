/** Per-section tracker lists (owner only, luma-test builds). */
import { apiFetch } from '@/apiClient';
import { SAVE_FAILED } from '@/utils/messages';

export type Section = 'tv' | 'movies';
export interface SectionTracker {
  id: number; name: string; type_name: string;
  /** enabled: the saved switch; active: saved on and usable (what the list shows). */
  enabled: boolean; active?: boolean; global_enabled: boolean; has_category: boolean;
  budget?: { used: number; cap: number } | null;
}
export type TrackersResult = { ok: true; trackers: SectionTracker[] } | { ok: false; error: string };

export const sectionTrackersUrl = (section: Section) => `/api/settings/sections/${section}/trackers`;

async function call(url: string, init?: RequestInit): Promise<TrackersResult> {
  try {
    const res = await apiFetch(url, init);
    const data = await res.json().catch(() => ({})) as { trackers?: SectionTracker[]; error?: string };
    if (res.ok && Array.isArray(data.trackers)) return { ok: true, trackers: data.trackers };
    return { ok: false, error: data.error || SAVE_FAILED };
  } catch {
    return { ok: false, error: SAVE_FAILED };
  }
}

export const loadSectionTrackers = (section: Section) => call(sectionTrackersUrl(section));

export const saveSectionTrackers = (section: Section, trackers: SectionTracker[]) =>
  call(sectionTrackersUrl(section), {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ trackers: trackers.map(t => ({ id: t.id, enabled: t.enabled })) }),
  });

/** Why a tracker switched on here still isn't searched, if it isn't. */
export function blockedReason(t: SectionTracker, section: Section): string {
  if (!t.global_enabled) return 'Off in Trackers';
  if (!t.has_category) return section === 'tv' ? 'No TV category' : 'No Movies category';
  return '';
}

/** What the switch shows: on only when saved on and usable. */
export const shownOn = (t: SectionTracker) =>
  t.active ?? (t.enabled && t.global_enabled && t.has_category);

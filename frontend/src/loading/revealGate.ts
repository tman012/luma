/** One deadline per surface: a sheet (from the tap) or Home (from its data) reveals everything that is ready,
 *  together, once all of it is ready or SURFACE_DEADLINE_MS has passed, whichever comes first. */
export const SURFACE_DEADLINE_MS = 1000;

/** Milliseconds left on the surface deadline that started at `startedAt` (Date.now() ms). */
export function deadlineLeft(startedAt: number, now = Date.now()): number {
  return Math.max(0, SURFACE_DEADLINE_MS - (now - startedAt));
}

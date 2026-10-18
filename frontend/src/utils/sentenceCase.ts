/**
 * v173 (Designer): labels are sentence case, never upper-cased by CSS. A raw value shown as a label
 * (a category or service type such as 'movie', 'tv', 'kodi') goes through here.
 */
const KNOWN: Record<string, string> = {
  tv: 'TV', movie: 'Movie', movies: 'Movies', plex: 'Plex', kodi: 'Kodi', adb: 'ADB',
};

export function sentenceCase(value: string | null | undefined): string {
  const v = String(value ?? '').trim();
  if (!v) return '';
  const known = KNOWN[v.toLowerCase()];
  if (known) return known;
  return v.charAt(0).toUpperCase() + v.slice(1);
}

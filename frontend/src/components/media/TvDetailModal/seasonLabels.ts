/** Season / Sources naming helpers shared by TV modal layouts. */
import type { SourcesTarget } from './types';

/** "Season 3", or "Specials" for season 0. */
export function seasonNumberLabel(n: number): string {
  return n === 0 ? 'Specials' : `Season ${n}`;
}

/** The season's own name when it differs from the generic label, else null. */
export function seasonCustomName(name: string | null | undefined, n: number): string | null {
  const t = (name || '').trim();
  if (!t) return null;
  // "Season 3", "Season 03", "Series 3", "S3", "3", "Specials" are all just the number.
  const m = t.match(/^(?:season|series|s)?\s*0*(\d+)$/i);
  if (m && Number(m[1]) === n) return null;
  if (n === 0 && /^specials?$/i.test(t)) return null;
  return t;
}

/** Main title: custom name, else "Season N" / "Specials". */
export function seasonTitle(name: string | null | undefined, n: number): string {
  return seasonCustomName(name, n) ?? seasonNumberLabel(n);
}

/** Eyebrow/subtitle prefix: "Season N" only when the title is a custom name. */
export function seasonEyebrow(name: string | null | undefined, n: number): string | null {
  return seasonCustomName(name, n) ? seasonNumberLabel(n) : null;
}

/** Poster overlay label: "S1", or "SP" for specials. */
export function seasonShortLabel(n: number): string {
  return n === 0 ? 'SP' : `S${n}`;
}

const pad = (n: number) => String(n).padStart(2, '0');

/** "S09E02 - Name" for episodes; "Season 9 - Name" / "Season 9" for seasons. */
export function sourcesTargetLabel(target: SourcesTarget, name?: string | null): string {
  if (target.type === 'episode') {
    const code = `S${pad(target.season)}E${pad(target.episode ?? 0)}`;
    return name ? `${code} - ${name}` : code;
  }
  const base = seasonNumberLabel(target.season);
  const custom = seasonCustomName(name, target.season);
  return custom ? `${base} - ${custom}` : base;
}

/** Look up the episode or season name for a Sources target. */
export function sourcesTargetName(
  target: SourcesTarget | null,
  seasons: { season_number: number; name?: string | null; episodes?: { episode_number: number; name?: string | null }[] }[],
  episode?: { seasonNumber: number; episodeNumber: number; title?: string | null } | null,
): string | null {
  if (!target) return null;
  const s = seasons.find((x) => x.season_number === target.season);
  if (target.type === 'season') return s?.name ?? null;
  const fromGrid = s?.episodes?.find((e) => e.episode_number === target.episode)?.name;
  if (fromGrid) return fromGrid;
  if (episode && episode.seasonNumber === target.season && episode.episodeNumber === target.episode) {
    return episode.title ?? null;
  }
  return null;
}

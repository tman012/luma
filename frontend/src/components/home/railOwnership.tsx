/**
 * v180: prod's library ownership on Home's rails. Plex library cards are owned
 * (prod's ratingKey fast path); Trakt cards resolve through prod's batch check
 * (POST /api/check-ownership-batch, read-only). The orb is prod's getOrbColor.
 * v181: as prod, all-owned library rails show no orb; only mixed (Trakt) rails do.
 */
import { useEffect, useState } from 'react';
import { resolveOwnership, mediaKey } from '@/services/ownership';
import { getOrbColor, type OrbColors } from '@/utils/orbColor';
import type { Card } from './homeApi';

export interface CardOwnership { owned: boolean; ownerType?: 'self' | 'shared' }

const yearOf = (c: Card): number | null => (/^\d{4}$/.test(c.subtitle || '') ? Number(c.subtitle) : null);
const typeOf = (c: Card): 'movie' | 'tv' => (c.type === 'movie' ? 'movie' : 'tv');

/** Ownership known without a request: a Plex library card is in the library. */
export function plexOwned(c: Card): CardOwnership | null {
  return c.tmdb_id ? null : { owned: true, ownerType: 'self' };
}

export function useRailOwnership(cards: Card[]): Record<string, CardOwnership> {
  const [map, setMap] = useState<Record<string, CardOwnership>>({});
  const sig = cards.map(c => c.key).join('|');
  useEffect(() => {
    let cancelled = false;
    const base: Record<string, CardOwnership> = {};
    const toCheck: Card[] = [];
    for (const c of cards) {
      const known = plexOwned(c);
      if (known) base[c.key] = known;
      else if (c.title) toCheck.push(c);
    }
    setMap(base);
    if (toCheck.length === 0) return;
    resolveOwnership(toCheck.map(c => ({
      title: c.title, year: yearOf(c), tmdbId: c.tmdb_id ?? null, ratingKey: null, mediaType: typeOf(c),
    }))).then((res) => {
      if (cancelled) return;
      const next = { ...base };
      for (const c of toCheck) {
        const m = res.get(mediaKey({ title: c.title, year: yearOf(c), mediaType: typeOf(c) }));
        next[c.key] = { owned: !!m?.owned, ownerType: m?.ownerType };
      }
      setMap(next);
    }).catch(() => {});
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sig]);
  return map;
}

/** Prod's orb for a card; null until ownership is resolved. */
export function cardOrb(o: CardOwnership | undefined): OrbColors | null {
  return getOrbColor({ isInPlex: o?.owned, ownerType: o?.ownerType, resolved: !!o });
}

export function OwnedOrb({ own }: { own: CardOwnership | undefined }) {
  const orb = cardOrb(own);
  if (!orb) return null;
  return (
    <div className="absolute left-3 top-3" data-owned-orb={own?.owned ? (own.ownerType === 'shared' ? 'shared' : 'owned') : 'missing'}>
      <span className={`block h-3 w-3 rounded-full ${orb.bg}`} style={{ boxShadow: orb.glow }} />
    </div>
  );
}

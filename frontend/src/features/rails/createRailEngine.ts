/**
 * Shared rail engine hook.
 *
 * Extracts the duplicated orchestration pattern from useExploreRails and
 * useHomeRails into a single generic hook:
 *   - Progressive loading (each rail renders independently)
 *   - Conditional rail hiding (absent until data arrives)
 *   - Optional sessionStorage caching with TTL
 *   - Optional safety timeout (force loading=false)
 *   - Pluggable per-rail and batch enrichment callbacks
 *
 * Feature-specific fetchers, parsers, and enrichment logic stay in each
 * wrapper hook. This file contains ONLY orchestration.
 */

import { useState, useEffect, useMemo, useRef } from 'react';

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/** Minimum shape a rail definition must satisfy for the engine. */
interface RailDefinitionBase<TRailId extends string> {
  id: TRailId;
  title: string;
  conditional: boolean;
}

/** Per-rail state returned to consumers. */
export interface RailState<TItem, TRailId extends string> {
  id: TRailId;
  title: string;
  items: TItem[];
  isLoading: boolean;
}

/** Engine configuration. */
interface RailEngineConfig<
  TItem extends { id: string },
  TRailId extends string,
  TDef extends RailDefinitionBase<TRailId>,
> {
  /** Ordered rail definitions. Output preserves this order. */
  definitions: readonly TDef[];

  /** Map from rail id → fetcher. Return `null` to signal a conditional rail is disabled. */
  fetchers: Record<TRailId, () => Promise<TItem[] | null>>;

  /** sessionStorage cache (omit to disable caching). */
  cache?: { prefix: string; ttlMs: number };

  /** Force all loading states to false after this many ms (0 = disabled). */
  safetyTimeoutMs?: number;

  /**
   * Called per-rail right after its fetch resolves successfully.
   * Use for per-rail enrichment (e.g. Home's watchlist stamping).
   */
  onRailFetched?: (
    def: TDef,
    items: TItem[],
    update: (enriched: TItem[]) => void,
    cancelled: () => boolean,
  ) => void;

  /**
   * Called once after ALL rail fetches have settled.
   * Use for batch enrichment (e.g. Explore's ownership + watchlist).
   */
  onAllSettled?: (
    resolved: ReadonlyMap<TRailId, TItem[]>,
    update: (railId: TRailId, items: TItem[]) => void,
    cancelled: () => boolean,
  ) => void;
}

// ---------------------------------------------------------------------------
// sessionStorage helpers
// ---------------------------------------------------------------------------

function getCached<T>(prefix: string, key: string, ttlMs: number): T[] | null {
  try {
    const raw = sessionStorage.getItem(`${prefix}${key}`);
    if (!raw) return null;
    const { items, ts } = JSON.parse(raw) as { items: T[]; ts: number };
    if (Date.now() - ts > ttlMs) {
      sessionStorage.removeItem(`${prefix}${key}`);
      return null;
    }
    return items;
  } catch {
    return null;
  }
}

function setCached<T>(prefix: string, key: string, items: T[]): void {
  try {
    sessionStorage.setItem(
      `${prefix}${key}`,
      JSON.stringify({ items, ts: Date.now() }),
    );
  } catch {
    // Storage full — silently ignore
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useRailEngine<
  TItem extends { id: string },
  TRailId extends string,
  TDef extends RailDefinitionBase<TRailId>,
>(config: RailEngineConfig<TItem, TRailId, TDef>): RailState<TItem, TRailId>[] {
  // Capture config at mount time — subsequent changes are ignored.
  const cfgRef = useRef(config);

  // ---- State initialisation (with optional cache warm) --------------------

  const [railMap, setRailMap] = useState<
    Record<string, RailState<TItem, TRailId>>
  >(() => {
    const { definitions, cache } = cfgRef.current;
    const initial: Record<string, RailState<TItem, TRailId>> = {};
    for (const def of definitions) {
      const cached = cache
        ? getCached<TItem>(cache.prefix, def.id, cache.ttlMs)
        : null;
      if (cached) {
        initial[def.id] = { id: def.id, title: def.title, items: cached, isLoading: false };
      } else if (!def.conditional) {
        initial[def.id] = { id: def.id, title: def.title, items: [], isLoading: true };
      }
    }
    return initial;
  });

  // ---- Effect: parallel fetch + enrichment --------------------------------

  useEffect(() => {
    const {
      definitions, fetchers, cache,
      safetyTimeoutMs, onRailFetched, onAllSettled,
    } = cfgRef.current;
    let cancelled = false;
    const resolved = new Map<TRailId, TItem[]>();

    // Safety timeout
    let safetyTimer: ReturnType<typeof setTimeout> | undefined;
    if (safetyTimeoutMs && safetyTimeoutMs > 0) {
      safetyTimer = setTimeout(() => {
        if (cancelled) return;
        setRailMap((prev) => {
          const next = { ...prev };
          for (const id of Object.keys(next)) {
            if (next[id]!.isLoading) {
              next[id] = { ...next[id]!, isLoading: false };
            }
          }
          return next;
        });
      }, safetyTimeoutMs);
    }

    // Parallel fetch — each rail updates state independently
    const promises = definitions.map(async (def) => {
      try {
        const items = await fetchers[def.id]();
        if (cancelled) return;

        // Conditional rail with no data → stay hidden
        if (items === null || (def.conditional && items.length === 0)) return;

        // Cache raw items
        if (cache) setCached(cache.prefix, def.id, items);

        // Track for batch enrichment
        resolved.set(def.id, items);

        // Progressive update
        setRailMap((prev) => ({
          ...prev,
          [def.id]: { id: def.id, title: def.title, items, isLoading: false },
        }));

        // Per-rail enrichment
        if (onRailFetched) {
          onRailFetched(
            def,
            items,
            (enriched) => {
              if (cancelled) return;
              setRailMap((prev) => ({
                ...prev,
                [def.id]: { ...prev[def.id]!, items: enriched },
              }));
            },
            () => cancelled,
          );
        }
      } catch {
        if (cancelled) return;
        // Non-conditional rail: mark done so skeleton disappears
        if (!def.conditional) {
          setRailMap((prev) => ({
            ...prev,
            [def.id]: { ...prev[def.id]!, isLoading: false },
          }));
        }
      }
    });

    // Batch enrichment after all rails settle
    if (onAllSettled) {
      Promise.all(promises).then(() => {
        if (cancelled) return;
        onAllSettled(
          resolved,
          (railId, items) => {
            if (cancelled) return;
            setRailMap((prev) => ({
              ...prev,
              [railId]: { ...prev[railId]!, items },
            }));
          },
          () => cancelled,
        );
      });
    }

    return () => {
      cancelled = true;
      if (safetyTimer) clearTimeout(safetyTimer);
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- Ordered output -----------------------------------------------------

  const { definitions } = cfgRef.current;
  return useMemo(
    () =>
      definitions
        .map((def) => railMap[def.id])
        .filter((s): s is RailState<TItem, TRailId> => s !== undefined),
    [definitions, railMap],
  );
}

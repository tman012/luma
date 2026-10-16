/**
 * Shared rail engine hook (Home and Explore).
 *
 * All rails reveal together: skeletons stay up until every rail has
 * succeeded (failed fetches retry in the background) or the hard cap hits,
 * then every rail swaps in on the same render with one shared fade. Results
 * that arrive after the reveal are dropped. A cached snapshot reveals
 * instantly with no skeletons. `refresh()` re-runs the gate while the
 * current rails stay on screen and swaps the new set in at once.
 *
 * Feature-specific fetchers, parsers, and enrichment stay in each wrapper
 * hook. This file contains ONLY orchestration; the logic lives in
 * railReveal.ts so it can be unit tested without React.
 */

import { useState, useEffect, useRef, useCallback } from 'react';
import {
  REVEAL_CAP_MS,
  loadLayout,
  loadSnapshot,
  revealedRails,
  runRevealGate,
  saveLayout,
  saveSnapshot,
  skeletonRails,
  withDeadline,
  type RevealGate,
  type RevealRailState,
  type StorageLike,
} from './railReveal';

interface RailDefinitionBase<TRailId extends string> {
  id: TRailId;
  title: string;
  conditional: boolean;
}

export type RailState<TItem, TRailId extends string> = RevealRailState<TItem, TRailId>;

interface RailEngineConfig<
  TItem extends { id: string },
  TRailId extends string,
  TDef extends RailDefinitionBase<TRailId>,
> {
  definitions: readonly TDef[];
  /** Map from rail id to fetcher. Throw (or reject) on failure so it is retried. Return `null` for a disabled rail. */
  fetchers: Record<TRailId, () => Promise<TItem[] | null>>;
  /** Storage key prefix; the snapshot and last layout are kept under it. */
  storageKey: string;
  /** How long a snapshot reveals instantly without refetching. */
  snapshotTtlMs: number;
  capMs?: number;
  /** Batch enrichment applied before the reveal (bounded by enrichDeadlineMs). */
  enrichAll?: (resolved: ReadonlyMap<TRailId, TItem[]>) => Promise<ReadonlyMap<TRailId, TItem[]>>;
  enrichDeadlineMs?: number;
}

export interface RailEngineResult<TItem, TRailId extends string> {
  rails: RailState<TItem, TRailId>[];
  /** Re-run the gate and swap everything in at once. Resolves after the swap. */
  refresh: () => Promise<void>;
}

function sessionStore(): StorageLike | null {
  try { return typeof sessionStorage !== 'undefined' ? sessionStorage : null; } catch { return null; }
}
function localStore(): StorageLike | null {
  try { return typeof localStorage !== 'undefined' ? localStorage : null; } catch { return null; }
}

export function useRailEngine<
  TItem extends { id: string },
  TRailId extends string,
  TDef extends RailDefinitionBase<TRailId>,
>(config: RailEngineConfig<TItem, TRailId, TDef>): RailEngineResult<TItem, TRailId> {
  const cfgRef = useRef(config);
  const snapKey = `${config.storageKey}snapshot`;
  const layoutKey = `${config.storageKey}layout`;

  const [initial] = useState(() => {
    const { definitions, snapshotTtlMs } = cfgRef.current;
    const snap = loadSnapshot<TRailId, TItem>(sessionStore(), snapKey, snapshotTtlMs);
    if (snap) return { rails: revealedRails<TItem, TRailId>(definitions, snap, false), cached: true };
    return {
      rails: skeletonRails<TItem, TRailId>(definitions, loadLayout<TRailId>(localStore(), layoutKey)),
      cached: false,
    };
  });
  const [rails, setRails] = useState<RailState<TItem, TRailId>[]>(initial.rails);
  const gateRef = useRef<RevealGate<TRailId, TItem> | null>(null);
  const mountedRef = useRef(true);

  const runGate = useCallback(async (fade: boolean): Promise<void> => {
    const { definitions, fetchers, capMs, enrichAll, enrichDeadlineMs } = cfgRef.current;
    gateRef.current?.cancel();
    const gate = runRevealGate<TRailId, TItem>({
      ids: definitions.map((d) => d.id),
      fetch: (id) => fetchers[id](),
      capMs: capMs ?? REVEAL_CAP_MS,
    });
    gateRef.current = gate;
    const result = await gate.done;
    if (!mountedRef.current || gateRef.current !== gate) return;

    let ok = result.ok;
    if (enrichAll) {
      const lists = new Map<TRailId, TItem[]>();
      for (const [id, items] of ok) if (items && items.length > 0) lists.set(id, items);
      if (lists.size > 0) {
        const enriched = await withDeadline(enrichAll(lists), enrichDeadlineMs ?? 3000, lists);
        if (!mountedRef.current || gateRef.current !== gate) return;
        const merged = new Map(ok);
        for (const [id, items] of enriched) merged.set(id, items);
        ok = merged;
      }
    }

    const next = revealedRails<TItem, TRailId>(definitions, ok, fade);
    // One state update: every rail appears on the same render.
    setRails(next);
    saveSnapshot(sessionStore(), snapKey, next);
    saveLayout(localStore(), layoutKey, next);
  }, [snapKey, layoutKey]);

  useEffect(() => {
    mountedRef.current = true;
    if (!initial.cached) void runGate(true);
    return () => {
      mountedRef.current = false;
      gateRef.current?.cancel();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const refresh = useCallback(() => runGate(false), [runGate]);

  return { rails, refresh };
}

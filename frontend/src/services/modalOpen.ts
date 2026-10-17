/** Phone cards and rows: the ids a card's popup opens with, warming them,
 * and opening only once the popup can show fully composed. */
import { buildModalOptions, type ModalNavigableItem } from '@/utils/modalNavigation';
import { openWhenReady, warmDetail } from '@/services/modalPrefetch';

export interface PopupIds { kind: 'movie' | 'tv'; ratingKey?: number; tmdbId?: number }

export function itemIds(item: ModalNavigableItem): PopupIds {
  const { target, options } = buildModalOptions(item, 'search');
  return {
    kind: target,
    ratingKey: options.ratingKey ? Number(options.ratingKey) : undefined,
    tmdbId: options.tmdbId ? Number(options.tmdbId) : undefined,
  };
}

export function warmKey(ids: PopupIds): string | null {
  return ids.ratingKey || ids.tmdbId ? `${ids.kind}:${ids.ratingKey ?? ''}:${ids.tmdbId ?? ''}` : null;
}

export function warmIds(ids: PopupIds): Promise<void> {
  return warmDetail(ids.kind, ids.ratingKey, ids.tmdbId).catch(() => {});
}

/** Open the popup for a tapped card once composed (600 ms cap); the card shows
 *  a pressed state meanwhile and its poster seeds the color blur. */
export function openItemWhenReady(item: ModalNavigableItem, el: HTMLElement | null, open: () => void): void {
  const ids = itemIds(item);
  if (!ids.ratingKey && !ids.tmdbId) { open(); return; }
  void openWhenReady(ids.kind, ids.ratingKey, ids.tmdbId, el, open);
}

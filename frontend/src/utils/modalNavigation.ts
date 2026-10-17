/**
 * Centralized modal navigation utility.
 *
 * This is the SINGLE source of truth for resolving a media item into
 * the correct modal open call (movie vs TV, with optional episode context).
 * Every page that opens modals from rail/card taps MUST use this utility.
 *
 * NEVER duplicate this logic in per-rail or per-page click handlers.
 */

import type {
  ModalOpenOptions,
  ModalSource,
  EpisodeContext,
  ModalHostContextValue,
} from '@/contexts/ModalHostContext';

// ---------------------------------------------------------------------------
// Generic media item shape — any source's item should be mappable to this
// ---------------------------------------------------------------------------

export interface ModalNavigableItem {
  mediaType: 'movie' | 'tv' | 'episode';
  tmdbId?: number | null;
  ratingKey?: number | null;
  showRatingKey?: number | null;
  seasonNumber?: number | null;
  episodeNumber?: number | null;
  episodeRatingKey?: number | null;
}

// ---------------------------------------------------------------------------
// Build ModalOpenOptions from any navigable item
// ---------------------------------------------------------------------------

/**
 * Given a media item from ANY source (Plex, TMDb, Trakt, indexer, etc),
 * returns the correct ModalOpenOptions for opening a modal.
 *
 * Rules:
 *  - Movie       -> openMovie with tmdbId/ratingKey
 *  - TV show     -> openTv with tmdbId/ratingKey, no episodeContext
 *  - Episode     -> openTv with show-level IDs + episodeContext
 *                   Falls back to plain TV modal if season/episode unknown
 *
 * @param item   The normalized rail/card item
 * @param origin The ModalSource tag for instrumentation
 * @param extra  Optional extra fields to merge (e.g. onPersonSearch)
 */
export function buildModalOptions(
  item: ModalNavigableItem,
  origin: ModalSource,
  extra?: Omit<ModalOpenOptions, 'tmdbId' | 'ratingKey' | 'origin' | 'episodeContext'>,
): { target: 'movie' | 'tv'; options: ModalOpenOptions } {
  const base: ModalOpenOptions = {
    tmdbId: item.tmdbId ?? undefined,
    origin,
    ...extra,
  };

  if (item.mediaType === 'episode') {
    // Episode: open the TV show modal, using the show's ratingKey
    const opts: ModalOpenOptions = {
      ...base,
      ratingKey: item.showRatingKey ?? item.ratingKey ?? undefined,
    };

    // Attach episode context only if we have both season + episode numbers
    if (
      item.seasonNumber != null &&
      item.seasonNumber > 0 &&
      item.episodeNumber != null &&
      item.episodeNumber > 0
    ) {
      const episodeContext: EpisodeContext = {
        seasonNumber: item.seasonNumber,
        episodeNumber: item.episodeNumber,
      };
      if (item.episodeRatingKey != null) {
        episodeContext.episodeRatingKey = item.episodeRatingKey;
      }
      opts.episodeContext = episodeContext;
    }

    return { target: 'tv', options: opts };
  }

  if (item.mediaType === 'tv') {
    return {
      target: 'tv',
      options: {
        ...base,
        ratingKey: item.ratingKey ?? undefined,
      },
    };
  }

  // Default: movie
  return {
    target: 'movie',
    options: {
      ...base,
      ratingKey: item.ratingKey ?? undefined,
    },
  };
}

// ---------------------------------------------------------------------------
// Convenience: dispatch directly to the modal host
// ---------------------------------------------------------------------------

/**
 * One-call helper that builds options AND dispatches to the correct
 * openMovie/openTv function. Useful when you have the modal host ref.
 */
export function openModalForItem(
  host: Pick<ModalHostContextValue, 'openMovie' | 'openTv'>,
  item: ModalNavigableItem,
  origin: ModalSource,
  extra?: Omit<ModalOpenOptions, 'tmdbId' | 'ratingKey' | 'origin' | 'episodeContext'>,
): boolean {
  const { target, options } = buildModalOptions(item, origin, extra);

  // Guard: if we have no ID at all, don't open a broken modal. Emit a global
  // signal so a tapped-but-inert tile can surface a toast instead of a silent
  // dead tap; returns false so callers can react directly too.
  if (!options.tmdbId && !options.ratingKey) {
    console.warn('[modalNavigation] Cannot open modal — no tmdbId or ratingKey', item);
    window.dispatchEvent(new CustomEvent('media-unavailable', {
      detail: { message: 'Details unavailable for this title' },
    }));
    return false;
  }

  if (target === 'tv') {
    host.openTv(options);
  } else {
    host.openMovie(options);
  }
  return true;
}

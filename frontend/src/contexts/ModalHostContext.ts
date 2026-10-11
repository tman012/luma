import { createContext, useContext } from 'react';

/** Fine-grained source tag for modal instrumentation */
export type ModalSource =
  | 'hero'
  | 'search'
  | 'explore'
  | 'screensaver'
  | 'continue_watching'
  | 'in_theaters'
  | 'now_available'
  | 'recently_added_movies'
  | 'recently_added_tv'
  | 'recently_watched'
  | 'similar_movies'
  | 'explore_in_theaters'
  | 'explore_trackers'
  | 'now_available_grid'
  | 'trakt_movies'
  | 'trakt_shows'
  | 'trending'
  | 'watchlist'
  | 'tv_calendar'
  | 'browse'
  | 'notification'
  | 'collections';

/** Episode context for opening TvDetailModal at a specific episode. */
export interface EpisodeContext {
  seasonNumber: number;
  episodeNumber: number;
  episodeRatingKey?: number;
}

export interface ModalOpenOptions {
  ratingKey?: number;
  tmdbId?: number;
  origin?: ModalSource;
  onPersonSearch?: (name: string, personId?: number) => void;
  /** When set, the TV modal should open to this specific episode. */
  episodeContext?: EpisodeContext;
  /** When set, the TV modal should auto-select this season on mount. */
  initialSeason?: number;
  /**
   * Open as a fresh root modal, discarding anything already on the stack
   * instead of stacking on top of it.
   *
   * The stack exists so that drilling *within* a modal — a cast member, then one
   * of their films — can be unwound step by step. An arrival from outside the
   * app is not a drill-down: tapping a notification while an earlier
   * notification's modal is still open should show that film and nothing else.
   * Without this, closing it revealed the previous notification's modal, which
   * reads as the app resurrecting screens you already dismissed.
   */
  replaceStack?: boolean;
  /**
   * When popping back to this movie on the modal stack, reopen the Similar
   * ("More Like This") panel. Set automatically when drilling into a title
   * from Similar so closing the child returns to Similar, not bare detail.
   */
  restoreSimilar?: boolean;
  /**
   * Original app-search text that led to opening this modal. Used as the
   * default plain-text Sources override (editable) so the user does not have
   * to retype after a fuzzy TMDb match.
   */
  searchQuery?: string;
}

export interface ModalHostContextValue {
  openMovie: (opts: ModalOpenOptions) => void;
  openTv: (opts: ModalOpenOptions) => void;
  /** Opens PersonDetailModal at the host level; participates in the modal stack
   *  so closing a credit modal returns to the originating person view. */
  openPerson: (opts: { personId: number; personName: string }) => void;
  close: () => void;
  /**
   * Register a nested UI back handler (e.g. Sources sheet). When the user
   * swipes/presses system back, AppModalHost calls this first. Return true to
   * consume the back (close the nested sheet only) so the parent detail modal
   * stays open — same pattern as Collections detail → list.
   */
  registerBackHandler: (handler: (() => boolean) | null) => void;
}

export const ModalHostContext = createContext<ModalHostContextValue | null>(null);

export function useModalHost(): ModalHostContextValue {
  const ctx = useContext(ModalHostContext);
  if (!ctx) {
    throw new Error('useModalHost must be used inside a ModalHost provider');
  }
  return ctx;
}

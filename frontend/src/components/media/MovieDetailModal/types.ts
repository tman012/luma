import type { MediaDetail, Ratings, UltraBlurColors } from '@/utils/modalParsers';
import type { ModalSource } from '@/contexts/ModalHostContext';
import type { FixMatchResult } from './FixMatchSheet';

export interface MovieModalViewProps {
  // Data from useMovieDetail
  detail: MediaDetail | null;
  ratings: Ratings | null;
  trailerKey: string | null | undefined;
  ultraBlurColors: UltraBlurColors | null;
  resolvedTmdbId: number | undefined;
  loading: boolean;
  error: boolean;
  // Phase animation
  phase: 'entering' | 'visible' | 'exiting' | 'gone';
  // Sub-modal visibility
  showSources: boolean;
  showTrailer: boolean;
  showFullPlot: boolean;
  showUpgrade: boolean;
  showReviews: boolean;
  showPlayback: boolean;
  showSimilar: boolean;
  showCollection: boolean;
  // Handlers
  onClose: () => void;
  setShowSources: (v: boolean) => void;
  setShowTrailer: (v: boolean) => void;
  setShowFullPlot: (v: boolean) => void;
  setShowUpgrade: (v: boolean) => void;
  setShowReviews: (v: boolean) => void;
  setShowPlayback: (v: boolean) => void;
  setShowSimilar: (v: boolean) => void;
  setShowCollection: (v: boolean) => void;
  onPersonSearch?: (name: string, personId?: number) => void;
  origin?: ModalSource;
  // Watchlist state
  inWatchlist: boolean;
  // Toast state
  grabToast: boolean;
  watchlistToast: boolean;
  // Callbacks for sub-modal success
  onSourcesGrabbed?: () => void;
  onUpgradeAdded?: () => void;
  /** Fired after successful remove-from-watchlist (unmonitor). */
  onWatchlistRemoved?: () => void;
  /** Original app-search query — seeds Sources plain-text override. */
  searchQuery?: string;
  /** Fired after Fix Match / Unmatch succeeds — reloads the popup. */
  onMatched?: (r: FixMatchResult) => void;
}

export interface MovieDetailModalProps {
  ratingKey?: number;
  tmdbId?: number;
  onClose: () => void;
  origin?: ModalSource;
  onPersonSearch?: (name: string, personId?: number) => void;
  /** Reopen Similar panel when this modal is restored from the stack. */
  restoreSimilar?: boolean;
  /** Original app-search query — seeds Sources plain-text override. */
  searchQuery?: string;
}

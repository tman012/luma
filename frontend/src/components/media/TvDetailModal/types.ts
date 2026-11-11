import type { TvShowDetail, UltraBlurColors, CastMember } from '@/utils/modalParsers';
import type { TvSeason, OwnershipMap, SonarrStatus } from '@/services/sonarrApi';
import type { ModalSource, EpisodeContext } from '@/contexts/ModalHostContext';
import type { PlexSeason } from '@/services/playbackApi';

export interface SourcesTarget {
  type: 'season' | 'episode';
  season: number;
  episode?: number;
}

/**
 * Normalized episode data from either Plex (owned) or TMDb (unowned).
 * Used by the episode-mode header in TvDetailModal layouts.
 */
export interface EpisodeData {
  title: string;
  overview: string;
  airDate: string | null;
  runtime: number | null; // minutes
  contentRating: string | null;
  rating: number | null;
  voteCount: number | null;
  seasonNumber: number;
  episodeNumber: number;
  isOwned: boolean;
  viewOffset?: number; // ms, for resume progress
  duration?: number; // ms, for progress calculation
  thumbPath?: string; // proxied Plex image path
  stillPath?: string; // TMDb still image path
  /** Episode-level cast (credits.cast + guest stars). Empty = fall back to series cast. */
  cast?: CastMember[];
}

export interface TvModalViewProps {
  /** Identifies one open of the sheet (the title it was opened for): its backdrop latches once per open. */
  openKey?: string;
  // Data from useTvDetail
  show: TvShowDetail | null;
  seasons: TvSeason[];
  tvdbId: number | null;
  ownership: OwnershipMap;
  showOwned: boolean;
  sonarrStatus: SonarrStatus | null;
  loading: boolean;
  error: boolean;
  seasonsLoading: boolean;
  ultraBlurColors: UltraBlurColors | null;
  /** The fetched palette alone (null until it answers): the phone sheet's blur draws this, never the poster seed. */
  sheetColors?: UltraBlurColors | null;
  resolvedTmdbId: number | undefined;
  // UI state
  selectedSeason: number;
  setSelectedSeason: (n: number) => void;
  // Phase animation
  phase: 'entering' | 'visible' | 'exiting' | 'gone';
  /** False while another sheet is on top of this one: no swipe, no Escape. */
  active?: boolean;
  // Sub-modal visibility
  showSources: SourcesTarget | null;
  showAddToSonarr: boolean;
  showFullPlot: boolean;
  showPlayback: boolean;
  // Handlers
  onClose: () => void;
  setShowSources: (v: SourcesTarget | null) => void;
  setShowAddToSonarr: (v: boolean) => void;
  setShowFullPlot: (v: boolean) => void;
  setShowPlayback: (v: boolean) => void;
  onPersonSearch?: (name: string, personId?: number) => void;
  origin?: ModalSource;
  // Toast state
  grabToast: boolean;
  watchlistToast: boolean;
  // Callbacks for sub-modal success
  onSourcesGrabbed?: () => void;
  onSonarrAdded?: () => void;
  // Episode mode
  episodeContext?: EpisodeContext;
  episodeData?: EpisodeData | null;
  episodeLoading?: boolean;
  showFullShow: boolean;
  setShowFullShow: (v: boolean) => void;
  onEpisodeClick?: (seasonNumber: number, episodeNumber: number) => void;
  // Playback
  playbackRatingKey?: number;
  playbackTitle?: string;
  onPlayEpisode?: (ratingKey: number, title: string) => void;
  // Season grid + shuffle (mobile only)
  seasonViewMode?: 'pills' | 'grid';
  setSeasonViewMode?: (mode: 'pills' | 'grid') => void;
  plexSeasons?: PlexSeason[];
  shuffleTarget?: { ratingKey: number; label: string } | null;
  setShuffleTarget?: (target: { ratingKey: number; label: string } | null) => void;
  /** Original app-search query — seeds Sources plain-text override. */
  searchQuery?: string;
}

export interface TvDetailModalProps {
  /** False while another sheet is on top of this one (it stays mounted underneath). Default true. */
  active?: boolean;
  /** The sheet started closing (its exit is playing); `onClose` follows when the exit is done. */
  onLeave?: () => void;
  ratingKey?: number;
  tmdbId?: number;
  onClose: () => void;
  origin?: ModalSource;
  onPersonSearch?: (name: string, personId?: number) => void;
  /** When provided, auto-select this season/episode on mount. */
  episodeContext?: EpisodeContext;
  /** When provided, auto-select this season on mount (without entering episode mode). */
  initialSeason?: number;
  /** Original app-search query — seeds Sources plain-text override. */
  searchQuery?: string;
}

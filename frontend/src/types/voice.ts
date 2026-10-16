// ---------------------------------------------------------------------------
// Voice routing
// ---------------------------------------------------------------------------

/** Where a voice command should be handled. */
export type VoiceRouteTarget = 'local_luma' | 'alfred' | 'ambiguous';

/** The kind of local action Luma can perform without calling Alfred. */
export type LocalAction =
  | { type: 'navigate'; path: string }
  | { type: 'search'; query: string }
  | { type: 'playback'; action: 'play' | 'pause' | 'resume' | 'stop' }
  | { type: 'open_modal'; modal: 'settings' | 'library' | 'explore' | 'watchlist' }
  | { type: 'reset_alfred' }
  | { type: 'dismiss' };

export interface VoiceRoute {
  target: VoiceRouteTarget;
  /** Only set when target === 'local_luma'. */
  localAction?: LocalAction;
  /** Original transcript, always present. */
  transcript: string;
  /** Confidence score 0-1 for the routing decision. */
  confidence: number;
}

// ---------------------------------------------------------------------------
// Alfred response contract
// ---------------------------------------------------------------------------

/** Structured response from Alfred. */
export interface AlfredResponse {
  type: string;
  status: 'ok' | 'error' | 'started' | 'pending';
  message: string;
  data?: Record<string, unknown>;
  suggested_ui_action?: AlfredUiAction;
}

/** A UI action Alfred can suggest for Luma to execute. */
export interface AlfredUiAction {
  action: string;
  /** Arbitrary params the action handler may need. */
  [key: string]: unknown;
}

/** One tile in a NovaResults overlay — sent by the show_results agent tool. */
export interface NovaResultItem {
  title: string;
  year?: number | null;
  tmdb_id?: number | null;
  rating_key?: number | null;
  media_type?: 'movie' | 'tv';
  poster_url?: string | null;
  backdrop_url?: string | null;
  rating?: number | null;
  /** Optional secondary line (e.g. "412 seeders on PHD", "Season 2 — 8 episodes"). */
  subtitle?: string | null;
  owned?: boolean;
  in_watchlist?: boolean;
  /** Tracker-release context — when present, opens the Radarr sources panel. */
  tracker?: string | null;
  seeders?: number | null;
  resolution?: string | null;
  pub_date?: string | null;
}

// ---------------------------------------------------------------------------
// Voice message (extended from existing)
// ---------------------------------------------------------------------------

export interface VoiceMessage {
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  /** Set when the message came from Alfred with structured data. */
  alfredResponse?: AlfredResponse;
  /** Which system handled this command. */
  source?: 'luma' | 'alfred';
}

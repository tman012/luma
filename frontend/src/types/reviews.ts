// ---------------------------------------------------------------------------
// Shared types for the Reviews system
// Fields are snake_case matching the backend response directly.
// ---------------------------------------------------------------------------

export interface CriticReview {
  critic: string;
  publication: string;
  quote: string;
  fresh: boolean;
  score: string | null;
  is_top_critic: boolean;
  date: string | null;
  review_url: string | null;
}

export interface AudienceReview {
  user: string;
  quote: string;
  rating: number | null;
  date: string | null;
  is_super_reviewer: boolean;
}

export interface ReviewsData {
  success: boolean;
  critic_score: number | null;
  audience_score: number | null;
  critic_reviews: CriticReview[];
  audience_reviews: AudienceReview[];
  reviews_url: string | null;
  url: string | null;
}

export type ReviewTab = 'critics' | 'audience';

export type SortMode = 'top' | 'newest' | 'positive' | 'critical';

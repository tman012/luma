/**
 * RatePage — route wrapper for RatePrompt.
 *
 * The deep-link target of the "rate this" push notification (/rate/:tmdbId).
 * All behaviour lives in RatePrompt, which also picks the compact-vs-expanded
 * layout, so the notification path and the in-app prompt cannot drift apart.
 * This file only supplies the id and decides where dismissing goes.
 */

import { useNavigate, useParams, useSearchParams } from 'react-router';
import { RatePrompt } from '@/components/taste/RatePrompt';

export function RatePage() {
  const { tmdbId } = useParams<{ tmdbId: string }>();
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const id = Number(tmdbId);

  if (!Number.isFinite(id)) {
    navigate('/', { replace: true });
    return null;
  }

  // ?force=1 re-asks even when a verdict is already stored. Without it, testing
  // the prompt means clearing the database between every attempt — rating a
  // film immediately disqualifies it from being prompted again, which is
  // correct in use and maddening while iterating on the UI.
  const force = search.get('force') === '1';

  // `replace` so the back gesture doesn't bounce straight back into the sheet
  // the user just dismissed.
  return (
    <RatePrompt
      tmdbId={id}
      force={force}
      onClose={() => navigate('/', { replace: true })}
    />
  );
}

export default RatePage;

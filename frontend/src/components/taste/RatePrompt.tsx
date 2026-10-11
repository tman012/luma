/**
 * RatePrompt — record a verdict on a film you just watched, then browse similar
 * films you don't own.
 *
 * Container only: fetching, verdict state and suggestion handling live here, and
 * presentation is delegated per layout mode — the same split MovieDetailModal
 * uses, for the same reason. A bottom sheet is right on a phone and wrong on a
 * 27" display; a centred dialog is the reverse.
 *
 *   compact   -> RateCompactSheet    (drag-to-dismiss bottom sheet)
 *   expanded  -> RateExpandedDialog  (centred modal, Escape, backdrop click)
 *
 * Two entry points, one container:
 *   - /rate/:tmdbId (RatePage) — the push notification's deep-link target
 *   - TastePromptHost — auto-presented in expanded mode after a film finishes
 *
 * Two stages, deliberately sequential — the verdict is asked on its own so it is
 * answered on its merits, not anchored by whatever is being offered next.
 * Suggestions appear only after a positive verdict; a negative one closes,
 * because there is nothing to act on and the verdict was the point of asking.
 *
 * Tapping a suggestion opens the standard media modal via openModalForItem, so
 * acquisition and ownership behave exactly as from the home screen. This
 * component owns no add button of its own.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import {
  fetchTasteFilm,
  submitVerdict,
  type TasteFilm,
  type TasteSuggestion,
  type Verdict,
} from '@/services/tasteApi';
import { RateCompactSheet } from './RateCompactSheet';
import { RateExpandedDialog } from './RateExpandedDialog';

export interface RateLayoutProps {
  film: TasteFilm | null;
  loading: boolean;
  error: string | null;
  /** Non-null while a verdict is in flight; carries which one. */
  saving: Verdict | null;
  /** True once a positive verdict exists — show suggestions instead of asking. */
  showList: boolean;
  suggestions: TasteSuggestion[];
  /** Drop z-index while a detail modal opened from a suggestion is on top. */
  behindModal: boolean;
  onChoose: (v: Verdict) => void;
  onOpenSuggestion: (s: TasteSuggestion) => void;
  onDismiss: () => void;
}

export interface RatePromptProps {
  tmdbId: number;
  /** Ask again even if a verdict already exists. For testing the prompt without
   *  clearing the store between attempts. */
  force?: boolean;
  /** Called after the exit animation. Navigate home, or clear the host state. */
  onClose: () => void;
  /** Fired when a verdict is stored, so a host can stop offering this film. */
  onRated?: (verdict: Verdict) => void;
}

export function RatePrompt({ tmdbId, force = false, onClose, onRated }: RatePromptProps) {
  const { mode } = useLayoutMode();
  const modalHost = useModalHost();
  const seedId = Number(tmdbId);

  const [film, setFilm] = useState<TasteFilm | null>(null);
  const [verdict, setVerdict] = useState<Verdict | null>(null);
  const [suggestions, setSuggestions] = useState<TasteSuggestion[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState<Verdict | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [behindModal, setBehindModal] = useState(false);
  const closing = useRef(false);

  // The layout owns its own exit animation, so it reports back when the
  // animation is done rather than the container guessing a duration.
  const dismiss = useCallback(() => {
    if (closing.current) return;
    closing.current = true;
    onClose();
  }, [onClose]);

  useEffect(() => {
    if (!behindModal) return;
    const restore = () => setBehindModal(false);
    window.addEventListener('modal-closed', restore);
    return () => window.removeEventListener('modal-closed', restore);
  }, [behindModal]);

  useEffect(() => {
    if (!Number.isFinite(seedId)) {
      setError('No film specified');
      setLoading(false);
      return;
    }
    fetchTasteFilm(seedId)
      .then((d) => {
        setFilm(d.film);
        // Re-opening after a positive verdict should land on the list, not ask
        // the same question again — unless we were told to force the question.
        setVerdict(!force && d.verdict === 'liked' ? 'liked' : null);
        setSuggestions(force ? [] : (d.suggestions || []));
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'Failed to load'))
      .finally(() => setLoading(false));
  }, [seedId, force]);

  const onChoose = useCallback(async (v: Verdict) => {
    if (!film) return;
    setSaving(v);
    setError(null);
    try {
      const r = await submitVerdict(seedId, v, film.title, film.year);
      onRated?.(v);
      if (v === 'disliked') {
        dismiss();
        return;
      }
      setSuggestions(r.suggestions || []);
      setVerdict(v);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(null);
    }
  }, [film, seedId, dismiss, onRated]);

  const onOpenSuggestion = useCallback((s: TasteSuggestion) => {
    setBehindModal(true);
    openModalForItem(
      modalHost,
      { mediaType: 'movie', tmdbId: s.tmdb_id },
      'notification',
    );
  }, [modalHost]);

  const layoutProps: RateLayoutProps = {
    film,
    loading,
    error,
    saving,
    showList: verdict === 'liked',
    suggestions,
    behindModal,
    onChoose,
    onOpenSuggestion,
    onDismiss: dismiss,
  };

  return mode === 'expanded'
    ? <RateExpandedDialog {...layoutProps} />
    : <RateCompactSheet {...layoutProps} />;
}

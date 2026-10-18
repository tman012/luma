/** Settings > TV in the URL: Show detail (?show=&season=&episode=) and its way back. */
import { useCallback } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router';

/** The Show detail's way back on phones (the Settings sheet's back button): true when it handled it. */
export function useTvBack(): (() => boolean) {
  const [params] = useSearchParams();
  const back = useBackFromShow();
  return () => { if (!params.has('show')) return false; back(); return true; };
}
export function useBackFromShow() {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  return useCallback(() => {
    if ((location.state as { tvBack?: boolean } | null)?.tvBack) { navigate(-1); return; }
    const next = new URLSearchParams(params);
    ['show', 'season', 'episode'].forEach(k => next.delete(k));
    setParams(next);
  }, [location.state, navigate, params, setParams]);
}

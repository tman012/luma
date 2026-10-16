import { useState, useEffect } from 'react';
import { apiFetch } from '@/services/api';
import type { KioskSettings } from '@/services/types';

let _cached: 'movie' | 'daily' | null = null;

export function useKioskMode() {
  const [mode, setMode] = useState<'movie' | 'daily' | null>(_cached);

  useEffect(() => {
    if (_cached) {
      setMode(_cached);
      return;
    }
    apiFetch<KioskSettings>('/api/kiosk/settings')
      .then((ks) => {
        const m = ks.kiosk_mode || 'movie';
        _cached = m;
        setMode(m);
      })
      .catch(() => {
        _cached = 'movie';
        setMode('movie');
      });
  }, []);

  return mode;
}

/** Call this to force re-fetch (e.g. after settings change) */
export function invalidateKioskMode() {
  _cached = null;
}

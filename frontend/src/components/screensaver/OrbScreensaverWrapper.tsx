import { useEffect, useRef } from 'react';
import { useModalHost } from '@/contexts/ModalHostContext';
import { assetUrl } from '@/urlHelpers';

interface OrbScreensaverWrapperProps {
  enabled?: boolean;
  idleTimeoutMs?: number;
  orbCount?: number;
}

export function OrbScreensaverWrapper({
  enabled = true,
  idleTimeoutMs = 120_000,
  orbCount = 9,
}: OrbScreensaverWrapperProps) {
  const scriptLoadedRef = useRef(false);
  const { openMovie } = useModalHost();

  useEffect(() => {
    // Set config before loading script
    window.SCREENSAVER_ENABLED = enabled;
    window.SCREENSAVER_IDLE_TIMEOUT_MS = idleTimeoutMs;
    window.SCREENSAVER_ORB_COUNT = orbCount;

    if (scriptLoadedRef.current && window.OrbScreensaver) {
      // Script already loaded — sync CONFIG values and toggle
      window.OrbScreensaver.config.idleTimeoutMs = idleTimeoutMs;
      window.OrbScreensaver.config.orbCount = orbCount;
      if (enabled) {
        window.OrbScreensaver.enable();
      } else {
        window.OrbScreensaver.disable();
      }
      return;
    }

    // Load the screensaver script
    const script = document.createElement('script');
    script.src = assetUrl('/static/js/movie-orb-screensaver.js');
    script.async = true;
    script.onload = () => {
      scriptLoadedRef.current = true;
      if (enabled) {
        window.OrbScreensaver?.enable();
      }
    };
    document.body.appendChild(script);

    // Bridge React interactions to screensaver activity tracking
    const activityHandler = () => {
      window.OrbScreensaver?.recordActivity();
    };

    const events = ['touchstart', 'mousedown', 'keydown'] as const;
    for (const event of events) {
      window.addEventListener(event, activityHandler, { passive: true });
    }

    return () => {
      for (const event of events) {
        window.removeEventListener(event, activityHandler);
      }
      window.OrbScreensaver?.disable();
      window.OrbScreensaver?.exit();
    };
  }, [enabled, idleTimeoutMs, orbCount]);

  // Listen for orb tap events from the screensaver
  useEffect(() => {
    function handleOrbTap(e: Event) {
      const detail = (e as CustomEvent).detail;
      if (detail?.ratingKey || detail?.tmdbId) {
        openMovie({
          ratingKey: detail.ratingKey || undefined,
          tmdbId: detail.tmdbId || undefined,
          origin: 'screensaver',
        });
      }
    }
    window.addEventListener('screensaver-orb-tap', handleOrbTap);
    return () => window.removeEventListener('screensaver-orb-tap', handleOrbTap);
  }, [openMovie]);

  return null;
}

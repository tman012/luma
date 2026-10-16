import { useState, useEffect } from 'react';
import { OrbScreensaverWrapper } from './OrbScreensaverWrapper';
import { DidYouKnowScreensaver } from './DidYouKnowScreensaver';
import { KioskScreensaver } from './KioskScreensaver';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import type { KioskSettings } from '@/services/types';

function isTabletViewport(): boolean {
  if (typeof window === 'undefined') return false;
  return (
    window.innerWidth <= 1100 ||
    window.innerHeight <= 800 ||
    /FullyKiosk|Fully Kiosk/i.test(navigator.userAgent)
  );
}

export function ScreensaverManager() {
  const { mode } = useLayoutMode();
  const [settings, setSettings] = useState<KioskSettings | null>(null);

  useEffect(() => {
    fetch('/api/kiosk/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: KioskSettings | null) => {
        if (data) setSettings(data);
      })
      .catch(() => {});
  }, []);

  // Never run screensaver on mobile devices
  if (mode === 'compact') return null;
  if (!settings) return null;

  const enabled = settings.screensaver_enabled;
  const idleTimeoutMs = settings.screensaver_idle_timeout_minutes * 60_000;
  const orbCount = isTabletViewport()
    ? settings.screensaver_tablet_orb_count
    : settings.screensaver_orb_count;
  const screensaverType = settings.screensaver_type;

  if (screensaverType === 'trivia') {
    return (
      <DidYouKnowScreensaver
        enabled={enabled}
        idleTimeoutMs={idleTimeoutMs}
      />
    );
  }

  if (screensaverType === 'kiosk') {
    return (
      <KioskScreensaver
        enabled={enabled}
        idleTimeoutMs={idleTimeoutMs}
      />
    );
  }

  return (
    <OrbScreensaverWrapper
      enabled={enabled}
      idleTimeoutMs={idleTimeoutMs}
      orbCount={orbCount}
    />
  );
}

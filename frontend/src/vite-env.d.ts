/// <reference types="vite/client" />

interface Window {
  SCREENSAVER_ENABLED?: boolean;
  SCREENSAVER_IDLE_TIMEOUT_MS?: number;
  SCREENSAVER_ORB_COUNT?: number;
  OrbScreensaver?: {
    config: { enabled: boolean; idleTimeoutMs: number; orbCount: number };
    enable: () => void;
    disable: () => void;
    exit: () => void;
    recordActivity: () => void;
  };
}

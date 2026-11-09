/** Full-screen startup / full-refresh wait: the existing navy glow, the start
 *  screen's loading circle, and the exact word Loading…. Not a detail sheet
 *  and not a skeleton. */
import { Center, StartShell, Status } from '@/components/start/StartUI';

export const STARTUP_LOADING_TEXT = 'Loading\u2026';

export function StartupLoading() {
  return (
    <StartShell step="startup-loading">
      <Center>
        <div role="status" aria-live="polite" aria-busy="true" data-testid="startup-loading">
          <Status text={STARTUP_LOADING_TEXT} prominent />
        </div>
      </Center>
    </StartShell>
  );
}

/** Drops the document splash (index.html) once real UI or a truthful error is up. */
export function signalContentReady(): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new Event('luma:content-ready'));
}

/** Shown instead of the app when Luma can't be reached on load (the service
 * worker served the saved app shell, but /api/auth/me failed). A signed-in
 * user must not land on the Sign in form just because the network is down. */
import { Bottom, Center, StartShell, Title, primaryBtn } from '@/components/start/StartUI';

export const OFFLINE_TITLE = "Can't reach Luma";
export const OFFLINE_RETRY = 'Try again';

export function OfflineNotice({ onRetry, busy }: { onRetry: () => void; busy: boolean }) {
  return (
    <StartShell step="offline">
      <Center>
        <div role="status" data-testid="offline-notice"><Title>{OFFLINE_TITLE}</Title></div>
        <Bottom>
          <button type="button" className="luma-start-btn" style={primaryBtn} onClick={onRetry} disabled={busy}
            data-testid="offline-retry">
            {OFFLINE_RETRY}
          </button>
        </Bottom>
      </Center>
    </StartShell>
  );
}

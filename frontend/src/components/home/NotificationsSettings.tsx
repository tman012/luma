/** Settings > Notifications (owner only): Web Push — Push notifications toggle, This browser,
 * Send test (premium pass: no Contact Email). Matches prod 1b4ea74
 * Services Web Push as closely as MINIMAL Settings allows. No helper text. Web Push only. */
import { useState } from 'react';
import { NoticeToast } from '@/components/start/SetupToast';
import { useKioskWidth } from '@/components/home/KioskHome';
import { WebPushConnector } from '@/components/home/WebPushConnector';

export function NotificationsSettings() {
  const [toast, setToast] = useState('');
  const wide = useKioskWidth();
  return (
    <section data-testid="notifications-section" style={{ padding: wide ? 0 : '0 20px', display: 'flex', flexDirection: 'column', gap: 28 }}>
      {toast && <NoticeToast key={toast} text={toast} onDone={() => setToast('')} />}
      <WebPushConnector onSaved={setToast} />
    </section>
  );
}

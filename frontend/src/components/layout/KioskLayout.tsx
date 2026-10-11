import { Outlet } from 'react-router';
import { useLayoutMode } from '@/contexts/LayoutModeContext';
import { TopBar } from './TopBar';

export function KioskLayout() {
  const { mode } = useLayoutMode();

  // TV mode uses expanded layout with TopBar visible
  const showTopBar = mode !== 'compact';

  return (
    <div className="relative min-h-dvh bg-kiosk-black">
      {showTopBar && <TopBar />}
      <main>
        <Outlet />
      </main>
    </div>
  );
}

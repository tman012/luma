import { Link } from 'react-router';

interface MobileSideMenuProps {
  open: boolean;
  onClose: () => void;
  onRoomsOpen?: () => void;
  onVibeOpen?: () => void;
  onLibrariesOpen?: () => void;
}

/** SPA routes use React Router Link; legacy routes use plain <a> */
const MENU_ITEMS = [
  { label: 'Home', href: '/', icon: 'home', spa: true },
  { label: 'Explore', href: '/explore', icon: 'compass', spa: true },
  { label: 'Luma AI', href: '/downloads', icon: 'vibe', spa: true },
  { label: 'Library', href: '/browse', icon: 'film', spa: true },
  { label: 'Watchlist', href: '/watchlist', icon: 'bookmark', spa: true },
  { label: 'Rooms', href: '/rooms', icon: 'play', spa: true },
  { label: 'Settings', href: '/settings', icon: 'settings', spa: true },
] as const;

function MenuIcon({ name }: { name: string }) {
  switch (name) {
    case 'home':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /><polyline points="9 22 9 12 15 12 15 22" />
        </svg>
      );
    case 'compass':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" /><polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
        </svg>
      );
    case 'film':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" /><line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" /><line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" /><line x1="2" y1="17" x2="7" y2="17" /><line x1="17" y1="7" x2="22" y2="7" /><line x1="17" y1="17" x2="22" y2="17" />
        </svg>
      );
    case 'bookmark':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="m19 21-7-5-7 5V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2z" />
        </svg>
      );
    case 'play':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <circle cx="12" cy="12" r="10" /><polygon points="10 8 16 12 10 16 10 8" />
        </svg>
      );
    case 'vibe':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.582a.5.5 0 0 1 0 .963L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z" />
          <path d="M20 3v4" /><path d="M22 5h-4" />
        </svg>
      );
    case 'settings':
      return (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
          <circle cx="12" cy="12" r="3" />
        </svg>
      );
    default:
      return null;
  }
}

export function MobileSideMenu({ open, onClose, onRoomsOpen, onVibeOpen, onLibrariesOpen }: MobileSideMenuProps) {
  return (
    <>
      {/* Overlay */}
      {open && (
        <div
          className="luma-fullscreen luma-bleed fixed inset-0 z-[999] bg-black/50"
          onClick={onClose}
        />
      )}

      {/* Slide-out menu */}
      <nav
        className="fixed top-0 left-0 z-[1000] h-full w-[280px] bg-kiosk-black overflow-y-auto transition-transform duration-300"
        style={{
          paddingTop: 'calc(env(safe-area-inset-top, 0px) + 80px)',
          transform: open ? 'translateX(0)' : 'translateX(-100%)',
        }}
      >
        {MENU_ITEMS.map((item) => {
          const cls = "flex items-center gap-4 px-6 py-[18px] text-white border-b border-white/5 active:bg-white/5";
          const inner = (
            <>
              <span className="text-[#888]">
                <MenuIcon name={item.icon} />
              </span>
              <span className="text-[15px]">{item.label}</span>
            </>
          );
          // Vibe item opens the modal instead of navigating (avoids iOS swipe-back flash)
          if (item.label === 'Luma AI' && onVibeOpen) {
            return (
              <button
                key={item.label}
                type="button"
                className={cls}
                onClick={() => { onClose(); onVibeOpen(); }}
              >
                {inner}
              </button>
            );
          }
          // Library item opens the bottom sheet instead of navigating
          if (item.label === 'Library' && onLibrariesOpen) {
            return (
              <button
                key={item.label}
                type="button"
                className={cls}
                onClick={() => { onClose(); onLibrariesOpen(); }}
              >
                {inner}
              </button>
            );
          }
          // Rooms item opens the bottom sheet instead of navigating
          if (item.label === 'Rooms' && onRoomsOpen) {
            return (
              <button
                key={item.label}
                type="button"
                className={cls}
                onClick={() => { onClose(); onRoomsOpen(); }}
              >
                {inner}
              </button>
            );
          }
          return item.spa ? (
            <Link key={item.label} to={item.href} replace={'replace' in item && !!item.replace} className={cls} onClick={onClose}>
              {inner}
            </Link>
          ) : (
            <a key={item.label} href={item.href} className={cls} onClick={onClose}>
              {inner}
            </a>
          );
        })}
      </nav>
    </>
  );
}

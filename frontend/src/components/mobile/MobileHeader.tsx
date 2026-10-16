import { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react';
import { MobileSearchOverlay } from './MobileSearchOverlay';
import { MobileSideMenu } from './MobileSideMenu';
import { RoomsSheet } from '@/components/rooms/RoomsSheet';
import { LibrariesSheet } from '@/components/libraries/LibrariesSheet';
import { useModalHost } from '@/contexts/ModalHostContext';

interface PersonSearchRequest {
  name: string;
  personId: number;
}

interface MobileHeaderProps {
  /** Whether the page has been scrolled (enables frosted glass effect) */
  scrolled?: boolean;
  /** Hide the search bar (e.g. on Rooms page) */
  hideSearch?: boolean;
  /** Called with the measured header height (px) after mount/resize */
  onHeightChange?: (height: number) => void;
  /** Ref to the scroll container — used to dismiss keyboard on scroll */
  scrollRef?: React.RefObject<HTMLElement | null>;
}

export function MobileHeader({ scrolled = false, hideSearch = false, onHeightChange, scrollRef }: MobileHeaderProps) {
  const [searchActive, setSearchActive] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [roomsOpen, setRoomsOpen] = useState(false);
  const [librariesOpen, setLibrariesOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [personRequest, setPersonRequest] = useState<PersonSearchRequest | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const headerRef = useRef<HTMLElement>(null);
  const { openVibeEngine } = useModalHost();

  // Measure and report header height after mount and on resize
  useLayoutEffect(() => {
    if (!onHeightChange || !headerRef.current) return;
    const report = () => {
      if (headerRef.current) onHeightChange(headerRef.current.offsetHeight);
    };
    report();
    const ro = new ResizeObserver(report);
    ro.observe(headerRef.current);
    return () => ro.disconnect();
  }, [onHeightChange]);

  // Dismiss keyboard when the page scroll container scrolls
  useEffect(() => {
    const el = scrollRef?.current;
    if (!el) return;
    const handler = () => inputRef.current?.blur();
    el.addEventListener('scroll', handler, { passive: true });
    return () => el.removeEventListener('scroll', handler);
  }, [scrollRef]);

  const handleSearchFocus = useCallback(() => {
    setSearchActive(true);
  }, []);

  const handleSearchClose = useCallback(() => {
    setSearchActive(false);
    setPersonRequest(null);
    setQuery('');
    inputRef.current?.blur();
  }, []);

  // Listen for person search events from modals (custom DOM event)
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail as { name: string; personId?: number };
      if (detail?.name && detail?.personId) {
        setPersonRequest({ name: detail.name, personId: detail.personId });
        setQuery(detail.name);
        setSearchActive(true);
      }
    };
    window.addEventListener('mobilePersonSearch', handler);
    return () => window.removeEventListener('mobilePersonSearch', handler);
  }, []);

  // Close menu on outside click or navigation
  useEffect(() => {
    if (!menuOpen) return;
    const handler = () => setMenuOpen(false);
    window.addEventListener('popstate', handler);
    return () => window.removeEventListener('popstate', handler);
  }, [menuOpen]);

  return (
    <>
      <header
        ref={headerRef}
        className="fixed top-0 inset-x-0 z-50 flex items-center gap-3 px-4 pb-3"
        style={{
          paddingTop: 'env(safe-area-inset-top, 0px)',
          backgroundColor: scrolled ? 'color-mix(in srgb, var(--color-kiosk-black) 80%, transparent)' : 'var(--color-kiosk-black)',
          backdropFilter: scrolled ? 'blur(20px)' : 'none',
          WebkitBackdropFilter: scrolled ? 'blur(20px)' : 'none',
        }}
      >
        {/* Hamburger */}
        <button
          type="button"
          onClick={() => setMenuOpen((v) => !v)}
          className="flex flex-col justify-center w-10 h-10 flex-shrink-0 gap-[6px]"
          aria-label="Menu"
        >
          <span className="block w-[26px] h-[2.5px] bg-white rounded-full" />
          <span className="block w-[26px] h-[2.5px] bg-white rounded-full" />
          <span className="block w-[26px] h-[2.5px] bg-white rounded-full" />
        </button>

        {/* Search bar — real input, always visible */}
        {!hideSearch && (
          <div className="flex-1 relative">
            <input
              ref={inputRef}
              type="search"
              inputMode="search"
              enterKeyHint="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onFocus={handleSearchFocus}
              placeholder="Search movies and shows..."
              autoComplete="off"
              autoCorrect="off"
              autoCapitalize="off"
              spellCheck={false}
              className="w-full py-3 px-5 bg-[#333] border-none rounded-full text-[15px] text-white placeholder:text-[#666] focus:outline-none focus:bg-[#444]"
            />
          </div>
        )}

      </header>

      {/* Side menu */}
      <MobileSideMenu open={menuOpen} onClose={() => setMenuOpen(false)} onRoomsOpen={() => setRoomsOpen(true)} onVibeOpen={openVibeEngine} onLibrariesOpen={() => setLibrariesOpen(true)} />

      {/* Rooms bottom sheet */}
      <RoomsSheet open={roomsOpen} onClose={() => setRoomsOpen(false)} />

      {/* Libraries bottom sheet */}
      <LibrariesSheet open={librariesOpen} onClose={() => setLibrariesOpen(false)} />

      {/* Search overlay — opens when input is focused or person search triggered */}
      {searchActive && (
        <MobileSearchOverlay
          initialQuery={personRequest ? '' : query}
          initialPerson={personRequest ?? undefined}
          onClose={handleSearchClose}
        />
      )}
    </>
  );
}

/** Search in a LUMA_MINIMAL build: the C2 palette on desktop (v215; the kiosk
 * overlay still shows person filmographies), prod 1b4ea74's SearchShelf below 1024px (v261). Opened by the header
 * search button (`open-search`) or '/' on desktop. Results open the
 * existing movie/TV popup. */
import { useCallback, useEffect, useState } from 'react';
import { GlobalSearchHost } from './GlobalSearchHost';
import { HomeSearchPalette } from './HomeSearchPalette';
import { SearchShelf } from './SearchShelf';
import { useModalHost } from '@/contexts/ModalHostContext';
import { useKioskWidth } from '@/components/home/KioskHome';

export function openSearch() {
  window.dispatchEvent(new Event('open-search'));
}

export function MinimalSearchHost() {
  const wide = useKioskWidth();
  const [phoneOpen, setPhoneOpen] = useState(false);
  const [searchSeed, setSearchSeed] = useState<string | undefined>(undefined);
  const modalHost = useModalHost();

  // v261: prod's Home handlePersonSearch (AuthenticatedHomePage.tsx:854-862) for popups the shelf opens: a cast
  // tap opens the person modal when it has an id, otherwise the shelf pre-filled with the name.
  const handlePersonSearch = useCallback((name: string, personId?: number) => {
    modalHost.close();
    if (personId) {
      setTimeout(() => modalHost.openPerson({ personId, personName: name }), 320);
    } else {
      setSearchSeed(name);
      setPhoneOpen(true);
    }
  }, [modalHost]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return;
      e.preventDefault();
      openSearch();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    if (wide) return;
    const onOpen = () => setPhoneOpen(true);
    window.addEventListener('open-search', onOpen);
    return () => window.removeEventListener('open-search', onOpen);
  }, [wide]);

  // v215 C2: on desktop '/' and `open-search` open the centered palette; the kiosk overlay stays for person views.
  if (wide) return <><GlobalSearchHost palette /><HomeSearchPalette /></>;
  return phoneOpen ? (
    <SearchShelf
      onClose={() => { setPhoneOpen(false); setSearchSeed(undefined); }}
      initialQuery={searchSeed}
      onPersonSearch={handlePersonSearch}
    />
  ) : null;
}

export function SearchIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  );
}

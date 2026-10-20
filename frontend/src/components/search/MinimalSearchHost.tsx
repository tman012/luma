/** Search in a LUMA_MINIMAL build: the C2 palette on desktop (v215; the kiosk
 * overlay still shows person filmographies), the phone overlay below 1024px). Opened by the header
 * search button (`open-search`) or '/' on desktop. Results open the
 * existing movie/TV popup. */
import { useEffect, useState } from 'react';
import { GlobalSearchHost } from './GlobalSearchHost';
import { HomeSearchPalette } from './HomeSearchPalette';
import { MobileSearchOverlay } from '@/components/mobile/MobileSearchOverlay';
import { useKioskWidth } from '@/components/home/KioskHome';

export function openSearch() {
  window.dispatchEvent(new Event('open-search'));
}

export function MinimalSearchHost() {
  const wide = useKioskWidth();
  const [phoneOpen, setPhoneOpen] = useState(false);

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
  return phoneOpen ? <MobileSearchOverlay onClose={() => setPhoneOpen(false)} /> : null;
}

export function SearchIcon({ size = 20 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
    </svg>
  );
}

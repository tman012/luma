/**
 * GlobalSearchHost — always-mounted host for the kiosk search overlay.
 *
 * The search overlay used to live inside HeroSection, so the TopBar search
 * button (which dispatches `open-search`) was a dead click on any page/state
 * where the Hero wasn't mounted. Mounting the overlay once at the app root
 * makes search available everywhere.
 *
 * Listens for:
 *   - `open-search`  → opens the empty search overlay
 *   - `search-query` → opens with { query, tab?, personId? } from event detail
 */

import { useEffect, useRef } from 'react';
import { KioskSearch, type KioskSearchHandle } from './KioskSearch';

export function GlobalSearchHost() {
  const searchRef = useRef<KioskSearchHandle>(null);

  useEffect(() => {
    const onOpen = () => searchRef.current?.openSearch('', 'all');
    const onQuery = (e: Event) => {
      const detail = (e as CustomEvent).detail;
      if (detail?.query) {
        searchRef.current?.openSearch(detail.query, detail.tab || 'all', detail.personId);
      } else {
        searchRef.current?.openSearch('', 'all');
      }
    };
    window.addEventListener('open-search', onOpen);
    window.addEventListener('search-query', onQuery);
    return () => {
      window.removeEventListener('open-search', onOpen);
      window.removeEventListener('search-query', onQuery);
    };
  }, []);

  return <KioskSearch ref={searchRef} />;
}

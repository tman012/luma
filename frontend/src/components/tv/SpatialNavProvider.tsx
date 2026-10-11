/**
 * SpatialNavProvider — initializes the spatial nav manager and makes it
 * available to useFocusable via the global setter.
 *
 * Must be rendered inside TVModeProvider. Only creates the manager when
 * TV mode is active.
 */

import { useEffect, type ReactNode } from 'react';
import { useSpatialNav } from '@/hooks/useSpatialNav';
import { setSpatialNavManager } from '@/hooks/useFocusable';

export function SpatialNavProvider({ children }: { children: ReactNode }) {
  const manager = useSpatialNav();

  // Set synchronously during render so children effects (which fire before
  // parent effects) can access the manager when they register elements.
  setSpatialNavManager(manager);

  // Cleanup on unmount only
  useEffect(() => {
    return () => setSpatialNavManager(null);
  }, []);

  return <>{children}</>;
}

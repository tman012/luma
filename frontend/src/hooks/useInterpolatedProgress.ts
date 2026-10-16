import { useState, useEffect, useRef } from 'react';

/**
 * Interpolates playback position locally so the timer ticks
 * every second instead of jumping whenever the server reports a new offset.
 */
export function useInterpolatedProgress(
  serverOffset: number,
  duration: number,
  isPlaying: boolean,
) {
  const [displayOffset, setDisplayOffset] = useState(serverOffset);
  const syncRef = useRef({ offset: serverOffset, wallTime: Date.now() });

  // Sync reference point whenever server data changes
  useEffect(() => {
    syncRef.current = { offset: serverOffset, wallTime: Date.now() };
    setDisplayOffset(serverOffset);
  }, [serverOffset]);

  // Tick locally every second while playing
  useEffect(() => {
    if (!isPlaying || duration <= 0) return;
    const id = setInterval(() => {
      const elapsed = Date.now() - syncRef.current.wallTime;
      const interpolated = Math.min(syncRef.current.offset + elapsed, duration);
      setDisplayOffset(interpolated);
    }, 1000);
    return () => clearInterval(id);
  }, [isPlaying, duration]);

  const pct = duration > 0 ? Math.round((displayOffset / duration) * 100) : 0;
  return { displayOffset, pct };
}

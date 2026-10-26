/** Live TV empty-art fallback (v290). Shared by RoomsSheet RoomCard + FullScreenRemote. */
import type { ActivityData } from '@/hooks/useNotifications';

type LiveProbe = Pick<ActivityData, 'appName' | 'title' | 'thumb' | 'art'> & {
  mediaType?: string;
};

/** True when ADB/activity playback is live and has no poster/backdrop (FE sky treatment). */
export function isLiveTvNoArt(
  a: LiveProbe | null | undefined,
  hasPlexSession: boolean,
): boolean {
  if (hasPlexSession || !a) return false;
  if (a.thumb || a.art) return false;
  const media = (a.mediaType || '').toLowerCase();
  if (media === 'live' || media === 'livetv' || media === 'live_tv') return true;
  const name = (a.appName || '').trim();
  if (/^tivimate$/i.test(name) || /^live tv$/i.test(name)) return true;
  const title = (a.title || '').trim();
  if (/^watching live tv$/i.test(title)) return true;
  return false;
}

/** AmEng "Live TV", or a real channel name when the activity title is one. */
export function liveTvDisplayTitle(a: LiveProbe): string {
  const t = (a.title || '').trim();
  if (!t) return 'Live TV';
  if (/^watching live tv$/i.test(t)) return 'Live TV';
  if (/^playing in\b/i.test(t)) return 'Live TV';
  return t;
}

/** Stroke TV + antenna glyph (Designer GO). */
export function LiveTvGlyph({
  size = 28,
  className,
}: {
  size?: number;
  className?: string;
}) {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden
    >
      <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
      <polyline points="17 2 12 7 7 2" />
    </svg>
  );
}

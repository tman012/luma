/**
 * Full-page "Connect <service>" state for screens that need a service that
 * isn't connected (Discover without TMDB, Watchlist without Radarr, …).
 */
import { requestConnect, type ServiceId } from './connectService';

const NAMES: Record<ServiceId, string> = {
  tmdb: 'TMDB', radarr: 'Radarr', sonarr: 'Sonarr', qbittorrent: 'qBittorrent',
};

export function ConnectServiceCard({ service, fullPage = true }: { service: ServiceId; fullPage?: boolean }) {
  const name = NAMES[service];
  return (
    <div
      className="flex flex-col items-center justify-center gap-5 px-6 text-center"
      style={{ minHeight: fullPage ? '80dvh' : 220 }}
      data-connect-card={service}
    >
      <div
        className="flex h-16 w-16 items-center justify-center rounded-full"
        style={{ background: 'rgba(255,255,255,0.08)', border: '1px solid rgba(255,255,255,0.14)' }}
        aria-hidden="true"
      >
        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" className="text-white/80">
          <path d="M9 7H7a5 5 0 0 0 0 10h2M15 7h2a5 5 0 0 1 0 10h-2M8 12h8" strokeLinecap="round" />
        </svg>
      </div>
      <p className="text-[22px] font-semibold text-white">Connect {name}</p>
      <button
        type="button"
        onClick={() => requestConnect(service, { force: true })}
        className="rounded-full bg-white px-7 py-2.5 text-[15px] font-semibold text-[#15102a] active:scale-[0.98]"
      >
        Connect
      </button>
    </div>
  );
}

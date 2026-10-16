/**
 * ServicesTab — Plex, Sonarr, Radarr, Indexer, qBittorrent,
 * TMDb, OMDb, Fanart.tv, Trakt, Web Push.
 */

import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';
import { useSettings } from './useSettings';
import { fetchRadarrProfiles, fetchSonarrProfiles, type QualityProfile } from '@/services/settingsApi';
import { UsersStatusCard } from './UsersStatusCard';

// -- Sonarr card (needs quality profile dropdown) -----------------

function SonarrCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);

  useEffect(() => {
    fetchSonarrProfiles().then(setProfiles);
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use first available' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  return (
    <SettingsCard
      title="Sonarr"
      subtitle="TV show management and monitoring"
      paths={['sonarr.url', 'sonarr.api_key', 'sonarr.default_quality_profile_id']}
      testService="sonarr"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
        </svg>
      }
    >
      <FormField label="URL" path="sonarr.url" placeholder="http://192.168.1.62:8989" />
      <FormField label="API Key" path="sonarr.api_key" type="password" placeholder="Sonarr API key" />
      <FormField
        label="Quality Profile"
        path="sonarr.default_quality_profile_id"
        type="select"
        options={profileOptions}
      />
    </SettingsCard>
  );
}

// -- Radarr card (needs quality profile dropdown) -----------------

function RadarrCard() {
  const { getByPath, setByPath, testConnection } = useSettings();
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles);
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use first available' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const upgradeProfiles = (getByPath('radarr.upgrade_quality_profile_ids') as number[]) ?? [];
  const toggleUpgradeProfile = useCallback(
    (id: number) => {
      const current = (getByPath('radarr.upgrade_quality_profile_ids') as number[]) ?? [];
      const next = current.includes(id)
        ? current.filter((x) => x !== id)
        : [...current, id];
      setByPath('radarr.upgrade_quality_profile_ids', next);
    },
    [getByPath, setByPath],
  );

  const handleTest = useCallback(async () => {
    return testConnection('radarr');
  }, [testConnection]);

  return (
    <SettingsCard
      title="Radarr"
      subtitle="Movie search, quality filtering, and downloads"
      paths={[
        'radarr.url',
        'radarr.api_key',
        'radarr.default_quality_profile_id',
        'radarr.minimum_availability',
        'radarr.monitoring_enabled',
        'radarr.upgrade_quality_profile_ids',
      ]}
      testService="radarr"
      onTest={handleTest}
      note="Indexers are managed in Radarr / Prowlarr. Movie sources use Radarr's interactive search."
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" /><line x1="2" y1="17" x2="7" y2="17" />
          <line x1="17" y1="17" x2="22" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
        </svg>
      }
    >
      <FormField label="URL" path="radarr.url" placeholder="http://192.168.1.62:7878" />
      <FormField label="API Key" path="radarr.api_key" type="password" placeholder="Radarr API key" />
      <FormField
        label="Quality Profile"
        path="radarr.default_quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Minimum Availability"
        path="radarr.minimum_availability"
        type="select"
        options={[
          { value: 'announced', label: 'Announced' },
          { value: 'inCinemas', label: 'In Cinemas' },
          { value: 'released', label: 'Released' },
        ]}
      />
      <div className="sm:col-span-2">
        <ToggleSwitch label="Monitoring Enabled" path="radarr.monitoring_enabled" />
      </div>
      {profiles.length > 0 && (
        <div className="sm:col-span-2 flex flex-col gap-1.5">
          <label className="text-[13px] font-medium text-white/60">Upgrade/Watchlist Profiles</label>
          <div className="flex flex-wrap gap-2 rounded-lg border border-white/10 bg-white/[0.03] p-3 min-h-[42px]">
            {profiles.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => toggleUpgradeProfile(p.id)}
                className={`rounded-lg px-3 py-1.5 text-[12px] font-medium transition-colors ${
                  upgradeProfiles.includes(p.id)
                    ? 'bg-emerald-500/20 text-emerald-400 border border-emerald-500/30'
                    : 'bg-white/5 text-white/40 border border-white/8 hover:bg-white/8'
                }`}
              >
                {p.name}
              </button>
            ))}
          </div>
          <span className="text-[11px] text-white/30">
            Profiles shown in the upgrade/watchlist quality picker. Leave empty to show all.
          </span>
        </div>
      )}
    </SettingsCard>
  );
}

// -- Indexer card (conditional provider fields) -------------------

function IndexerCard() {
  const { getByPath } = useSettings();
  const provider = (getByPath('indexer.provider') as string) ?? 'prowlarr';

  return (
    <SettingsCard
      title="Indexer"
      subtitle="Prowlarr or Torznab for Explore page"
      paths={[
        'indexer.provider',
        'indexer.base_url',
        'indexer.torznab_url',
        'indexer.api_key',
        'indexer.movie_categories',
        'indexer.max_age_days',
        'indexer.min_seeders',
        'indexer.limit',
      ]}
      testService="indexer"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      }
    >
      <FormField
        label="Provider"
        path="indexer.provider"
        type="select"
        options={[
          { value: 'prowlarr', label: 'Prowlarr' },
          { value: 'torznab', label: 'Torznab' },
        ]}
      />
      {provider === 'prowlarr' ? (
        <FormField label="Prowlarr URL" path="indexer.base_url" placeholder="http://192.168.1.62:9696" />
      ) : (
        <FormField label="Torznab URL" path="indexer.torznab_url" placeholder="http://host/api/v2.0/indexers/all/results/torznab" />
      )}
      <FormField label="API Key" path="indexer.api_key" type="password" placeholder="Indexer API key" />
      <FormField label="Movie Categories" path="indexer.movie_categories" placeholder="2000" />
      <FormField label="Max Age (days)" path="indexer.max_age_days" type="number" placeholder="30" min={1} max={365} />
      <FormField label="Min Seeders" path="indexer.min_seeders" type="number" placeholder="1" min={0} />
      <FormField label="Limit" path="indexer.limit" type="number" placeholder="100" min={10} max={500} />
    </SettingsCard>
  );
}

// -- Main export --------------------------------------------------

export function ServicesTab() {
  const items: CardItem[] = [
    {
      title: 'Users',
      subtitle: 'Manage registered users',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
          <circle cx="9" cy="7" r="4" />
          <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
          <path d="M16 3.13a4 4 0 0 1 0 7.75" />
        </svg>
      ),
      content: <UsersStatusCard />,
    },
    {
      title: 'Plex Media Server',
      subtitle: 'Media streaming and library management',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <polygon points="5 3 19 12 5 21 5 3" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Plex Media Server"
          subtitle="Media streaming and library management"
          paths={['plex.url', 'plex.token', 'plex.client_port']}
          testService="plex"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <polygon points="5 3 19 12 5 21 5 3" />
            </svg>
          }
        >
          <FormField label="Server URL" path="plex.url" placeholder="http://192.168.1.25:32400" />
          <FormField label="Token" path="plex.token" type="password" placeholder="Plex authentication token" />
          <FormField label="Client Port" path="plex.client_port" type="number" placeholder="32500" min={1} max={65535} />
        </SettingsCard>
      ),
    },
    {
      title: 'Sonarr',
      subtitle: 'TV show management and monitoring',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
        </svg>
      ),
      content: <SonarrCard />,
    },
    {
      title: 'Radarr',
      subtitle: 'Movie search, quality filtering, and downloads',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" /><line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" /><line x1="2" y1="7" x2="7" y2="7" /><line x1="2" y1="17" x2="7" y2="17" />
          <line x1="17" y1="17" x2="22" y2="17" /><line x1="17" y1="7" x2="22" y2="7" />
        </svg>
      ),
      content: <RadarrCard />,
    },
    {
      title: 'Indexer',
      subtitle: 'Prowlarr or Torznab for Explore page',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
        </svg>
      ),
      content: <IndexerCard />,
    },
    {
      title: 'qBittorrent',
      subtitle: 'Download client for torrents',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="qBittorrent"
          subtitle="Download client for torrents"
          paths={[
            'qbittorrent.url',
            'qbittorrent.username',
            'qbittorrent.password',
            'qbittorrent.app_category',
            'qbittorrent.radarr_category',
          ]}
          testService="qbittorrent"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
              <polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
            </svg>
          }
        >
          <FormField label="URL" path="qbittorrent.url" placeholder="http://192.168.1.62:8080" />
          <FormField label="Username" path="qbittorrent.username" placeholder="Username" />
          <FormField label="Password" path="qbittorrent.password" type="password" placeholder="Password" />
          <FormField label="App Category" path="qbittorrent.app_category" placeholder="media-search-app" />
          <FormField label="Radarr Category" path="qbittorrent.radarr_category" placeholder="radarr" />
        </SettingsCard>
      ),
    },
    {
      title: 'TMDb',
      subtitle: 'The Movie Database -- metadata and posters',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="TMDb"
          subtitle="The Movie Database -- metadata and posters"
          paths={['apis.tmdb.api_key', 'apis.tmdb.region', 'apis.tmdb.base_url', 'apis.tmdb.image_base_url']}
          testService="tmdb"
          testLabel="Test API Key"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
            </svg>
          }
        >
          <FormField label="API Key" path="apis.tmdb.api_key" type="password" placeholder="TMDb API key" />
          <FormField label="Region" path="apis.tmdb.region" placeholder="US" />
          <FormField label="Base URL" path="apis.tmdb.base_url" placeholder="https://api.themoviedb.org/3" />
          <FormField label="Image Base URL" path="apis.tmdb.image_base_url" placeholder="https://image.tmdb.org/t/p" />
        </SettingsCard>
      ),
    },
    {
      title: 'OMDb',
      subtitle: 'Open Movie Database -- IMDb ratings and votes',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="OMDb"
          subtitle="Open Movie Database -- IMDb ratings and votes"
          paths={['apis.omdb.api_key']}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
            </svg>
          }
        >
          <FormField label="API Key" path="apis.omdb.api_key" type="password" placeholder="OMDb API key" />
        </SettingsCard>
      ),
    },
    {
      title: 'Fanart.tv',
      subtitle: 'Clear logos, backgrounds, and artwork',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
          <circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Fanart.tv"
          subtitle="Clear logos, backgrounds, and artwork"
          paths={['apis.fanart.api_key']}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="3" y="3" width="18" height="18" rx="2" ry="2" />
              <circle cx="8.5" cy="8.5" r="1.5" /><polyline points="21 15 16 10 5 21" />
            </svg>
          }
        >
          <FormField label="API Key" path="apis.fanart.api_key" type="password" placeholder="Fanart.tv API key" />
        </SettingsCard>
      ),
    },
    {
      title: 'Trakt',
      subtitle: 'Trending shows and movies on the Explore page',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Trakt"
          subtitle="Trending shows and movies on the Explore page"
          paths={['apis.trakt.enabled', 'apis.trakt.client_id', 'apis.trakt.client_secret']}
          testService="trakt"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <polyline points="22 12 18 12 15 21 9 3 6 12 2 12" />
            </svg>
          }
        >
          <div className="sm:col-span-2">
            <ToggleSwitch label="Enable Trakt" path="apis.trakt.enabled" />
          </div>
          <FormField label="Client ID" path="apis.trakt.client_id" type="password" placeholder="Trakt API client ID" />
          <FormField label="Client Secret (optional)" path="apis.trakt.client_secret" type="password" placeholder="Trakt API client secret" />
        </SettingsCard>
      ),
    },
    {
      title: 'Home Assistant',
      subtitle: 'TV power control via HA scenes and entities',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          <polyline points="9 22 9 12 15 12 15 22" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Home Assistant"
          subtitle="TV power control via HA scenes and entities"
          paths={['homeassistant.url', 'homeassistant.access_token']}
          testService="homeassistant"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
              <polyline points="9 22 9 12 15 12 15 22" />
            </svg>
          }
        >
          <FormField label="URL" path="homeassistant.url" placeholder="http://192.168.1.50:8123" />
          <FormField label="Access Token" path="homeassistant.access_token" type="password" placeholder="Long-lived access token" />
        </SettingsCard>
      ),
    },
    {
      title: 'Web Push Notifications',
      subtitle: 'Lockscreen notifications for new media',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
          <path d="M13.73 21a2 2 0 0 1-3.46 0" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Web Push Notifications"
          subtitle="Lockscreen notifications when movies and TV shows are added"
          paths={['notifications.web_push.enabled', 'notifications.web_push.vapid_email']}
          testService="webpush"
          testLabel="Send Test"
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M18 8A6 6 0 0 0 6 8c0 7-3 9-3 9h18s-3-2-3-9" />
              <path d="M13.73 21a2 2 0 0 1-3.46 0" />
            </svg>
          }
        >
          <div className="sm:col-span-2">
            <ToggleSwitch
              label="Enable Push Sending"
              path="notifications.web_push.enabled"
              hint="When off, subscriptions are still stored but notifications are not sent"
            />
          </div>
          <FormField
            label="Contact Email"
            path="notifications.web_push.vapid_email"
            placeholder="mailto:admin@bosley.media"
            hint="Required by the push protocol. Not shared publicly."
          />
          {/* -- Setup Guides -- */}
          <div className="sm:col-span-2 mt-2 space-y-2">

            {/* -- iPhone / iPad -- */}
            <details className="group">
              <summary className="cursor-pointer text-sm font-medium text-white/70 hover:text-white/90 transition-colors select-none flex items-center gap-2">
                <svg className="w-4 h-4 transition-transform group-open:rotate-90" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                </svg>
                iPhone / iPad Setup
              </summary>
              <div className="mt-3 text-sm text-white/60 space-y-4 pl-6">
                <p className="text-white/50 text-xs">Requires iOS 16.4 or later. Must use Safari for all steps.</p>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">1</span>
                    <p className="text-white/90 font-medium">Install the SSL Certificate</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open <strong className="text-white/80">Safari</strong> and go to: <br />
                      <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">http://192.168.1.62:5001/static/caddy-root.crt</code>
                    </li>
                    <li>Safari will say <strong className="text-white/80">"Profile Downloaded"</strong> — close the prompt</li>
                    <li>Open <strong className="text-white/80">Settings &rarr; General &rarr; VPN & Device Management</strong></li>
                    <li>Tap the <strong className="text-white/80">"Caddy Local Authority"</strong> profile &rarr; tap <strong className="text-white/80">Install</strong> &rarr; enter your passcode</li>
                    <li>Now go to <strong className="text-white/80">Settings &rarr; General &rarr; About &rarr; Certificate Trust Settings</strong></li>
                    <li>Toggle <strong className="text-white/80">full trust ON</strong> for "Caddy Local Authority"</li>
                  </ol>
                  <p className="mt-2 text-amber-400/70 text-xs">Both steps 4 and 6 are required. Without enabling full trust, Safari still won't trust the connection.</p>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">2</span>
                    <p className="text-white/90 font-medium">Add to Home Screen</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>In <strong className="text-white/80">Safari</strong>, go to <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">https://192.168.1.62</code></li>
                    <li>Verify the <strong className="text-white/80">padlock icon</strong> shows in the address bar (certificate is trusted)</li>
                    <li>Tap the <strong className="text-white/80">Share button</strong> (square with up arrow at the bottom)</li>
                    <li>Scroll down and tap <strong className="text-white/80">"Add to Home Screen"</strong></li>
                    <li>Name it and tap <strong className="text-white/80">Add</strong></li>
                  </ol>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">3</span>
                    <p className="text-white/90 font-medium">Enable Notifications</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open the app from the <strong className="text-white/80">Home Screen icon</strong> (not Safari)</li>
                    <li>A blue banner will appear: "Get notified when new movies and shows are added"</li>
                    <li>Tap <strong className="text-white/80">Enable</strong></li>
                    <li>iOS will prompt — tap <strong className="text-white/80">Allow</strong></li>
                    <li>Come back to this page and tap <strong className="text-white/80">Send Test</strong> above to verify</li>
                  </ol>
                </div>

                <div className="border-t border-white/5 pt-3">
                  <p className="text-white/70 font-medium text-xs mb-1.5">Troubleshooting</p>
                  <ul className="list-disc list-outside pl-4 space-y-1 text-xs">
                    <li><strong className="text-white/60">Icon missing or generic:</strong> Certificate must be trusted first, then re-add to Home Screen</li>
                    <li><strong className="text-white/60">"Not Secure" warning:</strong> Go back to Step 1 — make sure full trust is enabled</li>
                    <li><strong className="text-white/60">Notification banner gone:</strong> Clear Safari website data for this site, then reopen from Home Screen</li>
                    <li><strong className="text-white/60">Notifications stopped:</strong> Open the app from the Home Screen icon — it re-subscribes automatically</li>
                  </ul>
                </div>
              </div>
            </details>

            {/* -- Android -- */}
            <details className="group">
              <summary className="cursor-pointer text-sm font-medium text-white/70 hover:text-white/90 transition-colors select-none flex items-center gap-2">
                <svg className="w-4 h-4 transition-transform group-open:rotate-90" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                </svg>
                Android Phone Setup
              </summary>
              <div className="mt-3 text-sm text-white/60 space-y-4 pl-6">

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">1</span>
                    <p className="text-white/90 font-medium">Install the SSL Certificate</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open <strong className="text-white/80">Chrome</strong> and go to: <br />
                      <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">http://192.168.1.62:5001/static/caddy-root.crt</code>
                    </li>
                    <li>Chrome will download the certificate file</li>
                    <li>Open <strong className="text-white/80">Settings &rarr; Security &rarr; Encryption & credentials &rarr; Install a certificate &rarr; CA certificate</strong></li>
                    <li>Select the downloaded <strong className="text-white/80">caddy-root.crt</strong> file and confirm</li>
                  </ol>
                  <p className="mt-2 text-white/40 text-xs">Path may vary by manufacturer (Samsung: Security &rarr; Other security settings &rarr; Install from storage).</p>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">2</span>
                    <p className="text-white/90 font-medium">Add to Home Screen</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>In <strong className="text-white/80">Chrome</strong>, go to <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">https://192.168.1.62</code></li>
                    <li>Verify the <strong className="text-white/80">padlock icon</strong> shows (certificate is trusted)</li>
                    <li>Tap the <strong className="text-white/80">three-dot menu</strong> &rarr; <strong className="text-white/80">"Add to Home screen"</strong> (or accept the install banner if it appears)</li>
                  </ol>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">3</span>
                    <p className="text-white/90 font-medium">Enable Notifications</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open the app from the <strong className="text-white/80">Home Screen icon</strong> (not Chrome)</li>
                    <li>A banner will appear: "Get notified when new movies and shows are added"</li>
                    <li>Tap <strong className="text-white/80">Enable</strong>, then <strong className="text-white/80">Allow</strong></li>
                    <li>Come back to this page and tap <strong className="text-white/80">Send Test</strong> above to verify</li>
                  </ol>
                </div>

                <div className="border-t border-white/5 pt-3">
                  <p className="text-white/70 font-medium text-xs mb-1.5">Troubleshooting</p>
                  <ul className="list-disc list-outside pl-4 space-y-1 text-xs">
                    <li><strong className="text-white/60">"Not Secure" warning:</strong> Go back to Step 1 — the CA certificate needs to be installed</li>
                    <li><strong className="text-white/60">Notifications stopped:</strong> Open the app from the Home Screen icon — it re-subscribes automatically</li>
                  </ul>
                </div>
              </div>
            </details>

            {/* -- Fire HD / Android Tablet -- */}
            <details className="group">
              <summary className="cursor-pointer text-sm font-medium text-white/70 hover:text-white/90 transition-colors select-none flex items-center gap-2">
                <svg className="w-4 h-4 transition-transform group-open:rotate-90" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                </svg>
                Fire HD / Android Tablet Setup (APK)
              </summary>
              <div className="mt-3 text-sm text-white/60 space-y-4 pl-6">
                <p className="text-white/50 text-xs">Fire tablets use a native APK wrapper instead of the browser PWA. This gives a full-screen app experience with notifications.</p>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">1</span>
                    <p className="text-white/90 font-medium">Enable Sideloading</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>On the Fire tablet, go to <strong className="text-white/80">Settings &rarr; Security & Privacy</strong></li>
                    <li>Enable <strong className="text-white/80">"Apps from Unknown Sources"</strong></li>
                    <li>If prompted for a specific app (e.g. Silk Browser), allow it</li>
                  </ol>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">2</span>
                    <p className="text-white/90 font-medium">Install the SSL Certificate</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open <strong className="text-white/80">Silk Browser</strong> and go to: <br />
                      <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">http://192.168.1.62:5001/static/caddy-root.crt</code>
                    </li>
                    <li>The file will download — open it from the notification or Downloads</li>
                    <li>Go to <strong className="text-white/80">Settings &rarr; Security & Privacy &rarr; Install a certificate &rarr; CA certificate</strong></li>
                    <li>Select <strong className="text-white/80">caddy-root.crt</strong> and confirm</li>
                  </ol>
                  <p className="mt-2 text-white/40 text-xs">On older Fire OS: Settings &rarr; Security &rarr; Install credentials from storage.</p>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">3</span>
                    <p className="text-white/90 font-medium">Install the App</p>
                  </div>
                  <p className="text-white/50 text-xs mb-2">Download directly to the tablet, or sideload via ADB from a computer.</p>

                  <div className="space-y-3">
                    <div>
                      <p className="text-white/70 font-medium text-xs mb-1">Option A: Download on the tablet</p>
                      <ol className="list-decimal list-outside pl-4 space-y-1.5">
                        <li>In Silk Browser, go to: <br />
                          <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">http://192.168.1.62:5001/static/luma.apk</code>
                        </li>
                        <li>Tap the downloaded file and tap <strong className="text-white/80">Install</strong></li>
                      </ol>
                    </div>
                    <div>
                      <p className="text-white/70 font-medium text-xs mb-1">Option B: Sideload via ADB</p>
                      <ol className="list-decimal list-outside pl-4 space-y-1.5">
                        <li>On the tablet, enable <strong className="text-white/80">Settings &rarr; Device Options &rarr; Developer Options &rarr; USB Debugging</strong>
                          <br /><span className="text-white/40 text-xs">(Tap "Device Options" or serial number 7 times to unlock Developer Options)</span>
                        </li>
                        <li>Connect the tablet via USB and run: <br />
                          <code className="text-blue-400/90 text-xs bg-white/5 px-1.5 py-0.5 rounded select-all">adb install luma.apk</code>
                        </li>
                      </ol>
                    </div>
                  </div>
                </div>

                <div>
                  <div className="flex items-center gap-2 mb-2">
                    <span className="flex-shrink-0 w-6 h-6 rounded-full bg-white/10 text-white/90 text-xs font-bold flex items-center justify-center">4</span>
                    <p className="text-white/90 font-medium">Open & Enable Notifications</p>
                  </div>
                  <ol className="list-decimal list-outside pl-4 space-y-1.5">
                    <li>Open <strong className="text-white/80">Bosley Media</strong> from the app drawer</li>
                    <li>The notification banner will appear — tap <strong className="text-white/80">Enable</strong>, then <strong className="text-white/80">Allow</strong></li>
                    <li>Come back to this page and tap <strong className="text-white/80">Send Test</strong> above to verify</li>
                  </ol>
                </div>

                <div className="border-t border-white/5 pt-3">
                  <p className="text-white/70 font-medium text-xs mb-1.5">Troubleshooting</p>
                  <ul className="list-disc list-outside pl-4 space-y-1 text-xs">
                    <li><strong className="text-white/60">App won't install:</strong> Make sure "Apps from Unknown Sources" is enabled in Step 1</li>
                    <li><strong className="text-white/60">SSL / connection error in app:</strong> Make sure the CA certificate is installed (Step 2)</li>
                    <li><strong className="text-white/60">Updating the app:</strong> Download and install the APK again — it will update in place</li>
                  </ul>
                </div>
              </div>
            </details>

          </div>
        </SettingsCard>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Services</h2>
        <p className="mt-1 text-[13px] text-white/40">
          External services, APIs, and integrations.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

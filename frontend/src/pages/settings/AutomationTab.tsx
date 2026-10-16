/**
 * AutomationTab — Trending Watchlist automation settings.
 * Global interval + Movie (Radarr) + TV (Sonarr) cards + unified activity log.
 */

import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';
import {
  fetchRadarrProfiles,
  fetchTrendingWatchlistStatus,
  triggerTrendingWatchlistRun,
  fetchTrendingTvWatchlistStatus,
  triggerTrendingTvWatchlistRun,
  fetchNowAvailableWatchlistStatus,
  triggerNowAvailableWatchlistRun,
  fetchAnticipatedWatchlistStatus,
  triggerAnticipatedWatchlistRun,
  fetchLatestAutoGrabStatus,
  triggerLatestAutoGrabRun,
  fetchUpgrade4kAutoGrabStatus,
  triggerUpgrade4kAutoGrabRun,
  fetchPlexHealth,
  type QualityProfile,
  type PlexLibrary,
  type TrendingWatchlistStatus,
  type TrendingTvWatchlistStatus,
  type NowAvailableWatchlistStatus,
  type AnticipatedWatchlistStatus,
  type LatestAutoGrabStatus,
  type Upgrade4kAutoGrabStatus,
} from '@/services/settingsApi';
import { fetchQualityProfiles as fetchSonarrProfiles } from '@/services/sonarrApi';
import { ActivityFeed } from '@/components/activity/ActivityFeed';
import { MovieNightCard } from './MovieNightCard';
import { GenreRouletteCard } from './GenreRouletteCard';
import { FranchiseCompleterCard } from './FranchiseCompleterCard';
import { WatchlistDigitalDatesCard } from './WatchlistDigitalDatesCard';
import { DownloadsGuardCard } from './DownloadsGuardCard';

export function AutomationTab() {
  const items: CardItem[] = [
    {
      title: 'Automation Schedule',
      subtitle: 'Shared interval for all automation tasks',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      ),
      content: <AutomationIntervalCard />,
    },
    {
      title: 'Movie Night',
      subtitle: 'Weekly canon: 4K remux for you, classics for users',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" />
          <line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" />
        </svg>
      ),
      content: <MovieNightCard />,
    },
    {
      title: 'Genre Roulette',
      subtitle: 'Nightly: random genres, films you don’t own',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="10" />
          <circle cx="12" cy="12" r="3" />
          <path d="M12 2v5M12 17v5M2 12h5M17 12h5" />
        </svg>
      ),
      content: <GenreRouletteCard />,
    },
    {
      title: 'Franchise Completer',
      subtitle: 'Fill gaps in super-popular almost-complete franchises',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
          <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          <path d="M8 7h8M8 11h6" />
        </svg>
      ),
      content: <FranchiseCompleterCard />,
    },
    {
      title: 'Watchlist Digital Dates',
      subtitle: 'Morning check for newly dated watchlist movies',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="3" y="4" width="18" height="18" rx="2" />
          <line x1="16" y1="2" x2="16" y2="6" />
          <line x1="8" y1="2" x2="8" y2="6" />
          <line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      ),
      content: <WatchlistDigitalDatesCard />,
    },
    {
      title: 'New Releases',
      subtitle: 'Latest, Trending, Anticipated & Now Available',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2v4" />
          <path d="M12 18v4" />
          <path d="m4.93 4.93 2.83 2.83" />
          <path d="m16.24 16.24 2.83 2.83" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      ),
      content: <NewReleasesCard />,
    },
    {
      title: '4K Remux Auto-Grab',
      subtitle: 'Upgrade your library to 2160p Remux automatically',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 7l9-4 9 4-9 4-9-4z" />
          <path d="M3 12l9 4 9-4" />
          <path d="M3 17l9 4 9-4" />
        </svg>
      ),
      content: <Upgrade4kAutoGrabCard />,
    },
    {
      title: 'Trending TV Shows',
      subtitle: 'Auto-add trending first-season Trakt shows',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
          <polyline points="17 2 12 7 7 2" />
        </svg>
      ),
      content: <TrendingTvWatchlistCard />,
    },
    {
      // Last of the automations, and the only one that deletes — kept apart
      // from the acquisition cards rather than filed among them.
      title: 'Downloads Guard',
      subtitle: 'Free space floor: delete oldest downloads to stay above it',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <ellipse cx="12" cy="5" rx="9" ry="3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
          <path d="M3 12c0 1.66 4 3 9 3s9-1.34 9-3" />
        </svg>
      ),
      content: <DownloadsGuardCard />,
    },
    {
      title: 'Activity Log',
      subtitle: 'Media added by automations',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <polyline points="14 2 14 8 20 8" />
          <line x1="16" y1="13" x2="8" y2="13" />
          <line x1="16" y1="17" x2="8" y2="17" />
        </svg>
      ),
      content: <UnifiedActivityLog />,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Automation</h2>
        <p className="mt-1 text-[13px] text-white/40">
          Acquisition automations, franchise completer, and activity log.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

// -- Run Now button component -------------------------------------

function RunNowButton({ onRun, running }: { onRun: () => void; running?: boolean }) {
  const [busy, setBusy] = useState(false);

  const handleClick = useCallback(() => {
    if (busy || running) return;
    setBusy(true);
    onRun();
    // Reset after 30s — cycles can take a while with API calls
    setTimeout(() => setBusy(false), 30000);
  }, [busy, running, onRun]);

  // Clear busy when running transitions from true to false (cycle finished)
  useEffect(() => {
    if (!running && busy) {
      // Small delay to let the status refresh propagate
      const t = setTimeout(() => setBusy(false), 500);
      return () => clearTimeout(t);
    }
  }, [running, busy]);

  return (
    <button
      onClick={handleClick}
      disabled={busy || running}
      className="px-4 py-2.5 text-[12px] font-medium rounded-lg bg-white/10 hover:bg-white/15 text-white/70 hover:text-white/90 transition-all disabled:opacity-40 disabled:cursor-not-allowed min-h-[36px]"
    >
      {busy || running ? 'Running...' : 'Run Now'}
    </button>
  );
}


/** Poll status until last_run advances past baseline (or timeout).
 *  Stopping on !running alone can freeze a stale "Last ran" if the cycle
 *  finishes between polls or last_run is stamped after running clears. */
function pollStatusUntilLastRun(
  fetchStatus: () => Promise<{ running?: boolean; last_run?: string | null }>,
  setStatus: (s: any) => void,
  baseline: string | null,
  maxPolls = 20,
  intervalMs = 3000,
) {
  let polls = 0;
  const iv = setInterval(() => {
    polls++;
    fetchStatus().then((s) => {
      setStatus(s);
      const advanced = Boolean(s?.last_run && s.last_run !== baseline);
      if ((!s?.running && advanced) || polls >= maxPolls) clearInterval(iv);
    }).catch(() => {});
    if (polls >= maxPolls) clearInterval(iv);
  }, intervalMs);
}

// -- Global Automation Interval card ------------------------------

function AutomationIntervalCard() {
  return (
    <SettingsCard
      title="Automation Schedule"
      subtitle="Shared interval for all automation tasks"
      paths={['automation.interval_hours']}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      }
    >
      <FormField
        label="Check Interval (hours)"
        path="automation.interval_hours"
        type="number"
        min={1}
        max={24}
        hint="How often to run all automation tasks (1-24 hours)"
      />
    </SettingsCard>
  );
}

// -- Trending Movie Watchlist card --------------------------------

function TrendingMovieWatchlistCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<TrendingWatchlistStatus | null>(null);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles);
    fetchTrendingWatchlistStatus().then(setStatus).catch(() => {});
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use first available' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const handleRunNow = useCallback(() => {
    triggerTrendingWatchlistRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchTrendingWatchlistStatus, setStatus, baseline);
  }, [status?.last_run]);

  return (
    <SettingsCard
      title="Trending Movies"
      subtitle="Auto-add trending Trakt movies to Radarr"
      paths={[
        'automation.trending_watchlist.enabled',
        'automation.trending_watchlist.quality_profile_id',
        'automation.trending_watchlist.limit',
        'automation.trending_watchlist.search_on_add',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18" />
          <line x1="7" y1="2" x2="7" y2="22" />
          <line x1="17" y1="2" x2="17" y2="22" />
          <line x1="2" y1="12" x2="22" y2="12" />
          <line x1="2" y1="7" x2="7" y2="7" />
          <line x1="2" y1="17" x2="7" y2="17" />
          <line x1="17" y1="7" x2="22" y2="7" />
          <line x1="17" y1="17" x2="22" y2="17" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Fetches trending movies from Trakt. Movies already in Plex or Radarr are skipped."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.trending_watchlist.enabled"
        hint="Automatically add trending movies to Radarr"
      />
      <ToggleSwitch
        label="Search on Add"
        path="automation.trending_watchlist.search_on_add"
        hint="Trigger Radarr search when adding a movie"
      />
      <FormField
        label="Quality Profile"
        path="automation.trending_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Movies per Cycle"
        path="automation.trending_watchlist.limit"
        type="number"
        min={1}
        max={40}
        hint="Number of trending movies to check (1-40)"
      />

      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
      </p>
    </SettingsCard>
  );
}

// -- Now Available Movie Watchlist card ----------------------------

function NowAvailableMovieWatchlistCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<NowAvailableWatchlistStatus | null>(null);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles);
    fetchNowAvailableWatchlistStatus().then(setStatus).catch(() => {});
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use first available' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const handleRunNow = useCallback(() => {
    triggerNowAvailableWatchlistRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchNowAvailableWatchlistStatus, setStatus, baseline);
  }, [status?.last_run]);

  return (
    <SettingsCard
      title="Now Available Movies"
      subtitle="Auto-add top available indexer movies to Radarr"
      paths={[
        'automation.now_available_watchlist.enabled',
        'automation.now_available_watchlist.quality_profile_id',
        'automation.now_available_watchlist.limit',
        'automation.now_available_watchlist.search_on_add',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
          <polyline points="7 10 12 15 17 10" />
          <line x1="12" y1="15" x2="12" y2="3" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Fetches top available movies from your indexers. Movies already in Plex or Radarr are skipped."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.now_available_watchlist.enabled"
        hint="Automatically add top available movies to Radarr"
      />
      <ToggleSwitch
        label="Search on Add"
        path="automation.now_available_watchlist.search_on_add"
        hint="Trigger Radarr search when adding a movie"
      />
      <FormField
        label="Quality Profile"
        path="automation.now_available_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Movies per Cycle"
        path="automation.now_available_watchlist.limit"
        type="number"
        min={1}
        max={100}
        hint="Number of available movies to check (1-100)"
      />

      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
      </p>
    </SettingsCard>
  );
}

// -- Anticipated Watchlist card -----------------------------------

function AnticipatedWatchlistCard() {
  const [radarrProfiles, setRadarrProfiles] = useState<QualityProfile[]>([]);
  const [sonarrProfiles, setSonarrProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<AnticipatedWatchlistStatus | null>(null);

  useEffect(() => {
    fetchRadarrProfiles().then(setRadarrProfiles);
    fetchSonarrProfiles().then(setSonarrProfiles);
    fetchAnticipatedWatchlistStatus().then(setStatus).catch(() => {});
  }, []);

  const radarrOptions = [
    { value: 0, label: 'Use first available' },
    ...radarrProfiles.map((p) => ({ value: p.id, label: p.name })),
  ];
  const sonarrOptions = [
    { value: 0, label: 'Use first available' },
    ...sonarrProfiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const handleRunNow = useCallback(() => {
    triggerAnticipatedWatchlistRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchAnticipatedWatchlistStatus, setStatus, baseline);
  }, [status?.last_run]);

  return (
    <SettingsCard
      title="Anticipated"
      subtitle="Pre-order anticipated titles to Radarr/Sonarr"
      paths={[
        'automation.anticipated_watchlist.enabled',
        'automation.anticipated_watchlist.movies_enabled',
        'automation.anticipated_watchlist.shows_enabled',
        'automation.anticipated_watchlist.movie_quality_profile_id',
        'automation.anticipated_watchlist.tv_quality_profile_id',
        'automation.anticipated_watchlist.limit',
        'automation.anticipated_watchlist.search_on_add',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2v4" />
          <path d="M12 18v4" />
          <path d="m4.93 4.93 2.83 2.83" />
          <path d="m16.24 16.24 2.83 2.83" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Fetches Trakt's most anticipated (unreleased) movies and shows and adds them monitored, so they auto-download the moment they release. Titles already in Plex, Radarr, or Sonarr are skipped."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.anticipated_watchlist.enabled"
        hint="Pre-order anticipated titles so they download on release"
      />
      <ToggleSwitch
        label="Search on Add"
        path="automation.anticipated_watchlist.search_on_add"
        hint="Trigger a search on add (harmless for unreleased; catches any already-available)"
      />
      <ToggleSwitch
        label="Include Movies"
        path="automation.anticipated_watchlist.movies_enabled"
        hint="Add anticipated movies to Radarr"
      />
      <ToggleSwitch
        label="Include Shows"
        path="automation.anticipated_watchlist.shows_enabled"
        hint="Add anticipated shows to Sonarr"
      />
      <FormField
        label="Movie Quality Profile (Radarr)"
        path="automation.anticipated_watchlist.movie_quality_profile_id"
        type="select"
        options={radarrOptions}
      />
      <FormField
        label="TV Quality Profile (Sonarr)"
        path="automation.anticipated_watchlist.tv_quality_profile_id"
        type="select"
        options={sonarrOptions}
      />
      <FormField
        label="Titles per Cycle"
        path="automation.anticipated_watchlist.limit"
        type="number"
        min={1}
        max={40}
        hint="Anticipated titles to check per media type (1-40)"
      />

      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
      </p>
    </SettingsCard>
  );
}

// -- Trending TV Watchlist card -----------------------------------

function TrendingTvWatchlistCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<TrendingTvWatchlistStatus | null>(null);

  useEffect(() => {
    fetchSonarrProfiles().then(setProfiles);
    fetchTrendingTvWatchlistStatus().then(setStatus).catch(() => {});
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use first available' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const handleRunNow = useCallback(() => {
    triggerTrendingTvWatchlistRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchTrendingTvWatchlistStatus, setStatus, baseline);
  }, [status?.last_run]);

  return (
    <SettingsCard
      title="Trending TV Shows"
      subtitle="Auto-add trending first-season Trakt shows to Sonarr"
      paths={[
        'automation.trending_tv_watchlist.enabled',
        'automation.trending_tv_watchlist.quality_profile_id',
        'automation.trending_tv_watchlist.limit',
        'automation.trending_tv_watchlist.search_on_add',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="7" width="20" height="15" rx="2" ry="2" />
          <polyline points="17 2 12 7 7 2" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Fetches trending TV shows from Trakt, filters to first-season-only via TMDb. Shows in Plex or Sonarr are skipped."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.trending_tv_watchlist.enabled"
        hint="Automatically add trending first-season shows to Sonarr"
      />
      <ToggleSwitch
        label="Search on Add"
        path="automation.trending_tv_watchlist.search_on_add"
        hint="Trigger Sonarr search when adding a show"
      />
      <FormField
        label="Quality Profile"
        path="automation.trending_tv_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Shows per Cycle"
        path="automation.trending_tv_watchlist.limit"
        type="number"
        min={1}
        max={40}
        hint="Number of trending shows to check (1-40)"
      />

      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
      </p>
    </SettingsCard>
  );
}

// -- Latest Auto-Grab card ----------------------------------------

function LatestAutoGrabCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [status, setStatus] = useState<LatestAutoGrabStatus | null>(null);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles);
    fetchLatestAutoGrabStatus().then(setStatus).catch(() => {});
  }, []);

  const profileOptions = [
    { value: 0, label: 'Use Radarr default' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const handleRunNow = useCallback(() => {
    triggerLatestAutoGrabRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchLatestAutoGrabStatus, setStatus, baseline, 40);
  }, [status?.last_run]);

  const lastResult = status?.last_result;

  return (
    <SettingsCard
      title="Latest Auto-Grab"
      subtitle="Grab high-seeder releases the moment they trend"
      paths={[
        'automation.latest_auto_grab.enabled',
        'automation.latest_auto_grab.min_seeders',
        'automation.latest_auto_grab.max_age_hours',
        'automation.latest_auto_grab.scan_interval_minutes',
        'automation.latest_auto_grab.quality_profile_id',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <circle cx="12" cy="12" r="10" />
          <polyline points="12 6 12 12 16 14" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Scans the Latest feed (browse + 2160p Remux sweep). Grabs any release ≥ seeder threshold inside the age window. Ownership uses Plex: movies in any non-4K movie library are skipped; movies only in your 4K library still get a TFlix-profile grab."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.latest_auto_grab.enabled"
        hint="Automatically grab matching releases on every scan"
      />
      <FormField
        label="Quality Profile"
        path="automation.latest_auto_grab.quality_profile_id"
        type="select"
        options={profileOptions}
        hint="Radarr profile to use for grabs (should not include 2160p Remux — that's the 4K auto-grab's job)"
      />
      <FormField
        label="Minimum Seeders"
        path="automation.latest_auto_grab.min_seeders"
        type="number"
        min={1}
        max={5000}
        hint="Skip releases below this seeder count (default 60)"
      />
      <FormField
        label="Max Age (hours)"
        path="automation.latest_auto_grab.max_age_hours"
        type="number"
        min={1}
        max={168}
        hint="Only consider releases newer than this (default 24)"
      />
      <FormField
        label="Scan Interval (minutes)"
        path="automation.latest_auto_grab.scan_interval_minutes"
        type="number"
        min={5}
        max={120}
        hint="How often to scan the Latest feed (default 15)"
      />

      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
        {lastResult && (
          <span className="text-white/45">
            {' '}— checked {lastResult.checked}, grabbed {lastResult.grabbed}
            {lastResult.skipped_in_plex ? `, owned in Plex ${lastResult.skipped_in_plex}` : ''}
            {lastResult.skipped_rejected ? `, rejected ${lastResult.skipped_rejected}` : ''}
          </span>
        )}
      </p>
    </SettingsCard>
  );
}

// -- 4K Remux Auto-Grab card --------------------------------------

function Upgrade4kAutoGrabCard() {
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [libraries, setLibraries] = useState<PlexLibrary[]>([]);
  const [status, setStatus] = useState<Upgrade4kAutoGrabStatus | null>(null);

  useEffect(() => {
    fetchRadarrProfiles().then(setProfiles);
    fetchPlexHealth().then((d) => {
      if (d.libraries) setLibraries(d.libraries);
    }).catch(() => {});
    fetchUpgrade4kAutoGrabStatus().then(setStatus).catch(() => {});
  }, []);

  const movieLibraries = libraries.filter((l) => l.type === 'movie');

  const profileOptions = [
    { value: 0, label: 'Select a profile…' },
    ...profiles.map((p) => ({ value: p.id, label: p.name })),
  ];

  const sectionOptions = [
    { value: 0, label: 'Select your 4K library…' },
    ...movieLibraries.map((l) => ({ value: l.id, label: l.title })),
  ];

  const handleRunNow = useCallback(() => {
    triggerUpgrade4kAutoGrabRun().catch(() => {});
    const baseline = status?.last_run ?? null;
    pollStatusUntilLastRun(fetchUpgrade4kAutoGrabStatus, setStatus, baseline, 40);
  }, [status?.last_run]);

  const lastResult = status?.last_result;

  return (
    <SettingsCard
      title="4K Remux Auto-Grab"
      subtitle="Upgrade your library to 2160p Remux automatically"
      paths={[
        'automation.upgrade_4k_auto_grab.enabled',
        'automation.upgrade_4k_auto_grab.min_seeders',
        'automation.upgrade_4k_auto_grab.max_age_hours',
        'automation.upgrade_4k_auto_grab.scan_interval_minutes',
        'automation.upgrade_4k_auto_grab.quality_profile_id',
        'automation.upgrade_4k_auto_grab.plex_4k_section_id',
      ]}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 7l9-4 9 4-9 4-9-4z" />
          <path d="M3 12l9 4 9-4" />
          <path d="M3 17l9 4 9-4" />
        </svg>
      }
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
      note="Watches the Latest feed for 2160p Remux releases. Auto-grabs them for any movie not already in your 4K Plex library — even if the movie exists in other libraries. The Radarr quality profile must accept 2160p Remux."
    >
      <ToggleSwitch
        label="Enable"
        path="automation.upgrade_4k_auto_grab.enabled"
        hint="Automatically grab matching 2160p Remux releases"
      />
      <FormField
        label="Quality Profile (4K)"
        path="automation.upgrade_4k_auto_grab.quality_profile_id"
        type="select"
        options={profileOptions}
        hint="Movies will be set to this Radarr profile before grabbing"
      />
      <FormField
        label="4K Plex Library"
        path="automation.upgrade_4k_auto_grab.plex_4k_section_id"
        type="select"
        options={sectionOptions}
        hint="Movies already in this Plex section are skipped"
      />
      <FormField
        label="Minimum Seeders"
        path="automation.upgrade_4k_auto_grab.min_seeders"
        type="number"
        min={1}
        max={5000}
        hint="Skip releases below this seeder count (default 100)"
      />
      <FormField
        label="Max Age (hours)"
        path="automation.upgrade_4k_auto_grab.max_age_hours"
        type="number"
        min={1}
        max={168}
        hint="Only consider releases newer than this (default 24)"
      />
      <FormField
        label="Scan Interval (minutes)"
        path="automation.upgrade_4k_auto_grab.scan_interval_minutes"
        type="number"
        min={5}
        max={120}
        hint="How often to scan the Latest feed (default 15)"
      />
      <p className="col-span-full text-[11px] text-white/30">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'Never'}
        {lastResult && (
          <span className="text-white/45">
            {' '}— checked {lastResult.checked}, candidates {lastResult.candidates}, grabbed {lastResult.grabbed}
            {lastResult.skipped_in_4k ? `, skipped (already 4K) ${lastResult.skipped_in_4k}` : ''}
            {lastResult.skipped_rejected ? `, rejected ${lastResult.skipped_rejected}` : ''}
          </span>
        )}
      </p>
    </SettingsCard>
  );
}


// -- New Releases (grouped Latest / Trending / Anticipated / Now Available) --

function NewReleasesCard() {
  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-4">
        <h3 className="text-[15px] font-semibold text-white/90">New Releases</h3>
        <p className="mt-1 text-[12px] text-white/40">
          Latest, Trending Movies, Anticipated, and Now Available keep their own
          schedules and backends — this card groups their settings in one place.
          Trending TV stays separate below.
        </p>
      </div>
      <div className="space-y-4 [&>*]:!mt-0">
        <LatestAutoGrabCard />
        <TrendingMovieWatchlistCard />
        <AnticipatedWatchlistCard />
        <NowAvailableMovieWatchlistCard />
      </div>
    </div>
  );
}

// -- Unified Activity Log -----------------------------------------

function UnifiedActivityLog() {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex h-[42px] w-[42px] items-center justify-center rounded-[10px] bg-white/5 text-white/50 shrink-0">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
          </svg>
        </div>
        <div>
          <h3 className="text-[15px] font-semibold text-white/90">Activity Log</h3>
          <p className="mt-0.5 text-[12px] text-white/35">Last 30 downloads across all sources</p>
        </div>
      </div>
      <ActivityFeed />
    </div>
  );
}

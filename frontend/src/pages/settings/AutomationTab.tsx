/**
 * AutomationTab — one switch per automation feature (see app/automation_features.py).
 * Auto-grab and 4K & Upgrades group several sources/jobs under one switch;
 * Genre Roulette, Movie Night, Franchise Completer and Watchlist Release Dates
 * are single features. Everything is off on a new install.
 */

import { useState, useEffect, useCallback, type ReactNode } from 'react';
import { SECONDARY_BTN } from './components/legacyTokens';
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
      title: 'Auto-grab',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M12 2v4" />
          <path d="M12 18v4" />
          <path d="m4.93 4.93 2.83 2.83" />
          <path d="m16.24 16.24 2.83 2.83" />
          <circle cx="12" cy="12" r="4" />
        </svg>
      ),
      content: <AutoGrabFeature />,
    },
    {
      title: '4K & upgrades',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 7l9-4 9 4-9 4-9-4z" />
          <path d="M3 12l9 4 9-4" />
          <path d="M3 17l9 4 9-4" />
        </svg>
      ),
      content: <UpgradesFeature />,
    },
    {
      title: 'Genre roulette',
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
      title: 'Movie night',
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
      title: 'Franchise completer',
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
      title: 'Watchlist release dates',
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
      // The only automation that deletes — kept apart from the acquisition
      // features rather than filed among them.
      title: 'Downloads guard',
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
      title: 'Activity log',
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
        <h2 className="text-xl font-semibold text-white/95">Automation</h2>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

// -- Feature switch card -------------------------------------------

function FeatureSwitchCard({
  title,
  path,
  extraPaths = [],
  inHeader = true,
  children,
}: {
  title: string;
  path: string;
  extraPaths?: string[];
  /** v175 (Designer): the switch sits in the header when it's the card's only toggle */
  inHeader?: boolean;
  children?: ReactNode;
}) {
  return (
    <SettingsCard
      title={title}
      paths={[path, ...extraPaths]}
      headerToggle={inHeader ? path : undefined}
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
          <line x1="12" y1="2" x2="12" y2="12" />
        </svg>
      }
    >
      {!inHeader && <ToggleSwitch label={title} path={path} />}
      {children}
    </SettingsCard>
  );
}

// -- Auto-grab feature ---------------------------------------------

function AutoGrabFeature() {
  return (
    <div className="space-y-4 [&>*]:!mt-0">
      <FeatureSwitchCard
        title="Auto-grab"
        path="automation.auto_grab.enabled"
        extraPaths={['automation.interval_hours']}
      >
        <FormField
          label="Check interval (hours)"
          path="automation.interval_hours"
          type="number"
          min={1}
          max={24}
        />
      </FeatureSwitchCard>
      <LatestAutoGrabCard />
      <TrendingMovieWatchlistCard />
      <TrendingTvWatchlistCard />
      <AnticipatedWatchlistCard />
      <NowAvailableMovieWatchlistCard />
    </div>
  );
}

// -- 4K & upgrades feature -----------------------------------------

function UpgradesFeature() {
  return (
    <div className="space-y-4 [&>*]:!mt-0">
      <FeatureSwitchCard
        title="4K & upgrades"
        inHeader={false}
        path="automation.upgrades.enabled"
        extraPaths={['automation.upgrade_scheduler.enabled', 'automation.upgrade_scheduler.run_hour']}
      >
        <ToggleSwitch
          label="Daily upgrade scan"
          path="automation.upgrade_scheduler.enabled"
        />
        <FormField
          label="Scan hour (0-23)"
          path="automation.upgrade_scheduler.run_hour"
          type="number"
          min={0}
          max={23}
        />
      </FeatureSwitchCard>
      <Upgrade4kAutoGrabCard />
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
      type="button"
      className={SECONDARY_BTN}
    >
      {busy || running ? 'Running…' : 'Run now'}
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
      title="Trending movies"
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
    >
      <ToggleSwitch
        label="Use this source"
        path="automation.trending_watchlist.enabled"
      />
      <ToggleSwitch
        label="Search on add"
        path="automation.trending_watchlist.search_on_add"
      />
      <FormField
        label="Quality profile"
        path="automation.trending_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Movies per cycle"
        path="automation.trending_watchlist.limit"
        type="number"
        min={1}
        max={40}
      />

      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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
      title="Now available"
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
    >
      <ToggleSwitch
        label="Use this source"
        path="automation.now_available_watchlist.enabled"
      />
      <ToggleSwitch
        label="Search on add"
        path="automation.now_available_watchlist.search_on_add"
      />
      <FormField
        label="Quality profile"
        path="automation.now_available_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Movies per cycle"
        path="automation.now_available_watchlist.limit"
        type="number"
        min={1}
        max={100}
      />

      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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
    >
      <ToggleSwitch
        label="Use this source"
        path="automation.anticipated_watchlist.enabled"
      />
      <ToggleSwitch
        label="Search on add"
        path="automation.anticipated_watchlist.search_on_add"
      />
      <ToggleSwitch
        label="Include movies"
        path="automation.anticipated_watchlist.movies_enabled"
      />
      <ToggleSwitch
        label="Include shows"
        path="automation.anticipated_watchlist.shows_enabled"
      />
      <FormField
        label="Movie quality profile (Radarr)"
        path="automation.anticipated_watchlist.movie_quality_profile_id"
        type="select"
        options={radarrOptions}
      />
      <FormField
        label="TV quality profile (Sonarr)"
        path="automation.anticipated_watchlist.tv_quality_profile_id"
        type="select"
        options={sonarrOptions}
      />
      <FormField
        label="Titles per cycle"
        path="automation.anticipated_watchlist.limit"
        type="number"
        min={1}
        max={40}
      />

      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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
      title="Trending TV"
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
    >
      <ToggleSwitch
        label="Use this source"
        path="automation.trending_tv_watchlist.enabled"
      />
      <ToggleSwitch
        label="Search on add"
        path="automation.trending_tv_watchlist.search_on_add"
      />
      <FormField
        label="Quality profile"
        path="automation.trending_tv_watchlist.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Shows per cycle"
        path="automation.trending_tv_watchlist.limit"
        type="number"
        min={1}
        max={40}
      />

      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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
      title="Latest feed"
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
    >
      <ToggleSwitch
        label="Use this source"
        path="automation.latest_auto_grab.enabled"
      />
      <FormField
        label="Quality profile"
        path="automation.latest_auto_grab.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="Minimum seeders"
        path="automation.latest_auto_grab.min_seeders"
        type="number"
        min={1}
        max={5000}
      />
      <FormField
        label="Max age (hours)"
        path="automation.latest_auto_grab.max_age_hours"
        type="number"
        min={1}
        max={168}
      />
      <FormField
        label="Scan interval (minutes)"
        path="automation.latest_auto_grab.scan_interval_minutes"
        type="number"
        min={5}
        max={120}
      />

      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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
      title="4K Remux auto-grab"
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
      headerToggle="automation.upgrade_4k_auto_grab.enabled"
      headerRight={<RunNowButton onRun={handleRunNow} running={status?.running} />}
    >
      <FormField
        label="Quality profile (4K)"
        path="automation.upgrade_4k_auto_grab.quality_profile_id"
        type="select"
        options={profileOptions}
      />
      <FormField
        label="4K Plex library"
        path="automation.upgrade_4k_auto_grab.plex_4k_section_id"
        type="select"
        options={sectionOptions}
      />
      <FormField
        label="Minimum seeders"
        path="automation.upgrade_4k_auto_grab.min_seeders"
        type="number"
        min={1}
        max={5000}
      />
      <FormField
        label="Max age (hours)"
        path="automation.upgrade_4k_auto_grab.max_age_hours"
        type="number"
        min={1}
        max={168}
      />
      <FormField
        label="Scan interval (minutes)"
        path="automation.upgrade_4k_auto_grab.scan_interval_minutes"
        type="number"
        min={5}
        max={120}
      />
      <p className="col-span-full text-[12px] text-white/50">
        Last ran: {status?.last_run ? new Date(status.last_run).toLocaleString() : 'never'}
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


// -- Unified Activity Log -----------------------------------------

function UnifiedActivityLog() {
  return (
    <div className="rounded-[16px] border border-white/[0.08] bg-white/[0.06] p-5">
      <div className="flex items-center gap-3 mb-4">
        <div className="flex h-[36px] w-[36px] items-center justify-center rounded-[10px] bg-white/[0.06] text-white/60 shrink-0">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
            <polyline points="14 2 14 8 20 8" />
            <line x1="16" y1="13" x2="8" y2="13" />
            <line x1="16" y1="17" x2="8" y2="17" />
          </svg>
        </div>
        <h3 className="text-[15px] font-semibold text-white/95">Activity log</h3>
      </div>
      <ActivityFeed />
    </div>
  );
}

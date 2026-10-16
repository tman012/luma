/**
 * UiKioskTab — Kiosk Mode toggle, Screensaver, Hero Display, and Daily Kiosk cards.
 */

import { useState, useEffect, useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';
import { useSettings } from './useSettings';
import { fetchPlexHealth, type PlexLibrary } from '@/services/settingsApi';

// -- Kiosk mode paths -----------------------------------------------

const KIOSK_MODE_PATHS = ['ui.kiosk_mode'];

// -- Screensaver card paths ---------------------------------------

const SCREENSAVER_PATHS = [
  'ui.screensaver.enabled',
  'ui.screensaver.type',
  'ui.screensaver.idle_timeout_ms',
  'ui.screensaver.orb_count',
  'ui.screensaver.cache_ttl_seconds',
  'ui.screensaver.library_name',
  'ui.screensaver.source',
];

// -- Hero card paths ----------------------------------------------

const HERO_PATHS = [
  'ui.hero.library_section_id',
  'ui.hero.limit',
  'ui.hero.max_age_days',
  'ui.hero.excluded_genres',
  'ui.hero.backdrop_source',
  'ui.hero.boost_popular_titles',
  'ui.hero.popularity_source',
];

// -- Smart Home (this device's default HA area) -----------------

const SMART_HOME_PATHS = ['ui.kiosk.home_area_id'];

interface HaArea {
  id: string;
  name: string;
}

// -- Daily Kiosk weather paths ------------------------------------

const WEATHER_PATHS = [
  'ui.daily_kiosk.weather.api_key',
  'ui.daily_kiosk.weather.zip_code',
  'ui.daily_kiosk.weather.country_code',
  'ui.daily_kiosk.weather.units',
  'ui.daily_kiosk.weather.state',
];

// -- Daily Kiosk sports paths ------------------------------------

const SPORTS_PATHS = [
  'ui.daily_kiosk.sports.enabled',
  'ui.daily_kiosk.sports.team_id',
  'ui.daily_kiosk.sports.team_name',
];

// -- Daily Kiosk news paths --------------------------------------

const NEWS_PATHS = [
  'ui.daily_kiosk.news.enabled',
  'ui.daily_kiosk.news.feed_url',
  'ui.daily_kiosk.news.feed_name',
];

const NEWS_FEEDS = [
  { value: 'https://moxie.foxnews.com/google-publisher/politics.xml', label: 'Fox News Politics' },
  { value: 'https://moxie.foxnews.com/google-publisher/latest.xml', label: 'Fox News Latest' },
  { value: 'https://moxie.foxnews.com/google-publisher/us.xml', label: 'Fox News US' },
  { value: 'https://moxie.foxnews.com/google-publisher/world.xml', label: 'Fox News World' },
  { value: 'https://moxie.foxnews.com/google-publisher/entertainment.xml', label: 'Fox News Entertainment' },
  { value: 'https://moxie.foxnews.com/google-publisher/sports.xml', label: 'Fox News Sports' },
  { value: 'https://moxie.foxnews.com/google-publisher/health.xml', label: 'Fox News Health' },
];

const MLB_TEAMS = [
  { id: 108, name: 'Los Angeles Angels' },
  { id: 109, name: 'Arizona Diamondbacks' },
  { id: 110, name: 'Baltimore Orioles' },
  { id: 111, name: 'Boston Red Sox' },
  { id: 112, name: 'Chicago Cubs' },
  { id: 113, name: 'Cincinnati Reds' },
  { id: 114, name: 'Cleveland Guardians' },
  { id: 115, name: 'Colorado Rockies' },
  { id: 116, name: 'Detroit Tigers' },
  { id: 117, name: 'Houston Astros' },
  { id: 118, name: 'Kansas City Royals' },
  { id: 119, name: 'Los Angeles Dodgers' },
  { id: 120, name: 'Washington Nationals' },
  { id: 121, name: 'New York Mets' },
  { id: 133, name: 'Athletics' },
  { id: 134, name: 'Pittsburgh Pirates' },
  { id: 135, name: 'San Diego Padres' },
  { id: 136, name: 'Seattle Mariners' },
  { id: 137, name: 'San Francisco Giants' },
  { id: 138, name: 'St. Louis Cardinals' },
  { id: 139, name: 'Tampa Bay Rays' },
  { id: 140, name: 'Texas Rangers' },
  { id: 141, name: 'Toronto Blue Jays' },
  { id: 142, name: 'Minnesota Twins' },
  { id: 143, name: 'Philadelphia Phillies' },
  { id: 144, name: 'Atlanta Braves' },
  { id: 145, name: 'Chicago White Sox' },
  { id: 146, name: 'Miami Marlins' },
  { id: 147, name: 'New York Yankees' },
  { id: 158, name: 'Milwaukee Brewers' },
];

export function UiKioskTab() {
  const { getByPath, setByPath } = useSettings();
  const [libraries, setLibraries] = useState<PlexLibrary[]>([]);
  const [haAreas, setHaAreas] = useState<HaArea[]>([]);
  const [haEnabled, setHaEnabled] = useState(false);

  useEffect(() => {
    fetchPlexHealth().then((data) => {
      if (data.libraries) setLibraries(data.libraries);
    });
  }, []);

  // Fetch HA areas once — the Smart Home card populates its dropdown from here.
  // If HA isn't configured, the endpoint returns { enabled: false } and we
  // render a helpful hint instead of an empty picker.
  useEffect(() => {
    let cancelled = false;
    fetch('/api/ha/areas')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        setHaEnabled(Boolean(data.enabled));
        if (Array.isArray(data.areas)) setHaAreas(data.areas);
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  const kioskMode = getByPath('ui.kiosk_mode') || 'movie';
  const isDailyMode = kioskMode === 'daily';

  // Excluded genres: stored as array, displayed as comma-separated
  const excludedGenres = getByPath('ui.hero.excluded_genres');
  const genresValue = Array.isArray(excludedGenres) ? excludedGenres.join(', ') : (excludedGenres ?? '');

  const handleGenresChange = useCallback(
    (val: string | number) => {
      const arr = String(val)
        .split(',')
        .map((s) => s.trim())
        .filter(Boolean);
      setByPath('ui.hero.excluded_genres', arr);
    },
    [setByPath],
  );

  const movieLibraries = libraries.filter((l) => l.type === 'movie');

  const items: CardItem[] = [
    // Kiosk Mode selector — always first
    {
      title: 'Kiosk Mode',
      subtitle: 'Choose your home screen experience',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Kiosk Mode"
          subtitle="Choose your home screen experience"
          paths={KIOSK_MODE_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="3" width="20" height="14" rx="2" /><line x1="8" y1="21" x2="16" y2="21" /><line x1="12" y1="17" x2="12" y2="21" />
            </svg>
          }
        >
          <div className="sm:col-span-2">
            <div className="flex items-center gap-4 p-3 rounded-xl bg-white/[0.03] border border-white/[0.06]">
              <button
                type="button"
                onClick={() => setByPath('ui.kiosk_mode', 'movie')}
                className={`flex-1 flex flex-col items-center gap-2 rounded-lg px-4 py-3 transition-all min-h-[80px] ${
                  !isDailyMode
                    ? 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'
                    : 'bg-white/[0.03] border border-white/[0.06] text-white/50 hover:bg-white/[0.06]'
                }`}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <path d="M4 8V4a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v4" /><rect x="2" y="8" width="20" height="14" rx="2" />
                  <path d="M12 12l-3 2v-4l3 2z" fill="currentColor" />
                </svg>
                <span className="text-[13px] font-semibold">Movie Kiosk</span>
                <span className="text-[11px] opacity-60">Hero + Explore</span>
              </button>
              <button
                type="button"
                onClick={() => setByPath('ui.kiosk_mode', 'daily')}
                className={`flex-1 flex flex-col items-center gap-2 rounded-lg px-4 py-3 transition-all min-h-[80px] ${
                  isDailyMode
                    ? 'bg-emerald-500/15 border border-emerald-500/30 text-emerald-400'
                    : 'bg-white/[0.03] border border-white/[0.06] text-white/50 hover:bg-white/[0.06]'
                }`}
              >
                <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                  <circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" />
                  <line x1="12" y1="21" x2="12" y2="23" /><line x1="4.22" y1="4.22" x2="5.64" y2="5.64" />
                  <line x1="18.36" y1="18.36" x2="19.78" y2="19.78" /><line x1="1" y1="12" x2="3" y2="12" />
                  <line x1="21" y1="12" x2="23" y2="12" /><line x1="4.22" y1="19.78" x2="5.64" y2="18.36" />
                  <line x1="18.36" y1="5.64" x2="19.78" y2="4.22" />
                </svg>
                <span className="text-[13px] font-semibold">Daily Kiosk</span>
                <span className="text-[11px] opacity-60">Weather + Info</span>
              </button>
            </div>
          </div>
        </SettingsCard>
      ),
    },
    // Smart Home — device's default HA area (shown in all modes)
    {
      title: 'Smart Home',
      subtitle: 'Which room this device lives in',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M3 12l9-9 9 9" /><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Smart Home"
          subtitle="Which Home Assistant area this device controls by default"
          paths={SMART_HOME_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M3 12l9-9 9 9" /><path d="M5 10v10a1 1 0 0 0 1 1h4v-6h4v6h4a1 1 0 0 0 1-1V10" />
            </svg>
          }
          note={haEnabled
            ? 'Tapping the kiosk background opens the smart-home panel to this area. Other areas are always reachable from the panel header.'
            : 'Configure Home Assistant (URL + access token) in the Connections tab to enable room selection.'}
        >
          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[14px] font-medium text-white/60">This Device's Room</label>
            <select
              value={String(getByPath('ui.kiosk.home_area_id') ?? '')}
              onChange={(e) => setByPath('ui.kiosk.home_area_id', e.target.value)}
              disabled={!haEnabled || haAreas.length === 0}
              className="w-full min-h-[44px] rounded-lg border border-white/10 bg-white/5 px-3 py-3 text-[15px] text-white outline-none transition-colors focus:border-white/20 focus:bg-white/8 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              <option value="" className="bg-kiosk-black text-white">— No default area —</option>
              {haAreas.map((a) => (
                <option key={a.id} value={a.id} className="bg-kiosk-black text-white">
                  {a.name}
                </option>
              ))}
            </select>
            <span className="text-[11px] text-white/30">
              {haEnabled
                ? 'Pulled from Home Assistant. Examples: Kitchen, Living Room, Master Bedroom.'
                : 'Home Assistant not connected.'}
            </span>
          </div>
        </SettingsCard>
      ),
    } as CardItem,
    // Daily Kiosk: Display — widget rotation timing (daily mode only)
    ...(isDailyMode
      ? [
          {
            title: 'Display',
            subtitle: 'Widget rotation timing',
            icon: (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
              </svg>
            ),
            content: (
              <SettingsCard
                title="Display"
                subtitle="How the rotating widgets behave"
                paths={['ui.daily_kiosk.widget_seconds']}
                icon={
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" />
                  </svg>
                }
                note="How long each widget (weather, sports, news, etc.) stays on screen before fading to the next."
              >
                <FormField
                  label="Widget display time (seconds)"
                  path="ui.daily_kiosk.widget_seconds"
                  type="number"
                  min={3}
                  max={60}
                  placeholder="8"
                  hint="Between 3 and 60 seconds. Default is 8."
                />
              </SettingsCard>
            ),
          } as CardItem,
          {
            title: 'Weather',
            subtitle: 'Weather widget configuration',
            icon: (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
              </svg>
            ),
            content: (
              <SettingsCard
                title="Weather"
                subtitle="OpenWeatherMap widget configuration"
                paths={WEATHER_PATHS}
                icon={
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z" />
                  </svg>
                }
                note="Get a free API key from openweathermap.org"
              >
                <FormField
                  label="API Key"
                  path="ui.daily_kiosk.weather.api_key"
                  type="password"
                  placeholder="Enter OpenWeatherMap API key"
                  hint="Free tier supports 1,000 calls/day"
                />
                <FormField
                  label="ZIP Code"
                  path="ui.daily_kiosk.weather.zip_code"
                  placeholder="90210"
                  hint="ZIP or postal code for your location"
                />
                <FormField
                  label="Country Code"
                  path="ui.daily_kiosk.weather.country_code"
                  placeholder="US"
                  hint="ISO 3166 country code"
                />
                <FormField
                  label="State / Region"
                  path="ui.daily_kiosk.weather.state"
                  placeholder="MD"
                  hint="Shown after city name (e.g. MD, CA)"
                />
                <FormField
                  label="Units"
                  path="ui.daily_kiosk.weather.units"
                  type="select"
                  options={[
                    { value: 'imperial', label: 'Fahrenheit' },
                    { value: 'metric', label: 'Celsius' },
                    { value: 'standard', label: 'Kelvin' },
                  ]}
                />
              </SettingsCard>
            ),
          } as CardItem,
          {
            title: 'Sports',
            subtitle: 'MLB team scores & schedule',
            icon: (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <circle cx="12" cy="12" r="10" /><path d="M4.93 4.93c4.08 2.38 8.6 7.9 14.14 14.14" /><path d="M19.07 4.93c-4.08 2.38-8.6 7.9-14.14 14.14" />
              </svg>
            ),
            content: (
              <SettingsCard
                title="Sports"
                subtitle="MLB team scores & schedule on Daily Kiosk"
                paths={SPORTS_PATHS}
                icon={
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <circle cx="12" cy="12" r="10" /><path d="M4.93 4.93c4.08 2.38 8.6 7.9 14.14 14.14" /><path d="M19.07 4.93c-4.08 2.38-8.6 7.9-14.14 14.14" />
                  </svg>
                }
                note="Uses free MLB Stats API — no API key needed"
              >
                <div className="sm:col-span-2">
                  <ToggleSwitch label="Enabled" path="ui.daily_kiosk.sports.enabled" />
                </div>
                <div className="sm:col-span-2 flex flex-col gap-1.5">
                  <label className="text-[14px] font-medium text-white/60">Team</label>
                  <select
                    value={String(getByPath('ui.daily_kiosk.sports.team_id') ?? 110)}
                    onChange={(e) => {
                      const id = Number(e.target.value);
                      const team = MLB_TEAMS.find((t) => t.id === id);
                      setByPath('ui.daily_kiosk.sports.team_id', id);
                      if (team) setByPath('ui.daily_kiosk.sports.team_name', team.name);
                    }}
                    className="w-full min-h-[44px] rounded-lg border border-white/10 bg-white/5 px-3 py-3 text-[15px] text-white outline-none transition-colors focus:border-white/20 focus:bg-white/8 cursor-pointer"
                  >
                    {MLB_TEAMS.map((t) => (
                      <option key={t.id} value={t.id} className="bg-kiosk-black text-white">
                        {t.name}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] text-white/30">Select your favorite MLB team</span>
                </div>
              </SettingsCard>
            ),
          } as CardItem,
          {
            title: 'News',
            subtitle: 'News headlines widget',
            icon: (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2" />
                <path d="M18 14h-8M15 18h-5M10 6h8v4h-8z" />
              </svg>
            ),
            content: (
              <SettingsCard
                title="News"
                subtitle="RSS news headlines on Daily Kiosk"
                paths={NEWS_PATHS}
                icon={
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                    <path d="M4 22h16a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H8a2 2 0 0 0-2 2v16a2 2 0 0 1-2 2zm0 0a2 2 0 0 1-2-2v-9c0-1.1.9-2 2-2h2" />
                    <path d="M18 14h-8M15 18h-5M10 6h8v4h-8z" />
                  </svg>
                }
              >
                <div className="sm:col-span-2">
                  <ToggleSwitch label="Enabled" path="ui.daily_kiosk.news.enabled" />
                </div>
                <div className="sm:col-span-2 flex flex-col gap-1.5">
                  <label className="text-[14px] font-medium text-white/60">Feed</label>
                  <select
                    value={String(getByPath('ui.daily_kiosk.news.feed_url') ?? '')}
                    onChange={(e) => {
                      const url = e.target.value;
                      const feed = NEWS_FEEDS.find((f) => f.value === url);
                      setByPath('ui.daily_kiosk.news.feed_url', url);
                      if (feed) setByPath('ui.daily_kiosk.news.feed_name', feed.label);
                    }}
                    className="w-full min-h-[44px] rounded-lg border border-white/10 bg-white/5 px-3 py-3 text-[15px] text-white outline-none transition-colors focus:border-white/20 focus:bg-white/8 cursor-pointer"
                  >
                    {NEWS_FEEDS.map((f) => (
                      <option key={f.value} value={f.value} className="bg-kiosk-black text-white">
                        {f.label}
                      </option>
                    ))}
                  </select>
                  <span className="text-[11px] text-white/30">Select news source</span>
                </div>
                <FormField
                  label="Display Name"
                  path="ui.daily_kiosk.news.feed_name"
                  placeholder="Fox News Politics"
                  hint="Label shown on the widget"
                />
              </SettingsCard>
            ),
          } as CardItem,
        ]
      : []),
    {
      title: 'Screensaver',
      subtitle: 'Screensaver settings for kiosk mode',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Screensaver"
          subtitle="Screensaver settings for kiosk mode"
          paths={SCREENSAVER_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
            </svg>
          }
        >
          <div className="sm:col-span-2">
            <ToggleSwitch label="Enabled" path="ui.screensaver.enabled" />
          </div>
          <FormField
            label="Type"
            path="ui.screensaver.type"
            type="select"
            options={[
              { value: 'orb', label: 'Orb' },
              { value: 'trivia', label: 'Movie Trivia' },
              { value: 'kiosk', label: 'Kiosk Discovery' },
            ]}
            hint="Visual style of the screensaver"
          />
          <FormField
            label="Idle Timeout (ms)"
            path="ui.screensaver.idle_timeout_ms"
            type="number"
            placeholder="10000"
            min={5000}
            max={600000}
            step={1000}
            hint="5,000 to 600,000 ms"
          />
          <FormField
            label="Orb Count"
            path="ui.screensaver.orb_count"
            type="number"
            placeholder="9"
            min={1}
            max={20}
          />
          <FormField
            label="Cache TTL (seconds)"
            path="ui.screensaver.cache_ttl_seconds"
            type="number"
            placeholder="1800"
            min={300}
            max={7200}
          />
          <FormField
            label="Library Name"
            path="ui.screensaver.library_name"
            placeholder="Recently Released"
          />
          <FormField
            label="Source"
            path="ui.screensaver.source"
            type="select"
            options={[
              { value: 'plex_library', label: 'Plex Library' },
              { value: 'hero', label: 'Hero Section' },
            ]}
          />
        </SettingsCard>
      ),
    },
    {
      title: 'Hero Display',
      subtitle: 'Featured content hero section on the Hub',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="4" width="20" height="16" rx="2" /><path d="M2 8h20" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Hero Display"
          subtitle="Featured content hero section on the Hub"
          paths={HERO_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="4" width="20" height="16" rx="2" /><path d="M2 8h20" />
            </svg>
          }
        >
          <FormField
            label="Plex Library"
            path="ui.hero.library_section_id"
            type="select"
            options={[
              { value: 0, label: 'Default (first movie library)' },
              ...movieLibraries.map((l) => ({ value: l.id, label: l.title })),
            ]}
            hint="Which Plex library to pull hero movies from"
          />
          <FormField
            label="Number of Movies"
            path="ui.hero.limit"
            type="number"
            placeholder="10"
            min={1}
            max={50}
            hint="Movies in hero rotation"
          />
          <FormField
            label="Added Within (days)"
            path="ui.hero.max_age_days"
            type="number"
            placeholder="30"
            min={1}
            max={365}
            hint="Only show movies added in the last N days"
          />
          <FormField
            label="Excluded Genres"
            path="ui.hero.excluded_genres"
            placeholder="Sport, Sports"
            hint="Comma-separated genres to skip"
            value={String(genresValue)}
            onChange={handleGenresChange}
          />
          <FormField
            label="Backdrop Source"
            path="ui.hero.backdrop_source"
            type="select"
            options={[
              { value: 'plex', label: 'Plex' },
              { value: 'tmdb', label: 'TMDb' },
            ]}
            hint="Where hero backdrop images come from"
          />
          <div className="sm:col-span-2">
            <ToggleSwitch
              label="Boost Popular Titles"
              path="ui.hero.boost_popular_titles"
              hint="Sort by popularity instead of date added"
            />
          </div>
          <FormField
            label="Popularity Source"
            path="ui.hero.popularity_source"
            type="select"
            options={[
              { value: 'tmdb', label: 'TMDb' },
              { value: 'imdb', label: 'IMDb' },
            ]}
            hint="Used when Boost is enabled"
          />
        </SettingsCard>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">UI / Kiosk</h2>
        <p className="mt-1 text-[13px] text-white/40">
          Appearance, screensaver, and hero display configuration.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

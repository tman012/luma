/**
 * DailyKioskPage — Echo Show-style ambient home screen.
 *
 * Full-bleed rotating wallpaper, top-left clock, top-right current weather,
 * large centered 5-day forecast with animated colorful icons,
 * bottom-right date, bottom-center Nova suggestion text.
 * Uses Amazon Ember font family.
 */

import { useState, useEffect, useCallback, useRef } from 'react';
import { useModalHost } from '@/contexts/ModalHostContext';
import { openModalForItem } from '@/utils/modalNavigation';
import { SmartHomeModal } from '@/components/smarthome/SmartHomeModal';

// ── Amazon Ember font injection ────────────────────────────────────

const EMBER_FONT_ID = 'daily-kiosk-ember-font';
function ensureEmberFont() {
  if (document.getElementById(EMBER_FONT_ID)) return;
  const style = document.createElement('style');
  style.id = EMBER_FONT_ID;
  style.textContent = `
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_Lt.ttf') format('truetype');
      font-weight: 300;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_LtIt.ttf') format('truetype');
      font-weight: 300;
      font-style: italic;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/AmazonEmber_Rg.ttf') format('truetype');
      font-weight: 400;
      font-style: normal;
      font-display: swap;
    }
    @font-face {
      font-family: 'Amazon Ember';
      src: url('/fonts/Amazon-Ember-Medium.ttf') format('truetype');
      font-weight: 500;
      font-style: normal;
      font-display: swap;
    }
    @keyframes dk-suggestion-fade {
      0% { opacity: 0; transform: translateY(6px); }
      4% { opacity: 1; transform: translateY(0); }
      96% { opacity: 1; transform: translateY(0); }
      100% { opacity: 0; transform: translateY(-6px); }
    }
  `;
  document.head.appendChild(style);
}

const EMBER = "'Amazon Ember', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";

// ── Seasonal wallpaper URLs ────────────────────────────────────────

const SEASONAL_WALLPAPERS: Record<string, string[]> = {
  spring: [
    'https://images.unsplash.com/photo-1459478309853-2c33a60058e7?w=1920&q=80&auto=format', // tulip field
    'https://images.unsplash.com/photo-1470071459604-3b5ec3a7fe05?w=1920&q=80&auto=format', // misty green valley
    'https://images.unsplash.com/photo-1504567961542-e24d9439a724?w=1920&q=80&auto=format', // blossoming tree
    'https://images.unsplash.com/photo-1448375240586-882707db888b?w=1920&q=80&auto=format', // lush forest path
    'https://images.unsplash.com/photo-1465146344425-f00d5f5c8f07?w=1920&q=80&auto=format', // orange wildflowers
    'https://images.unsplash.com/photo-1518495973542-4542c06a5843?w=1920&q=80&auto=format', // sunlight through trees
    'https://images.unsplash.com/photo-1501854140801-50d01698950b?w=1920&q=80&auto=format', // aerial green hills
    'https://images.unsplash.com/photo-1475113548554-5a36f1f523d6?w=1920&q=80&auto=format', // spring leaves
  ],
  summer: [
    'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?w=1920&q=80&auto=format', // tropical beach
    'https://images.unsplash.com/photo-1505144808419-1957a94ca61e?w=1920&q=80&auto=format', // sunset beach
    'https://images.unsplash.com/photo-1441974231531-c6227db76b6e?w=1920&q=80&auto=format', // sunlit forest
    'https://images.unsplash.com/photo-1464822759023-fed622ff2c3b?w=1920&q=80&auto=format', // mountain vista
    'https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=1920&q=80&auto=format', // alpine peaks
    'https://images.unsplash.com/photo-1433086966358-54859d0ed716?w=1920&q=80&auto=format', // waterfall
    'https://images.unsplash.com/photo-1501785888041-af3ef285b470?w=1920&q=80&auto=format', // lake panorama
    'https://images.unsplash.com/photo-1472214103451-9374bd1c798e?w=1920&q=80&auto=format', // green countryside
  ],
  fall: [
    'https://images.unsplash.com/photo-1507003211169-0a1dd7228f2d?w=1920&q=80&auto=format', // autumn forest road
    'https://images.unsplash.com/photo-1477414348463-c0eb7f1359b6?w=1920&q=80&auto=format', // golden canopy
    'https://images.unsplash.com/photo-1473448912268-2022ce9509d8?w=1920&q=80&auto=format', // fall foliage
    'https://images.unsplash.com/photo-1508193638397-1c4234db14d8?w=1920&q=80&auto=format', // autumn lake
    'https://images.unsplash.com/photo-1541789094913-f3809a8f3ba5?w=1920&q=80&auto=format', // fall mountain
    'https://images.unsplash.com/photo-1506744038136-46273834b3fb?w=1920&q=80&auto=format', // misty valley
    'https://images.unsplash.com/photo-1509316975850-ff9c5deb0cd9?w=1920&q=80&auto=format', // pine forest
    'https://images.unsplash.com/photo-1418065460487-3e41a6c84dc5?w=1920&q=80&auto=format', // foggy woods
  ],
  winter: [
    'https://images.unsplash.com/photo-1519681393784-d120267933ba?w=1920&q=80&auto=format', // snowy mountain stars
    'https://images.unsplash.com/photo-1491002052546-bf38f186af56?w=1920&q=80&auto=format', // frozen lake
    'https://images.unsplash.com/photo-1457269449834-928af64c684d?w=1920&q=80&auto=format', // winter forest
    'https://images.unsplash.com/photo-1483921020237-2ff51e8e4b22?w=1920&q=80&auto=format', // snowy cabin
    'https://images.unsplash.com/photo-1478265409131-1f65c88f965c?w=1920&q=80&auto=format', // winter mountain
    'https://images.unsplash.com/photo-1516715094483-75da7dee9758?w=1920&q=80&auto=format', // snow-covered trees
    'https://images.unsplash.com/photo-1444927714506-8492d94b4e3d?w=1920&q=80&auto=format', // winter sunrise
    'https://images.unsplash.com/photo-1510797215324-95aa89f43c33?w=1920&q=80&auto=format', // snowy field
  ],
};

function getSeason(): string {
  const month = new Date().getMonth(); // 0-11
  if (month >= 2 && month <= 4) return 'spring';
  if (month >= 5 && month <= 7) return 'summer';
  if (month >= 8 && month <= 10) return 'fall';
  return 'winter';
}

const WALLPAPERS = SEASONAL_WALLPAPERS[getSeason()]!;

// ── Nova suggestion phrases ───────────────────────────────────────

const NOVA_SUGGESTIONS = [
  "\"Nova, what movies came out this week?\"",
  "\"Nova, play something funny\"",
  "\"Nova, what's trending on Plex?\"",
  "\"Nova, find me a thriller\"",
  "\"Nova, what should I watch tonight?\"",
  "\"Nova, search for sci-fi movies\"",
  "\"Nova, what's new in my library?\"",
  "\"Nova, recommend something like Inception\"",
  "\"Nova, show me action movies\"",
  "\"Nova, what are the top rated movies?\"",
];

// ── Types ──────────────────────────────────────────────────────────

interface WeatherCurrent {
  temp: number;
  feels_like: number;
  humidity: number;
  description: string;
  icon: string;
  wind_speed: number;
}

interface WeatherForecastDay {
  date: string;
  temp_min: number;
  temp_max: number;
  icon: string;
  description: string;
}

interface WeatherHourly {
  time: string;
  temp: number;
  icon: string;
  description: string;
}

interface WeatherData {
  configured: boolean;
  location: string;
  units: string;
  current: WeatherCurrent;
  forecast: WeatherForecastDay[];
  hourly: WeatherHourly[];
}

// ── Now Available types ────────────────────────────────────────────

interface NowAvailableData {
  found: boolean;
  title: string;
  year: string | number;
  tmdb_id: number;
  media_type: string;
  added_at: string;
  backdrop_url: string;
  overview?: string;
  cast?: string[];
  rating?: number;
  vote_count?: number;
}

// ── News types ─────────────────────────────────────────────────────

interface NewsItem {
  title: string;
  description: string;
  link: string;
  image: string;
  timeAgo: string;
  pubDate: string;
}

interface NewsData {
  enabled: boolean;
  feed_name: string;
  items: NewsItem[];
}

// ── TV calendar types ──────────────────────────────────────────────

interface TvCalendarEpisode {
  series_title: string;
  season: number | null;
  episode: number | null;
  episode_title: string;
  air_date_utc: string | null;
  air_date_local: string | null;
  network: string;
  runtime: number;
  overview: string;
  poster_url: string | null;
  tvdb_id: number | null;
}

interface TvCalendarData {
  enabled: boolean;
  episodes: TvCalendarEpisode[];
}

// ── Weather icons (static SVGs, rendered as background-image to prevent
//    browser SVG animation engines from animating them) ──────────────

function WeatherIcon({ code, size = 120 }: { code: string; size?: number }) {
  return (
    <div
      role="img"
      style={{
        width: size,
        height: size,
        backgroundImage: `url(/weather/${code}.svg)`,
        backgroundSize: 'contain',
        backgroundRepeat: 'no-repeat',
        backgroundPosition: 'center',
      }}
    />
  );
}

// ── Date/time helpers ──────────────────────────────────────────────

function formatTime(date: Date): string {
  const h = date.getHours() % 12 || 12;
  const m = date.getMinutes().toString().padStart(2, '0');
  return `${h}:${m}`;
}

function formatDate(date: Date): string {
  return date.toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
  });
}

function formatDayShort(dateStr: string): string {
  const d = new Date(dateStr + 'T12:00:00');
  return d.toLocaleDateString('en-US', { weekday: 'short' });
}

function formatVoteCount(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}


// ── Nova suggestion rotator ────────────────────────────────────────

function NovaSuggestion() {
  const [index, setIndex] = useState(() => Math.floor(Math.random() * NOVA_SUGGESTIONS.length));
  const [key, setKey] = useState(0);

  useEffect(() => {
    const interval = setInterval(() => {
      setIndex((prev) => (prev + 1) % NOVA_SUGGESTIONS.length);
      setKey((k) => k + 1);
    }, 18000);
    return () => clearInterval(interval);
  }, []);

  return (
    <div
      key={key}
      style={{
        fontFamily: EMBER,
        fontWeight: 400,
        fontStyle: 'italic',
        fontSize: 'clamp(20px, 2.2vw, 32px)',
        color: 'rgba(255,255,255,0.98)',
        textShadow: '0 1px 3px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.35)',
        textAlign: 'center',
        animation: 'dk-suggestion-fade 18s ease-in-out forwards',
        letterSpacing: '0.3px',
      }}
    >
      <span style={{ color: 'rgba(255,255,255,0.85)', fontStyle: 'italic', fontWeight: 400 }}>Try </span>
      {NOVA_SUGGESTIONS[index]}
    </div>
  );
}

// ── Wallpaper background with crossfade ────────────────────────────

const WALLPAPER_INTERVAL = 90000; // 90 seconds between changes
const WALLPAPER_FADE = 3000;     // 3s gentle crossfade

function WallpaperBackground() {
  const [currentIdx, setCurrentIdx] = useState(() => Math.floor(Math.random() * WALLPAPERS.length));
  const [nextIdx, setNextIdx] = useState<number | null>(null);
  const [transitioning, setTransitioning] = useState(false);

  useEffect(() => {
    WALLPAPERS.forEach((url) => { const img = new Image(); img.src = url; });
  }, []);

  useEffect(() => {
    const interval = setInterval(() => {
      const next = (currentIdx + 1) % WALLPAPERS.length;
      setNextIdx(next);
      setTransitioning(true);
      setTimeout(() => {
        setCurrentIdx(next);
        setNextIdx(null);
        setTransitioning(false);
      }, WALLPAPER_FADE);
    }, WALLPAPER_INTERVAL);
    return () => clearInterval(interval);
  }, [currentIdx]);

  return (
    <div style={{ position: 'absolute', inset: 0, contain: 'strict', isolation: 'isolate', zIndex: 0 }}>
      <div
        key={`cur-${currentIdx}`}
        style={{
          position: 'absolute', inset: 0,
          backgroundImage: `url(${WALLPAPERS[currentIdx]})`,
          backgroundSize: 'cover', backgroundPosition: 'center',
          transition: `opacity ${WALLPAPER_FADE}ms ease-in-out`,
          willChange: 'opacity',
          opacity: transitioning ? 0 : 1,
        }}
      />
      {nextIdx !== null && (
        <div
          key={`nxt-${nextIdx}`}
          style={{
            position: 'absolute', inset: 0,
            backgroundImage: `url(${WALLPAPERS[nextIdx]})`,
            backgroundSize: 'cover', backgroundPosition: 'center',
            transition: `opacity ${WALLPAPER_FADE}ms ease-in-out`,
            willChange: 'opacity',
            opacity: transitioning ? 1 : 0,
          }}
        />
      )}
    </div>
  );
}

// ── Scrim overlay ──────────────────────────────────────────────────

function ScrimOverlay() {
  // Uniform dim across the whole wallpaper — no gradient bands. The wallpaper
  // stays fully visible (just darker), and widget text/images read cleanly
  // without any "fog" halo around individual elements.
  return (
    <div
      style={{
        position: 'absolute',
        inset: 0,
        background: 'rgba(0,0,0,0.38)',
        pointerEvents: 'none',
      }}
    />
  );
}

// ── Sports types (ESPN/teamtracker shape) ─────────────────────────

interface SportsTeam {
  id: string;
  name: string;             // short display (e.g., "Orioles")
  longName: string;         // full display (e.g., "Baltimore Orioles")
  abbreviation: string;     // "BAL"
  homeAway: string;         // "home" | "away"
  score: string | number | null;
  isWinner: boolean;
  record: string;           // e.g. "87-68"
  logo: string;
  primaryColor: string;     // hex without '#'
  altColor: string;
  winProbability: number | null;
}

interface BaseballSituation {
  outs: number | null;
  balls: number | null;
  strikes: number | null;
  onFirst: boolean;
  onSecond: boolean;
  onThird: boolean;
  atBat: 'home' | 'away' | null;
}

interface FootballSituation {
  downDistanceText: string | null;
  possessionTeamId: string;
  teamTimeouts: number | null;
  opponentTimeouts: number | null;
}

interface SportsGame {
  label: string;           // "Upcoming" | "Live" | "Final"
  state: string;           // "pre" | "in" | "post"
  gameDate: string;
  detail: string;          // e.g. "Bot 5th", "7:42 - 2nd Quarter"
  shortDetail: string;     // compact version
  clock: string;           // displayClock e.g. "7:42"
  period: number | null;
  venue: string;
  location: string;
  tvNetwork: string;
  seriesSummary: string;
  team: SportsTeam;        // the user's configured team
  opponent: SportsTeam;
  lastPlay: string;
  baseball: BaseballSituation | null;
  football: FootballSituation | null;
}

interface SportsData {
  enabled: boolean;
  league: string;
  sport: string;           // "baseball" | "football" | "basketball" | "hockey" | "soccer"
  team_name: string;
  last_game: SportsGame | null;
  next_game: SportsGame | null;
  live: boolean;
}

// ── Sports helpers ─────────────────────────────────────────────────

function formatGameDay(dateStr: string): string {
  const d = new Date(dateStr);
  const now = new Date();
  const tomorrow = new Date();
  tomorrow.setDate(now.getDate() + 1);
  if (d.toDateString() === now.toDateString()) return 'Today';
  if (d.toDateString() === tomorrow.toDateString()) return 'Tomorrow';
  const diff = (d.getTime() - now.getTime()) / (1000 * 60 * 60 * 24);
  if (diff < 6) return d.toLocaleDateString('en-US', { weekday: 'long' });
  return d.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
}


// ── Sports widget ──────────────────────────────────────────────────
// Mirrors HA TeamTracker's live card layout: huge watermark logos,
// team blocks with logo/name/record/timeouts, massive scores, sport-
// specific situation row (baseball diamond + BSO, or football down &
// distance), and a scrolling last-play marquee below.

function SportsWidget({ data, textShadow: ts }: { data: SportsData; textShadow: string }) {
  const { last_game, next_game, sport } = data;

  // Primary display: live game > next game > last completed
  const primary = next_game && next_game.state === 'in'
    ? next_game
    : (next_game || last_game);
  const showLastScore = !!next_game && !!last_game && last_game.state === 'post' && primary !== last_game;

  if (!primary) return null;

  const isLive = primary.state === 'in';
  const isFinal = primary.state === 'post';
  const isPre = primary.state === 'pre';
  const gameTime = new Date(primary.gameDate);
  const timeStr = gameTime.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

  // Home team always on the right, away on the left. Falls back to the
  // user's team on the right if HA's homeAway field isn't populated.
  const userTeamIsHome = primary.team.homeAway === 'home' || !primary.opponent.homeAway;
  const leftTeam = userTeamIsHome ? primary.opponent : primary.team;
  const rightTeam = userTeamIsHome ? primary.team : primary.opponent;
  const leftAccent = leftTeam.primaryColor ? `#${leftTeam.primaryColor}` : '#ffffff';
  const rightAccent = rightTeam.primaryColor ? `#${rightTeam.primaryColor}` : '#ffffff';

  return (
    <div style={{
      position: 'relative',
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 'clamp(6px, 1vh, 14px)',
      width: 'min(1100px, 90vw)',
      // GPU-promote the layer so live polling re-renders don't repaint the
      // whole stack — keeps the layout from "scrolling" into place.
      willChange: 'transform',
      transform: 'translateZ(0)',
    }}>
      {/* Watermark logos — huge behind the scoreboard, less transparent
          during live games so the team identity is unmistakable. */}
      <div style={{ position: 'absolute', top: '-35%', left: '-18%', width: '58%', height: '140%', overflow: 'hidden', pointerEvents: 'none' }}>
        <img src={leftTeam.logo} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain', opacity: 0.22 }} />
      </div>
      <div style={{ position: 'absolute', top: '-35%', right: '-18%', width: '58%', height: '140%', overflow: 'hidden', pointerEvents: 'none' }}>
        <img src={rightTeam.logo} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain', opacity: 0.22 }} />
      </div>

      {/* Main scoreboard row — CSS grid with `1fr auto 1fr` columns so the
          two team blocks always have identical width and the score stays
          dead-center regardless of name length. */}
      <div style={{
        position: 'relative',
        display: 'grid',
        gridTemplateColumns: '1fr auto 1fr',
        alignItems: 'center',
        gap: 'clamp(16px, 3vw, 40px)',
        width: '100%',
      }}>
        <TeamBlock team={leftTeam} ts={ts}
          possession={isLive && sport === 'football' && primary.football?.possessionTeamId === leftTeam.id}
          timeouts={sport === 'football'
            ? (leftTeam === primary.team ? primary.football?.teamTimeouts : primary.football?.opponentTimeouts) ?? null
            : null}
          accent={leftAccent} />

        {/* Score / pre-game time zone — fixed width keeps the grid stable
            when the inning text length changes ("Top 5th" → "End 5th"). */}
        <div style={{
          textAlign: 'center',
          width: 'clamp(280px, 30vw, 380px)',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          gap: 'clamp(4px, 0.6vh, 10px)',
        }}>
          {isPre ? (
            <>
              <div style={{
                fontSize: 'clamp(28px, 4vw, 48px)',
                fontWeight: 400,
                color: '#ffffff',
                textShadow: ts,
                lineHeight: 1.2,
              }}>
                {formatGameDay(primary.gameDate)}
              </div>
              <div style={{
                fontSize: 'clamp(36px, 5vw, 58px)',
                fontWeight: 300,
                color: '#ffffff',
                textShadow: ts,
                lineHeight: 1,
              }}>
                {timeStr}
              </div>
            </>
          ) : (
            <>
              {/* Live baseball: inning sits ABOVE the score. Other
                  live/final sports keep the legacy "label below score"
                  layout. */}
              {isLive && sport === 'baseball' && (
                <div style={{
                  fontSize: 'clamp(20px, 2.2vw, 30px)',
                  fontWeight: 500,
                  color: '#4FC3F7',
                  textShadow: ts,
                  letterSpacing: '1.4px',
                  textTransform: 'uppercase',
                  lineHeight: 1.1,
                  marginBottom: 'clamp(2px, 0.4vh, 6px)',
                }}>
                  {primary.detail || primary.shortDetail || 'Live'}
                </div>
              )}
              <ScoreDisplay team={leftTeam} opponent={rightTeam} isFinal={isFinal} ts={ts} />
              {!(isLive && sport === 'baseball') && (
                <div style={{
                  fontSize: 'clamp(20px, 2.2vw, 30px)',
                  fontWeight: 500,
                  color: isLive ? '#4FC3F7' : 'rgba(255,255,255,0.72)',
                  textShadow: ts,
                  letterSpacing: '1.2px',
                  textTransform: 'uppercase',
                  lineHeight: 1.1,
                }}>
                  {isLive
                    ? (primary.detail || primary.shortDetail || 'Live')
                    : 'Final'}
                </div>
              )}
            </>
          )}
        </div>

        <TeamBlock team={rightTeam} ts={ts}
          possession={isLive && sport === 'football' && primary.football?.possessionTeamId === rightTeam.id}
          timeouts={sport === 'football'
            ? (rightTeam === primary.team ? primary.football?.teamTimeouts : primary.football?.opponentTimeouts) ?? null
            : null}
          accent={rightAccent} />
      </div>

      {/* ── Live baseball situation: diamond → outs → balls/strikes ──
          (Inning label is above the score, rendered in the score column.) */}
      {isLive && sport === 'baseball' && primary.baseball && (
        <>
          {/* Diamond, centered */}
          <div style={{ marginTop: 'clamp(10px, 1.4vh, 18px)' }}>
            <BaseballDiamond
              onFirst={primary.baseball.onFirst}
              onSecond={primary.baseball.onSecond}
              onThird={primary.baseball.onThird}
            />
          </div>

          {/* Outs — symmetrical stack: OUTS label above, dots below, both
              centered on the same vertical axis as the diamond. */}
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 'clamp(6px, 0.9vh, 12px)',
            marginTop: 'clamp(8px, 1vh, 14px)',
          }}>
            <span style={{
              fontSize: 'clamp(14px, 1.5vw, 20px)',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.62)',
              textShadow: ts,
              letterSpacing: '0.24em',
              textTransform: 'uppercase',
            }}>
              Outs
            </span>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              gap: 'clamp(10px, 1.2vw, 16px)',
            }}>
              {[0, 1, 2].map(i => (
                <div key={i} style={{
                  width: 'clamp(18px, 2vw, 26px)',
                  height: 'clamp(18px, 2vw, 26px)',
                  borderRadius: '50%',
                  border: '2px solid rgba(255,255,255,0.6)',
                  background: i < (primary.baseball!.outs ?? 0) ? '#ffffff' : 'transparent',
                }} />
              ))}
            </div>
          </div>
        </>
      )}

      {isLive && sport === 'football' && primary.football?.downDistanceText && (
        <div style={{
          fontSize: 'clamp(22px, 2.6vw, 36px)',
          fontWeight: 500,
          color: '#ffffff',
          textShadow: ts,
          marginTop: 'clamp(4px, 0.6vh, 10px)',
          letterSpacing: '0.5px',
        }}>
          {primary.football.downDistanceText}
        </div>
      )}

      {/* Balls + Strikes row — just above the last-play marquee for baseball */}
      {isLive && sport === 'baseball' && primary.baseball && (
        <div style={{
          display: 'flex',
          alignItems: 'baseline',
          justifyContent: 'center',
          gap: 'clamp(24px, 3vw, 42px)',
          marginTop: 'clamp(14px, 1.8vh, 24px)',
          fontVariantNumeric: 'tabular-nums',
        }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'clamp(8px, 1vw, 14px)' }}>
            <span style={{
              fontSize: 'clamp(20px, 2.3vw, 30px)',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.72)',
              textShadow: ts,
              letterSpacing: '0.5px',
            }}>
              Balls
            </span>
            <span style={{
              fontSize: 'clamp(30px, 3.6vw, 46px)',
              fontWeight: 500,
              color: '#ffffff',
              textShadow: ts,
              lineHeight: 1,
            }}>
              {primary.baseball.balls ?? 0}
            </span>
          </div>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 'clamp(8px, 1vw, 14px)' }}>
            <span style={{
              fontSize: 'clamp(20px, 2.3vw, 30px)',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.72)',
              textShadow: ts,
              letterSpacing: '0.5px',
            }}>
              Strikes
            </span>
            <span style={{
              fontSize: 'clamp(30px, 3.6vw, 46px)',
              fontWeight: 500,
              color: '#ffffff',
              textShadow: ts,
              lineHeight: 1,
            }}>
              {primary.baseball.strikes ?? 0}
            </span>
          </div>
        </div>
      )}

      {/* Last play (live) — full sentence, crossfades to the next play */}
      {isLive && primary.lastPlay && (
        <LastPlayFade text={primary.lastPlay} ts={ts} />
      )}

      {/* Venue + TV network footer — centered, bright, readable */}
      {(primary.venue || primary.tvNetwork) && (
        <div style={{
          marginTop: 'clamp(10px, 1.2vh, 18px)',
          textAlign: 'center',
          fontSize: 'clamp(19px, 2vw, 26px)',
          color: 'rgba(255,255,255,0.92)',
          textShadow: ts,
          fontWeight: 400,
          maxWidth: 'min(80vw, 900px)',
          lineHeight: 1.3,
        }}>
          {primary.venue}
          {primary.location ? ` \u00b7 ${primary.location}` : ''}
          {primary.tvNetwork ? ` \u00b7 ${primary.tvNetwork}` : ''}
        </div>
      )}

      {/* Last game score footnote — when showing upcoming (not live) */}
      {showLastScore && last_game && (
        <>
          <div style={{ width: 'clamp(200px, 30vw, 400px)', height: '1px', background: 'rgba(255,255,255,0.15)', marginTop: 'clamp(8px, 1vh, 14px)' }} />
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'clamp(6px, 0.8vw, 12px)',
            marginTop: 'clamp(6px, 0.8vh, 10px)',
          }}>
            <img src={last_game.team.logo} alt=""
              style={{ width: 'clamp(32px, 3.5vw, 46px)', height: 'clamp(32px, 3.5vw, 46px)', objectFit: 'contain' }} />
            <span style={{
              fontSize: 'clamp(22px, 2.8vw, 32px)',
              fontWeight: 400,
              color: 'rgba(255,255,255,0.85)',
              textShadow: ts,
            }}>
              {last_game.team.abbreviation} {last_game.team.score}
            </span>
            <span style={{ fontSize: 'clamp(18px, 2vw, 24px)', color: 'rgba(255,255,255,0.5)' }}>{'\u2013'}</span>
            <span style={{
              fontSize: 'clamp(22px, 2.8vw, 32px)',
              fontWeight: 400,
              color: 'rgba(255,255,255,0.85)',
              textShadow: ts,
            }}>
              {last_game.opponent.score} {last_game.opponent.abbreviation}
            </span>
            <img src={last_game.opponent.logo} alt=""
              style={{ width: 'clamp(32px, 3.5vw, 46px)', height: 'clamp(32px, 3.5vw, 46px)', objectFit: 'contain' }} />
          </div>
          <div style={{
            fontSize: 'clamp(18px, 2vw, 24px)',
            fontWeight: 400,
            color: 'rgba(255,255,255,0.75)',
            textShadow: ts,
            marginTop: 'clamp(4px, 0.5vh, 8px)',
            textAlign: 'center',
          }}>
            Final {'\u00b7'} {new Date(last_game.gameDate).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })}
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Last-play line for live games. Shows the full sentence (wrapped, centered)
 * and crossfades to the next play when it changes — no horizontal ticker.
 */
function LastPlayFade({ text, ts }: { text: string; ts: string }) {
  const [shown, setShown] = useState(text);
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (text === shown) return;
    // Fade the current sentence out, swap, then fade the next one in.
    setVisible(false);
    const t = setTimeout(() => {
      setShown(text);
      setVisible(true);
    }, 450);
    return () => clearTimeout(t);
  }, [text, shown]);

  return (
    <div style={{
      marginTop: 'clamp(10px, 1.4vh, 18px)',
      maxWidth: 'min(80vw, 900px)',
      textAlign: 'center',
    }}>
      <div style={{
        fontSize: 'clamp(18px, 2vw, 26px)',
        fontWeight: 400,
        color: 'rgba(255,255,255,0.85)',
        textShadow: ts,
        lineHeight: 1.3,
        opacity: visible ? 1 : 0,
        transition: 'opacity 0.45s ease',
        willChange: 'opacity',
      }}>
        {shown}
      </div>
    </div>
  );
}

function ScoreDisplay({ team, opponent, isFinal, ts }: {
  team: SportsTeam; opponent: SportsTeam; isFinal: boolean; ts: string;
}) {
  const toNum = (v: string | number | null): number => {
    if (v == null) return 0;
    const n = typeof v === 'number' ? v : parseInt(v, 10);
    return Number.isFinite(n) ? n : 0;
  };
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 'clamp(14px, 3vw, 36px)' }}>
      <span style={{
        fontSize: 'clamp(64px, 10vw, 120px)',
        fontWeight: 400,
        color: '#ffffff',
        textShadow: ts,
        lineHeight: 1,
        opacity: isFinal && !team.isWinner ? 0.5 : 1,
        // Fixed digit width so a score going 9→10 doesn't shift the layout
        // around it (read as "scroll glitch" during live polling).
        fontVariantNumeric: 'tabular-nums',
        minWidth: '1.4ch',
        textAlign: 'center',
      }}>
        {toNum(team.score)}
      </span>
      <span style={{
        fontSize: 'clamp(36px, 5vw, 62px)',
        fontWeight: 400,
        color: 'rgba(255,255,255,0.7)',
        textShadow: ts,
        lineHeight: 1,
      }}>
        {'\u2013'}
      </span>
      <span style={{
        fontSize: 'clamp(64px, 10vw, 120px)',
        fontWeight: 400,
        color: '#ffffff',
        textShadow: ts,
        lineHeight: 1,
        opacity: isFinal && !opponent.isWinner ? 0.5 : 1,
        fontVariantNumeric: 'tabular-nums',
        minWidth: '1.4ch',
        textAlign: 'center',
      }}>
        {toNum(opponent.score)}
      </span>
    </div>
  );
}

function TeamBlock({ team, ts, possession, timeouts, accent }: {
  team: SportsTeam;
  ts: string;
  possession: boolean;
  timeouts: number | null;
  accent: string;
}) {
  return (
    <div style={{
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      gap: 'clamp(6px, 1vh, 14px)',
      minWidth: 'clamp(110px, 15vw, 170px)',
      position: 'relative',
    }}>
      {/* Possession indicator (football, primarily) */}
      {possession && (
        <div style={{
          position: 'absolute',
          top: '-12px',
          right: '-8px',
          width: 'clamp(10px, 1.2vw, 14px)',
          height: 'clamp(10px, 1.2vw, 14px)',
          borderRadius: '50%',
          background: '#4FC3F7',
          boxShadow: '0 0 16px rgba(79, 195, 247, 0.8)',
        }} />
      )}
      <img
        src={team.logo}
        alt={team.name}
        style={{
          width: 'clamp(80px, 13vw, 140px)',
          height: 'clamp(80px, 13vw, 140px)',
          objectFit: 'contain',
        }}
      />
      <div style={{
        fontSize: 'clamp(22px, 2.8vw, 32px)',
        fontWeight: 500,
        color: '#ffffff',
        textShadow: ts,
        textAlign: 'center',
        whiteSpace: 'nowrap',
      }}>
        {team.name || team.abbreviation}
      </div>
      {team.record && (
        <div style={{
          fontSize: 'clamp(17px, 2vw, 24px)',
          fontWeight: 400,
          color: 'rgba(255,255,255,0.8)',
          textShadow: ts,
        }}>
          {team.record}
        </div>
      )}
      {/* Football timeouts (dots) — only shown when timeouts is a number */}
      {timeouts != null && (
        <div style={{
          display: 'flex',
          gap: 'clamp(4px, 0.6vw, 8px)',
          marginTop: 'clamp(2px, 0.3vh, 6px)',
        }}>
          {[0, 1, 2].map(i => (
            <div key={i} style={{
              width: 'clamp(18px, 2.2vw, 28px)',
              height: 'clamp(5px, 0.7vh, 8px)',
              borderRadius: '3px',
              background: accent,
              border: '1px solid rgba(255,255,255,0.5)',
              opacity: i < timeouts ? 1 : 0.2,
            }} />
          ))}
        </div>
      )}
    </div>
  );
}

function BaseballDiamond({ onFirst, onSecond, onThird }: {
  onFirst: boolean; onSecond: boolean; onThird: boolean;
}) {
  // Size constant — we use an inline <svg> so the diamond renders cleanly at any scale.
  const size = 'clamp(90px, 11vw, 150px)';
  return (
    <div style={{ width: size, height: size, position: 'relative' }}>
      <svg viewBox="0 0 100 100" style={{ width: '100%', height: '100%' }}>
        {/* Diamond outline — 4 edges */}
        {/* Home (bottom), 1st (right), 2nd (top), 3rd (left) */}
        <line x1="50" y1="85" x2="85" y2="50" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
        <line x1="85" y1="50" x2="50" y2="15" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
        <line x1="50" y1="15" x2="15" y2="50" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />
        <line x1="15" y1="50" x2="50" y2="85" stroke="rgba(255,255,255,0.35)" strokeWidth="1.5" />

        {/* Bases — rotated squares */}
        {/* 2nd base (top) */}
        <g transform="translate(50 15) rotate(45)">
          <rect x="-9" y="-9" width="18" height="18"
            fill={onSecond ? '#FFD54F' : 'transparent'}
            stroke="rgba(255,255,255,0.85)" strokeWidth="2" />
        </g>
        {/* 1st base (right) */}
        <g transform="translate(85 50) rotate(45)">
          <rect x="-9" y="-9" width="18" height="18"
            fill={onFirst ? '#FFD54F' : 'transparent'}
            stroke="rgba(255,255,255,0.85)" strokeWidth="2" />
        </g>
        {/* 3rd base (left) */}
        <g transform="translate(15 50) rotate(45)">
          <rect x="-9" y="-9" width="18" height="18"
            fill={onThird ? '#FFD54F' : 'transparent'}
            stroke="rgba(255,255,255,0.85)" strokeWidth="2" />
        </g>
      </svg>
    </div>
  );
}

// ── Widget cycler ──────────────────────────────────────────────────

const WIDGET_DURATION = 8000; // 8 seconds per widget
const FADE_MS = 1000;         // smooth 1s crossfade

interface WidgetSlot {
  key: string;
  node: React.ReactNode;
}

function useWidgetCycler(slots: WidgetSlot[], lockedKey: string | null = null, durationMs: number = WIDGET_DURATION) {
  const available = slots.filter((s) => s.node != null);
  const availableLen = available.length;
  const [activeIdx, setActiveIdx] = useState(0);
  const [fading, setFading] = useState(false);

  // Keep refs so timers read the freshest values without re-subscribing
  const availRef = useRef(available);
  availRef.current = available;
  const lockedKeyRef = useRef(lockedKey);
  lockedKeyRef.current = lockedKey;
  const activeIdxRef = useRef(activeIdx);
  activeIdxRef.current = activeIdx;

  // Clamp index if the list shrinks
  useEffect(() => {
    if (availableLen === 0) return;
    if (activeIdx >= availableLen) setActiveIdx(0);
  }, [availableLen, activeIdx]);

  // Smooth crossfade to a target index (guarded against concurrent calls)
  const transitionRef = useRef<{
    fadeTimeout?: ReturnType<typeof setTimeout>;
    raf1?: number;
    raf2?: number;
  }>({});

  const fadeToIndex = useCallback((nextIdx: number) => {
    if (nextIdx < 0 || nextIdx >= availRef.current.length) return;
    const t = transitionRef.current;
    if (t.fadeTimeout) clearTimeout(t.fadeTimeout);
    if (t.raf1 != null) cancelAnimationFrame(t.raf1);
    if (t.raf2 != null) cancelAnimationFrame(t.raf2);

    setFading(true);
    t.fadeTimeout = setTimeout(() => {
      setActiveIdx(nextIdx);
      t.raf1 = requestAnimationFrame(() => {
        t.raf2 = requestAnimationFrame(() => setFading(false));
      });
    }, FADE_MS);
  }, []);

  // When a new lock engages (e.g. a game just went live) snap to it immediately
  useEffect(() => {
    if (!lockedKey) return;
    const lockedIdx = availRef.current.findIndex((s) => s.key === lockedKey);
    if (lockedIdx >= 0 && lockedIdx !== activeIdx) fadeToIndex(lockedIdx);
    // We intentionally omit activeIdx from deps — we only want this to fire
    // when the lock itself changes, not on every index change.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lockedKey, fadeToIndex]);

  // Auto-advance timer. Restarts on every activeIdx change (auto OR manual),
  // so "if the user stops swiping the timer continues and auto scrolls".
  useEffect(() => {
    if (availableLen <= 1) return;
    const locked = lockedKeyRef.current;
    if (locked) {
      const lockedIdx = availRef.current.findIndex((s) => s.key === locked);
      // Already resting on the locked widget → stop rotating until lock drops
      if (lockedIdx === activeIdx) return;
    }
    const tid = setTimeout(() => {
      const lockedNow = lockedKeyRef.current;
      let nextIdx: number;
      if (lockedNow) {
        const lockedIdx = availRef.current.findIndex((s) => s.key === lockedNow);
        nextIdx = lockedIdx >= 0 ? lockedIdx : (activeIdx + 1) % availableLen;
      } else {
        nextIdx = (activeIdx + 1) % availableLen;
      }
      fadeToIndex(nextIdx);
    }, durationMs);
    return () => clearTimeout(tid);
  }, [activeIdx, availableLen, lockedKey, fadeToIndex, durationMs]);

  // Cleanup any pending transition on unmount
  useEffect(() => {
    return () => {
      const t = transitionRef.current;
      if (t.fadeTimeout) clearTimeout(t.fadeTimeout);
      if (t.raf1 != null) cancelAnimationFrame(t.raf1);
      if (t.raf2 != null) cancelAnimationFrame(t.raf2);
    };
  }, []);

  const goNext = useCallback(() => {
    const len = availRef.current.length;
    if (len <= 1) return;
    fadeToIndex((activeIdxRef.current + 1) % len);
  }, [fadeToIndex]);

  const goPrev = useCallback(() => {
    const len = availRef.current.length;
    if (len <= 1) return;
    fadeToIndex((activeIdxRef.current - 1 + len) % len);
  }, [fadeToIndex]);

  if (availableLen === 0) {
    return { activeKey: null, activeNode: null, fading: false, goNext, goPrev };
  }
  const idx = activeIdx % availableLen;
  return {
    activeKey: available[idx]!.key,
    activeNode: available[idx]!.node,
    fading,
    goNext,
    goPrev,
  };
}

// ── News widget ────────────────────────────────────────────────────

function NewsWidget({ data }: { data: NewsData }) {
  void data;
  return null;
}

function NewsOverlay({ data, textShadow: ts, storyIndex }: { data: NewsData; textShadow: string; storyIndex: number }) {
  const items = data.items.filter((i) => i.image);
  if (items.length === 0) return null;
  const idx = storyIndex % items.length;
  const story = items[idx]!;

  return (
    <>
      {/* Full-screen news image */}
      <div style={{
        position: 'absolute',
        inset: 0,
        zIndex: 11,
      }}>
        <img
          src={story.image}
          alt=""
          style={{ width: '100%', height: '100%', objectFit: 'cover' }}
        />
        {/* Scrim */}
        <div style={{
          position: 'absolute',
          inset: 0,
          background: 'linear-gradient(to top, rgba(0,0,0,0.9) 0%, rgba(0,0,0,0.5) 35%, rgba(0,0,0,0.1) 60%, transparent 100%)',
        }} />
      </div>

      {/* Headline — bottom-left, spanning most of the width */}
      <div style={{
        position: 'absolute',
        bottom: 'clamp(80px, 10vh, 130px)',
        left: 'clamp(36px, 4vw, 64px)',
        right: 'clamp(36px, 4vw, 64px)',
        zIndex: 12,
      }}>
        <div style={{
          fontSize: 'clamp(28px, 3.5vw, 56px)',
          fontWeight: 500,
          color: '#ffffff',
          textShadow: ts,
          lineHeight: 1.2,
        }}>
          {story.title}
        </div>
        <div style={{
          fontSize: 'clamp(15px, 1.6vw, 22px)',
          fontWeight: 400,
          color: 'rgba(255,255,255,0.75)',
          textShadow: ts,
          marginTop: 'clamp(8px, 1vh, 16px)',
        }}>
          {data.feed_name}{story.timeAgo ? ` · ${story.timeAgo}` : ''}
        </div>
      </div>
    </>
  );
}

// ── Now Available overlay — full-screen backdrop with title ────────

function NowAvailableWidget({ data }: { data: NowAvailableData }) {
  void data;
  return null;
}

// ── TV calendar widget ─────────────────────────────────────────────
// "Today"-only shelf: one big centered day header on top; underneath, a
// row of columns per episode with the airtime above each poster, then
// series title, S·E code, and episode title — matching the weather widget
// family (font, colors, text-shadow halo, clamp sizing).
function TvCalendarWidget({ data, textShadow: ts }: { data: TvCalendarData; textShadow: string }) {
  // Keep only episodes airing today in the device's local timezone.
  const now = new Date();
  const todayKey = now.toDateString();
  const todaysEpisodes = data.episodes.filter((ep) => {
    const src = ep.air_date_utc || ep.air_date_local;
    if (!src) return false;
    const d = new Date(src);
    if (Number.isNaN(d.getTime())) return false;
    return d.toDateString() === todayKey;
  });

  if (todaysEpisodes.length === 0) return null;

  const slotCount = Math.min(4, todaysEpisodes.length);
  const episodes = todaysEpisodes.slice(0, slotCount);

  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        gap: 'clamp(18px, 2.4vh, 32px)',
      }}
    >
      {/* "Today" — single centered header, same weight as a weather day */}
      <span
        style={{
          fontSize: 'clamp(28px, 3.6vw, 44px)',
          fontWeight: 500,
          color: 'rgba(255,255,255,0.85)',
          textShadow: ts,
          letterSpacing: '0.5px',
        }}
      >
        Today
      </span>

      <div
        style={{
          display: 'flex',
          alignItems: 'flex-start',
          gap: 'clamp(32px, 6vw, 72px)',
        }}
      >
      {episodes.map((ep, i) => {
        const seCode = (ep.season != null && ep.episode != null)
          ? `S${String(ep.season).padStart(2, '0')} · E${String(ep.episode).padStart(2, '0')}`
          : '';
        const airSrc = ep.air_date_utc || ep.air_date_local;
        const airDate = airSrc ? new Date(airSrc) : null;
        const timeLabel = airDate && !Number.isNaN(airDate.getTime())
          ? airDate.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })
          : '';
        return (
          <div
            key={`${ep.tvdb_id ?? i}-${ep.air_date_utc ?? i}-${ep.season ?? ''}-${ep.episode ?? ''}`}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 'clamp(8px, 1.4vh, 16px)',
              width: 'clamp(150px, 16vw, 210px)',
            }}
          >
            {/* Airtime — above the poster */}
            <span
              style={{
                fontSize: 'clamp(20px, 2.4vw, 28px)',
                fontWeight: 400,
                color: 'rgba(255,255,255,0.75)',
                textShadow: ts,
                letterSpacing: '0.5px',
                whiteSpace: 'nowrap',
              }}
            >
              {timeLabel}
            </span>

            {/* Poster */}
            {ep.poster_url ? (
              <img
                src={ep.poster_url}
                alt={ep.series_title}
                loading="lazy"
                style={{
                  width: '100%',
                  aspectRatio: '2 / 3',
                  objectFit: 'cover',
                  borderRadius: '10px',
                  boxShadow: '0 10px 30px rgba(0,0,0,0.55), 0 2px 8px rgba(0,0,0,0.5)',
                  background: 'rgba(255,255,255,0.04)',
                }}
              />
            ) : (
              <div
                style={{
                  width: '100%',
                  aspectRatio: '2 / 3',
                  borderRadius: '10px',
                  background: 'rgba(255,255,255,0.06)',
                  boxShadow: '0 10px 30px rgba(0,0,0,0.55)',
                }}
              />
            )}

            {/* Series title — bold, primary readout */}
            <span
              style={{
                fontSize: 'clamp(20px, 2.3vw, 30px)',
                fontWeight: 500,
                color: '#ffffff',
                textShadow: ts,
                lineHeight: 1.15,
                textAlign: 'center',
                // Clamp to two lines without spilling into the next column.
                display: '-webkit-box',
                WebkitLineClamp: 2,
                WebkitBoxOrient: 'vertical',
                overflow: 'hidden',
                maxWidth: '100%',
              }}
            >
              {ep.series_title}
            </span>

            {/* S/E code — muted accent matching the low-temp row in weather */}
            {seCode && (
              <span
                style={{
                  fontSize: 'clamp(16px, 1.9vw, 22px)',
                  fontWeight: 400,
                  color: 'rgba(255,255,255,0.6)',
                  textShadow: ts,
                  letterSpacing: '0.06em',
                  lineHeight: 1,
                }}
              >
                {seCode}
              </span>
            )}

            {/* Episode title — single line, smallest, most muted */}
            {ep.episode_title && (
              <span
                style={{
                  fontSize: 'clamp(14px, 1.6vw, 19px)',
                  fontWeight: 300,
                  color: 'rgba(255,255,255,0.52)',
                  textShadow: ts,
                  fontStyle: 'italic',
                  textAlign: 'center',
                  maxWidth: '100%',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {ep.episode_title}
              </span>
            )}
          </div>
        );
      })}
      </div>
    </div>
  );
}

function NowAvailableOverlay({
  data,
  textShadow: ts,
  onOpenDetail,
}: {
  data: NowAvailableData;
  textShadow: string;
  onOpenDetail: () => void;
}) {
  // First two sentences of the plot overview
  const plotSnippet = (() => {
    const overview = (data.overview || '').trim();
    if (!overview) return '';
    const sentences = overview.match(/[^.!?]+[.!?]+(?:\s|$)/g);
    if (!sentences || sentences.length === 0) return overview;
    return sentences.slice(0, 2).join('').trim();
  })();

  const castLine = (data.cast && data.cast.length > 0)
    ? `Starring ${data.cast.join(', ')}`
    : '';

  return (
    <>
      {/* Full-screen backdrop image (static) */}
      <div style={{
        position: 'absolute',
        inset: 0,
        zIndex: 11,
        overflow: 'hidden',
      }}>
        <img
          key={data.backdrop_url}
          src={data.backdrop_url}
          alt=""
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'cover',
          }}
        />
        {/* Uniform dim — no gradient fog. The backdrop reads as a cohesive
            darkened image; the title/plot/cast sit on top with text-shadow. */}
        <div style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0,0,0,0.38)',
        }} />
      </div>

      {/* Bottom-left: "NOW AVAILABLE" label + clickable title + plot + cast */}
      <div style={{
        position: 'absolute',
        bottom: 'clamp(100px, 12vh, 160px)',
        left: 'clamp(40px, 5vw, 80px)',
        zIndex: 12,
        maxWidth: '70vw',
      }}>
        {/* Label row — "NOW AVAILABLE" eyebrow plus IMDb rating + vote
            count pill. Vote count is what ranks the pick (higher = more
            widely-seen), and showing both numbers together gives the
            viewer quick social proof for why this title was surfaced. */}
        <div style={{
          display: 'flex',
          alignItems: 'center',
          gap: 'clamp(12px, 1.4vw, 18px)',
          marginBottom: 'clamp(8px, 1.2vh, 18px)',
          flexWrap: 'wrap',
        }}>
          <span style={{
            fontSize: 'clamp(14px, 1.6vw, 22px)',
            fontWeight: 500,
            letterSpacing: '0.25em',
            textTransform: 'uppercase',
            color: 'rgba(120, 200, 255, 0.9)',
            textShadow: ts,
          }}>
            Now Available
          </span>
          {typeof data.rating === 'number' && data.rating > 0 && (
            <span style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: '6px',
              fontSize: 'clamp(13px, 1.4vw, 19px)',
              fontWeight: 500,
              color: 'rgba(255, 216, 120, 0.95)',
              textShadow: ts,
              letterSpacing: '0.04em',
            }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" stroke="none" aria-hidden="true" style={{ filter: 'drop-shadow(0 1px 2px rgba(0,0,0,0.5))' }}>
                <path d="M12 2l2.4 5.4 5.9.6-4.4 4 1.2 5.8L12 14.9 6.9 17.8 8.1 12l-4.4-4 5.9-.6z" />
              </svg>
              <span style={{ fontWeight: 600, letterSpacing: '0.08em', fontSize: '0.85em', opacity: 0.9 }}>IMDb</span>
              {data.rating.toFixed(1)}
              {typeof data.vote_count === 'number' && data.vote_count > 0 && (
                <span style={{
                  marginLeft: '6px',
                  fontSize: 'clamp(11px, 1.1vw, 15px)',
                  fontWeight: 400,
                  color: 'rgba(255, 255, 255, 0.72)',
                  letterSpacing: '0.04em',
                }}>
                  ({formatVoteCount(data.vote_count)} votes)
                </span>
              )}
            </span>
          )}
        </div>
        {/* Title — clickable */}
        <button
          type="button"
          onClick={onOpenDetail}
          style={{
            display: 'block',
            background: 'transparent',
            border: 'none',
            padding: 0,
            margin: 0,
            textAlign: 'left',
            cursor: 'pointer',
            fontSize: 'clamp(42px, 5.5vw, 80px)',
            fontWeight: 500,
            lineHeight: 1.05,
            color: '#ffffff',
            textShadow: ts,
            letterSpacing: '-0.5px',
            fontFamily: 'inherit',
          }}
          aria-label={`Open details for ${data.title}`}
        >
          {data.title}
        </button>
        {/* Plot snippet */}
        {plotSnippet && (
          <div style={{
            fontSize: 'clamp(18px, 1.9vw, 26px)',
            fontWeight: 400,
            lineHeight: 1.4,
            color: 'rgba(255,255,255,0.98)',
            textShadow: ts,
            marginTop: 'clamp(14px, 1.8vh, 26px)',
            maxWidth: '60vw',
          }}>
            {plotSnippet}
          </div>
        )}
        {/* Cast */}
        {castLine && (
          <div style={{
            fontSize: 'clamp(16px, 1.7vw, 22px)',
            fontWeight: 300,
            color: 'rgba(255,255,255,0.6)',
            textShadow: ts,
            marginTop: 'clamp(10px, 1.2vh, 18px)',
            letterSpacing: '0.02em',
          }}>
            {castLine}
          </div>
        )}
      </div>
    </>
  );
}

// ── Main page ──────────────────────────────────────────────────────

export function DailyKioskPage() {
  const [now, setNow] = useState(new Date());
  const [weather, setWeather] = useState<WeatherData | null>(null);
  const [sports, setSports] = useState<SportsData | null>(null);
  const [news, setNews] = useState<NewsData | null>(null);
  const [nowAvailable, setNowAvailable] = useState<NowAvailableData | null>(null);
  const [tvCalendar, setTvCalendar] = useState<TvCalendarData | null>(null);
  const [homeAreaId, setHomeAreaId] = useState<string>('');
  const [widgetSeconds, setWidgetSeconds] = useState<number>(8);
  const [smartHomeOpen, setSmartHomeOpen] = useState(false);
  const { openMovie, openTv } = useModalHost();

  const openNowAvailableDetail = useCallback(() => {
    if (!nowAvailable || !nowAvailable.tmdb_id) return;
    openModalForItem(
      { openMovie, openTv },
      {
        mediaType: nowAvailable.media_type === 'tv' ? 'tv' : 'movie',
        tmdbId: nowAvailable.tmdb_id,
      },
      'now_available',
    );
  }, [nowAvailable, openMovie, openTv]);

  useEffect(() => { ensureEmberFont(); }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  const fetchWeather = useCallback(async () => {
    try {
      const resp = await fetch('/api/weather');
      const data = await resp.json();
      if (data.configured && !data.error) setWeather(data);
      else setWeather(null);
    } catch { setWeather(null); }
  }, []);

  const fetchSports = useCallback(async () => {
    try {
      const resp = await fetch('/api/sports');
      const data = await resp.json();
      if (data.enabled && !data.error) setSports(data);
      else setSports(null);
    } catch { setSports(null); }
  }, []);

  const fetchNews = useCallback(async () => {
    try {
      const resp = await fetch('/api/news');
      const data = await resp.json();
      if (data.enabled && !data.error && data.items?.length > 0) setNews(data);
      else setNews(null);
    } catch { setNews(null); }
  }, []);

  const fetchNowAvailable = useCallback(async () => {
    try {
      const resp = await fetch('/api/automation/latest');
      const data = await resp.json();
      if (data.found && data.backdrop_url) setNowAvailable(data);
      else setNowAvailable(null);
    } catch { setNowAvailable(null); }
  }, []);

  const fetchTvCalendar = useCallback(async () => {
    try {
      const resp = await fetch('/api/sonarr/calendar?days=14&limit=4');
      const data = await resp.json();
      if (data && data.enabled && Array.isArray(data.episodes) && data.episodes.length > 0) {
        setTvCalendar(data);
      } else {
        setTvCalendar(null);
      }
    } catch { setTvCalendar(null); }
  }, []);

  // Fetch the kiosk's configured HA area so the smart-home modal can open
  // pre-selected to this device's room. Happens once on mount; no polling —
  // the user would have to change it in Settings to invalidate.
  useEffect(() => {
    let cancelled = false;
    fetch('/api/kiosk/settings')
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (cancelled || !data) return;
        if (typeof data.home_area_id === 'string') setHomeAreaId(data.home_area_id);
        if (typeof data.daily_kiosk_widget_seconds === 'number' && data.daily_kiosk_widget_seconds > 0) {
          setWidgetSeconds(data.daily_kiosk_widget_seconds);
        }
      })
      .catch(() => {});
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    fetchWeather();
    fetchSports();
    fetchNews();
    fetchNowAvailable();
    fetchTvCalendar();
    const wId = setInterval(fetchWeather, 15 * 60 * 1000);
    const nId = setInterval(fetchNews, 15 * 60 * 1000);
    const naId = setInterval(fetchNowAvailable, 15 * 60 * 1000);
    const tvId = setInterval(fetchTvCalendar, 15 * 60 * 1000);
    return () => { clearInterval(wId); clearInterval(nId); clearInterval(naId); clearInterval(tvId); };
  }, [fetchWeather, fetchSports, fetchNews, fetchNowAvailable, fetchTvCalendar]);

  // Sports polls on its own cadence so live games can refresh near-realtime.
  // The backend also uses a short 10s cache during live games, so even a
  // faster client poll is bounded. Restart this interval whenever the live
  // flag flips, so we promptly step up (or back down) the refresh rate.
  const sportsLiveFlag = sports?.live === true;
  useEffect(() => {
    const interval = sportsLiveFlag ? 12 * 1000 : 15 * 60 * 1000;
    const id = setInterval(fetchSports, interval);
    return () => clearInterval(id);
  }, [sportsLiveFlag, fetchSports]);

  // Tight drop shadow — gives text an edge against the wallpaper without
  // the visible dark halo the previous wide-blur stack produced.
  const ts = '0 1px 2px rgba(0,0,0,0.55), 0 2px 6px rgba(0,0,0,0.35)';
  const pad = 'clamp(32px, 4vw, 64px)';

  // Build widget list for cycling
  const weatherWidget = weather && weather.forecast.length > 0 ? (
    <div style={{
      display: 'flex',
      alignItems: 'flex-start',
      gap: 'clamp(32px, 7vw, 80px)',
    }}>
      {weather.forecast.slice(0, 5).map((day) => (
        <div
          key={day.date}
          style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            gap: 'clamp(6px, 1.2vh, 14px)',
            minWidth: 'clamp(110px, 14vw, 160px)',
          }}
        >
          <span style={{
            fontSize: 'clamp(26px, 3.5vw, 42px)',
            fontWeight: 500,
            color: 'rgba(255,255,255,0.85)',
            textShadow: ts,
            letterSpacing: '0.5px',
          }}>
            {formatDayShort(day.date)}
          </span>
          <WeatherIcon code={day.icon.replace('n', 'd')} size={Math.min(window.innerWidth * 0.12, 130)} />
          <span style={{
            fontSize: 'clamp(36px, 5vw, 58px)',
            fontWeight: 400,
            color: '#ffffff',
            textShadow: ts,
            lineHeight: 1,
          }}>
            {Math.round(day.temp_max)}{'\u00b0'}
          </span>
          <span style={{
            fontSize: 'clamp(24px, 3vw, 36px)',
            fontWeight: 400,
            color: 'rgba(255,255,255,0.95)',
            textShadow: ts,
            lineHeight: 1,
          }}>
            {Math.round(day.temp_min)}{'\u00b0'}
          </span>
        </div>
      ))}
    </div>
  ) : null;

  const hourlyWidget = weather && weather.hourly && weather.hourly.length > 0 ? (
    <div style={{
      display: 'flex',
      alignItems: 'flex-start',
      gap: 'clamp(32px, 7vw, 80px)',
    }}>
      {weather.hourly.slice(0, 5).map((hr, i) => {
        const h24 = parseInt(hr.time.split(':')[0] ?? '0', 10);
        const h12 = h24 % 12 || 12;
        const ampm = h24 < 12 ? 'AM' : 'PM';
        return (
          <div
            key={i}
            style={{
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: 'clamp(6px, 1.2vh, 14px)',
              minWidth: 'clamp(110px, 14vw, 160px)',
            }}
          >
            <span style={{
              fontSize: 'clamp(26px, 3.5vw, 42px)',
              fontWeight: 500,
              color: 'rgba(255,255,255,0.85)',
              textShadow: ts,
              letterSpacing: '0.5px',
            }}>
              {h12} {ampm}
            </span>
            <WeatherIcon code={hr.icon} size={Math.min(window.innerWidth * 0.12, 130)} />
            <span style={{
              fontSize: 'clamp(36px, 5vw, 58px)',
              fontWeight: 400,
              color: '#ffffff',
              textShadow: ts,
              lineHeight: 1,
            }}>
              {hr.temp}{'\u00b0'}
            </span>
            {/* Invisible spacer to match daily widget's low-temp row height */}
            <span style={{
              fontSize: 'clamp(24px, 3vw, 36px)',
              lineHeight: 1,
              visibility: 'hidden',
            }}>
              {'\u00a0'}
            </span>
          </div>
        );
      })}
    </div>
  ) : null;

  const sportsWidget = sports && (sports.last_game || sports.next_game)
    ? <SportsWidget data={sports} textShadow={ts} />
    : null;

  const newsWidget = news && news.items.length > 0
    ? <NewsWidget data={news} />
    : null;

  const nowAvailableWidget = nowAvailable
    ? <NowAvailableWidget data={nowAvailable} />
    : null;

  const tvCalendarWidget = tvCalendar && tvCalendar.episodes.length > 0
    ? <TvCalendarWidget data={tvCalendar} textShadow={ts} />
    : null;

  // When any tracked game is live, pin the cycler to the sports widget so the
  // screen keeps snapping back between swipes until the game ends.
  // Trust the backend's `live` flag (true when any tracked game is IN progress).
  // Fallback to scanning last/next in case a deployed backend is older.
  const isGameLive = Boolean(
    sports && (
      sports.live === true
      || sports.last_game?.state === 'in'
      || sports.next_game?.state === 'in'
    ),
  );
  const lockedKey = isGameLive && sportsWidget ? 'sports' : null;

  const { activeKey, activeNode, fading, goNext, goPrev } = useWidgetCycler(
    [
      { key: 'daily', node: weatherWidget },
      { key: 'hourly', node: hourlyWidget },
      { key: 'tv-calendar', node: tvCalendarWidget },
      { key: 'now-available', node: nowAvailableWidget },
      { key: 'news', node: newsWidget },
      { key: 'sports', node: sportsWidget },
    ],
    lockedKey,
    widgetSeconds * 1000,
  );

  // ── Horizontal swipe → manual widget advance ─────────────────────
  // Tap/click still works (threshold guards a swipe from becoming a click);
  // a real horizontal drag jumps to the adjacent widget and the auto-timer
  // resets from the new position.
  const touchStartRef = useRef<{ x: number; y: number } | null>(null);
  const onTouchStart = useCallback((e: React.TouchEvent) => {
    const t = e.touches[0];
    if (!t) return;
    touchStartRef.current = { x: t.clientX, y: t.clientY };
  }, []);

  // A click counts as a "background tap" only when it did not originate on a
  // real interactive element (buttons, links). Without this, tapping the
  // "Now Available" title or any future button would also open the modal.
  const isBackgroundTap = useCallback((target: EventTarget | null): boolean => {
    if (!(target instanceof Element)) return true;
    return !target.closest('button, a, [role="button"], input, select, textarea');
  }, []);

  const onTouchEnd = useCallback((e: React.TouchEvent) => {
    const start = touchStartRef.current;
    touchStartRef.current = null;
    if (!start) return;
    const t = e.changedTouches[0];
    if (!t) return;
    const dx = t.clientX - start.x;
    const dy = t.clientY - start.y;
    // Dominant horizontal drag → cycler swipe.
    if (Math.abs(dx) >= 60 && Math.abs(dx) >= Math.abs(dy) * 1.2) {
      if (dx < 0) goNext(); else goPrev();
      return;
    }
    // Short press on empty background → open smart home panel.
    if (Math.abs(dx) < 10 && Math.abs(dy) < 10 && isBackgroundTap(e.target)) {
      setSmartHomeOpen(true);
    }
  }, [goNext, goPrev, isBackgroundTap]);

  const onRootClick = useCallback((e: React.MouseEvent) => {
    // Desktop/mouse pointers: no swipe, just open on background click.
    if (isBackgroundTap(e.target)) setSmartHomeOpen(true);
  }, [isBackgroundTap]);

  // Advance news story each time activeKey transitions TO 'news'
  const prevKeyRef = useRef(activeKey);
  const [newsStoryIdx, setNewsStoryIdx] = useState(0);
  useEffect(() => {
    if (activeKey === 'news' && prevKeyRef.current !== 'news') {
      setNewsStoryIdx((i) => i + 1);
    }
    prevKeyRef.current = activeKey;
  }, [activeKey]);

  return (
    <div
      onTouchStart={onTouchStart}
      onTouchEnd={onTouchEnd}
      onClick={onRootClick}
      style={{
        position: 'relative',
        width: '100vw',
        height: '100dvh',
        overflow: 'hidden',
        fontFamily: EMBER,
        background: '#0a0a0a',
        touchAction: 'pan-y',
        cursor: 'pointer',
      }}
    >
      <WallpaperBackground />
      <ScrimOverlay />
      {/* Extra scrim during live games so the scoreboard reads as the focal
          point of the screen — the wallpaper retreats into the background. */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          background: 'rgba(0,0,0,0.7)',
          pointerEvents: 'none',
          opacity: (isGameLive && activeKey === 'sports') ? 1 : 0,
          transition: 'opacity 0.5s ease',
        }}
      />

      {/* ── Top-left: Clock ── */}
      <div
        style={{
          position: 'absolute',
          top: pad,
          left: pad,
          zIndex: 10,
          opacity: (isGameLive && activeKey === 'sports') ? 0.32 : 1,
          transition: 'opacity 0.4s ease',
        }}
      >
        <div
          style={{
            fontSize: 'clamp(48px, 6vw, 80px)',
            fontWeight: 500,
            lineHeight: 0.95,
            color: '#ffffff',
            textShadow: ts,
            letterSpacing: '-2px',
          }}
        >
          {formatTime(now)}
        </div>
      </div>

      {/* ── Top-right: Current temp + condition + location ── */}
      {weather && (
        <div
          style={{
            position: 'absolute',
            top: pad,
            right: pad,
            zIndex: 10,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'flex-end',
            gap: '2px',
            opacity: isGameLive ? 0.32 : 1,
            transition: 'opacity 0.4s ease',
          }}
        >
          {/* Temperature */}
          <div style={{
            fontSize: 'clamp(32px, 4vw, 48px)',
            fontWeight: 400,
            lineHeight: 1,
            color: '#ffffff',
            textShadow: ts,
          }}>
            {weather.current.temp}<span style={{ fontSize: '0.5em', verticalAlign: 'super' }}>{'\u00b0'}</span>
          </div>
          {/* Icon + condition inline */}
          <div style={{
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
            marginTop: '2px',
          }}>
            <WeatherIcon code={weather.current.icon} size={20} />
            <span style={{
              fontSize: 'clamp(13px, 1.3vw, 17px)',
              fontWeight: 400,
              color: 'rgba(255,255,255,0.8)',
              textShadow: ts,
              textTransform: 'capitalize',
            }}>
              {weather.current.description}
            </span>
          </div>
          {/* Location */}
          <div style={{
            fontSize: 'clamp(12px, 1.2vw, 15px)',
            fontWeight: 400,
            color: 'rgba(255,255,255,0.6)',
            textShadow: ts,
            marginTop: '1px',
          }}>
            {weather.location}
          </div>
        </div>
      )}

      {/* ── Now Available: full-screen overlay ── */}
      <div style={{
        position: 'absolute',
        inset: 0,
        zIndex: 11,
        transition: `opacity ${FADE_MS}ms ease-in-out`,
        willChange: 'opacity',
        opacity: activeKey === 'now-available' && !fading ? 1 : 0,
        pointerEvents: activeKey === 'now-available' ? 'auto' : 'none',
      }}>
        {nowAvailable && (
          <NowAvailableOverlay
            data={nowAvailable}
            textShadow={ts}
            onOpenDetail={openNowAvailableDetail}
          />
        )}
      </div>

      {/* ── News: full-screen overlay — always rendered, opacity-driven ── */}
      <div style={{
        position: 'absolute',
        inset: 0,
        zIndex: 11,
        transition: `opacity ${FADE_MS}ms ease-in-out`,
        willChange: 'opacity',
        opacity: activeKey === 'news' && !fading ? 1 : 0,
        pointerEvents: activeKey === 'news' ? 'auto' : 'none',
      }}>
        {news && <NewsOverlay data={news} textShadow={ts} storyIndex={newsStoryIdx} />}
      </div>

      {/* ── Center: other widgets — always rendered, opacity-driven.
            The wrapper fills the viewport and uses flex centering so the
            content's anchor never shifts when the widget swaps or when
            its internal size changes (e.g. a baseball score going 9→10
            or an inning text "Top 5th" → "End 5th"). The previous
            translate(-50%,-50%) approach re-centered on every dimension
            change, which read as a "scroll" glitch during live polling. */}
      <div
        style={{
          position: 'absolute',
          inset: 0,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          zIndex: 10,
          pointerEvents: 'none',
          transition: `opacity ${FADE_MS}ms ease-in-out`,
          willChange: 'opacity',
          opacity: activeKey !== 'news' && activeKey !== 'now-available' && !fading ? 1 : 0,
        }}
      >
        <div style={{ pointerEvents: 'auto' }}>
          {activeKey !== 'news' && activeKey !== 'now-available' ? activeNode : null}
        </div>
      </div>

      {/* ── Bottom-center: Nova suggestion (hidden when the live sports
             widget is the active page so the scoreboard owns the screen) ── */}
      {!(isGameLive && activeKey === 'sports') && (
        <div
          style={{
            position: 'absolute',
            bottom: pad,
            left: '50%',
            transform: 'translateX(-50%)',
            zIndex: 10,
            whiteSpace: 'nowrap',
          }}
        >
          <NovaSuggestion />
        </div>
      )}

      {/* ── Bottom-right: Date ── */}
      <div
        style={{
          position: 'absolute',
          bottom: pad,
          right: pad,
          zIndex: 10,
          opacity: (isGameLive && activeKey === 'sports') ? 0.32 : 1,
          transition: 'opacity 0.4s ease',
        }}
      >
        <div
          style={{
            fontSize: 'clamp(22px, 2.8vw, 32px)',
            fontWeight: 400,
            color: 'rgba(255,255,255,0.85)',
            textShadow: ts,
          }}
        >
          {formatDate(now)}
        </div>
      </div>

      {/* ── Smart home control modal (tap-to-open) ── */}
      <SmartHomeModal
        open={smartHomeOpen}
        onClose={() => setSmartHomeOpen(false)}
        defaultAreaId={homeAreaId}
      />
    </div>
  );
}

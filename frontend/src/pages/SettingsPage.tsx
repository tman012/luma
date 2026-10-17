/**
 * SettingsPage — SPA replacement for the legacy settings.html.
 *
 * Desktop: Back button + fixed left sidebar (260px) with tab list + scrollable right content.
 * Mobile: MobileHeader + horizontal scrollable pill tab bar.
 *
 * Tabs: Services | Rooms | UI | Media | Network | Security | Automation
 */

import { Component, useState, useCallback, useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router';
import { useUiDensity } from '@/hooks/useUiDensity';
import { useBodyScroll } from '@/hooks/useBodyScroll';
import { SettingsProvider } from './settings/SettingsContext';
import { CardNavContext } from './settings/components/MobileCardNav';
import { ToastContainer } from './settings/components/Toast';
import { ServicesTab } from './settings/ServicesTab';
import { RoomsTab } from './settings/RoomsTab';
import { UiKioskTab } from './settings/UiKioskTab';
import { MediaPathsTab } from './settings/MediaPathsTab';
import { NetworkTab } from './settings/NetworkTab';
import { AutomationTab } from './settings/AutomationTab';
import { SecurityTab } from './settings/SecurityTab';
import { AiTab } from './settings/AiTab';
import { cn } from '@/utils/cn';

// ── Error boundary to catch and display render crashes ──────────
class SettingsErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state: { error: Error | null } = { error: null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return (
        <div className="min-h-dvh bg-kiosk-black text-white flex items-center justify-center p-8">
          <div className="max-w-lg space-y-4">
            <h2 className="text-xl font-semibold text-red-400">Settings failed to load</h2>
            <pre className="text-sm text-white/60 whitespace-pre-wrap break-words bg-white/5 rounded-lg p-4 border border-white/10">
              {this.state.error.message}
              {'\n\n'}
              {this.state.error.stack}
            </pre>
            <button
              type="button"
              onClick={() => this.setState({ error: null })}
              className="px-4 py-2 bg-accent hover:bg-accent-hover text-white rounded-lg text-sm font-medium"
            >
              Retry
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Tab definitions ──────────────────────────────────────────────

type TabId = 'services' | 'ai' | 'rooms' | 'ui' | 'media' | 'network' | 'security' | 'automation';

interface TabDef {
  id: TabId;
  label: string;
  subtitle: string;
  section: string;
  icon: React.ReactNode;
}

const TABS: TabDef[] = [
  {
    id: 'services',
    label: 'Services',
    subtitle: 'Plex \u00b7 Sonarr \u00b7 Radarr \u00b7 Indexer',
    section: 'Services',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M12 2v4M12 18v4M4.93 4.93l2.83 2.83M16.24 16.24l2.83 2.83M2 12h4M18 12h4M4.93 19.07l2.83-2.83M16.24 7.76l2.83-2.83" />
      </svg>
    ),
  },
  {
    id: 'ai',
    label: 'AI',
    subtitle: 'OpenAI \u00b7 Content preferences',
    section: 'Services',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M12 2a8 8 0 0 1 8 8c0 3.4-2.1 6.3-5 7.5V20a2 2 0 0 1-2 2h-2a2 2 0 0 1-2-2v-2.5C6.1 16.3 4 13.4 4 10a8 8 0 0 1 8-8z" />
        <line x1="10" y1="22" x2="14" y2="22" />
        <line x1="9" y1="9" x2="9.01" y2="9" /><line x1="15" y1="9" x2="15.01" y2="9" />
      </svg>
    ),
  },
  {
    id: 'rooms',
    label: 'Rooms & Devices',
    subtitle: 'Shield \u00b7 Android TV \u00b7 Chromecast',
    section: 'Hardware',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="2" y="7" width="20" height="15" rx="2" ry="2" /><polyline points="17 2 12 7 7 2" />
      </svg>
    ),
  },
  {
    id: 'ui',
    label: 'UI & Kiosk',
    subtitle: 'App name \u00b7 Screensaver \u00b7 Hero display',
    section: 'Application',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
      </svg>
    ),
  },
  {
    id: 'media',
    label: 'Media Paths',
    subtitle: 'System paths \u00b7 Library configuration',
    section: 'Application',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
      </svg>
    ),
  },
  {
    id: 'network',
    label: 'Network',
    subtitle: 'Flask port \u00b7 Cloudflare tunnel',
    section: 'Application',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
        <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
        <line x1="6" y1="6" x2="6.01" y2="6" /><line x1="6" y1="18" x2="6.01" y2="18" />
      </svg>
    ),
  },
  {
    id: 'security',
    label: 'Security',
    subtitle: 'Require PIN \u00b7 Trusted proxies',
    section: 'Application',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <rect x="4" y="11" width="16" height="10" rx="2" /><path d="M8 11V7a4 4 0 0 1 8 0v4" />
      </svg>
    ),
  },
  {
    id: 'automation',
    label: 'Automation',
    subtitle: 'Trending watchlists \u00b7 Scheduling',
    section: 'Application',
    icon: (
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
        <path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />
      </svg>
    ),
  },

];

// ── Tab content map ──────────────────────────────────────────────

const TAB_CONTENT: Record<TabId, React.ReactNode> = {
  services: <ServicesTab />,
  ai: <AiTab />,
  rooms: <RoomsTab />,
  ui: <UiKioskTab />,
  media: <MediaPathsTab />,
  network: <NetworkTab />,
  security: <SecurityTab />,
  automation: <AutomationTab />,
};

// ── Page component ───────────────────────────────────────────────

function SettingsPageInner() {
  const navigate = useNavigate();
  const d = useUiDensity();
  const isMobile = d.mode === 'compact';
  useBodyScroll();

  // ?tab=<id> opens a tab directly (e.g. from a "Connect Radarr" card).
  const [activeTab, setActiveTab] = useState<TabId | null>(() => {
    const t = new URLSearchParams(window.location.search).get('tab');
    return TABS.some(x => x.id === t) ? (t as TabId) : null;
  });
  const [activeCard, setActiveCard] = useState<number | null>(null);
  const [activeCardTitle, setActiveCardTitle] = useState<string>('');

  // Card nav context value — provided to MobileCardNav via context
  const handleDrill = useCallback((index: number, title: string) => {
    setActiveCard(index);
    setActiveCardTitle(title);
  }, []);

  const cardNavValue = useMemo(() => ({
    activeCard,
    onDrill: handleDrill,
  }), [activeCard, handleDrill]);

  // Back handler — pops the deepest active level
  const handleBack = useCallback(() => {
    if (activeCard !== null) {
      setActiveCard(null);
      setActiveCardTitle('');
    } else if (activeTab) {
      setActiveTab(null);
    } else {
      navigate('/');
    }
  }, [activeCard, activeTab, navigate]);

  // Current header title
  const headerTitle = activeCard !== null
    ? activeCardTitle
    : activeTab
      ? TABS.find(t => t.id === activeTab)?.label ?? 'Settings'
      : 'Settings';

  // Build section-grouped sidebar tabs
  const sectionGroups: { section: string; tabs: TabDef[] }[] = [];
  for (const tab of TABS) {
    const last = sectionGroups[sectionGroups.length - 1];
    if (last && last.section === tab.section) {
      last.tabs.push(tab);
    } else {
      sectionGroups.push({ section: tab.section, tabs: [tab] });
    }
  }

  // ── Mobile layout ────────────────────────────────────────────

  if (isMobile) {
    return (
      <div
        className="bg-kiosk-black text-white"
        style={{ minHeight: '100dvh' }}
      >
        {/* Single unified header — safe area aware */}
        <div
          className="fixed top-0 inset-x-0 z-40 bg-kiosk-black/95 backdrop-blur-md border-b border-white/[0.06]"
          style={{ paddingTop: 'env(safe-area-inset-top, 0px)' }}
        >
          <div className="flex items-center gap-3 px-4 min-h-[56px]">
            <button
              type="button"
              onClick={handleBack}
              className="flex items-center justify-center w-9 h-9 -ml-1 rounded-lg text-white/60 active:bg-white/10 transition-colors"
              aria-label="Back"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 18 9 12 15 6" />
              </svg>
            </button>
            <span className="text-[17px] font-semibold text-white/90">
              {headerTitle}
            </span>
          </div>
        </div>

        {/* Content area */}
        <div
          className="px-4 pb-8"
          style={{ paddingTop: 'calc(68px + env(safe-area-inset-top, 0px))' }}
        >
          {activeTab ? (
            <CardNavContext.Provider value={cardNavValue}>
              {TAB_CONTENT[activeTab]}
            </CardNavContext.Provider>
          ) : (
            /* Root settings list */
            <div className="-mx-4">
              {TABS.map((tab, i) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => { setActiveTab(tab.id); setActiveCard(null); }}
                  className={cn(
                    'flex w-full items-center gap-4 px-5 py-4 min-h-[64px] text-left transition-colors active:bg-white/[0.04]',
                    i > 0 && 'border-t border-white/[0.04]',
                  )}
                >
                  <span className="text-white/50 shrink-0">{tab.icon}</span>
                  <div className="flex-1 min-w-0">
                    <p className="text-[16px] font-medium text-white/90 leading-snug">{tab.label}</p>
                    <p className="text-[13px] text-white/40 mt-0.5 leading-snug">{tab.subtitle}</p>
                  </div>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="text-white/20 shrink-0">
                    <polyline points="9 18 15 12 9 6" />
                  </svg>
                </button>
              ))}
            </div>
          )}
        </div>

        <ToastContainer />
      </div>
    );
  }

  // ── Desktop / Tablet layout ──────────────────────────────────

  const desktopTab = activeTab ?? 'services';

  return (
    <div className="flex h-dvh bg-kiosk-black text-white">
      {/* Sidebar */}
      <aside data-scroll-container className="luma-scroll-y sticky top-0 flex h-dvh w-[260px] shrink-0 flex-col border-r border-white/[0.06] bg-white/[0.02]">
        {/* Title */}
        <div className="flex items-center gap-2 border-b border-white/[0.06] px-5 py-4">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="text-white/30">
            <circle cx="12" cy="12" r="3" /><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06A1.65 1.65 0 0 0 4.68 15a1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06A1.65 1.65 0 0 0 9 4.68a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z" />
          </svg>
          <span className="text-[16px] font-semibold text-white/85">Settings</span>
        </div>

        {/* Tab list */}
        <nav className="flex-1 p-4 space-y-1">
          {sectionGroups.map((group, gi) => (
            <div key={group.section} className={gi > 0 ? 'mt-5' : ''}>
              <p className="mb-2 px-3 text-[11px] font-bold uppercase tracking-[1.2px] text-white/25">
                {group.section}
              </p>
              {group.tabs.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveTab(tab.id)}
                  className={cn(
                    'flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-[14px] font-medium transition-all mb-0.5',
                    desktopTab === tab.id
                      ? 'bg-emerald-500/8 text-emerald-400 border border-emerald-500/15'
                      : 'text-white/50 border border-transparent hover:bg-white/5 hover:text-white/75',
                  )}
                >
                  <span className="w-5 text-center">{tab.icon}</span>
                  {tab.label}
                </button>
              ))}
            </div>
          ))}
        </nav>
      </aside>

      {/* Content area */}
      <main data-scroll-container className="luma-scroll-y flex-1 px-6 py-8 sm:px-10 lg:px-12">
        <div className="mx-auto max-w-[900px]">
          {TAB_CONTENT[desktopTab]}
        </div>
      </main>

      <ToastContainer />
    </div>
  );
}

// ── Wrapped with provider ────────────────────────────────────────

export function SettingsPage() {
  return (
    <SettingsErrorBoundary>
      <SettingsProvider>
        <SettingsPageInner />
      </SettingsProvider>
    </SettingsErrorBoundary>
  );
}

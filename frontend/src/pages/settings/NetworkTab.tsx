/**
 * NetworkTab — Flask port, secret key, docker mode, Cloudflare Tunnel.
 */

import { useState, useEffect } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ToggleSwitch } from './components/ToggleSwitch';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';

const SERVER_PATHS = [
  'network.flask_port',
  'network.flask_secret_key',
  'network.is_docker',
];

const TUNNEL_PATHS = [
  'network.tunnel.enabled',
  'network.tunnel.token',
];

/* -- Tunnel status badge ---------------------------------------- */

interface TunnelStatus {
  running: boolean;
  enabled: boolean;
  pid: number | null;
  uptime_seconds: number | null;
  last_error: string;
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return m > 0 ? `${h}h ${m}m` : `${h}h`;
}

function TunnelStatusBadge() {
  const [status, setStatus] = useState<TunnelStatus | null>(null);

  useEffect(() => {
    let active = true;
    const poll = async () => {
      try {
        const res = await fetch('/api/tunnel/status');
        if (res.ok && active) setStatus(await res.json());
      } catch { /* ignore */ }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => { active = false; clearInterval(id); };
  }, []);

  if (!status) return null;

  const color = status.running
    ? 'bg-emerald-500/20 text-emerald-400'
    : status.last_error
      ? 'bg-amber-500/20 text-amber-400'
      : 'bg-white/10 text-white/40';

  const label = status.running
    ? `Running${status.uptime_seconds ? ` — ${formatUptime(status.uptime_seconds)}` : ''}`
    : status.last_error
      ? 'Error'
      : 'Stopped';

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-[11px] font-medium ${color}`}
      title={status.last_error || undefined}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${
        status.running ? 'bg-emerald-400' : status.last_error ? 'bg-amber-400' : 'bg-white/30'
      }`} />
      {label}
    </span>
  );
}

/* -- Setup guide ------------------------------------------------ */

function TunnelSetupGuide() {
  const [open, setOpen] = useState(false);

  return (
    <div className="sm:col-span-2">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 text-[12px] text-white/40 hover:text-white/60 transition-colors"
      >
        <svg
          width="12" height="12" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          className={`transition-transform ${open ? 'rotate-90' : ''}`}
        >
          <polyline points="9 18 15 12 9 6" />
        </svg>
        Setup guide
      </button>
      {open && (
        <ol className="mt-3 space-y-2 text-[12px] text-white/50 list-decimal list-inside">
          <li>Go to <span className="text-white/70">Cloudflare Zero Trust</span> dashboard</li>
          <li>Navigate to <span className="text-white/70">Networks &rarr; Tunnels</span></li>
          <li>Click <span className="text-white/70">Create a tunnel</span> &rarr; choose <span className="text-white/70">Cloudflared</span></li>
          <li>Name your tunnel (e.g. "luma")</li>
          <li>Copy the tunnel token from the install command</li>
          <li>Paste it above, enable the toggle, and save</li>
          <li>Back in Cloudflare, add a <span className="text-white/70">Public Hostname</span> pointing to <code className="text-white/60 bg-white/5 px-1 rounded">http://localhost:5001</code></li>
          <li>Access your app at the public hostname from anywhere</li>
        </ol>
      )}
    </div>
  );
}

/* -- NetworkTab ------------------------------------------------- */

export function NetworkTab() {
  const items: CardItem[] = [
    {
      title: 'Server',
      subtitle: 'Flask application network settings',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
          <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
          <line x1="6" y1="6" x2="6.01" y2="6" />
          <line x1="6" y1="18" x2="6.01" y2="18" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Server"
          subtitle="Flask application network settings"
          paths={SERVER_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2" />
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2" />
              <line x1="6" y1="6" x2="6.01" y2="6" />
              <line x1="6" y1="18" x2="6.01" y2="18" />
            </svg>
          }
        >
          <FormField
            label="Flask Port"
            path="network.flask_port"
            type="number"
            placeholder="5001"
            min={1}
            max={65535}
          />
          <FormField
            label="Flask Secret Key"
            path="network.flask_secret_key"
            type="password"
            placeholder="Secret key"
          />
          <div className="sm:col-span-2">
            <ToggleSwitch
              label="Docker Mode"
              path="network.is_docker"
            />
          </div>
        </SettingsCard>
      ),
    },
    {
      title: 'Cloudflare Tunnel',
      subtitle: 'Secure remote access without opening ports',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="Cloudflare Tunnel"
          subtitle="Secure remote access without opening router ports"
          paths={TUNNEL_PATHS}
          testService="tunnel"
          testLabel="Check Binary"
          headerRight={<TunnelStatusBadge />}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
            </svg>
          }
          note="Tunnel runs inside the Docker container. No extra services or open ports needed."
        >
          <div className="sm:col-span-2">
            <ToggleSwitch
              label="Enable Remote Access"
              path="network.tunnel.enabled"
            />
          </div>
          <div className="sm:col-span-2">
            <FormField
              label="Tunnel Token"
              path="network.tunnel.token"
              type="password"
              placeholder="Paste token from Cloudflare Zero Trust dashboard"
            />
          </div>
          <TunnelSetupGuide />
        </SettingsCard>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Network</h2>
        <p className="mt-1 text-[13px] text-white/40">
          Flask server and network configuration.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

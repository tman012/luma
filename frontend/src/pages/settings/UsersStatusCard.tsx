/**
 * UsersStatusCard — admin-only card that shows all registered users
 * and the health of their configured services (Plex, Radarr, Sonarr).
 *
 * Fetches GET /api/admin/users/status on mount.
 */

import { useState, useEffect } from 'react';
import { apiFetch } from '../../apiClient';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface ServiceStatus {
  configured: boolean;
  online: boolean;
  server_name?: string;
  error?: string;
}

interface UserStatusRaw {
  user_id: number;
  name: string;
  email: string | null;
  is_admin: boolean;
  use_shared_server: boolean;
  services: {
    plex: ServiceStatus;
    radarr: ServiceStatus;
    sonarr: ServiceStatus;
  };
}

interface UserStatus {
  id: number;
  name: string;
  email: string | null;
  is_admin: boolean;
  use_shared_server: boolean;
  plex: ServiceStatus;
  radarr: ServiceStatus;
  sonarr: ServiceStatus;
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function StatusDot({ status }: { status: ServiceStatus }) {
  if (!status.configured) {
    return (
      <span
        style={{
          display: 'inline-block',
          width: 8,
          height: 8,
          borderRadius: '50%',
          backgroundColor: 'rgba(255,255,255,0.2)',
          marginRight: 6,
        }}
      />
    );
  }
  const color = status.online ? '#4ade80' : '#f87171';
  const glow = status.online
    ? '0 0 6px rgba(74,222,128,0.5)'
    : '0 0 6px rgba(248,113,113,0.5)';
  return (
    <span
      style={{
        display: 'inline-block',
        width: 8,
        height: 8,
        borderRadius: '50%',
        backgroundColor: color,
        boxShadow: glow,
        marginRight: 6,
      }}
    />
  );
}

function ServiceIndicator({ label, status }: { label: string; status: ServiceStatus }) {
  let text: string;
  let textColor: string;

  if (!status.configured) {
    text = 'Not configured';
    textColor = 'rgba(255,255,255,0.3)';
  } else if (status.online) {
    text = label === 'Plex' && status.server_name
      ? status.server_name
      : 'Online';
    textColor = 'rgba(74,222,128,0.9)';
  } else {
    text = 'Offline';
    textColor = 'rgba(248,113,113,0.9)';
  }

  return (
    <div style={{ display: 'flex', alignItems: 'center', minWidth: 0 }}>
      <StatusDot status={status} />
      <span
        style={{
          fontSize: 12,
          fontWeight: 500,
          color: textColor,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis',
        }}
      >
        {label}: {text}
      </span>
    </div>
  );
}

function Badge({ children, color }: { children: React.ReactNode; color: string }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        fontSize: 10,
        fontWeight: 600,
        letterSpacing: '0.5px',
        textTransform: 'uppercase' as const,
        padding: '2px 8px',
        borderRadius: 6,
        backgroundColor: color,
        color: 'rgba(255,255,255,0.9)',
      }}
    >
      {children}
    </span>
  );
}

function UserRow({ user }: { user: UserStatus }) {
  return (
    <div
      style={{
        display: 'flex',
        flexDirection: 'column',
        gap: 8,
        padding: '14px 16px',
        borderRadius: 12,
        backgroundColor: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      {/* Name + badges row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 14, fontWeight: 600, color: 'rgba(255,255,255,0.85)' }}>
          {user.name}
        </span>
        {user.email && (
          <span style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)' }}>
            {user.email}
          </span>
        )}
        {user.is_admin && <Badge color="rgba(168,85,247,0.25)">Admin</Badge>}
        {user.use_shared_server && <Badge color="rgba(59,130,246,0.25)">Shared Server</Badge>}
      </div>

      {/* Service indicators */}
      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <ServiceIndicator label="Plex" status={user.plex} />
        <ServiceIndicator label="Radarr" status={user.radarr} />
        <ServiceIndicator label="Sonarr" status={user.sonarr} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main component
// ---------------------------------------------------------------------------

export function UsersStatusCard() {
  const [users, setUsers] = useState<UserStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    apiFetch('/api/admin/users/status')
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((data) => {
        if (!cancelled) {
          const mapped = ((data.users ?? []) as UserStatusRaw[]).map((u) => ({
            id: u.user_id,
            name: u.name,
            email: u.email,
            is_admin: u.is_admin,
            use_shared_server: u.use_shared_server,
            plex: u.services?.plex ?? { configured: false, online: false },
            radarr: u.services?.radarr ?? { configured: false, online: false },
            sonarr: u.services?.sonarr ?? { configured: false, online: false },
          }));
          setUsers(mapped);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message ?? 'Failed to load');
          setLoading(false);
        }
      });

    return () => { cancelled = true; };
  }, []);

  return (
    <div
      style={{
        borderRadius: 16,
        border: '1px solid rgba(255,255,255,0.06)',
        backgroundColor: 'rgba(255,255,255,0.02)',
        padding: '20px 20px 16px',
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 16 }}>
        <div
          style={{
            width: 32,
            height: 32,
            borderRadius: 8,
            backgroundColor: 'rgba(255,255,255,0.05)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" style={{ color: 'rgba(255,255,255,0.5)' }}>
            <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" />
            <circle cx="9" cy="7" r="4" />
            <path d="M23 21v-2a4 4 0 0 0-3-3.87" />
            <path d="M16 3.13a4 4 0 0 1 0 7.75" />
          </svg>
        </div>
        <div>
          <h3 style={{ fontSize: 15, fontWeight: 600, color: 'rgba(255,255,255,0.85)', margin: 0 }}>
            Users
          </h3>
          <p style={{ fontSize: 12, color: 'rgba(255,255,255,0.35)', margin: 0 }}>
            Registered users and service health
          </p>
        </div>
      </div>

      {/* Content */}
      {loading && (
        <div style={{ padding: '20px 0', textAlign: 'center' }}>
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)' }}>Loading users...</span>
        </div>
      )}

      {error && (
        <div
          style={{
            padding: '12px 16px',
            borderRadius: 10,
            backgroundColor: 'rgba(248,113,113,0.1)',
            border: '1px solid rgba(248,113,113,0.2)',
          }}
        >
          <span style={{ fontSize: 13, color: 'rgba(248,113,113,0.9)' }}>
            Failed to load users: {error}
          </span>
        </div>
      )}

      {!loading && !error && users.length === 0 && (
        <div style={{ padding: '20px 0', textAlign: 'center' }}>
          <span style={{ fontSize: 13, color: 'rgba(255,255,255,0.4)' }}>No users found</span>
        </div>
      )}

      {!loading && !error && users.length > 0 && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {users.map((user) => (
            <UserRow key={user.id} user={user} />
          ))}
        </div>
      )}
    </div>
  );
}

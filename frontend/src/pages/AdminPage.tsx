/**
 * AdminPage — User management for Taylor.
 * Create users, reset PINs, delete accounts.
 */
import { useState, useEffect } from 'react';
import { useNavigate } from 'react-router';
import { useAuth } from '@/contexts/AuthContext';

interface User {
  id: number;
  name: string;
  is_admin: number;
  created_at: string;
}

type ModalState =
  | { type: 'create' }
  | { type: 'reset-pin'; user: User }
  | { type: 'delete'; user: User }
  | null;

export function AdminPage() {
  const { user, authenticated, loading } = useAuth();
  const navigate = useNavigate();

  const [users, setUsers] = useState<User[]>([]);
  const [fetchError, setFetchError] = useState('');
  const [modal, setModal] = useState<ModalState>(null);

  // Redirect non-admins
  useEffect(() => {
    if (!loading && (!authenticated || !user?.is_admin)) {
      navigate('/', { replace: true });
    }
  }, [loading, authenticated, user, navigate]);

  async function fetchUsers() {
    try {
      const res = await fetch('/api/admin/users');
      if (!res.ok) throw new Error('Failed to load users');
      const data = await res.json();
      setUsers(data.users);
    } catch (e: any) {
      setFetchError(e.message);
    }
  }

  useEffect(() => {
    if (authenticated && user?.is_admin) fetchUsers();
  }, [authenticated, user]);

  if (loading) return <div className="admin-page"><p>Loading…</p></div>;

  return (
    <div className="admin-page">
      <div className="admin-header">
        <button className="admin-back-btn" onClick={() => navigate('/')}>← Home</button>
        <h1 className="admin-title">User Management</h1>
        <button
          className="admin-add-btn"
          onClick={() => setModal({ type: 'create' })}
        >
          + Add User
        </button>
      </div>

      {fetchError && <p className="admin-error">{fetchError}</p>}

      <div className="admin-user-list">
        {users.map(u => (
          <div key={u.id} className="admin-user-row">
            <div className="admin-user-info">
              <span className="admin-user-name">{u.name}</span>
              {u.is_admin ? <span className="admin-badge">Admin</span> : null}
            </div>
            <div className="admin-user-actions">
              <button
                className="admin-action-btn"
                onClick={() => setModal({ type: 'reset-pin', user: u })}
              >
                Reset PIN
              </button>
              {!u.is_admin && (
                <button
                  className="admin-action-btn admin-action-btn--danger"
                  onClick={() => setModal({ type: 'delete', user: u })}
                >
                  Delete
                </button>
              )}
            </div>
          </div>
        ))}
      </div>

      {modal && (
        <AdminModal
          modal={modal}
          onClose={() => setModal(null)}
          onDone={() => { setModal(null); fetchUsers(); }}
        />
      )}
    </div>
  );
}

// ── Modal ─────────────────────────────────────────────────────────────────────

function AdminModal({
  modal,
  onClose,
  onDone,
}: {
  modal: NonNullable<ModalState>;
  onClose: () => void;
  onDone: () => void;
}) {
  const [name, setName] = useState('');
  const [pin, setPin] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function handleCreate() {
    if (!name.trim()) return setError('Name is required');
    if (!pin || pin.length < 4 || pin.length > 6 || !/^\d+$/.test(pin)) {
      return setError('PIN must be 4–6 digits');
    }
    setBusy(true);
    try {
      const res = await fetch('/api/admin/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name.trim(), pin }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleResetPin() {
    if (modal.type !== 'reset-pin') return;
    if (!pin || pin.length < 4 || pin.length > 6 || !/^\d+$/.test(pin)) {
      return setError('PIN must be 4–6 digits');
    }
    setBusy(true);
    try {
      const res = await fetch(`/api/admin/users/${modal.user.id}/pin`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (modal.type !== 'delete') return;
    setBusy(true);
    try {
      const res = await fetch(`/api/admin/users/${modal.user.id}`, { method: 'DELETE' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Failed');
      onDone();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-modal-overlay" onClick={onClose}>
      <div className="admin-modal" onClick={e => e.stopPropagation()}>
        {modal.type === 'create' && (
          <>
            <h2 className="admin-modal-title">Add User</h2>
            <label className="admin-modal-label">Name</label>
            <input
              className="admin-modal-input"
              type="text"
              placeholder="Full name"
              value={name}
              onChange={e => setName(e.target.value)}
              autoFocus
              autoCapitalize="words"
            />
            <label className="admin-modal-label">PIN (4–6 digits)</label>
            <input
              className="admin-modal-input"
              type="tel"
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="e.g. 1234"
              maxLength={6}
              value={pin}
              onChange={e => setPin(e.target.value.replace(/\D/g, ''))}
            />
            {error && <p className="admin-modal-error">{error}</p>}
            <div className="admin-modal-actions">
              <button className="admin-modal-cancel" onClick={onClose}>Cancel</button>
              <button className="admin-modal-confirm" onClick={handleCreate} disabled={busy}>
                {busy ? 'Creating…' : 'Create User'}
              </button>
            </div>
          </>
        )}

        {modal.type === 'reset-pin' && (
          <>
            <h2 className="admin-modal-title">Reset PIN — {modal.user.name}</h2>
            <label className="admin-modal-label">New PIN (4–6 digits)</label>
            <input
              className="admin-modal-input"
              type="tel"
              inputMode="numeric"
              pattern="[0-9]*"
              placeholder="New PIN"
              maxLength={6}
              value={pin}
              onChange={e => setPin(e.target.value.replace(/\D/g, ''))}
              autoFocus
            />
            {error && <p className="admin-modal-error">{error}</p>}
            <div className="admin-modal-actions">
              <button className="admin-modal-cancel" onClick={onClose}>Cancel</button>
              <button className="admin-modal-confirm" onClick={handleResetPin} disabled={busy}>
                {busy ? 'Saving…' : 'Reset PIN'}
              </button>
            </div>
          </>
        )}

        {modal.type === 'delete' && (
          <>
            <h2 className="admin-modal-title">Delete {modal.user.name}?</h2>
            <p className="admin-modal-body">
              This will permanently remove their account and all their settings.
            </p>
            {error && <p className="admin-modal-error">{error}</p>}
            <div className="admin-modal-actions">
              <button className="admin-modal-cancel" onClick={onClose}>Cancel</button>
              <button
                className="admin-modal-confirm admin-modal-confirm--danger"
                onClick={handleDelete}
                disabled={busy}
              >
                {busy ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

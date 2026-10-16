/**
 * IndexerCard — Manage Torznab/Newznab indexers (Luma backend)
 */

import { useState, useEffect } from 'react';
import { useSettings } from './useSettings';

interface Indexer {
  id: string;
  name: string;
  type: 'torznab' | 'newznab';
  url: string;
  api_key: string;
  categories: string;
  priority: number;
  enabled: boolean;
  rate_limit?: {
    requests: number;
    period_seconds: number;
  } | null;
}

export function IndexerCard() {
  const { getByPath, setByPath } = useSettings();
  const [indexers, setIndexers] = useState<Indexer[]>([]);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<Partial<Indexer>>({});
  const [importing, setImporting] = useState(false);
  const [importResult, setImportResult] = useState<string | null>(null);
  const [testResults, setTestResults] = useState<Record<string, { success: boolean; message: string }>>({});

  const backend = getByPath('indexer.backend') || 'prowlarr';

  useEffect(() => {
    if (backend === 'luma') {
      loadIndexers();
    }
  }, [backend]);

  const loadIndexers = async () => {
    setLoading(true);
    try {
      const resp = await fetch('/api/settings/indexers');
      if (resp.ok) {
        const data = await resp.json();
        setIndexers(data);
      }
    } catch (err) {
      console.error('Failed to load indexers:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleAdd = () => {
    setEditing('new');
    setEditForm({
      name: '',
      type: 'torznab',
      url: '',
      api_key: '',
      categories: '2000',
      priority: 100,
      enabled: true,
      rate_limit: null,
    });
  };

  const handleEdit = (indexer: Indexer) => {
    setEditing(indexer.id);
    setEditForm({ ...indexer });
  };

  const handleCancel = () => {
    setEditing(null);
    setEditForm({});
  };

  const handleSave = async () => {
    if (!editForm.name || !editForm.url) {
      alert('Name and URL are required');
      return;
    }

    try {
      if (editing === 'new') {
        const resp = await fetch('/api/settings/indexers', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(editForm),
        });
        if (!resp.ok) throw new Error('Failed to add indexer');
      } else {
        const resp = await fetch(`/api/settings/indexers/${editing}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(editForm),
        });
        if (!resp.ok) throw new Error('Failed to update indexer');
      }
      await loadIndexers();
      handleCancel();
    } catch (err) {
      console.error('Save failed:', err);
      alert('Failed to save indexer');
    }
  };

  const handleDelete = async (id: string) => {
    if (!confirm('Delete this indexer?')) return;
    try {
      const resp = await fetch(`/api/settings/indexers/${id}`, { method: 'DELETE' });
      if (!resp.ok) throw new Error('Failed to delete');
      await loadIndexers();
    } catch (err) {
      console.error('Delete failed:', err);
      alert('Failed to delete indexer');
    }
  };

  const handleTest = async (id: string) => {
    setTestResults((prev) => ({ ...prev, [id]: { success: false, message: 'Testing...' } }));
    try {
      const resp = await fetch(`/api/settings/indexers/${id}/test`, { method: 'POST' });
      const result = await resp.json();
      setTestResults((prev) => ({
        ...prev,
        [id]: { success: result.success, message: result.message },
      }));
    } catch (err) {
      setTestResults((prev) => ({
        ...prev,
        [id]: { success: false, message: 'Test failed' },
      }));
    }
  };

  const handleImport = async () => {
    const prowlarrUrl = prompt('Prowlarr URL:', (getByPath('indexer.base_url') as string) || '');
    if (!prowlarrUrl) return;

    const prowlarrKey = prompt('Prowlarr API Key:', '');
    if (!prowlarrKey) return;

    setImporting(true);
    setImportResult(null);
    try {
      const resp = await fetch('/api/settings/indexers/import-prowlarr', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prowlarr_url: prowlarrUrl, prowlarr_api_key: prowlarrKey }),
      });
      const result = await resp.json();
      if (result.success) {
        setImportResult(`✓ ${result.message}`);
        await loadIndexers();
      } else {
        setImportResult(`✗ ${result.message || result.error}`);
      }
    } catch (err: any) {
      setImportResult(`✗ Import failed: ${err.message || String(err)}`);
    } finally {
      setImporting(false);
    }
  };

  if (backend !== 'luma') {
    return (
      <div className="rounded-lg border border-white/10 bg-white/[0.04] p-6">
        <h3 className="text-lg font-medium text-white mb-4">Indexers</h3>
        <label className="flex items-center gap-3 text-sm text-zinc-300">
          <input
            type="checkbox"
            checked={backend === 'luma'}
            onChange={(e) => setByPath('indexer.backend', e.target.checked ? 'luma' : 'prowlarr')}
            className="rounded bg-white/10 border-white/20"
          />
          Use Luma backend
        </label>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.04] p-6">
      <div className="flex items-center justify-between mb-4">
        <h3 className="text-lg font-medium text-white">Indexers</h3>
        <div className="flex gap-2">
          <button
            onClick={handleImport}
            disabled={importing}
            className="px-3 py-1 text-xs bg-white/10 hover:bg-white/15 text-white rounded"
          >
            {importing ? 'Importing...' : 'Import from Prowlarr'}
          </button>
          <button
            onClick={handleAdd}
            className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded"
          >
            + Add
          </button>
        </div>
      </div>

      {importResult && (
        <div className="mb-4 p-3 text-sm bg-white/5 border border-white/10 rounded">
          {importResult}
        </div>
      )}

      {loading ? (
        <div className="text-sm text-zinc-500">Loading...</div>
      ) : indexers.length === 0 && !editing ? (
        <div className="text-sm text-zinc-500">No indexers configured</div>
      ) : (
        <div className="space-y-2">
          {indexers.map((ix) =>
            editing === ix.id ? (
              <IndexerForm key={ix.id} form={editForm} setForm={setEditForm} onSave={handleSave} onCancel={handleCancel} />
            ) : (
              <IndexerRow
                key={ix.id}
                indexer={ix}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onTest={handleTest}
                testResult={testResults[ix.id]}
              />
            )
          )}
          {editing === 'new' && (
            <IndexerForm form={editForm} setForm={setEditForm} onSave={handleSave} onCancel={handleCancel} />
          )}
        </div>
      )}
    </div>
  );
}

function IndexerRow({
  indexer,
  onEdit,
  onDelete,
  onTest,
  testResult,
}: {
  indexer: Indexer;
  onEdit: (ix: Indexer) => void;
  onDelete: (id: string) => void;
  onTest: (id: string) => void;
  testResult?: { success: boolean; message: string };
}) {
  return (
    <div className="p-3 bg-white/5 rounded border border-white/10 space-y-2">
      <div className="flex items-center gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="font-medium text-white text-sm">{indexer.name}</span>
            {!indexer.enabled && <span className="text-xs text-zinc-500">(disabled)</span>}
            {indexer.rate_limit && (
              <span className="text-xs text-yellow-500">
                ({indexer.rate_limit.requests}/{indexer.rate_limit.period_seconds}s)
              </span>
            )}
          </div>
          <div className="text-xs text-zinc-500 truncate">{indexer.url}</div>
        </div>
        <div className="flex gap-1">
          <button
            onClick={() => onTest(indexer.id)}
            className="px-2 py-1 text-xs bg-white/10 hover:bg-white/15 text-white rounded"
          >
            Test
          </button>
          <button
            onClick={() => onEdit(indexer)}
            className="px-2 py-1 text-xs bg-white/10 hover:bg-white/15 text-white rounded"
          >
            Edit
          </button>
          <button
            onClick={() => onDelete(indexer.id)}
            className="px-2 py-1 text-xs bg-red-900 hover:bg-red-800 text-white rounded"
          >
            Delete
          </button>
        </div>
      </div>
      {testResult && (
        <div
          className={`text-xs p-2 rounded ${
            testResult.success
              ? 'bg-green-900/20 text-green-400 border border-green-800'
              : 'bg-red-900/20 text-red-400 border border-red-800'
          }`}
        >
          {testResult.message}
        </div>
      )}
    </div>
  );
}

function IndexerForm({
  form,
  setForm,
  onSave,
  onCancel,
}: {
  form: Partial<Indexer>;
  setForm: (f: Partial<Indexer>) => void;
  onSave: () => void;
  onCancel: () => void;
}) {
  const [hasRateLimit, setHasRateLimit] = useState(!!form.rate_limit);

  const handleRateLimitToggle = (enabled: boolean) => {
    setHasRateLimit(enabled);
    if (enabled && !form.rate_limit) {
      setForm({ ...form, rate_limit: { requests: 10, period_seconds: 3600 } });
    } else if (!enabled) {
      setForm({ ...form, rate_limit: null });
    }
  };

  return (
    <div className="p-4 bg-white/5 rounded border border-white/10 space-y-3">
      <input
        type="text"
        placeholder="Name"
        value={form.name || ''}
        onChange={(e) => setForm({ ...form, name: e.target.value })}
        className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white placeholder:text-zinc-600"
      />
      <select
        value={form.type || 'torznab'}
        onChange={(e) => setForm({ ...form, type: e.target.value as 'torznab' | 'newznab' })}
        className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white"
      >
        <option value="torznab">Torznab</option>
        <option value="newznab">Newznab</option>
      </select>
      <input
        type="text"
        placeholder="URL"
        value={form.url || ''}
        onChange={(e) => setForm({ ...form, url: e.target.value })}
        className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white placeholder:text-zinc-600"
      />
      <input
        type="password"
        placeholder="API Key"
        value={form.api_key === '********' ? '' : (form.api_key || '')}
        onChange={(e) => setForm({ ...form, api_key: e.target.value })}
        className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white placeholder:text-zinc-600"
      />
      <div className="flex gap-3">
        <input
          type="text"
          placeholder="Categories"
          value={form.categories || ''}
          onChange={(e) => setForm({ ...form, categories: e.target.value })}
          className="flex-1 px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white placeholder:text-zinc-600"
        />
        <input
          type="number"
          placeholder="Priority"
          value={form.priority ?? 100}
          onChange={(e) => setForm({ ...form, priority: parseInt(e.target.value) || 100 })}
          className="w-24 px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white placeholder:text-zinc-600"
        />
      </div>
      <label className="flex items-center gap-2 text-sm text-zinc-300">
        <input
          type="checkbox"
          checked={form.enabled ?? true}
          onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
          className="rounded bg-white/10 border-white/20"
        />
        Enabled
      </label>
      <label className="flex items-center gap-2 text-sm text-zinc-300">
        <input
          type="checkbox"
          checked={hasRateLimit}
          onChange={(e) => handleRateLimitToggle(e.target.checked)}
          className="rounded bg-white/10 border-white/20"
        />
        Rate limit
      </label>
      {hasRateLimit && (
        <div className="pl-6 space-y-2">
          <div className="flex gap-3">
            <div className="flex-1">
              <label className="block text-xs text-zinc-400 mb-1">Requests</label>
              <input
                type="number"
                min="1"
                max="10000"
                value={form.rate_limit?.requests ?? 10}
                onChange={(e) =>
                  setForm({
                    ...form,
                    rate_limit: { ...form.rate_limit!, requests: parseInt(e.target.value) || 10 },
                  })
                }
                className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white"
              />
            </div>
            <div className="flex-1">
              <label className="block text-xs text-zinc-400 mb-1">Period (seconds)</label>
              <input
                type="number"
                min="1"
                max="86400"
                value={form.rate_limit?.period_seconds ?? 3600}
                onChange={(e) =>
                  setForm({
                    ...form,
                    rate_limit: { ...form.rate_limit!, period_seconds: parseInt(e.target.value) || 3600 },
                  })
                }
                className="w-full px-3 py-2 text-sm bg-white/5 border border-white/10 rounded text-white"
              />
            </div>
          </div>
        </div>
      )}
      <div className="flex gap-2 justify-end">
        <button onClick={onCancel} className="px-3 py-1 text-xs bg-white/10 hover:bg-white/15 text-white rounded">
          Cancel
        </button>
        <button onClick={onSave} className="px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 text-white rounded">
          Save
        </button>
      </div>
    </div>
  );
}

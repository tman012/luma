/**
 * MediaPathsTab — System Paths, Media Types, and Library Paths.
 */

import { useCallback } from 'react';
import { SettingsCard } from './components/SettingsCard';
import { FormField } from './components/FormField';
import { ListEditor } from './components/ListEditor';
import { MobileCardNav, type CardItem } from './components/MobileCardNav';
import { useSettings } from './useSettings';
import { syncFromPlex } from '@/services/settingsApi';

// -- System Paths -------------------------------------------------

const SYSTEM_PATHS = ['media.downloads_root', 'media.database_path'];

// -- Media Types --------------------------------------------------

function MediaTypesEditor() {
  return (
    <ListEditor
      path="media.types"
      title="Media Types"
      subtitle="Define type categories for library routing, ignore if using ARR media management"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
          <line x1="7" y1="7" x2="7.01" y2="7" />
        </svg>
      }
      blankItem={() => ({
        label: '',
        category: 'movie',
        description: '',
        quality_keywords: [],
        year_rule: '',
        is_default: false,
      })}
      getItemName={(item) => (item.label as string) ?? ''}
      renderItem={(item) => (
        <div>
          <span className="text-[14px] font-medium text-white/80">
            {(item.label as string) || 'Untitled'}
          </span>
          <div className="mt-0.5 flex items-center gap-2">
            <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase bg-blue-500/15 text-blue-400">
              {(item.category as string) ?? 'movie'}
            </span>
            {item.description ? (
              <span className="text-[12px] text-white/30">
                {String(item.description)}
              </span>
            ) : null}
            {item.is_default ? (
              <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase bg-emerald-500/15 text-emerald-400">
                Default
              </span>
            ) : null}
          </div>
        </div>
      )}
      renderForm={(draft, setDraft) => (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Label</label>
            <input
              type="text"
              value={(draft.label as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, label: e.target.value })}
              placeholder="e.g. 4K Movies"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Category</label>
            <select
              value={(draft.category as string) ?? 'movie'}
              onChange={(e) => setDraft({ ...draft, category: e.target.value })}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
            >
              <option value="movie">Movie</option>
              <option value="tv">TV</option>
            </select>
          </div>
          <div className="sm:col-span-2 flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Description</label>
            <input
              type="text"
              value={(draft.description as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, description: e.target.value })}
              placeholder="What this type means"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Release Date</label>
            <select
              value={(draft.year_rule as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, year_rule: e.target.value })}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
            >
              <option value="">None</option>
              <option value="recent">Recent (within last year)</option>
            </select>
          </div>
          <div className="flex items-end pb-1">
            <div className="flex items-center gap-3">
              <button
                type="button"
                role="switch"
                aria-checked={Boolean(draft.is_default)}
                onClick={() => setDraft({ ...draft, is_default: !draft.is_default })}
                className={`relative h-[26px] w-[46px] shrink-0 rounded-full transition-colors ${draft.is_default ? 'bg-emerald-500' : 'bg-white/15'}`}
              >
                <span className={`absolute top-[3px] left-[3px] h-[20px] w-[20px] rounded-full bg-white shadow-sm transition-transform ${draft.is_default ? 'translate-x-[20px]' : ''}`} />
              </button>
              <span className="text-[13px] text-white/60">Default for category</span>
            </div>
          </div>
        </div>
      )}
    />
  );
}

// -- Library Paths ------------------------------------------------

function LibraryPathsEditor() {
  const { getByPath, setByPath, showToast } = useSettings();

  const mediaTypes = (getByPath('media.types') as Record<string, unknown>[] | undefined) ?? [];
  const typeOptions = mediaTypes.map((t) => ({
    value: (t.label as string) ?? '',
    label: (t.label as string) ?? 'Unnamed',
  }));

  const handleSync = useCallback(async () => {
    showToast('Syncing from Plex...', 'info');
    try {
      const result = await syncFromPlex();
      if (result.success) {
        if (result.paths) {
          setByPath('media.paths', result.paths);
        }
        showToast('Synced from Plex', 'success');
      } else {
        showToast(result.message ?? 'Sync failed', 'error');
      }
    } catch (e) {
      showToast((e as Error).message, 'error');
    }
  }, [showToast, setByPath]);

  return (
    <ListEditor
      path="media.paths"
      title="Library Paths"
      subtitle="Media library directories -- sync from Plex or add manually"
      icon={
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      }
      headerRight={
        <button
          type="button"
          onClick={handleSync}
          className="inline-flex items-center gap-1.5 rounded-lg border border-indigo-500/20 bg-indigo-500/12 px-3 py-2 text-[12px] font-semibold text-indigo-400 hover:bg-indigo-500/20 min-h-[36px]"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <polyline points="23 4 23 10 17 10" /><polyline points="1 20 1 14 7 14" />
            <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15" />
          </svg>
          Sync from Plex
        </button>
      }
      blankItem={() => ({
        name: '',
        path: '',
        kodi_path: '',
        type: typeOptions[0]?.value ?? '',
      })}
      getItemName={(item) => (item.name as string) ?? ''}
      renderItem={(item) => (
        <div>
          <span className="text-[14px] font-medium text-white/80">
            {(item.name as string) || 'Untitled'}
          </span>
          <div className="mt-0.5 flex items-center gap-2 text-[12px] text-white/30">
            {item.type ? (
              <span className="rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase bg-indigo-500/15 text-indigo-400">
                {String(item.type)}
              </span>
            ) : null}
            {item.path ? <span>{String(item.path)}</span> : null}
          </div>
        </div>
      )}
      renderForm={(draft, setDraft) => (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Name</label>
            <input
              type="text"
              value={(draft.name as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              placeholder="Movies"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Type</label>
            <select
              value={(draft.type as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, type: e.target.value })}
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white outline-none"
            >
              <option value="">-- Select --</option>
              {typeOptions.map((opt) => (
                <option key={opt.value} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Local Path</label>
            <input
              type="text"
              value={(draft.path as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, path: e.target.value })}
              placeholder="/media/movies"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
            />
            <span className="text-[11px] text-white/30">Container/host path</span>
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-[13px] font-medium text-white/60">Kodi Path</label>
            <input
              type="text"
              value={(draft.kodi_path as string) ?? ''}
              onChange={(e) => setDraft({ ...draft, kodi_path: e.target.value })}
              placeholder="smb://192.168.1.25/Media/Video/movies"
              className="w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2.5 text-[14px] text-white placeholder:text-white/25 outline-none focus:border-white/20"
            />
            <span className="text-[11px] text-white/30">Full SMB/NFS path for Kodi playback</span>
          </div>
        </div>
      )}
    />
  );
}

// -- Tab ----------------------------------------------------------

export function MediaPathsTab() {
  const items: CardItem[] = [
    {
      title: 'System Paths',
      subtitle: 'Downloads and database locations',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
          <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
        </svg>
      ),
      content: (
        <SettingsCard
          title="System Paths"
          subtitle="Downloads and database locations"
          paths={SYSTEM_PATHS}
          icon={
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
              <ellipse cx="12" cy="5" rx="9" ry="3" /><path d="M21 12c0 1.66-4 3-9 3s-9-1.34-9-3" />
              <path d="M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5" />
            </svg>
          }
        >
          <FormField
            label="Downloads Root"
            path="media.downloads_root"
            placeholder="/downloads"
          />
          <FormField
            label="Database Path"
            path="media.database_path"
            placeholder="/app/data/media.db"
          />
        </SettingsCard>
      ),
    },
    {
      title: 'Media Types',
      subtitle: 'Type categories for library routing',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M20.59 13.41l-7.17 7.17a2 2 0 0 1-2.83 0L2 12V2h10l8.59 8.59a2 2 0 0 1 0 2.82z" />
          <line x1="7" y1="7" x2="7.01" y2="7" />
        </svg>
      ),
      content: <MediaTypesEditor />,
    },
    {
      title: 'Library Paths',
      subtitle: 'Media library directories',
      icon: (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
        </svg>
      ),
      content: <LibraryPathsEditor />,
    },
  ];

  return (
    <div className="space-y-6">
      <div className="settings-tab-header">
        <h2 className="text-xl font-semibold text-white/90">Media Paths</h2>
        <p className="mt-1 text-[13px] text-white/40">
          File system paths for media libraries and downloads.
        </p>
      </div>

      <MobileCardNav items={items} />
    </div>
  );
}

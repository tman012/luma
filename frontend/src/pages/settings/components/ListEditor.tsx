/**
 * ListEditor — generic add/edit/delete list for media types and library paths.
 * Items are stored in the settings context at a given dot-path (e.g. "media.types").
 */

import { useState, useCallback, type ReactNode } from 'react';
import { useSettings } from '../useSettings';
import { cn } from '@/utils/cn';

interface ListEditorProps {
  /** Settings dot-path for the array (e.g. "media.types") */
  path: string;
  title: string;
  subtitle?: string;
  icon?: ReactNode;
  /** Renders each item in the list */
  renderItem: (item: Record<string, unknown>, index: number) => ReactNode;
  /** Renders the inline add/edit form */
  renderForm: (
    draft: Record<string, unknown>,
    setDraft: (d: Record<string, unknown>) => void,
  ) => ReactNode;
  /** Creates a blank item for "Add" */
  blankItem: () => Record<string, unknown>;
  /** Extracts display name from an item */
  getItemName: (item: Record<string, unknown>) => string;
  /** Extra header-right content */
  headerRight?: ReactNode;
}

export function ListEditor({
  path,
  title,
  subtitle,
  icon,
  renderItem,
  renderForm,
  blankItem,
  getItemName,
  headerRight,
}: ListEditorProps) {
  const { getByPath, setByPath, showToast } = useSettings();

  const items = (getByPath(path) as Record<string, unknown>[] | undefined) ?? [];

  const [formOpen, setFormOpen] = useState(false);
  const [editIndex, setEditIndex] = useState(-1); // -1 = adding new
  const [draft, setDraft] = useState<Record<string, unknown>>({});

  const openAdd = useCallback(() => {
    setDraft(blankItem());
    setEditIndex(-1);
    setFormOpen(true);
  }, [blankItem]);

  const openEdit = useCallback(
    (index: number) => {
      const item = items[index];
      if (!item) return;
      setDraft(structuredClone(item));
      setEditIndex(index);
      setFormOpen(true);
    },
    [items],
  );

  const cancel = useCallback(() => {
    setFormOpen(false);
    setDraft({});
  }, []);

  const save = useCallback(() => {
    const name = getItemName(draft);
    if (!name) {
      showToast('Name is required', 'error');
      return;
    }
    const updated = [...items];
    if (editIndex >= 0) {
      updated[editIndex] = draft;
    } else {
      updated.push(draft);
    }
    setByPath(path, updated);
    setFormOpen(false);
    setDraft({});
    showToast(editIndex >= 0 ? `Updated "${name}"` : `Added "${name}"`, 'success');
  }, [draft, editIndex, items, path, setByPath, showToast, getItemName]);

  const remove = useCallback(
    (index: number) => {
      const item = items[index];
      if (!item) return;
      const name = getItemName(item);
      const updated = items.filter((_, i) => i !== index);
      setByPath(path, updated);
      showToast(`Removed "${name}"`, 'info');
      if (formOpen && editIndex === index) {
        setFormOpen(false);
      }
    },
    [items, path, setByPath, showToast, formOpen, editIndex, getItemName],
  );

  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.04] p-6">
      {/* Header */}
      <div className="mb-5 flex items-start justify-between gap-4">
        <div className="flex items-center gap-3">
          {icon && (
            <div className="flex h-[42px] w-[42px] items-center justify-center rounded-[10px] bg-white/5 text-white/50 shrink-0">
              {icon}
            </div>
          )}
          <div>
            <h3 className="text-[15px] font-semibold text-white/90">{title}</h3>
            {subtitle && (
              <p className="mt-0.5 text-[12px] text-white/35">{subtitle}</p>
            )}
          </div>
        </div>
        <div className="flex gap-2">
          {headerRight}
          <button
            type="button"
            onClick={openAdd}
            className="inline-flex items-center gap-1.5 rounded-lg bg-accent hover:bg-accent-hover px-3 py-2 text-[12px] font-semibold text-white transition-colors  min-h-[36px]"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
            </svg>
            Add
          </button>
        </div>
      </div>

      {/* List items */}
      <div className="flex flex-col gap-2">
        {items.length === 0 && !formOpen && (
          <p className="py-6 text-center text-[13px] text-white/25">
            No items configured
          </p>
        )}
        {items.map((item, i) => (
          <div
            key={i}
            className="flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-4 py-3 transition-colors hover:border-white/10"
          >
            <div className="min-w-0 flex-1">{renderItem(item, i)}</div>
            <div className="flex shrink-0 gap-2">
              <button
                type="button"
                onClick={() => openEdit(i)}
                className="rounded-lg p-2 text-white/30 transition-colors hover:bg-white/5 hover:text-white/60"
                aria-label="Edit"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
                  <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
                </svg>
              </button>
              <button
                type="button"
                onClick={() => remove(i)}
                className="rounded-lg p-2 text-white/30 transition-colors hover:bg-red-500/10 hover:text-red-400"
                aria-label="Delete"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <polyline points="3 6 5 6 21 6" /><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                </svg>
              </button>
            </div>
          </div>
        ))}
      </div>

      {/* Inline form */}
      {formOpen && (
        <div className="mt-3 rounded-xl border border-dashed border-white/12 bg-white/[0.02] p-5">
          <p className="mb-4 text-[14px] font-semibold text-white/60">
            {editIndex >= 0 ? 'Edit item' : 'Add item'}
          </p>
          {renderForm(draft, setDraft)}
          <div className="mt-4 flex gap-2.5">
            <button
              type="button"
              onClick={save}
              className="inline-flex items-center gap-1.5 rounded-lg bg-accent hover:bg-accent-hover px-3.5 py-2 text-[12px] font-semibold text-white transition-colors  min-h-[36px]"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="20 6 9 17 4 12" />
              </svg>
              {editIndex >= 0 ? 'Update' : 'Save'}
            </button>
            <button
              type="button"
              onClick={cancel}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-lg border border-white/10 bg-white/[0.06] px-3.5 py-2 text-[12px] font-semibold text-white/60 transition-colors hover:bg-white/10 min-h-[36px]',
              )}
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

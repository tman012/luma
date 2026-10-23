/**
 * storageApi — downloads-folder disk usage + oldest-first space reclaim.
 *
 * Backs the storage pill on the compact home page. `cleanupDownloads` is
 * destructive and permanent; only call it behind an explicit user confirm.
 */

import { apiFetch } from './api';

export interface StorageUsage {
  path: string;
  available: boolean;
  totalBytes: number;
  usedBytes: number;
  freeBytes: number;
  /** Default reclaim size the backend will use (bytes). */
  cleanupTargetBytes: number;
  /** v260: the guard's minimum age; the sheet's "last N hours" (prod: 48). */
  minAgeHours: number;
}

export interface CleanupResult {
  success: boolean;
  freedBytes: number;
  deletedCount: number;
  deleted: { name: string; sizeBytes: number }[];
  failed: string[];
  /** True when the folder ran out of eligible entries before hitting target. */
  short: boolean;
  after: StorageUsage | null;
  error?: string;
  /** The server wants the owner signed in with a session (LAN access isn't enough). */
  signIn?: boolean;
}

interface RawUsage {
  path?: string;
  available?: boolean;
  total_bytes?: number;
  used_bytes?: number;
  free_bytes?: number;
  cleanup_target_bytes?: number;
  min_age_hours?: number;
}

const GIB = 1024 ** 3;

function mapUsage(raw: RawUsage | null | undefined): StorageUsage {
  return {
    path: raw?.path ?? '',
    available: raw?.available ?? false,
    totalBytes: raw?.total_bytes ?? 0,
    usedBytes: raw?.used_bytes ?? 0,
    freeBytes: raw?.free_bytes ?? 0,
    cleanupTargetBytes: raw?.cleanup_target_bytes ?? 100 * GIB,
    minAgeHours: raw?.min_age_hours ?? 48,
  };
}

export async function fetchDownloadsStorage(): Promise<StorageUsage> {
  const raw = await apiFetch<RawUsage>('/api/storage/downloads');
  return mapUsage(raw);
}

const NOTHING: CleanupResult = {
  success: false, freedBytes: 0, deletedCount: 0, deleted: [], failed: [], short: false, after: null,
};

export async function cleanupDownloads(targetBytes?: number): Promise<CleanupResult> {
  let raw: Awaited<ReturnType<typeof postCleanup>>;
  try {
    raw = await postCleanup(targetBytes);
  } catch (e) {
    // 401: destructive actions need a signed-in owner session, even on the LAN.
    if ((e as { status?: number })?.status === 401) return { ...NOTHING, signIn: true };
    throw e;
  }
  return {
    success: raw.success ?? false,
    freedBytes: raw.freed_bytes ?? 0,
    deletedCount: raw.deleted_count ?? 0,
    deleted: (raw.deleted ?? []).map((d) => ({ name: d.name, sizeBytes: d.size_bytes })),
    failed: raw.failed ?? [],
    short: raw.short ?? false,
    after: raw.after ? mapUsage(raw.after) : null,
    error: raw.error,
  };
}

function postCleanup(targetBytes?: number) {
  return apiFetch<{
    success?: boolean;
    freed_bytes?: number;
    deleted_count?: number;
    deleted?: { name: string; size_bytes: number }[];
    failed?: string[];
    short?: boolean;
    after?: RawUsage;
    error?: string;
  }>('/api/storage/downloads/cleanup', {
    method: 'POST',
    body: JSON.stringify(targetBytes ? { target_bytes: targetBytes } : {}),
  });
}

/** Human-readable size — "1.5 TB", "820 GB", "-". */
export function formatBytes(bytes: number): string {
  if (!bytes || bytes <= 0) return '—';
  const tb = bytes / 1024 ** 4;
  if (tb >= 1) return `${tb.toFixed(tb >= 10 ? 0 : 1)} TB`;
  const gb = bytes / GIB;
  if (gb >= 1) return `${Math.round(gb)} GB`;
  return `${Math.round(bytes / 1024 ** 2)} MB`;
}

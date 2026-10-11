/**
 * sourcesShared — presentational + routing helpers shared by the movie
 * (SourcesPanel) and TV (TvSourcesPanel) sources panels for the "View all
 * results" feature.
 *
 * These were originally defined locally in the movie SourcesPanel; they now
 * live here so the TV panel can reuse them verbatim instead of duplicating the
 * toggle / badges / grab-routing logic. The helpers are typed against a minimal
 * structural shape (ReleaseLike) so both ProcessedRelease (movie) and
 * TvProcessedRelease (TV) satisfy them.
 */

import { useState, useEffect, useRef } from 'react';

// ---------------------------------------------------------------------------
// Source-type classification (shared by movie + TV panels)
// ---------------------------------------------------------------------------

/**
 * Canonical segment order, best → worst. Only four UX buckets:
 * REMUX, BLURAY, WEB-DL, Other.
 */
export const SOURCE_TYPE_SEGMENT_ORDER = [
  'REMUX', 'BLURAY', 'WEB-DL', 'Other',
];

/**
 * Classify a release into one of four UX type buckets from its title (and, for
 * Arr rows, quality string): REMUX, BLURAY, WEB-DL, or Other.
 *
 * Order matters: REMUX / WEB-DL / BLURAY are matched first. Everything else
 * (WEBRip, ISO/BDMV, BDRip, BRRip, HDTV, CAM, TELESYNC, UNKNOWN, etc.) lands in
 * Other. Bare "WEB" (without Rip) still counts as WEB-DL.
 */
export function classifySourceType(text: string): string {
  // Normalize scene separators (dots/underscores) to spaces; keep hyphens so
  // "WEB-DL" survives. Pad so \b anchors work at the ends.
  const t = ` ${(text || '').toUpperCase().replace(/[._]/g, ' ')} `;
  const h = (re: RegExp) => re.test(t);

  if (h(/\bREMUX\b/)) return 'REMUX';

  // Rips / broadcast / junk → Other. MUST run before WEB-DL / BLURAY: Arr
  // quality strings often say "Bluray-1080p" / "WEBDL-1080p" even for BDRip /
  // WEBRip / HDTV / BRRip releases. Matching BluRay first folded those into
  // BLURAY and emptied the Other segment (Moneyball 1080p BDRips vanished).
  if (
    h(/\bWEB[ -]?RIP\b/) || h(/\bWEBRIP\b/) ||
    h(/\bBD[ -]?RIP\b/) || h(/\bBDRIP\b/) ||
    h(/\bBR[ -]?RIP\b/) || h(/\bBRRIP\b/) ||
    h(/\bDVD[ -]?RIP\b/) || h(/\bDVDRIP\b/) ||
    h(/\bHDTV\b/) || h(/\bPDTV\b/) || h(/\bSDTV\b/) ||
    h(/\bTELESYNC\b/) || h(/\bTELECINE\b/) ||
    h(/\bCAMRIP\b/) || h(/\bCAM\b/) ||
    h(/\bSCREENER\b/) || h(/\bSCR\b/) ||
    h(/\bBDMV\b/) || h(/\bISO\b/) ||
    h(/\bR5\b/)
  ) return 'Other';

  // True WEB-DL (or bare WEB without Rip) and true Blu-ray encodes only.
  if (h(/\bWEB[ -]?DL\b/) || h(/\bWEBDL\b/) || h(/\bWEB\b/)) return 'WEB-DL';
  if (h(/\bBLU[ -]?RAY\b/) || h(/\bBLURAY\b/)) return 'BLURAY';

  return 'Other';
}

/** Order the source types present, best→worst, appending anything unmapped. */
export function orderSourceTypes(present: Iterable<string>): string[] {
  const set = new Set(present);
  const ordered = SOURCE_TYPE_SEGMENT_ORDER.filter((s) => set.has(s));
  const extra = [...set].filter((s) => !SOURCE_TYPE_SEGMENT_ORDER.includes(s)).sort();
  return [...ordered, ...extra];
}

/** Distinct resolutions present, ordered highest→lowest by pixel height
 *  ('Unknown'/non-numeric last). De-dupes so case variants can't double up
 *  (callers should already lowercase, but this is belt-and-suspenders). */
export function orderResolutions(present: Iterable<string>): string[] {
  const val = (r: string) => {
    const m = r.match(/(\d{3,4})p/i);
    return m ? parseInt(m[1]!, 10) : -1;
  };
  return [...new Set(present)].sort((a, b) => val(b) - val(a));
}

/**
 * Reorder a release list so each tracker's single top-seeded release comes
 * first (those "tracker tops" sorted by seeders desc), then every remaining
 * release after (also seeders desc). Lets the user see the best from every
 * tracker up front instead of one tracker dominating the head of the list.
 */
export function sortTrackerTopsFirst<T extends SeedRow>(releases: T[]): T[] {
  const best = new Map<string, T>();
  for (const r of releases) {
    const cur = best.get(r.indexer);
    if (!cur || r.seeders > cur.seeders) best.set(r.indexer, r);
  }
  const tops = new Set(best.values());
  const topList = [...tops].sort((a, b) => b.seeders - a.seeders);
  const rest = releases.filter((r) => !tops.has(r)).sort((a, b) => b.seeders - a.seeders);
  return [...topList, ...rest];
}

/** Minimal shape the shared helpers read off a release row. */
export interface ReleaseLike {
  rejected?: boolean;
  rejectionReasons?: string[];
  origin?: 'arr' | 'indexer';
  protocol?: string;
}

/** Identity passed to grabToLibrary once a TMDb match is known. */
export interface MediaGrabContext {
  tmdbId?: number;
  title: string;
  year: number | null;
}

/** Back-compat alias — the movie panel referred to this as MovieGrabContext. */
export type MovieGrabContext = MediaGrabContext;

// ---------------------------------------------------------------------------
// Tracker top-seeder preview pills (shown on segment tiles so you can judge
// seed health per tracker WITHOUT drilling into the segment and backing out).
// ---------------------------------------------------------------------------

type BadgeColor = { bg: string; fg: string; border: string };

/** Minimal shape needed to compute the top seeder per tracker. */
export interface SeedRow {
  indexer: string;
  seeders: number;
}

function formatSeeders(n: number): string {
  if (n >= 10000) return `${Math.round(n / 1000)}k`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}

/** Highest-seeded release per tracker, trackers sorted by that seed count desc. */
export function topSeedersByTracker(releases: SeedRow[]): { indexer: string; seeders: number }[] {
  const max = new Map<string, number>();
  for (const r of releases) {
    const cur = max.get(r.indexer);
    if (cur === undefined || r.seeders > cur) max.set(r.indexer, r.seeders);
  }
  return [...max.entries()]
    .map(([indexer, seeders]) => ({ indexer, seeders }))
    .sort((a, b) => b.seeders - a.seeders);
}

/** Row of mini pills: one per tracker, showing that tracker's best seed count
 *  within the given release set. Nothing renders when there are no releases. */
export function TrackerSeedPills({ releases, colorFor, max = 6, align = 'flex-start' }: {
  releases: SeedRow[];
  colorFor: (indexer: string) => BadgeColor;
  max?: number;
  align?: 'flex-start' | 'center';
}) {
  const top = topSeedersByTracker(releases).slice(0, max);
  if (top.length === 0) return null;
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', justifyContent: align }}>
      {top.map(({ indexer, seeders }) => {
        const c = colorFor(indexer);
        return (
          <span
            key={indexer}
            style={{
              display: 'inline-flex', alignItems: 'center', gap: '5px',
              padding: '3px 8px', borderRadius: '8px', fontSize: '11px', fontWeight: 700,
              background: c.bg, color: c.fg, border: `1px solid ${c.border}`, lineHeight: 1.3,
            }}
          >
            {indexer}
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: '2px', color: '#4ade80' }}>
              <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 19V5M5 12l7-7 7 7" />
              </svg>
              {formatSeeders(seeders)}
            </span>
          </span>
        );
      })}
    </div>
  );
}

/** Full-width compact wizard row: label + source count, optionally with the
 *  tracker top-seeder pills (shown at the type level, hidden at the broad
 *  resolution level where a count is enough). */
export function SegmentRow({ label, count, releases, colorFor, onClick, showPills = true }: {
  label: string;
  count: number;
  releases: SeedRow[];
  colorFor: (indexer: string) => BadgeColor;
  onClick: () => void;
  showPills?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        width: '100%', minHeight: '64px', display: 'flex', flexDirection: 'column',
        gap: '10px', alignItems: 'stretch', justifyContent: 'center', padding: '14px 20px',
        borderRadius: '16px', border: '1px solid rgba(255,255,255,0.12)',
        background: 'rgba(255,255,255,0.06)', color: '#fff', cursor: 'pointer', transition: 'background 0.15s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '18px', fontWeight: 700 }}>{label}</span>
        <span style={{ fontSize: '14px', fontWeight: 500, color: 'rgba(255,255,255,0.35)' }}>{count} sources</span>
      </div>
      {showPills && <TrackerSeedPills releases={releases} colorFor={colorFor} />}
    </button>
  );
}

/** A release needs grab-to-library (not the normal Arr grab) when it's a raw
 *  indexer row OR a rejected Arr row. */
export function needsLibraryGrab(release: ReleaseLike): boolean {
  return release.origin === 'indexer' || !!release.rejected;
}

/** Usenet can't ride the qBit pipeline — grab must be disabled (risk R3). */
/** Scroll child into view inside container only — never scrolls outer sheet/page.
 *  block 'start': pin child top to scroller top when it fits; if taller than the
 *  visible area, keep the bottom clear (See all / Download) using insetBottom.
 *  block 'nearest': only scroll if clipped; insetBottom pads above the clip edge.
 */
export function scrollChildIntoContainer(
  container: HTMLElement,
  child: HTMLElement,
  block: 'nearest' | 'start' = 'nearest',
  insetBottom = 0,
): void {
  const cRect = container.getBoundingClientRect();
  const eRect = child.getBoundingClientRect();
  const viewBottom = cRect.bottom - insetBottom;
  let next = container.scrollTop;
  if (block === 'start') {
    if (eRect.height <= cRect.height - insetBottom) {
      next += eRect.top - cRect.top;
    } else {
      next += eRect.bottom - viewBottom;
    }
  } else if (eRect.top < cRect.top) {
    next += eRect.top - cRect.top;
  } else if (eRect.bottom > viewBottom) {
    next += eRect.bottom - viewBottom;
  } else {
    return;
  }
  container.scrollTop = Math.max(0, next);
}

export const COMPACT_SOURCES_WIZARD_HEIGHT = 'min(430px, calc(100% - 60px))';
export const COMPACT_SOURCES_SHEET_MAX_HEIGHT = 'calc(100% - 60px)';
export const COMPACT_SOURCES_RESULTS_HEIGHT = 'calc(100% - 60px)';

export function grabDisabledReason(release: ReleaseLike): string | null {
  if (release.protocol && release.protocol !== 'torrent') {
    return 'Usenet release — not supported by the download pipeline';
  }
  return null;
}

// ---------------------------------------------------------------------------
// Plain-text indexer query override (Sources escape hatch)
// ---------------------------------------------------------------------------

/**
 * Compact control for overriding the Prowlarr/indexer search string without
 * changing the modal's media identity (poster/title/TMDb). Used by movie + TV
 * Sources sheets. The applied query feeds fetchAllResults only; grab/rename
 * still uses MediaGrabContext.
 */
export function IndexerQueryOverride({
  mediaTitle,
  defaultQuery = null,
  appliedQuery,
  onApply,
  onClear,
  disabled = false,
}: {
  /** Canonical library / TMDb title shown in the modal (never overwritten). */
  mediaTitle: string;
  /**
   * Preferred draft when the override box opens (e.g. the original app-search
   * query). Falls back to mediaTitle when absent.
   */
  defaultQuery?: string | null;
  /** Active override, or null when searching with the media title. */
  appliedQuery: string | null;
  onApply: (query: string) => void;
  onClear: () => void;
  disabled?: boolean;
}) {
  const seed = (defaultQuery || '').trim() || mediaTitle;
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState(appliedQuery ?? seed);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) setDraft(appliedQuery ?? seed);
  }, [appliedQuery, seed, open]);

  useEffect(() => {
    if (open) {
      const t = setTimeout(() => inputRef.current?.focus(), 50);
      return () => clearTimeout(t);
    }
  }, [open]);

  const submit = () => {
    const q = draft.trim();
    if (!q) return;
    // Same as media title while an override is already active → clear back to
    // the default (segmented) search. Otherwise ALWAYS Apply — even when q
    // equals the media title. Applying the title is meaningful: it forces the
    // flat Override path and drops the metadata year, which is what unblocks
    // sports/PPV titles when the seeded draft happens to match the TMDb name.
    // (Previously: same-as-title + no appliedQuery was a silent no-op, so
    // Override Apply did nothing whenever search text matched the title.)
    if (
      q.toLowerCase() === (mediaTitle || '').trim().toLowerCase() &&
      appliedQuery
    ) {
      onClear();
      setOpen(false);
      return;
    }
    onApply(q);
    setOpen(false);
  };

  if (open) {
    return (
      <div
        style={{
          flexShrink: 0,
          padding: '0 16px 12px',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
        }}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <input
            ref={inputRef}
            type="search"
            enterKeyHint="search"
            autoCapitalize="none"
            autoCorrect="off"
            spellCheck={false}
            value={draft}
            disabled={disabled}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                submit();
              } else if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                setOpen(false);
              }
            }}
            placeholder="Plain-text tracker query"
            aria-label="Override indexer search query"
            style={{
              flex: 1,
              minWidth: 0,
              height: '44px',
              padding: '0 14px',
              borderRadius: '12px',
              border: '1px solid rgba(255,255,255,0.18)',
              background: 'rgba(255,255,255,0.08)',
              color: '#fff',
              fontSize: '15px',
              fontWeight: 500,
              outline: 'none',
            }}
          />
          <button
            type="button"
            disabled={disabled || !draft.trim()}
            onClick={submit}
            style={{
              flexShrink: 0,
              height: '44px',
              padding: '0 16px',
              borderRadius: '12px',
              border: 'none',
              background: draft.trim() ? '#fff' : 'rgba(255,255,255,0.15)',
              color: draft.trim() ? '#000' : 'rgba(255,255,255,0.4)',
              fontSize: '14px',
              fontWeight: 700,
              cursor: draft.trim() ? 'pointer' : 'default',
            }}
          >
            Search
          </button>
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '8px' }}>
          <span style={{ fontSize: '12px', color: 'rgba(255,255,255,0.35)', lineHeight: 1.3 }}>
            Indexers only — library title stays “{mediaTitle || 'this title'}”
          </span>
          <button
            type="button"
            onClick={() => setOpen(false)}
            style={{
              background: 'none',
              border: 'none',
              color: 'rgba(255,255,255,0.45)',
              fontSize: '13px',
              fontWeight: 600,
              cursor: 'pointer',
              padding: '4px 0',
            }}
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  if (appliedQuery) {
    return (
      <div
        style={{
          flexShrink: 0,
          padding: '0 16px 12px',
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
        }}
        onTouchStart={(e) => e.stopPropagation()}
      >
        <button
          type="button"
          disabled={disabled}
          onClick={() => setOpen(true)}
          style={{
            flex: 1,
            minWidth: 0,
            minHeight: '40px',
            display: 'flex',
            alignItems: 'center',
            gap: '8px',
            padding: '8px 12px',
            borderRadius: '12px',
            border: '1px solid rgba(250,204,21,0.35)',
            background: 'rgba(250,204,21,0.1)',
            color: '#facc15',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <span style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '0.02em', opacity: 0.85, flexShrink: 0 }}>
            PLAIN TEXT
          </span>
          <span style={{
            fontSize: '13px',
            fontWeight: 600,
            color: '#fff',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}>
            {appliedQuery}
          </span>
        </button>
        <button
          type="button"
          disabled={disabled}
          onClick={onClear}
          aria-label="Clear plain-text override"
          style={{
            flexShrink: 0,
            height: '40px',
            padding: '0 12px',
            borderRadius: '12px',
            border: '1px solid rgba(255,255,255,0.12)',
            background: 'rgba(255,255,255,0.06)',
            color: 'rgba(255,255,255,0.7)',
            fontSize: '13px',
            fontWeight: 600,
            cursor: 'pointer',
          }}
        >
          Reset
        </button>
      </div>
    );
  }

  return (
    <div
      style={{
        flexShrink: 0,
        padding: '0 16px 10px',
        display: 'flex',
        justifyContent: 'center',
      }}
      onTouchStart={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        disabled={disabled}
        onClick={() => {
          setDraft(seed);
          setOpen(true);
        }}
        style={{
          minHeight: '36px',
          padding: '6px 12px',
          borderRadius: '999px',
          border: '1px solid rgba(255,255,255,0.12)',
          background: 'transparent',
          color: 'rgba(255,255,255,0.45)',
          fontSize: '13px',
          fontWeight: 600,
          cursor: 'pointer',
        }}
      >
        Override search
      </button>
    </div>
  );
}

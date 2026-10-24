import { useState, useEffect } from 'react';
import { addToSonarr, fetchQualityProfiles, type QualityProfile } from '@/services/sonarrApi';
import { FocusZone } from '@/components/tv/FocusZone';
import { useFocusable } from '@/hooks/useFocusable';

// Minimal shape the season selector needs — accepts both the full TvSeason
// (CompactLayout) and the narrower season type used by ExpandedLayout.
interface PickableSeason {
  season_number: number;
  name?: string;
  episode_count?: number;
}

interface AddToSonarrModalProps {
  tvdbId: number;
  title: string;
  seasons?: PickableSeason[];
  onClose: () => void;
  onAdded: () => void;
}

function DropdownOption({ focusKey, label, sub, checkbox, active, onSelect }: {
  focusKey: string;
  label: string;
  sub?: string;
  checkbox?: boolean;
  active: boolean;
  onSelect: () => void;
}) {
  const { ref, focusProps } = useFocusable({
    focusKey,
    zoneId: 'add-to-sonarr-modal',
    onEnter: onSelect,
  });
  return (
    <button
      ref={ref}
      {...focusProps}
      type="button"
      onClick={onSelect}
      style={{
        display: 'flex', alignItems: 'center', gap: '10px', width: '100%',
        padding: '11px 14px', border: 'none',
        background: active && !checkbox ? 'rgba(255,255,255,0.08)' : 'transparent',
        color: active ? '#fff' : 'rgba(255,255,255,0.7)',
        fontSize: '15px', fontWeight: 600, cursor: 'pointer', textAlign: 'left',
        transition: 'background 0.12s',
      }}
    >
      {checkbox && (
        <span style={{
          width: '18px', height: '18px', flexShrink: 0, borderRadius: '5px',
          border: active ? 'none' : '1px solid rgba(255,255,255,0.3)',
          background: active ? '#4ade80' : 'transparent',
          color: '#000', display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: '13px', fontWeight: 800,
        }}>{active ? '✓' : ''}</span>
      )}
      <span>{label}</span>
      {sub && (
        <span style={{ marginLeft: 'auto', fontSize: '12px', fontWeight: 500, color: 'rgba(255,255,255,0.4)' }}>{sub}</span>
      )}
    </button>
  );
}

function DropdownTrigger({ focusKey, value, open, onToggle }: {
  focusKey: string;
  value: string;
  open: boolean;
  onToggle: () => void;
}) {
  const { ref, focusProps } = useFocusable({ focusKey, zoneId: 'add-to-sonarr-modal', onEnter: onToggle });
  return (
    <button
      ref={ref}
      {...focusProps}
      type="button"
      onClick={onToggle}
      style={{
        display: 'flex', alignItems: 'center', width: '100%',
        padding: '14px 16px', borderRadius: '12px',
        border: '1px solid rgba(255,255,255,0.14)',
        background: 'rgba(255,255,255,0.05)', color: '#fff',
        fontSize: '15px', fontWeight: 600, cursor: 'pointer',
      }}
    >
      <span>{value}</span>
      <span style={{ marginLeft: 'auto', color: 'rgba(255,255,255,0.5)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s', fontSize: '12px' }}>▾</span>
    </button>
  );
}

const panelStyle: React.CSSProperties = {
  marginTop: '8px', borderRadius: '12px', overflow: 'hidden',
  border: '1px solid rgba(255,255,255,0.12)', background: 'rgba(20,20,22,0.98)',
  maxHeight: '260px', overflowY: 'auto',
};

const fieldLabelStyle: React.CSSProperties = {
  display: 'block', fontSize: '12px', fontWeight: 600, color: 'rgba(255,255,255,0.4)',
  marginBottom: '8px', letterSpacing: '0.4px',
};

export function AddToSonarrModal({ tvdbId, title, seasons, onClose, onAdded }: AddToSonarrModalProps) {
  const pickableSeasons = (seasons ?? []).filter((s) => s.season_number > 0);
  const allSeasonNumbers = pickableSeasons.map((s) => s.season_number);

  // Seasons default to "all" (every season selected).
  const [chosenSeasons, setChosenSeasons] = useState<number[]>(() => allSeasonNumbers);
  const [profiles, setProfiles] = useState<QualityProfile[]>([]);
  const [profileId, setProfileId] = useState<number | null>(null);
  const [openMenu, setOpenMenu] = useState<null | 'profile' | 'seasons'>(null);
  const [adding, setAdding] = useState(false);
  const [added, setAdded] = useState(false);
  const [error, setError] = useState(false);

  // Load quality profiles; default to the first one.
  useEffect(() => {
    let cancelled = false;
    fetchQualityProfiles().then((list) => {
      if (cancelled) return;
      setProfiles(list);
      const first = list[0];
      if (first) setProfileId((prev) => prev ?? first.id);
    });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    function handleKey(e: KeyboardEvent) {
      if (e.key === 'Escape') { e.stopPropagation(); if (openMenu) setOpenMenu(null); else onClose(); }
    }
    window.addEventListener('keydown', handleKey);
    return () => window.removeEventListener('keydown', handleKey);
  }, [onClose, openMenu]);

  const allSelected = allSeasonNumbers.length > 0 && chosenSeasons.length === allSeasonNumbers.length;
  const toggleSeason = (n: number) =>
    setChosenSeasons((prev) =>
      prev.includes(n) ? prev.filter((x) => x !== n) : [...prev, n].sort((a, b) => a - b),
    );
  const toggleAll = () => setChosenSeasons(allSelected ? [] : [...allSeasonNumbers]);

  const seasonSummary = allSelected ? 'All seasons'
    : chosenSeasons.length === 0 ? 'Select seasons'
    : chosenSeasons.length === 1
      ? (pickableSeasons.find((s) => s.season_number === chosenSeasons[0])?.name || `Season ${chosenSeasons[0]}`)
      : `${chosenSeasons.length} seasons`;
  const profileName = profiles.find((p) => p.id === profileId)?.name
    || (profiles.length ? 'Select profile' : 'Default');

  const canAdd = chosenSeasons.length > 0 || allSeasonNumbers.length === 0;

  const handleAdd = async () => {
    if (adding || added || !canAdd) return;
    setAdding(true);
    setError(false);
    try {
      // Whole show → 'all'; otherwise monitor exactly the chosen seasons.
      const monitor = allSelected || allSeasonNumbers.length === 0 ? 'all' : 'seasons';
      const seasonsArg = monitor === 'all' ? undefined : chosenSeasons;
      const result = await addToSonarr(tvdbId, title, monitor, true, seasonsArg, profileId ?? undefined);
      if (result.success || (result.message && result.message.includes('already'))) {
        setAdded(true);
        setTimeout(() => { onAdded(); onClose(); }, 1500);
      } else {
        setAdding(false);
        setError(true);
        setTimeout(() => setError(false), 3000);
      }
    } catch {
      setAdding(false);
      setError(true);
      setTimeout(() => setError(false), 3000);
    }
  };

  const addButtonFocus = useFocusable({
    focusKey: 'sonarr-add',
    zoneId: 'add-to-sonarr-modal',
    onEnter: handleAdd,
  });

  return (
    <div className="luma-bleed"
      style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.98)', zIndex: 100000, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '20px' }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <FocusZone zoneId="add-to-sonarr-modal" orientation="vertical" trapFocus onEscape={onClose}>
        <div style={{ background: 'var(--color-kiosk-black)', borderRadius: '20px', padding: '30px 28px', maxWidth: '420px', width: '100%' }} onClick={(e) => e.stopPropagation()}>
          <h2 style={{ marginBottom: '24px', fontSize: '22px', fontWeight: 700, textAlign: 'center', color: 'white' }}>
            Add to watchlist
          </h2>

          {/* Quality profile — single select */}
          <div style={{ marginBottom: '18px', position: 'relative' }}>
            <label style={fieldLabelStyle}>Quality profile</label>
            <DropdownTrigger
              focusKey="sonarr-profile-trigger"
              value={profileName}
              open={openMenu === 'profile'}
              onToggle={() => setOpenMenu((m) => (m === 'profile' ? null : 'profile'))}
            />
            {openMenu === 'profile' && profiles.length > 0 && (
              <div style={panelStyle}>
                {profiles.map((p) => (
                  <DropdownOption
                    key={p.id}
                    focusKey={`sonarr-profile-${p.id}`}
                    label={p.name}
                    active={profileId === p.id}
                    onSelect={() => { setProfileId(p.id); setOpenMenu(null); }}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Seasons — multi select (menu stays open on click) */}
          <div style={{ marginBottom: '24px', position: 'relative' }}>
            <label style={fieldLabelStyle}>Seasons</label>
            <DropdownTrigger
              focusKey="sonarr-seasons-trigger"
              value={seasonSummary}
              open={openMenu === 'seasons'}
              onToggle={() => setOpenMenu((m) => (m === 'seasons' ? null : 'seasons'))}
            />
            {openMenu === 'seasons' && (
              <div style={panelStyle}>
                {allSeasonNumbers.length > 0 && (
                  <DropdownOption
                    focusKey="sonarr-season-all"
                    label="All seasons"
                    checkbox
                    active={allSelected}
                    onSelect={toggleAll}
                  />
                )}
                {pickableSeasons.map((s) => (
                  <DropdownOption
                    key={s.season_number}
                    focusKey={`sonarr-season-${s.season_number}`}
                    label={s.name || `Season ${s.season_number}`}
                    sub={(s.episode_count ?? 0) > 0 ? `${s.episode_count} ep${s.episode_count !== 1 ? 's' : ''}` : undefined}
                    checkbox
                    active={chosenSeasons.includes(s.season_number)}
                    onSelect={() => toggleSeason(s.season_number)}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Add button */}
          <button
            ref={addButtonFocus.ref}
            {...addButtonFocus.focusProps}
            type="button"
            onClick={handleAdd}
            disabled={adding || added || !canAdd}
            style={{
              width: '100%',
              padding: '16px',
              borderRadius: '50px',
              border: 'none',
              background: added ? '#4ade80' : error ? '#ef4444' : '#3b82f6',   // v167 (Designer): #3b82f6 primary
              color: added ? '#000' : '#ffffff',
              fontSize: '16px',
              fontWeight: 700,
              cursor: adding || added || !canAdd ? 'default' : 'pointer',
              transition: 'all 0.2s',
              opacity: adding || !canAdd ? 0.6 : 1,
            }}
          >
            {added ? 'Added!' : adding ? 'Adding...' : error ? 'Failed - try again'
              : chosenSeasons.length === 0 && allSeasonNumbers.length > 0 ? 'Select a season'
              : 'Add to watchlist'}
          </button>
        </div>
      </FocusZone>
    </div>
  );
}

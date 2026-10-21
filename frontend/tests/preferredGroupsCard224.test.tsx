import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { PreferredGroups } from '@/components/library/TvLibrarySettings';
afterEach(cleanup);
describe('v224 Preferred groups card (Designer)', () => {
  it('card: the Group input and Add are the last row inside the glass card, even when empty', () => {
    render(<PreferredGroups card value={[]} onChange={() => {}} />);
    const c = screen.getByTestId('tv-groups-card');
    const row = screen.getByTestId('tv-group-add-row');
    expect(c.contains(row)).toBe(true);
    expect(c.lastElementChild).toBe(row);
    expect(row.contains(screen.getByLabelText('Group'))).toBe(true);
    expect(row.textContent).toContain('Add');
    expect(row.style.minHeight).toBe('56px');
  });
  it('card with groups: rows then the add row, divider above it', () => {
    render(<PreferredGroups card value={['NTb', 'FLUX']} onChange={() => {}} />);
    const c = screen.getByTestId('tv-groups-card');
    expect(screen.getAllByTestId('tv-group')).toHaveLength(2);
    expect(c.lastElementChild).toBe(screen.getByTestId('tv-group-add-row'));
    expect(screen.getByTestId('tv-group-add-row').style.borderTop).toContain('1px solid');
  });
  it('without card (TV page): no card, add row outside as before', () => {
    render(<PreferredGroups value={[]} onChange={() => {}} />);
    expect(screen.queryByTestId('tv-groups-card')).toBeNull();
    expect(screen.queryByTestId('tv-group-add-row')).toBeNull();
    expect(screen.getByLabelText('Group')).toBeTruthy();
  });
});

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { PreferredGroups } from '@/components/library/TvLibrarySettings';

describe('v185.2a preferred groups', () => {
  it('starts empty, adds, reorders and removes', () => {
    const on = vi.fn();
    const { rerender } = render(<PreferredGroups value={[]} onChange={on} />);
    expect(screen.queryAllByTestId('tv-group')).toHaveLength(0);
    fireEvent.change(screen.getByLabelText('Group'), { target: { value: ' NTb ' } });
    fireEvent.click(screen.getByText('Add'));
    expect(on).toHaveBeenLastCalledWith(['NTb']);
    rerender(<PreferredGroups value={['KiNGS', 'NTb']} onChange={on} />);
    fireEvent.click(screen.getByLabelText('Move NTb up'));
    expect(on).toHaveBeenLastCalledWith(['NTb', 'KiNGS']);
    fireEvent.click(screen.getByLabelText('Remove KiNGS'));
    expect(on).toHaveBeenLastCalledWith(['NTb']);
  });
});

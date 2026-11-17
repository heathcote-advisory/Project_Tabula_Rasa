import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { HelpPage } from './HelpPage';
import { helpDraft, severityFor, SUPPORT_EMAIL } from './helpDraft';

describe('private support intake', () => {
  it.each([
    ['Security or data loss', 'P0'], ['Receipts', 'P1'], ['Export', 'P1'],
    ['Routine defect', 'P2'], ['Privacy request', 'P2'], ['Product feedback', 'P3'],
  ] as const)('classifies %s as provisional %s for operator verification', (category, expected) => {
    expect(severityFor(category)).toBe(expected);
  });

  it('requires review, never transmits, and invalidates the preview on edits', async () => {
    const fetcher = vi.spyOn(globalThis, 'fetch');
    const user = userEvent.setup();
    render(<MemoryRouter><HelpPage /></MemoryRouter>);
    expect(screen.getByRole('button', { name: 'Review email draft' })).toBeDisabled();
    await user.type(screen.getByLabelText('Safe request or reproduction reference (optional)'), 'safe-request');
    await user.click(screen.getByLabelText(/I reviewed these fields/));
    await user.click(screen.getByRole('button', { name: 'Review email draft' }));
    const draft = screen.getByRole('link', { name: 'Open in mail client' });
    expect(draft.getAttribute('href')).toContain('mailto:' + SUPPORT_EMAIL);
    expect(decodeURIComponent(draft.getAttribute('href')!)).toContain('not opted in');
    expect(decodeURIComponent(draft.getAttribute('href')!)).toContain('safe-request');
    expect(fetcher).not.toHaveBeenCalled();
    await user.type(screen.getByLabelText('Release number (optional)'), 'release-1');
    expect(screen.queryByRole('link', { name: 'Open in mail client' })).not.toBeInTheDocument();
    fetcher.mockRestore();
  });

  it('keeps research separate and clears answers when withdrawn', async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><HelpPage /></MemoryRouter>);
    const choice = screen.getByLabelText(/Optional: share qualitative/);
    expect(choice).not.toBeChecked();
    await user.click(choice);
    fireEvent.change(screen.getByLabelText('Optional research answers'), { target: { value: 'Synthetic interview answer' } });
    await user.click(choice);
    await user.click(choice);
    expect(screen.getByLabelText('Optional research answers')).toHaveValue('');
    expect(helpDraft('Product feedback', '', '', false, 'PRIVATE ANSWER')).not.toContain('PRIVATE ANSWER');
    expect(helpDraft('Product feedback', '', '', true, 'Synthetic answer')).toContain('Synthetic answer');
  });
});

import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('public policy and private draft flow require no identity or automatic upload', async ({ page }) => {
  const unexpected: string[] = [];
  await page.route('**/api/v1/**', async route => {
    if (new URL(route.request().url()).pathname === '/api/v1/public/policies') {
      await route.fulfill({ json: { version: 'synthetic', effective_date: '2026-11-16',
        digest: 'a'.repeat(64), beta: false, support_email: null,
        terms: '# Synthetic Terms\n\n## Limits\n\nReview the permitted bookkeeping scope.',
        privacy: '# Synthetic Privacy\n\nPrivate support and essential sessions only.' } });
    } else { unexpected.push(route.request().url()); await route.abort(); }
  });
  await page.goto('/terms');
  await expect(page.getByRole('heading', { name: 'Synthetic Terms' })).toBeVisible();
  await expect(page.getByText(/Policy version: synthetic/)).toBeVisible();
  await page.getByRole('link', { name: 'Help & feedback' }).click();
  await expect(page.getByRole('button', { name: 'Review email draft' })).toBeDisabled();
  await page.getByLabel('Safe request or reproduction reference (optional)').fill('synthetic-request');
  await page.getByLabel(/I reviewed these fields/).check();
  await page.getByRole('button', { name: 'Review email draft' }).click();
  await expect(page.getByRole('link', { name: 'Open in mail client' })).toHaveAttribute('href', /^mailto:heathcoteadvisoryllc@gmail.com/);
  await expect(page.getByRole('region', { name: 'Email draft' })).toContainText('not opted in');
  expect(unexpected).toEqual([]);
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.screenshot({ path: test.info().outputPath('help.png'), fullPage: true });
});

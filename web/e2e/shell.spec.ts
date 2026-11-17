import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { captureEvidence, retainEvidence } from './visual-evidence';

const session = {
  actor_id: 'synthetic', organization_id: 'synthetic', entity: { currency_code: 'USD', id: 'alpha', name: 'Alpha demonstration' },
  capabilities: ['bookkeeping:read', 'bookkeeping:write'], cache_scope: 'e2e-alpha', csrf_token: 'synthetic-csrf',
  trusted_development: true,
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/session', route => route.fulfill({ json: session }));
  await page.route('**/api/v1/businesses', route => route.fulfill({ json: { items: [session.entity] } }));
  await page.route('**/api/v1/entities/alpha/reports/summary', route => route.fulfill({ json: {
    profit_loss: { income: '0.00', expenses: '0.00', net: '0.00' },
    balance_sheet: { assets: [], liabilities: [], equity: [], current_earnings: '0.00', total_assets: '0.00',
      total_liabilities: '0.00', total_equity: '0.00', difference: '0.00' }, account_balances: [],
  } }));
  await page.route('**/api/v1/entities/alpha/accounts?*', route => route.fulfill({ json: { items: [] } }));
});

test.afterEach(async () => { await retainEvidence(test.info()); });

test('deep links, navigation, responsive layout and empty state', async ({ page }) => {
  for (const width of [1440, 1200, 900, 390, 320]) {
    await page.setViewportSize({ width, height: width < 760 ? 844 : 1000 });
    await page.goto('/accounts');
    await expect(page.getByRole('heading', { name: 'Accounts', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'No accounts yet' })).toBeVisible();
    if (width <= 760) await page.getByRole('button', { name: 'Menu', exact: true }).click();
    await page.getByRole('link', { name: 'Reports', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Reports', exact: true })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Balance sheet', exact: true })).toBeVisible();
    await expect(page.getByRole('main')).toBeFocused();
    if (width <= 760) {
      await page.goBack();
      await expect(page.getByRole('heading', { name: 'Accounts', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Menu', exact: true })).toHaveAttribute('aria-expanded', 'false');
      await page.goForward();
      await expect(page.getByRole('heading', { name: 'Reports', exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Menu', exact: true })).toHaveAttribute('aria-expanded', 'false');
    }
    const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    expect(accessibility.violations).toEqual([]);
    await captureEvidence(page, test.info(), 'shell-' + width + '.png');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  }
});

test('visible validation, conflict and unavailable states with request references', async ({ page }) => {
  for (const code of ['validation', 'stale', 'unavailable']) {
    await page.route('**/api/v1/entities/alpha/accounts?*', route => route.fulfill({
      status: code === 'stale' ? 409 : code === 'validation' ? 422 : 503,
      json: { error: { code, message: 'Safe transport message' }, request_id: 'smoke-reference' },
    }));
    await page.goto('/accounts');
    await expect(page.getByRole('alert')).toBeVisible();
    await expect(page.getByText('Request reference: smoke-reference')).toBeVisible();
    await expect(page.getByRole('button', { name: code === 'stale' ? 'Review latest record' : 'Reload' })).toBeVisible();
  }
});

test('unknown route and unavailable context are safe', async ({ page }) => {
  for (const path of ['/missing', '/workspace/missing']) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
    await expect(page.locator('header').first()).toContainText('Tabula Rasa');
  }
  await page.route('**/api/v1/session', route => route.fulfill({
    status: 401, json: { error: { code: 'unauthorized', message: 'Sign in' }, request_id: 'context-ref' },
  }));
  await page.goto('/');
  await expect(page.getByRole('alert')).toContainText('Business access is unavailable');
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0);
  await expect(page.getByRole('navigation', { name: 'Policies and help' })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Help & feedback' })).toBeVisible();
});

import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { captureEvidence, retainEvidence } from './visual-evidence';

import { session, longName, report, coreResponse } from './core-fixture';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', route => route.fulfill({ json: coreResponse(new URL(route.request().url())) }));
});

test.afterEach(async () => { await retainEvidence(test.info()); });

test('integrated financial surfaces reflow at effective 200 and 400 percent widths', async ({ page }) => {
  test.setTimeout(90_000);
  // Browser zoom changes the effective CSS viewport. These dimensions exercise
  // layout reflow only; actual browser zoom and device use remain manual checks.
  for (const [width, reflow] of [[720, '1440px desktop at 200% equivalent CSS width'], [320, '1280px desktop at 400% equivalent CSS width']] as const) {
    await page.setViewportSize({ width, height: 844 });
    for (const [surface, path] of [['reports', '/reports'], ['ledger', '/transactions']] as const) {
      await page.goto(path);
      await expect(page.getByRole('cell', { name: surface === 'ledger' ? '19.25' : '999999999999.99', exact: true }).first()).toBeVisible();
      await page.getByRole('button', { name: 'Menu', exact: true }).focus();
      await page.keyboard.press('Enter');
      const current = page.getByRole('navigation', { name: 'Main navigation' }).locator('[aria-current]');
      await expect(current).toHaveCount(1);
      await expect(current).toHaveCSS('border-inline-start-style', 'solid');
      await captureEvidence(page, test.info(), `${surface}-menu-reflow-${width}.png`, reflow);
      await page.getByRole('button', { name: 'Menu', exact: true }).press('Enter');
      const from = page.getByLabel('From', { exact: true });
      await from.focus();
      await expect(from).toBeFocused();
      await expect(from).toHaveCSS('outline-style', 'solid');
      if (surface === 'ledger') {
        const accounts = page.getByLabel('Accounts', { exact: true });
        await accounts.focus(); await page.keyboard.press('Home');
        await expect(accounts).toHaveValues(['account:account']);
        await expect(page.getByText('Selected: ' + longName, { exact: true })).toBeVisible();
        const region = page.getByRole('region', { name: 'Ledger', exact: true });
        await region.focus(); await page.keyboard.press('ArrowRight');
        await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
        await region.evaluate(element => { element.scrollLeft = 0; });
      }
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()).violations).toEqual([]);
      await captureEvidence(page, test.info(), `${surface}-reflow-${width}.png`, reflow);
    }
  }
});

test('core visual evidence with negative, zero, populated and long synthetic values', async ({ page }) => {
  test.setTimeout(90_000);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const [surface, route] of [['dashboard', '/'], ['reports', '/reports'], ['ledger', '/transactions']] as const) {
      await page.goto(route);
      await expect(page.getByRole('heading', { name: surface === 'ledger' ? 'Transactions' : surface === 'reports' ? 'Reports' : 'Dashboard', exact: true })).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: /Loading/ })).toHaveCount(0);
      await expect(page.getByRole('cell', { name: surface === 'ledger' ? '19.25' : '999999999999.99', exact: true }).first()).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
      const accessibility = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
      expect(accessibility.violations).toEqual([]);
      if (surface === 'ledger') {
        const region = page.getByRole('region', { name: 'Ledger', exact: true });
        await expect(region.getByRole('rowheader', { name: 'Expense', exact: true })).toHaveCSS('font-weight', '400');
        await expect(region.getByRole('rowheader', { name: 'Expense · Mixed-use', exact: true }).locator('.recordBadge')).toHaveText(['Expense', 'Mixed-use']);
        await expect(region.getByRole('columnheader', { name: 'Debit', exact: true })).toHaveCSS('text-align', 'right');
        const actions = region.getByRole('link', { name: /^Receipts for/ });
        const actionLeft = (await actions.boundingBox())!.x;
        await region.focus();
        await page.keyboard.press('ArrowRight');
        await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
        if (width >= 1000) {
          await expect(actions.locator('..').locator('..')).toHaveCSS('position', 'sticky');
          expect(Math.abs((await actions.boundingBox())!.x - actionLeft)).toBeLessThanOrEqual(1);
          const bounds = await region.boundingBox();
          expect((await actions.boundingBox())!.x).toBeLessThan(bounds!.x + bounds!.width);
        }
        await region.evaluate(element => { element.scrollLeft = 0; });
      }
      await captureEvidence(page, test.info(), `${surface}-${width}.png`);
    }
  }
});


test('workflow route links preserve dirty cancellation, opener focus and successful mobile navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/transactions/new');
  await page.getByLabel('Description / memo').fill('Preserved synthetic draft');
  const entries = page.getByRole('navigation', { name: 'Entries', exact: true });
  const journals = entries.getByRole('link', { name: 'Journals', exact: true });
  await expect(entries.getByRole('link', { name: 'Transactions', exact: true })).toHaveAttribute('aria-current', 'page');
  await journals.focus();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('alertdialog', { name: 'Unsaved changes' });
  await expect(dialog.getByRole('button', { name: 'Keep editing' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(journals).toBeFocused();
  await expect(page.getByLabel('Description / memo')).toHaveValue('Preserved synthetic draft');
  await page.getByRole('button', { name: 'Menu', exact: true }).click();
  const reports = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Reports', exact: true });
  await reports.click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole('button', { name: 'Keep editing' }).click();
  await expect(reports).toBeFocused();
  await expect(page.getByRole('button', { name: 'Menu', exact: true })).toHaveAttribute('aria-expanded', 'true');
  await reports.press('Enter');
  await dialog.getByRole('button', { name: 'Discard draft and leave' }).click();
  await expect(page.getByRole('heading', { name: 'Reports', exact: true })).toBeVisible();
  await expect(page.getByRole('main')).toBeFocused();
  await expect(page.getByRole('button', { name: 'Menu', exact: true })).toHaveAttribute('aria-expanded', 'false');
  for (const [route, label] of [['/journals', 'Journals'], ['/mixed-use', 'Mixed-use expenses'], ['/recurring', 'Recurring expenses']] as const) {
    await page.goto(route);
    await expect(page.getByRole('navigation', { name: 'Entries', exact: true }).getByRole('link', { name: label, exact: true })).toHaveAttribute('aria-current', 'page');
    await page.getByRole('button', { name: 'Menu', exact: true }).click();
    const current = page.getByRole('navigation', { name: 'Main navigation' }).getByRole('link', { name: 'Transactions', exact: true });
    await expect(current).toHaveAttribute('aria-current', 'true');
    await expect(current).toHaveCSS('border-inline-start-style', 'solid');
  }
  await captureEvidence(page, test.info(), 'workflow-menu-390.png');
  await page.goto('/Journals/new');
  await expect(page.getByRole('navigation', { name: 'Entries', exact: true }).getByRole('link', { name: 'Journals', exact: true })).toHaveAttribute('aria-current', 'page');
  await page.goto('/mileage/new');
  const mileage = page.getByRole('navigation', { name: 'Mileage workflows' });
  await expect(mileage.locator('[aria-current]')).toHaveCount(1);
  await expect(mileage.getByRole('link', { name: 'Add trip' })).toHaveAttribute('aria-current', 'page');
  await mileage.getByRole('link', { name: 'View mileage log' }).click();
  await expect(page.getByRole('heading', { name: 'Mileage', exact: true })).toBeVisible();
});

test('ledger filters retain transport scope, reset defaults, complete-entry search and sorting', async ({ page }) => {
  await page.goto('/transactions');
  const ledger = page.getByRole('region', { name: 'Ledger', exact: true });
  await expect(ledger.locator('tbody')).toHaveCount(3);
  await page.getByLabel('Search transactions').fill(' needle ');
  await expect(page.getByRole('status').filter({ hasText: /matching entries$/ })).toHaveText('1 matching entries');
  await expect(ledger.locator('tbody')).toHaveCount(1);
  await expect(ledger.getByRole('row')).toHaveCount(4); // header, parent, both siblings
  await expect(ledger.getByLabel('Sibling retained', { exact: true })).toBeVisible();
  await expect(ledger.locator('.postingRow').getByRole('button')).toHaveCount(0);
  await expect(ledger.locator('.postingRow').getByRole('link')).toHaveCount(0);
  await page.getByRole('button', { name: 'Clear search' }).click();
  await expect(page.getByRole('status').filter({ hasText: /matching entries$/ })).toHaveText('3 matching entries');
  await page.getByLabel('Sort entries by').selectOption('Amount');
  await page.getByLabel('Entry order').selectOption('Ascending');
  await expect(ledger.locator('tbody').first()).toContainText('Source-owned synthetic expense');
  await expect(ledger.locator('tbody').last()).toContainText('Complete synthetic journal');
  await expect(ledger.locator('tbody').filter({ hasText: 'Source-owned synthetic expense' }).getByRole('button')).toHaveCount(0);
  await expect(ledger.getByRole('link', { name: 'Manage mixed-use expense' })).toHaveAttribute('href', '/mixed-use');
  const filters = page.getByRole('region', { name: 'Ledger filters' });
  await expect(filters.getByLabel('Accounts', { exact: true })).toHaveAccessibleDescription(/no selection includes all/);
  const request = page.waitForRequest(req => req.url().includes('/ledger?') && new URL(req.url()).searchParams.get('start') === '2026-10-01');
  await filters.getByLabel('From', { exact: true }).fill('2026-10-01');
  await request;
  await filters.getByLabel('To', { exact: true }).fill('2026-10-31');
  await filters.getByLabel('Type', { exact: true }).selectOption('Journal');
  await filters.getByLabel('Accounts', { exact: true }).selectOption('account:account');
  const tagged = page.waitForRequest(req => req.url().includes('/ledger?') && new URL(req.url()).searchParams.getAll('tag_ids').includes('tag'));
  await filters.getByLabel('Tags', { exact: true }).selectOption('tag');
  const params = new URL((await tagged).url()).searchParams;
  await expect(filters.getByText('Selected: ' + longName, { exact: true })).toBeVisible();
  await expect(page.getByRole('status').filter({ hasText: /matching entries$/ })).toHaveText('0 matching entries');
  await captureEvidence(page, test.info(), 'ledger-selected-filters.png');
  expect(params.get('start')).toBe('2026-10-01'); expect(params.get('end')).toBe('2026-10-31');
  expect(params.get('kind')).toBe('Journal'); expect(params.getAll('account_ids')).toEqual(['account']);
  await page.getByLabel('Search transactions').fill('no matching synthetic record');
  await expect(page.getByRole('heading', { name: 'No matching entries' })).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('status').filter({ hasText: /matching entries$/ })).toHaveText('3 matching entries');
  await expect(filters.getByLabel('From', { exact: true })).toHaveValue('');
  await expect(filters.getByLabel('To', { exact: true })).toHaveValue('');
  await expect(filters.getByLabel('Type', { exact: true })).toHaveValue('All');
  await expect(filters.getByLabel('Accounts', { exact: true })).toHaveValues([]);
  await expect(filters.getByLabel('Tags', { exact: true })).toHaveValues([]);
  await expect(page.getByLabel('Search transactions')).toHaveValue('');
  await expect(page.getByLabel('Sort entries by')).toHaveValue('Date');
  await expect(page.getByLabel('Entry order')).toHaveValue('Descending');
});

test('report dates preserve server values, as-of context and equation feedback', async ({ page }) => {
  await page.goto('/reports');
  const profit = page.getByRole('region', { name: 'Profit and loss', exact: true });
  await expect(profit.locator('dd').last()).toHaveText('-150.25');
  await expect(profit.locator('dd').last()).toHaveCSS('font-variant-numeric', 'tabular-nums');
  const requested = page.waitForRequest(req => req.url().includes('/reports/summary?') && new URL(req.url()).searchParams.get('end') === '2026-10-31');
  await page.getByLabel('From', { exact: true }).fill('2026-10-01');
  await page.getByLabel('To / balance sheet as of').fill('2026-10-31');
  const params = new URL((await requested).url()).searchParams;
  expect(params.get('start')).toBe('2026-10-01');
  await expect(profit).toContainText('2026-10-01 through 2026-10-31');
  await expect(page.getByRole('region', { name: 'Balance sheet', exact: true })).toContainText('As of 2026-10-31');
  await expect(page.getByRole('table').last()).toContainText('999999999999.99');
  await page.getByRole('button', { name: 'Clear dates' }).click();
  await expect(page.getByLabel('From', { exact: true })).toHaveValue('');
  await expect(page.getByLabel('To / balance sheet as of')).toHaveValue('');
  await page.route('**/reports/summary*', route => route.fulfill({ json: { ...report, balance_sheet: { ...report.balance_sheet, difference: '-5.25' } } }));
  await page.reload();
  await expect(page.getByRole('status')).toContainText('difference of -5.25');
  await expect(page.getByRole('status')).not.toContainText('Assets = Liabilities + Equity.');
});

test('reports distinguish loading, zero, unavailable values and failed reads', async ({ page }) => {
  let release: () => void = () => {};
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route('**/reports/summary*', async route => { await pending; return route.fulfill({ json: {
    profit_loss: { income: '0.00', expenses: '0.00', net: '0.00' },
    balance_sheet: { assets: [], liabilities: [], equity: [], current_earnings: '0.00', total_assets: '0.00', total_liabilities: '0.00', total_equity: '0.00', difference: '0.00' }, account_balances: [],
  } }); });
  await page.goto('/reports');
  await expect(page.getByRole('status')).toHaveText('Loading reports…');
  await expect(page.locator('dd')).toHaveCount(0);
  await captureEvidence(page, test.info(), 'reports-loading.png');
  release();
  await expect(page.getByRole('status')).toHaveText('Assets = Liabilities + Equity.');
  await expect(page.getByText('No accounts in Assets.', { exact: true })).toBeVisible();
  await expect(page.getByRole('region', { name: 'Profit and loss', exact: true }).locator('dd')).toHaveText(['0.00', '0.00', '0.00']);
  await captureEvidence(page, test.info(), 'reports-zero.png');
  await page.route('**/reports/summary*', route => route.fulfill({ json: { ...report,
    profit_loss: { ...report.profit_loss, net: null }, balance_sheet: { ...report.balance_sheet, difference: null } } }));
  await page.reload();
  await expect(page.getByRole('region', { name: 'Profit and loss', exact: true }).locator('dd').last()).toHaveText('—');
  await expect(page.getByRole('status')).toHaveText('Accounting-equation status is unavailable.');
  await captureEvidence(page, test.info(), 'reports-unavailable.png');
  await page.route('**/reports/summary*', route => route.fulfill({ status: 503, json: { error: { code: 'unavailable' }, request_id: 'synthetic-failure' } }));
  await page.reload();
  await expect(page.getByRole('alert')).toContainText('synthetic-failure');
  await expect(page.getByRole('alert')).toContainText('data could not be loaded');
  await expect(page.getByRole('alert')).not.toContainText('draft');
  await expect(page.locator('dd')).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Reload' })).toBeVisible();
  await captureEvidence(page, test.info(), 'reports-error.png');
  await page.route('**/ledger*', route => route.fulfill({ status: 503, json: { error: { code: 'unavailable' } } }));
  await page.goto('/transactions');
  await expect(page.getByRole('alert')).toContainText('data could not be loaded');
  await expect(page.getByRole('alert')).not.toContainText('draft');
});

test('read-only scope retains navigation and inspection without create or edit affordances', async ({ page }) => {
  await page.route('**/api/v1/session', route => route.fulfill({ json: { ...session, capabilities: ['bookkeeping:read'] } }));
  await page.goto('/transactions');
  await expect(page.getByRole('region', { name: 'Ledger', exact: true })).toBeVisible();
  await expect(page.getByRole('link', { name: 'Add transaction', exact: true })).toHaveCount(0);
  await expect(page.getByRole('link', { name: 'Add journal', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Edit / })).toHaveCount(0);
  await expect(page.getByRole('link', { name: /^Receipts for/ })).toHaveCount(1);
  await page.goto('/mileage');
  await expect(page.getByRole('navigation', { name: 'Mileage workflows' }).getByRole('link', { name: 'Add trip' })).toHaveCount(0);
});

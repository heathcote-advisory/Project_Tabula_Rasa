import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';
import { account, coreResponse, ordinary, journal, session } from './core-fixture';
import { secondaryResponse } from './secondary-fixture';

const description = 'First few words followed by a complete description with  exact spacing\nand a second line that must stay available.';
test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/**', route => {
    const url = new URL(route.request().url()), path = url.pathname;
    const json = path.endsWith('/session') ? { ...session, trusted_development: false, entity: { ...session.entity, role: 'owner' } }
      : path === '/api/v1/businesses' ? { items: [session.entity, { id: 'beta', name: 'Second business with long descriptive text', currency_code: 'CAD' }] }
      : path.endsWith('/customer-lifecycle') ? { requests: [], last_owned_businesses: [] }
      : path.endsWith('/ledger') ? { items: [{ ...ordinary, description, receipt_count: 2 }, journal] }
      : path.endsWith('/accounts') ? { items: [{ ...account, id: 'child', name: 'A child', parent_id: 'parent' },
        { ...account, id: 'debt', name: 'Debt', classification: 'Liability' }, { ...account, id: 'parent', name: 'Z parent' }] }
      : path.includes('/mileage') ? secondaryResponse(url) : coreResponse(url);
    return route.fulfill({ json });
  });
});

test('compact descriptions, conditional journal columns and accessible receipt counts survive desktop and narrow viewports', async ({ page }) => {
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await page.goto('/transactions');
    const region = page.getByRole('region', { name: 'Ledger', exact: true });
    await expect(region.getByRole('columnheader', { name: 'ID', exact: true })).toHaveCount(0);
    await expect(region.getByRole('columnheader', { name: 'Debit', exact: true })).toBeVisible();
    await page.getByLabel('Search transactions').fill(ordinary.record_id);
    await expect(region.getByRole('columnheader', { name: 'Debit', exact: true })).toHaveCount(0);
    await expect(region.getByRole('columnheader', { name: 'Credit', exact: true })).toHaveCount(0);
    const summary = region.getByRole('button', { name: description, exact: true });
    await expect(summary).toHaveAttribute('title', description);
    await expect(summary).toHaveAttribute('aria-label', description);
    const row = region.locator('tbody > tr').first();
    const before = (await row.boundingBox())!.height;
    const preview = summary.locator('span');
    expect(await preview.evaluate(node => node.scrollWidth > node.clientWidth)).toBe(true);
    await summary.focus(); await page.keyboard.press('Enter');
    await expect(region.getByRole('tooltip')).toHaveText(description);
    await expect(region.getByRole('tooltip')).toBeVisible();
    expect(await region.getByRole('tooltip').evaluate(node => {
      const bounds = node.getBoundingClientRect();
      const painted = document.elementFromPoint(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
      return node.matches(':popover-open') && bounds.top >= 0 && bounds.bottom <= innerHeight
        && bounds.left >= 0 && bounds.right <= innerWidth && (painted === node || node.contains(painted));
    })).toBe(true);
    expect((await row.boundingBox())!.height).toBe(before);
    await page.keyboard.press('Escape');
    await expect(region.getByRole('tooltip')).toHaveCount(0);
    await summary.click();
    await expect(region.getByRole('tooltip')).toBeVisible();
    await page.getByRole('heading', { name: 'Transactions', exact: true }).click();
    await expect(region.getByRole('tooltip')).toHaveCount(0);
    await expect(region.getByLabel('2 receipt(s)')).toHaveText('📎 (2)');
    await page.getByLabel('Search transactions').fill('Sibling retained');
    await expect(region.getByRole('rowheader', { name: '↳ Posting', exact: true })).toHaveCount(2);
    await expect(region.getByRole('columnheader', { name: 'Credit', exact: true })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  }
});

test('business actions align and Data & backup leaves handoff and red closure last', async ({ page }) => {
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await page.goto('/businesses');
    const selected = page.getByText('Selected business', { exact: true });
    const open = page.getByRole('button', { name: 'Open business', exact: true });
    await expect(selected).toBeVisible(); await expect(open).toBeVisible();
    const a = (await selected.boundingBox())!, b = (await open.boundingBox())!;
    expect(Math.abs(a.x - b.x)).toBeLessThanOrEqual(1); expect(Math.abs(a.width - b.width)).toBeLessThanOrEqual(1);
    expect(a.height).toBeGreaterThanOrEqual(44); expect(b.height).toBeGreaterThanOrEqual(44);
    await open.click(); await expect(page.getByRole('alertdialog', { name: 'Switch business' })).toBeVisible();
    await page.getByRole('button', { name: 'Keep current business' }).click();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    await page.goto('/data');
    await expect(page.getByRole('heading', { name: 'Closure and privacy requests' })).toBeVisible();
    const headings = await page.locator('main h2').allTextContents();
    expect(headings.slice(-2)).toEqual(['Accountant handoff', 'Closure and privacy requests']);
    await expect(page.getByRole('button', { name: 'Request business closure' })).toHaveClass(/action-destructive/);
  }
});

test('account sorting keeps children with their parent at narrow widths', async ({ page }) => {
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: 900 }); await page.goto('/accounts');
    const region = page.getByRole('region', { name: 'Accounts', exact: true });
    await expect(region.getByRole('rowheader')).toHaveText(['Z parent', '↳ Level 2, child of Z parent. A child', 'Debt']);
    await region.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(region.getByRole('rowheader')).toHaveText(['Debt', 'Z parent', '↳ Level 2, child of Z parent. A child']);
    await region.getByRole('button', { name: 'Name', exact: true }).click();
    await expect(region.getByRole('columnheader', { name: 'Name', exact: true })).toHaveAttribute('aria-sort', 'descending');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  }
});

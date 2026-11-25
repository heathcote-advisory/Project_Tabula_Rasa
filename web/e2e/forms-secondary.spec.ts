import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { captureEvidence, retainEvidence } from './visual-evidence';
import { writeFile } from 'node:fs/promises';

const baselineCapture = process.env.TABULA_RASA_VISUAL_BASELINE === '1';
import { session, longName, longId, category, receipt, secondaryResponse } from './secondary-fixture';

async function transport(page: Page, capabilities = session.capabilities) {
  await page.route('**/api/v1/**', route => route.fulfill({ json: secondaryResponse(new URL(route.request().url()), capabilities) }));
}

async function capture(page: Page, name: string, accessibility = true) {
  const width = page.viewportSize()!.width;
  const contained = await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth);
  if (!baselineCapture) {
    if (!contained) {
      // Keep bounds/style evidence for engine-specific regressions. Wide table
      // descendants are expected only when their named region contains them.
      const bounds = await page.evaluate(() => ({ viewport: document.documentElement.clientWidth, documentWidth: document.documentElement.scrollWidth,
        elements: Array.from(document.querySelectorAll<HTMLElement>('body *')).filter(element => {
          const rect = element.getBoundingClientRect();
          return rect.right > document.documentElement.clientWidth || element.scrollWidth > element.clientWidth;
        }).map(element => {
          const rect = element.getBoundingClientRect(); const style = getComputedStyle(element);
          return { tag: element.tagName, id: element.id, className: element.className, parent: element.parentElement?.className,
            left: rect.left, right: rect.right, width: rect.width, scrollWidth: element.scrollWidth, clientWidth: element.clientWidth,
            minWidth: style.minWidth, maxWidth: style.maxWidth, overflowX: style.overflowX, display: style.display,
            gridTemplateColumns: style.gridTemplateColumns, whiteSpace: style.whiteSpace, overflowWrap: style.overflowWrap,
            insideTableRegion: Boolean(element.closest('.tableScroll')) };
        }) }));
      await writeFile(test.info().outputPath(`${name}-${width}-overflow.json`), JSON.stringify(bounds, null, 2));
    }
    expect(contained, name + ' must contain horizontal overflow in named table regions').toBe(true);
    if (accessibility) {
      const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
      expect(results.violations, name).toEqual([]);
    }
  }
  await captureEvidence(page, test.info(), `${name}-${width}.png`);
}

test.beforeEach(async ({ page }) => { await transport(page); });
test.afterEach(async () => { await retainEvidence(test.info()); });

test('current main import copy and conversion routes consume the shared light presentation', async ({ page }) => {
  test.setTimeout(90_000);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const [name, path, heading] of [
      ['data-imports', '/data', 'Data & backup'], ['mixed-copy', '/mixed-use/mixed/copy', 'Copy mixed-use expense'],
      ['expense-recurring', '/transactions/transaction/recurring', 'Make expense recurring'],
    ] as const) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: /Loading/ })).toHaveCount(0);
      if (name === 'data-imports') await expect(page.getByLabel('QuickBooks mileage CSV', { exact: true })).toBeVisible();
      if (name === 'mixed-copy') await expect(page.getByRole('button', { name: 'Save mixed-use copy', exact: true })).toBeVisible();
      if (name === 'expense-recurring') await expect(page.getByLabel('Schedule name', { exact: true })).toBeVisible();
      await capture(page, name);
    }
  }
});

test('secondary detail actions say Manage for writers and View for read-only users', async ({ page }) => {
  for (const [path, label, heading] of [
    ['/recurring', longName, 'Recurring schedule'], ['/mixed-use', '2026-10-07 · ' + longName, 'Mixed-use expense'],
    ['/assets', longName, 'Asset detail'],
  ] as const) {
    await page.goto(path);
    const manage = page.getByRole('link', { name: 'Manage ' + label, exact: true });
    await expect(manage).toBeVisible();
    await expect(manage).toHaveText('Manage↗');
    await expect(page.getByRole('link', { name: 'View ' + label, exact: true })).toHaveCount(0);
    await manage.click();
    await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toBeVisible();
  }
  await page.route('**/api/v1/session', route => route.fulfill({ json: { ...session, capabilities: ['bookkeeping:read'] } }));
  for (const [path, label] of [['/recurring', longName], ['/mixed-use', '2026-10-07 · ' + longName], ['/assets', longName]] as const) {
    await page.goto(path);
    await expect(page.getByRole('link', { name: 'View ' + label, exact: true })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Manage ' + label, exact: true })).toHaveCount(0);
    await page.getByRole('link', { name: 'View ' + label, exact: true }).click();
    await expect(page.getByRole('heading', { name: /^Saved / }).first()).toBeVisible();
    await expect(page.getByRole('button', { name: /Save changes|Pause schedule|Post due depreciation/ })).toHaveCount(0);
  }
});

test('reference management stays under Categories and contacts across links, creates and merges', async ({ page }) => {
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const path of ['/categories', '/categories/new', '/payees', '/payees/new', '/payees/merge', '/tags', '/tags/new', '/tags/merge']) {
      await page.goto(path);
      const group = page.getByRole('navigation', { name: 'Reference data', exact: true });
      await expect(group.getByRole('link')).toHaveText(['Categories', 'Payees', 'Tags']);
      await expect(group.locator('[aria-current="page"]')).toHaveCount(1);
      await expect(page.getByRole('navigation', { name: 'Entries', exact: true })).toHaveCount(0);
      if (width === 390) await page.getByRole('button', { name: 'Menu', exact: true }).click();
      const main = page.getByRole('navigation', { name: 'Main navigation' });
      await expect(main.locator('[aria-current]')).toHaveCount(1);
      await expect(main.getByRole('link', { name: 'Categories & contacts', exact: true })).toHaveAttribute('aria-current', /page|true/);
      await expect(main.getByRole('link', { name: 'Transactions', exact: true })).not.toHaveAttribute('aria-current');
    }
    await page.goto('/categories');
    await page.getByRole('navigation', { name: 'Reference data' }).getByRole('link', { name: 'Payees', exact: true }).click();
    await expect(page.getByRole('heading', { name: 'Payees', exact: true })).toBeVisible();
    await expect(page.getByRole('main')).toBeFocused();
    await capture(page, 'reference-navigation');
    await page.goto('/categories/new');
    await page.getByLabel('Category name').fill('Synthetic category draft');
    const tags = page.getByRole('navigation', { name: 'Reference data' }).getByRole('link', { name: 'Tags', exact: true });
    await tags.focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('alertdialog', { name: 'Unsaved changes' })).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(tags).toBeFocused();
    await expect(page.getByLabel('Category name')).toHaveValue('Synthetic category draft');
  }
});

test('mileage filters keep compact aligned dates beside tall tag choices', async ({ page }) => {
  for (const width of [1440, 1024, 390, 320]) {
    await page.setViewportSize({ width, height: width > 760 ? 1000 : 844 });
    await page.goto('/mileage');
    const filters = page.getByRole('region', { name: 'Mileage filters' });
    const from = filters.getByLabel('From', { exact: true });
    const to = filters.getByLabel('To', { exact: true });
    const tags = filters.getByLabel('Filter by tags', { exact: true });
    await expect(tags.getByRole('option')).toHaveCount(2);
    await tags.selectOption('source');
    await expect(filters.getByText('Selected: Second synthetic source with another long descriptive name')).toBeVisible();
    const bounds = await Promise.all([from, to, tags].map(control => control.boundingBox()));
    const controlHeight = await page.evaluate(() => matchMedia('(min-width: 761px) and (pointer: fine)').matches ? 40 : 44);
    expect(bounds[0]!.height).toBe(controlHeight); expect(bounds[1]!.height).toBe(controlHeight);
    expect(bounds[2]!.height).toBeGreaterThanOrEqual(128);
    if (width > 760) {
      expect(Math.abs(bounds[0]!.y - bounds[1]!.y)).toBeLessThanOrEqual(1);
      if (bounds[2]!.x > bounds[1]!.x) expect(Math.abs(bounds[0]!.y - bounds[2]!.y)).toBeLessThanOrEqual(1);
      else expect(bounds[2]!.y).toBeGreaterThan(bounds[0]!.y + bounds[0]!.height);
    } else {
      expect(bounds[1]!.y).toBeGreaterThan(bounds[0]!.y + bounds[0]!.height);
      expect(bounds[2]!.y).toBeGreaterThan(bounds[1]!.y + bounds[1]!.height);
    }
    await filters.getByRole('button', { name: 'Clear filters' }).click();
    await expect(tags).toHaveValues([]);
    const download = await page.getByRole('button', { name: 'Download filtered mileage CSV', exact: true }).boundingBox();
    const table = await page.getByRole('region', { name: 'Mileage log', exact: true }).boundingBox();
    expect(table!.y - download!.y - download!.height).toBeGreaterThanOrEqual(16);
    const region = page.getByRole('region', { name: 'Mileage log', exact: true });
    const actionCell = region.locator('tbody td.tableActions').first();
    const actionHeader = region.getByRole('columnheader', { name: 'Actions', exact: true });
    if (width >= 1000) {
      await expect(actionHeader).toHaveCSS('position', 'sticky');
      await expect(actionCell).toHaveCSS('position', 'sticky');
      await region.evaluate(element => { element.scrollLeft = 400; });
      const [container, action] = await Promise.all([region.boundingBox(), actionCell.boundingBox()]);
      expect(action!.x).toBeGreaterThanOrEqual(container!.x);
      expect(action!.x + action!.width).toBeLessThanOrEqual(container!.x + container!.width + 1);
      await region.evaluate(element => { element.scrollLeft = 0; });
    } else await expect(actionCell).toHaveCSS('position', 'static');
    await capture(page, 'mileage-filter-alignment');
  }
});

test('reference pages use one visible title and retain named tables and protected editors', async ({ page }) => {
  for (const width of [1440, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const [route, title, edit] of [['categories', 'Categories', 'Edit category'], ['payees', 'Payees', 'Edit payee'], ['tags', 'Tags', 'Edit tag']] as const) {
      await page.goto('/' + route);
      await expect(page.getByRole('table', { name: title, exact: true })).toBeVisible();
      await expect(page.getByRole('heading', { name: title, exact: true })).toHaveCount(1);
      await expect(page.locator('caption')).toHaveCount(0);
      await capture(page, route + '-title');
      await page.getByRole('button', { name: /^Edit / }).first().click();
      await expect(page.getByRole('heading', { name: edit, exact: true })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Save changes', exact: true })).toBeVisible();
      await page.getByRole('button', { name: 'Back to records', exact: true }).click();
      await expect(page.getByRole('table', { name: title, exact: true })).toBeVisible();
    }
  }
  await page.route('**/api/v1/session', route => route.fulfill({ json: { ...session, capabilities: ['bookkeeping:read'] } }));
  for (const route of ['categories', 'payees', 'tags']) {
    await page.goto('/' + route);
    await expect(page.getByRole('table')).toBeVisible();
    await expect(page.getByRole('button', { name: /^Edit / })).toHaveCount(0);
    await page.route('**/api/v1/entities/alpha/' + route + '*', route => route.fulfill({ json: { items: [] } }));
    await page.reload();
    await expect(page.getByRole('heading', { name: 'No records to edit' })).toBeVisible();
    await expect(page.getByRole('table')).toHaveCount(0);
  }
});

test('integrated form and receipt reflow keeps keyboard controls and retention guidance', async ({ page }) => {
  test.setTimeout(90_000);
  for (const width of [720, 320]) {
    const reflow = width === 720 ? '1440px desktop at 200% equivalent CSS width' : '1280px desktop at 400% equivalent CSS width';
    await page.setViewportSize({ width, height: 844 });
    await page.goto('/transactions/new');
    await expect(page.getByLabel('From account', { exact: true })).toContainText(longName);
    await page.getByLabel('Description / memo').fill('Synthetic review draft with a long sentence and readable field labels.');
    const back = page.getByRole('button', { name: 'Back to records', exact: true });
    await back.focus(); await page.keyboard.press('Enter');
    const dialog = page.getByRole('alertdialog', { name: 'Discard draft' });
    await expect(dialog.getByRole('button', { name: 'Keep editing' })).toBeFocused();
    await captureEvidence(page, test.info(), `form-confirmation-reflow-${width}.png`, reflow);
    await page.keyboard.press('Escape'); await expect(back).toBeFocused();
    await capture(page, 'transaction-reflow');
    // A direct load discards only this synthetic fixture draft, avoiding an
    // unrelated navigation confirmation on the way to the receipt surface.
    await page.goto('/transactions/transaction/receipts');
    await expect(page.getByText('Inspect ' + receipt.filename, { exact: true })).toBeVisible();
    const inspect = page.locator('summary').filter({ hasText: 'Inspect ' + receipt.filename });
    await inspect.focus(); await page.keyboard.press('Enter');
    await expect(page.getByText(receipt.sha256, { exact: false }).first()).toBeVisible();
    await capture(page, 'receipt-reflow');
    await captureEvidence(page, test.info(), `receipt-keyboard-reflow-${width}.png`, reflow);
  }
});

test('identity availability surfaces share controls without exposing private routes', async ({ page }) => {
  test.skip(baselineCapture, 'Identity sessions were delivered after the original visual baseline.');
  test.setTimeout(90_000);
  let state: 'signed-out' | 'unavailable' | 'needs-setup' = 'signed-out';
  await page.route('**/api/v1/session', route => state === 'needs-setup'
    ? route.fulfill({ json: { ...session, trusted_development: false, state: 'needs_setup', entity: null, capabilities: [] } })
    : route.fulfill({ status: state === 'signed-out' ? 401 : 503, json: { error: { code: state === 'signed-out' ? 'unauthorized' : 'unavailable' } } }));
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const mode of ['signed-out', 'unavailable', 'needs-setup'] as const) {
      state = mode;
      await page.goto('/accounts');
      await expect(page.getByRole('heading', { name: mode === 'needs-setup' ? 'My Businesses' : 'Project Tabula Rasa', exact: true })).toBeVisible();
      await expect(page.getByRole('navigation', { name: 'Main navigation' })).toHaveCount(0);
      await expect(page.getByRole('region', { name: 'Accounts', exact: true })).toHaveCount(0);
      if (mode === 'signed-out') await expect(page.getByRole('link', { name: 'Sign in', exact: true })).toHaveAttribute('href', '/api/v1/auth/login');
      else await expect(page.getByRole('button', { name: 'Sign out', exact: true })).toBeVisible();
      if (mode === 'unavailable') await expect(page.getByRole('alert')).toContainText('The requested data could not be loaded.');
      await capture(page, 'identity-' + mode);
      if (mode === 'needs-setup') {
        const summary = page.getByText('Beta scope and service limits', { exact: true });
        const exclusions = page.getByText(/The beta excludes bank feeds/);
        await expect(exclusions).toBeHidden();
        await summary.focus();
        await page.keyboard.press('Enter');
        await expect(exclusions).toBeVisible();
        const horizontalOverflow = await page.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
        expect(horizontalOverflow).toBe(false);
      }
    }
  }
});

test('core form visual evidence at desktop and both narrow widths', async ({ page }) => {
  test.setTimeout(180_000);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const [name, path, heading] of [['transaction-create', '/transactions/new', 'Save transaction'], ['journal-create', '/journals/new', 'Save journal'], ['merge-payees', '/payees/merge', 'Merge payees']] as const) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: /Loading/ })).toHaveCount(0);
      if (name === 'merge-payees') await page.getByRole('button', { name: 'Merge payees', exact: true }).click();
      if (!baselineCapture && name !== 'merge-payees') {
        const memo = page.getByRole('textbox', { name: 'Description / memo', exact: true });
        await expect(memo).toHaveCSS('min-height', '112px');
        await expect(memo).toHaveCSS('resize', 'vertical');
        expect((await memo.boundingBox())!.height).toBeGreaterThanOrEqual(112);
        if (name === 'transaction-create') {
          const tags = page.getByRole('listbox', { name: 'Tags', exact: true });
          await expect(tags).toHaveCSS('min-height', '128px');
          expect((await tags.boundingBox())!.height).toBeGreaterThanOrEqual(128);
        }
      }
      await capture(page, name);
    }
  }
});

test('populated secondary visual evidence preserves provenance and readable recovery surfaces', async ({ page }) => {
  test.setTimeout(180_000);
  for (const width of [1440, 390, 320]) {
    await page.setViewportSize({ width, height: width === 1440 ? 1000 : 844 });
    for (const [name, path, heading] of [['asset-detail', '/assets/asset', 'Asset detail'], ['mixed-use-detail', '/mixed-use/mixed', 'Mixed-use expense'],
      ['recurring-detail', '/recurring/recurring', 'Recurring schedule'], ['mileage-list', '/mileage', 'Mileage'], ['mileage-create', '/mileage/new', 'Add trip'],
      ['receipt-management', '/transactions/transaction/receipts', 'Receipts']] as const) {
      await page.goto(path);
      await expect(page.getByRole('heading', { name: heading, exact: true }).first()).toBeVisible();
      await expect(page.getByRole('status').filter({ hasText: /Loading/ })).toHaveCount(0);
      if (name === 'receipt-management') await page.getByText('Inspect ' + receipt.filename, { exact: true }).click();
      if (name === 'asset-detail') {
        await expect(page.getByLabel('Cost basis')).toBeDisabled();
        await expect(page.getByRole('region', { name: 'Depreciation schedule', exact: true }).getByText(longId)).toBeVisible();
      }
      if (name === 'mileage-list' && !baselineCapture) {
        const region = page.getByRole('region', { name: 'Mileage log', exact: true });
        await region.focus(); await page.keyboard.press('ArrowRight');
        await expect.poll(() => region.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
        await region.evaluate(element => { element.scrollLeft = 0; });
        await expect(region.getByRole('columnheader', { name: 'Miles', exact: true })).toHaveCSS('text-align', 'right');
      }
      await capture(page, name);
    }
  }
});

test('protected conflicts retain draft and loaded token through keyboard review and explicit reload', async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 844 });
  const submitted: { baseline: string; changes: { name: string } }[] = [];
  let latest = false;
  await page.route(/\/api\/v1\/entities\/alpha\/categories(?:\/[^/]+)?$/, async route => {
    if (route.request().method() === 'PATCH') {
      submitted.push(route.request().postDataJSON());
      return route.fulfill({ status: 409, json: { error: { code: 'stale' }, request_id: 'synthetic-conflict-reference-with-long-description-0123456789' } });
    }
    return route.fulfill({ json: { items: [{ ...category, name: latest ? 'Latest saved category' : category.name, baseline: latest ? 'latest-category' : category.baseline }] } });
  });
  await page.goto('/categories');
  await page.getByRole('button', { name: 'Edit ' + category.name, exact: true }).click();
  const name = page.getByLabel('Category name');
  await name.fill('Preserved synthetic draft');
  await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Your draft is preserved');
  expect(submitted).toEqual([{ baseline: category.baseline, changes: { name: 'Preserved synthetic draft', kind: 'Expense', parent_id: '' } }]);
  const review = page.getByRole('button', { name: 'Review latest record', exact: true });
  await review.focus(); await page.keyboard.press('Enter');
  const dialog = page.getByRole('alertdialog', { name: 'Reload record', exact: true });
  await expect(dialog.getByRole('button', { name: 'Keep draft', exact: true })).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(dialog.getByRole('button', { name: 'Discard draft and reload', exact: true })).toBeFocused();
  await page.keyboard.press('Escape'); await expect(review).toBeFocused();
  await expect(name).toHaveValue('Preserved synthetic draft');
  expect(submitted).toHaveLength(1);
  await capture(page, 'protected-conflict');
  latest = true;
  await review.click(); await dialog.getByRole('button', { name: 'Discard draft and reload', exact: true }).click();
  await expect(name).toHaveValue('Latest saved category');
  await name.fill('New explicit draft'); await page.getByRole('button', { name: 'Save changes', exact: true }).click();
  expect(submitted[1].baseline).toBe('latest-category');
});

test('transaction validation associates multiline errors and journal rows keep keyboard operations', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/api/v1/entities/alpha/transactions', route => route.request().method() === 'POST'
    ? route.fulfill({ status: 422, json: { error: { code: 'validation', fields: [{ field: 'description', code: 'invalid' }, { field: 'reference', code: 'invalid' }] }, request_id: 'synthetic-validation' } })
    : route.fallback());
  await page.goto('/transactions/new');
  await page.getByLabel('From account', { exact: true }).selectOption('account');
  await page.getByLabel('Category', { exact: true }).selectOption('category');
  await page.getByLabel('Amount', { exact: true }).fill('12.34');
  await page.getByLabel('Description / memo').fill('Synthetic first line\nSynthetic second line retained during validation.');
  await page.getByRole('button', { name: 'Save transaction', exact: true }).click();
  const description = page.getByLabel('Description / memo');
  await expect(description).toHaveAttribute('aria-invalid', 'true');
  await expect(description).toHaveAccessibleDescription('Check this value.');
  await expect(description).toBeFocused();
  await expect(description).toHaveValue('Synthetic first line\nSynthetic second line retained during validation.');
  await expect(description).toHaveCSS('border-top-color', 'rgb(179, 58, 58)');
  const reference = page.getByLabel('Reference', { exact: true });
  await expect(reference).toHaveAttribute('aria-invalid', 'true');
  await expect(reference).toHaveCSS('border-top-color', 'rgb(179, 58, 58)');
  await capture(page, 'transaction-validation');
  await page.goto('/journals/new');
  await page.getByRole('button', { name: 'Add posting', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Posting 3', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Remove posting 3', exact: true }).focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'Posting 3', exact: true })).toHaveCount(0);
  await expect(page.getByRole('status')).toContainText('Difference: 0.00');
  await capture(page, 'journal-keyboard');
});

test('read-only secondary details and unconfigured receipts expose truthful availability', async ({ page }) => {
  await page.unroute('**/api/v1/**'); await transport(page, ['bookkeeping:read']);
  await page.setViewportSize({ width: 320, height: 844 });
  for (const [path, title] of [['/assets/asset', 'Saved asset terms'], ['/mixed-use/mixed', 'Saved mixed-use expense'], ['/recurring/recurring', 'Saved recurring schedule']] as const) {
    await page.goto(path);
    await expect(page.getByRole('heading', { name: title, exact: true })).toBeVisible();
    await expect(page.getByRole('button', { name: /Save changes|Delete|Post due|Pause schedule|End schedule/ })).toHaveCount(0);
    await capture(page, title.toLowerCase().replaceAll(' ', '-'));
  }
  await page.route('**/api/v1/entities/alpha/transactions/transaction/receipts', route => route.fulfill({ status: 503, json: { error: { code: 'document_storage_unavailable' } } }));
  await page.goto('/transactions/transaction/receipts');
  await expect(page.getByText('Receipt storage must be configured for this business before receipts are available.')).toBeVisible();
  await expect(page.getByLabel('Receipt files', { exact: true })).toHaveCount(0);
  await capture(page, 'receipts-unavailable');
});

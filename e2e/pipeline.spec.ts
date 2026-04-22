import { test, expect } from '@playwright/test';

async function createTestIssue(page: any, title: string) {
  await page.goto('/issues/new');
  await page.waitForLoadState('domcontentloaded');

  const csrfToken = await page.locator('input[name="csrf_token"]').inputValue();
  expect(csrfToken).toBeTruthy();

  await page.fill('input[name="title"]', title);
  await page.fill('textarea[name="body"]', 'Test body for pipeline e2e.');

  await page.click('button[type="submit"]:has-text("Create Issue")');
  await page.waitForTimeout(2000);

  const errorElement = page.locator('.bg-red-900\\/30');
  if (await errorElement.isVisible().catch(() => false)) {
    const errorText = await errorElement.textContent();
    throw new Error(`Form submission failed: ${errorText}`);
  }

  await expect(page).toHaveURL(/\/issues\/SUP-\d+/, { timeout: 5000 });
  return page.url();
}

test.describe('Pipeline Execution UI', () => {
  test('advance button triggers confirmation dialog', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Advance Dialog Issue');
    await page.goto(issueUrl);
    await page.waitForTimeout(1000);

    let dialogMessage = '';
    page.once('dialog', async dialog => {
      dialogMessage = dialog.message();
      await dialog.dismiss();
    });

    await page.click('#issue-actions button:has-text("Advance Stage")');
    await page.waitForTimeout(500);

    expect(dialogMessage.toLowerCase()).toContain('run');
  });

  test('retry button triggers confirmation dialog', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Retry Dialog Issue');
    await page.goto(issueUrl);
    await page.waitForTimeout(1000);

    let dialogMessage = '';
    page.once('dialog', async dialog => {
      dialogMessage = dialog.message();
      await dialog.dismiss();
    });

    await page.click('#issue-actions button:has-text("Retry Stage")');
    await page.waitForTimeout(500);

    expect(dialogMessage.toLowerCase()).toContain('retry');
  });

  test('stage detail run button triggers confirmation', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Stage Run Dialog Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    await page.goto(`/issues/${issueId}/stage/spec`);
    await page.waitForTimeout(1000);

    let dialogMessage = '';
    page.once('dialog', async dialog => {
      dialogMessage = dialog.message();
      await dialog.dismiss();
    });

    await page.click('button:has-text("Run Stage")');
    await page.waitForTimeout(500);

    expect(dialogMessage.toLowerCase()).toContain('run');
  });

  test('status endpoint returns HTML for existing issue', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Status Endpoint Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    const response = await page.request.get(`/pipeline/issues/${issueId}/status`);
    expect(response.status()).toBe(200);
    const contentType = response.headers()['content-type'];
    expect(contentType).toContain('text/html');
  });

  test('events endpoint returns JSON for existing issue', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Events Endpoint Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    const response = await page.request.get(`/pipeline/issues/${issueId}/events`);
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(Array.isArray(body)).toBe(true);
  });

  test('events stream endpoint connects', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Events Stream Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    // Use fetch with AbortController to avoid hanging on SSE stream
    const result = await page.evaluate(async (url: string) => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2000);
      try {
        const response = await fetch(url, { signal: controller.signal });
        clearTimeout(timeout);
        return { status: response.status, contentType: response.headers.get('content-type') };
      } catch (e) {
        clearTimeout(timeout);
        return { error: String(e) };
      }
    }, `/pipeline/issues/${issueId}/events/stream`);

    expect(result.status).toBe(200);
    expect(result.contentType).toContain('text/event-stream');
  });
});

test.describe('Dashboard SSE', () => {
  test('dashboard SSE endpoint returns event stream', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(500);
    const connected = await page.evaluate(() => {
      return new Promise<boolean>((resolve) => {
        const es = new EventSource('/pipeline/sse/dashboard');
        es.onopen = () => { es.close(); resolve(true); };
        es.onerror = () => { es.close(); resolve(false); };
        setTimeout(() => { es.close(); resolve(false); }, 3000);
      });
    });
    expect(connected).toBe(true);
  });
});

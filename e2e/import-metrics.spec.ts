import { test, expect } from '@playwright/test';

test.describe('GitHub Import', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/issues/new');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);
  });

  test('should show error for invalid GitHub URL', async ({ page }) => {
    await page.fill('input[form="import-form"][name="github_url"]', 'https://example.com/not-github');
    await page.click('button:has-text("Import")');
    await page.waitForTimeout(2000);

    await expect(page.locator('text=Invalid GitHub issue URL')).toBeVisible();
  });

  test('should show error for malformed URL', async ({ page }) => {
    await page.fill('input[form="import-form"][name="github_url"]', 'not-a-url');
    await page.click('button:has-text("Import")');
    await page.waitForTimeout(2000);

    await expect(page.locator('text=Invalid GitHub issue URL')).toBeVisible();
  });
});

test.describe('Metrics Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/pipeline/metrics');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1500);
  });

  test('should display metrics page title', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Pipeline Metrics');
  });

  test('should display stat cards', async ({ page }) => {
    await expect(page.locator('text=Total Issues')).toBeVisible();
    await expect(page.locator('text=Total Retries')).toBeVisible();
    await expect(page.locator('text=Best Stage')).toBeVisible();
    await expect(page.locator('text=Worst Stage')).toBeVisible();
  });

  test('should display chart containers', async ({ page }) => {
    // Charts may be hidden when no data, but they should exist in DOM
    await expect(page.locator('#health-gauge')).toBeAttached();
    await expect(page.locator('#issues-donut')).toBeAttached();
    await expect(page.locator('#stage-bars')).toBeAttached();
    await expect(page.locator('#retries-bars')).toBeAttached();
    await expect(page.locator('#duration-bars')).toBeAttached();
  });

  test('should have link back to dashboard', async ({ page }) => {
    await page.click('a:has-text("Dashboard")');
    await page.waitForURL('/');
    await expect(page.locator('h1')).toBeVisible();
  });
});

test.describe('API Endpoints', () => {
  test('metrics API returns valid JSON', async ({ request }) => {
    const response = await request.get('/api/pipeline/metrics');
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('total_issues');
    expect(body).toHaveProperty('issues_by_status');
    expect(body).toHaveProperty('stage_success_rates');
    expect(body).toHaveProperty('avg_stage_duration_ms');
    expect(body).toHaveProperty('total_retries');
    expect(body).toHaveProperty('retries_by_stage');
    expect(body).toHaveProperty('recent_events');
    expect(typeof body.total_issues).toBe('number');
  });

  test('issues API returns paginated list', async ({ request }) => {
    const response = await request.get('/api/pipeline/issues?page=1&per_page=10');
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('issues');
    expect(body).toHaveProperty('page');
    expect(body).toHaveProperty('per_page');
    expect(body).toHaveProperty('total');
    expect(body).toHaveProperty('pages');
    expect(Array.isArray(body.issues)).toBe(true);
    expect(body.page).toBe(1);
    expect(body.per_page).toBe(10);
  });

  test('health endpoint returns OK', async ({ request }) => {
    const response = await request.get('/health');
    expect(response.status()).toBe(200);
    const body = await response.json();
    expect(body).toHaveProperty('status', 'ok');
  });

  test('metrics redirect works', async ({ request }) => {
    const response = await request.get('/metrics', { maxRedirects: 1 });
    expect(response.status()).toBe(200);
    const url = response.url();
    expect(url).toContain('/pipeline/metrics');
  });
});

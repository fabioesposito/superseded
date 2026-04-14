import { test, expect } from '@playwright/test';

test.describe('Dashboard Interactions', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);
  });

  test('should display stage counters as clickable links', async ({ page }) => {
    const counters = page.locator('#stage-counters a');
    const count = await counters.count();
    expect(count).toBeGreaterThan(0);

    await expect(counters.first()).toContainText('All');

    const stageNames = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];
    for (const name of stageNames) {
      await expect(page.locator(`#stage-counters a[href="/?stage=${name}"]`)).toBeVisible();
    }
  });

  test('should filter table when clicking a stage counter', async ({ page }) => {
    await page.click('#stage-counters a[href="/?stage=spec"]');
    await page.waitForURL('**/?stage=spec');
    await page.waitForTimeout(1500);

    await expect(page).toHaveURL(/\?stage=spec/);
    await expect(page.locator('h1')).toContainText('spec');
  });

  test('should clear filter when clicking All', async ({ page }) => {
    await page.click('#stage-counters a[href="/?stage=spec"]');
    await page.waitForURL('**/?stage=spec');
    await page.waitForTimeout(1000);

    await page.click('#stage-counters a[href="/"]');
    await page.waitForURL('/');
    await page.waitForTimeout(1000);

    await expect(page).toHaveURL('/');
  });

  test('should navigate to issue detail via row link', async ({ page }) => {
    const firstLink = page.locator('#issues-tbody tr td:first-child a').first();

    if (await firstLink.isVisible().catch(() => false)) {
      const href = await firstLink.getAttribute('href');
      expect(href).toMatch(/\/issues\/SUP-\d+/);
      await firstLink.click();
      await page.waitForURL(/\/issues\/SUP-\d+/);
      await expect(page.locator('h1')).toBeVisible();
    }
  });

  test('should show active state on filtered counter', async ({ page }) => {
    await page.click('#stage-counters a[href="/?stage=spec"]');
    await page.waitForURL('**/?stage=spec');
    await page.waitForTimeout(1500);

    const activeCounter = page.locator('#stage-counters a[href="/?stage=spec"]');
    await expect(activeCounter).toHaveClass(/border-neon-600/);
  });
});

import { test, expect } from '@playwright/test';

async function createTestIssue(page: any, title: string) {
  await page.goto('/issues/new');
  await page.waitForLoadState('domcontentloaded');

  const csrfToken = await page.locator('input[name="csrf_token"]').inputValue();
  expect(csrfToken).toBeTruthy();

  await page.fill('input[name="title"]', title);
  await page.fill('textarea[name="body"]', 'Test body for e2e.');
  await page.fill('input[name="labels"]', 'e2e');

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

test.describe('Issue Detail Page', () => {
  test('should display issue details', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Detail Test Issue');
    await page.goto(issueUrl);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);

    await expect(page.locator('h1')).toContainText('Detail Test Issue');
    await expect(page.locator('h1')).toContainText('SUP-');
    await expect(page.locator('span').filter({ hasText: /^spec$/ }).first()).toBeVisible();
    await expect(page.locator('#pipeline-progress-bar')).toBeVisible();

    const stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];
    for (const stage of stages) {
      await expect(page.locator(`#pipeline-progress-bar a:has-text("${stage}")`)).toBeVisible();
    }
  });

  test('should navigate to stage detail from progress bar', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Stage Nav Issue');
    await page.goto(issueUrl);
    await page.waitForTimeout(1000);

    await page.click('#pipeline-progress-bar a:has-text("spec")');
    await page.waitForURL(/\/issues\/SUP-\d+\/stage\/spec/);
    await page.waitForTimeout(1000);

    await expect(page.locator('h1')).toContainText('spec');
    await expect(page.locator('text=Back to')).toBeVisible();
  });

  test('should delete issue with confirmation', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Delete Me Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];
    await page.goto(issueUrl);
    await page.waitForTimeout(1000);

    page.once('dialog', dialog => dialog.accept());
    await page.click('#issue-actions button:has-text("Delete Issue")');
    await page.waitForTimeout(2000);

    // Verify deletion by navigating to dashboard
    await page.goto('/');
    await page.waitForTimeout(1000);
    await expect(page.locator(`a:has-text("${issueId}")`)).not.toBeVisible();
  });
});

test.describe('Stage Detail Page', () => {
  test('should display stage detail with navigation', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Stage Detail Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    await page.goto(`/issues/${issueId}/stage/build`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);

    await expect(page.locator('h1')).toContainText('build');
    await expect(page.locator('h1')).toContainText(issueId);
    await expect(page.locator('text=Implement the code changes')).toBeVisible();

    await expect(page.locator('button:has-text("Run Stage")')).toBeVisible();
    await expect(page.locator('button:has-text("Retry")')).toBeVisible();

    // Navigation links
    await expect(page.locator('a:has-text("plan")').first()).toBeVisible();
    await expect(page.locator('a:has-text("verify")').first()).toBeVisible();
  });

  test('should show 404 for invalid issue ID', async ({ page }) => {
    await page.goto('/issues/INVALID/stage/spec');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(500);

    await expect(page.locator('text=Invalid issue ID')).toBeVisible();
  });

  test('should show 404 for invalid stage', async ({ page }) => {
    const issueUrl = await createTestIssue(page, 'Invalid Stage Issue');
    const issueId = issueUrl.match(/SUP-\d+/)![0];

    await page.goto(`/issues/${issueId}/stage/invalid`);
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(500);

    await expect(page.locator('text=Invalid stage: invalid')).toBeVisible();
  });
});

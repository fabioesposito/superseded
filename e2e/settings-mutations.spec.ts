import { test, expect } from '@playwright/test';

test.describe('Settings Mutations', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(1000);
  });

  test('should add and delete a repository', async ({ page }) => {
    const repoName = `e2e-repo-${Date.now()}`;

    await page.click('button:has-text("Add Repo")');
    await expect(page.locator('text=Add Repository')).toBeVisible();

    await page.fill('input[name="name"]', repoName);
    const gitUrl = `https://github.com/test/${repoName}.git`;
    await page.fill('input[name="git_url"]', gitUrl);
    await page.fill('input[name="path"]', `/tmp/${repoName}`);
    await page.fill('input[name="branch"]', 'main');

    await page.click('button[type="submit"]:has-text("Save Repo")');
    await page.waitForTimeout(1500);

    // Verify repo appears in table (scope to name cell to avoid matching path cell)
    await expect(page.locator('td.font-mono.text-neon-400').filter({ hasText: repoName })).toBeVisible();
    await expect(page.locator('td.font-mono').filter({ hasText: gitUrl }).first()).toBeVisible();

    // Delete the repo
    page.once('dialog', dialog => dialog.accept());
    const removeButton = page.locator(`tr:has-text("${repoName}") button:has-text("Remove")`);
    await removeButton.click();
    await page.waitForTimeout(1500);

    // Verify repo is gone
    await expect(page.locator(`text=${repoName}`)).not.toBeVisible();
  });

  test('should save GitHub token', async ({ page }) => {
    await page.fill('#token-config input[name="github_token"]', 'ghp_test_token_12345');
    await page.click('#token-config button:has-text("Save Token")');
    await page.waitForTimeout(1500);

    await expect(page.locator('text=GitHub token saved successfully.')).toBeVisible();
  });

  test('should save API keys', async ({ page }) => {
    await page.fill('#api-keys-config input[name="openai_api_key"]', 'sk-openai-test');
    await page.fill('#api-keys-config input[name="anthropic_api_key"]', 'sk-ant-test');
    await page.fill('#api-keys-config input[name="opencode_api_key"]', 'oc-test');

    await page.click('#api-keys-config button:has-text("Save API Keys")');
    await page.waitForTimeout(1500);

    await expect(page.locator('text=API keys saved successfully.')).toBeVisible();
  });

  test('should save source root', async ({ page }) => {
    await page.fill('#source-root-config input[name="source_code_root"]', '/tmp/source-root');
    await page.click('#source-root-config button:has-text("Save Source Root")');
    await page.waitForTimeout(1500);

    await expect(page.locator('text=Source root saved successfully.')).toBeVisible();
  });

  test('should save notifications settings', async ({ page }) => {
    // Click the visible toggle div (the checkbox is sr-only, so force the click)
    await page.check('#notifications-field input[name="enabled"]', { force: true });
    await page.fill('#notifications-field input[name="ntfy_topic"]', 'superseded-e2e');

    await page.click('#notifications-field button:has-text("Save Notifications")');
    await page.waitForTimeout(1500);

    await expect(page.locator('text=Saved!')).toBeVisible();
  });

  test('should save server settings via API', async ({ page }) => {
    await page.goto('/settings');
    await page.waitForTimeout(500);
    const csrfToken = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const response = await page.request.post('/settings/server', {
      form: { host: '127.0.0.1', port: '8000', csrf_token: csrfToken },
    });
    expect(response.status()).toBe(200);
    const html = await response.text();
    expect(html).toContain('Server settings saved');
  });

  test('should show validation error for relative source root via API', async ({ page }) => {
    await page.goto('/settings');
    const csrfToken = await page.locator('meta[name="csrf-token"]').getAttribute('content');
    const response = await page.request.post('/settings/source-root', {
      form: { source_code_root: 'relative/path', csrf_token: csrfToken },
    });
    expect(response.status()).toBe(400);
    const html = await response.text();
    expect(html).toContain('Path must be absolute');
  });
});

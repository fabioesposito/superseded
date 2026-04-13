import { test, expect } from '@playwright/test';

test.describe('Settings Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/settings');
  });

  test('should display settings page with title', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('Settings');
    await expect(page.locator('text=Manage repositories and pipeline agents')).toBeVisible();
  });

  test('should display Add Repo button', async ({ page }) => {
    await expect(page.locator('button:has-text("Add Repo")')).toBeVisible();
  });

  test('should show add repository form when clicking Add Repo button', async ({ page }) => {
    await page.click('button:has-text("Add Repo")');
    await expect(page.locator('text=Add Repository')).toBeVisible();
    await expect(page.locator('input[name="name"]')).toBeVisible();
    await expect(page.locator('input[name="git_url"]')).toBeVisible();
    await expect(page.locator('input[name="path"]')).toBeVisible();
    await expect(page.locator('input[name="branch"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]:has-text("Save Repo")')).toBeVisible();
    await expect(page.locator('button:has-text("Cancel")')).toBeVisible();
  });

  test('should display Repositories section', async ({ page }) => {
    await expect(page.locator('h2:has-text("Repositories")')).toBeVisible();
  });

  test('should display Pipeline Agents section', async ({ page }) => {
    await expect(page.locator('h2:has-text("Pipeline Agents")')).toBeVisible();
    await expect(page.locator('text=Configure which CLI and model each pipeline stage uses')).toBeVisible();
  });

  test('should have agent configuration form with all stages', async ({ page }) => {
    const stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];
    for (const stage of stages) {
      await expect(page.locator(`select[name="${stage}_cli"]`)).toBeVisible();
      await expect(page.locator(`input[name="${stage}_model"]`)).toBeVisible();
    }
  });

  test('should have opencode as default CLI for all stages', async ({ page }) => {
    const stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];
    for (const stage of stages) {
      await expect(page.locator(`select[name="${stage}_cli"]`)).toHaveValue('opencode');
    }
  });

  test('should have opencode-go/kimi-k2.5 as default model for all stages', async ({ page }) => {
    const stages = ['spec', 'plan', 'build', 'verify', 'review', 'ship'];
    for (const stage of stages) {
      await expect(page.locator(`input[name="${stage}_model"]`)).toHaveValue('opencode-go/kimi-k2.5');
    }
  });

  test('cancel button should hide the add repository form', async ({ page }) => {
    await page.click('button:has-text("Add Repo")');
    await expect(page.locator('text=Add Repository')).toBeVisible();
    await page.click('button:has-text("Cancel")');
    await expect(page.locator('text=Add Repository')).not.toBeVisible();
  });

  test('should validate required fields in add repo form', async ({ page }) => {
    await page.click('button:has-text("Add Repo")');
    const nameInput = page.locator('input[name="name"]');
    const pathInput = page.locator('input[name="path"]');
    await expect(nameInput).toHaveAttribute('required', '');
    await expect(pathInput).toHaveAttribute('required', '');
  });

  test('page should have proper styling classes', async ({ page }) => {
    const mainContent = page.locator('text=Manage repositories and pipeline agents').locator('..').locator('..');
    await expect(page.locator('h1')).toHaveClass(/text-3xl/);
    await expect(page.locator('h1')).toHaveClass(/font-bold/);
  });

  test('should save agent configuration changes', async ({ page }) => {
    // Change the spec stage CLI to opencode
    await page.selectOption('select[name="spec_cli"]', 'opencode');
    await page.fill('input[name="spec_model"]', 'test-model-123');

    // Click save
    await page.click('button[type="submit"]:has-text("Save Agents")');

    // Wait for the form to be replaced (HTMX swap)
    await page.waitForTimeout(500);

    // Verify the values persist after save
    await expect(page.locator('select[name="spec_cli"]')).toHaveValue('opencode');
    await expect(page.locator('input[name="spec_model"]')).toHaveValue('test-model-123');
  });

  test('should save agent config for all pipeline stages', async ({ page }) => {
    // Update multiple stages
    await page.selectOption('select[name="spec_cli"]', 'opencode');
    await page.fill('input[name="spec_model"]', 'claude-3-opus');
    await page.selectOption('select[name="build_cli"]', 'codex');
    await page.fill('input[name="build_model"]', 'gpt-4');
    await page.selectOption('select[name="ship_cli"]', 'opencode');

    // Save
    await page.click('button[type="submit"]:has-text("Save Agents")');
    await page.waitForTimeout(500);

    // Verify all changes persisted
    await expect(page.locator('select[name="spec_cli"]')).toHaveValue('opencode');
    await expect(page.locator('input[name="spec_model"]')).toHaveValue('claude-3-opus');
    await expect(page.locator('select[name="build_cli"]')).toHaveValue('codex');
    await expect(page.locator('input[name="build_model"]')).toHaveValue('gpt-4');
    await expect(page.locator('select[name="ship_cli"]')).toHaveValue('opencode');
  });
});

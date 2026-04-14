import { test, expect } from '@playwright/test';

test.describe('New Issue Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/issues/new');
  });

  test('should display new issue form', async ({ page }) => {
    await expect(page.locator('h1')).toContainText('New Issue');
    await expect(page.locator('input[name="title"]')).toBeVisible();
    await expect(page.locator('textarea[name="body"]')).toBeVisible();
    await expect(page.locator('button[type="submit"]:has-text("Create Issue")')).toBeVisible();
  });

  test('should have CSRF token in form', async ({ page }) => {
    const csrfInput = page.locator('#form-container input[name="csrf_token"]');
    // Hidden inputs are attached but not visible
    await expect(csrfInput).toBeAttached();
    await expect(csrfInput).toHaveAttribute('type', 'hidden');
    const tokenValue = await csrfInput.inputValue();
    expect(tokenValue).toBeTruthy();
    expect(tokenValue.length).toBeGreaterThan(20);
  });

  test('should create a new issue successfully', async ({ page }) => {
    // First, visit the page to get a fresh CSRF cookie
    await page.goto('/issues/new');
    
    // Verify CSRF token is present
    const csrfToken = await page.locator('input[name="csrf_token"]').inputValue();
    expect(csrfToken).toBeTruthy();
    
    // Fill in the form
    await page.fill('input[name="title"]', 'Test Issue Created by Playwright');
    await page.fill('textarea[name="body"]', 'This is a test issue body created during automated testing.');
    await page.fill('input[name="labels"]', 'test, automation');

    // Submit the form
    await page.click('button[type="submit"]:has-text("Create Issue")');
    
    // Wait for navigation (allow time for the server to process)
    await page.waitForTimeout(2000);
    
    // Check if there's an error
    const errorElement = page.locator('.bg-red-900\\/30');
    if (await errorElement.isVisible().catch(() => false)) {
      const errorText = await errorElement.textContent();
      throw new Error(`Form submission failed with error: ${errorText}`);
    }

    // Should redirect to the issue detail page
    await expect(page).toHaveURL(/\/issues\/SUP-\d+/, { timeout: 5000 });

    // Verify we're on the issue page
    await expect(page.locator('h1')).toContainText('Test Issue Created by Playwright');
  });

  test('should validate required title field', async ({ page }) => {
    const titleInput = page.locator('input[name="title"]');
    await expect(titleInput).toHaveAttribute('required', '');
  });

  test('should have import from GitHub section', async ({ page }) => {
    await expect(page.locator('text=Import from GitHub')).toBeVisible();
    // The github_url input is outside the form but associated via form="import-form"
    await expect(page.locator('input[form="import-form"][name="github_url"]')).toBeVisible();
    await expect(page.locator('button:has-text("Import")')).toBeVisible();
  });

  test('should have assignee dropdown with opencode and claude-code', async ({ page }) => {
    const assigneeSelect = page.locator('select[name="assignee"]');
    await expect(assigneeSelect).toBeVisible();
    await expect(assigneeSelect.locator('option[value=""]')).toHaveText('auto');
    // Options exist but aren't "visible" as separate elements
    await expect(assigneeSelect.locator('option[value="claude-code"]')).toBeAttached();
    await expect(assigneeSelect.locator('option[value="opencode"]')).toBeAttached();
  });

  test('should cancel and return to dashboard', async ({ page }) => {
    await page.click('a:has-text("Cancel")');
    await expect(page).toHaveURL('/', { timeout: 5000 });
  });
});

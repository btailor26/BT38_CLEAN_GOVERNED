import { test, expect } from '@playwright/test';

test('BT38 login page loads', async ({ page }) => {
  await page.goto('/login');

  await expect(page).toHaveTitle(/BT38|Inventory|Login/i);

  await expect(
    page.locator('input[type="password"]')
  ).toBeVisible();
});

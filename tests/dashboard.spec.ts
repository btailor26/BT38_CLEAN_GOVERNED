import { test, expect } from '@playwright/test';

test('protected dashboard redirects unauthenticated user to login', async ({ page }) => {
  await page.goto('/dashboard');

  await expect(page).toHaveURL(/login/);
});

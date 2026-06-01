import { test, expect } from '@playwright/test';

test.describe('Authentication flows', () => {

  test('successful login redirects to home', async ({ page }) => {
    const apiBase = 'http://localhost:8000/api/v1';

    await page.request.post(`${apiBase}/auth/register`, {
      data: {
        name: 'E2E Login User',
        email: 'e2e-login@test.com',
        password: 'secret123',
        role: 'client',
      },
    }).catch(() => {});

    await page.goto('http://localhost:5173/login');
    await expect(page.locator('h1')).toHaveText('Login');

    await page.fill('input[id="email"]', 'e2e-login@test.com');
    await page.fill('input[id="password"]', 'secret123');
    await page.click('button[type="submit"]');

    await expect(page).toHaveURL('http://localhost:5173/');
    await expect(page.locator('h1')).toHaveText('Welcome');
    await expect(page.getByText('Hello,').first()).toBeVisible();
    await expect(page.locator('text=Logout')).toBeVisible();
  });

  test('failed login shows error message', async ({ page }) => {
    await page.goto('http://localhost:5173/login');
    await expect(page.locator('h1')).toHaveText('Login');

    await page.fill('input[id="email"]', 'no-such-user@test.com');
    await page.fill('input[id="password"]', 'wrongpass');
    await page.click('button[type="submit"]');

    await expect(page.locator('[role="alert"]')).toBeVisible();
    await expect(page).toHaveURL(/\/login/);
  });

  test('logout clears session and redirects to login', async ({ page }) => {
    const apiBase = 'http://localhost:8000/api/v1';

    await page.request.post(`${apiBase}/auth/register`, {
      data: {
        name: 'E2E Logout User',
        email: 'e2e-logout@test.com',
        password: 'secret123',
        role: 'client',
      },
    }).catch(() => {});

    await page.goto('http://localhost:5173/login');
    await page.fill('input[id="email"]', 'e2e-logout@test.com');
    await page.fill('input[id="password"]', 'secret123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('http://localhost:5173/');

    await page.click('button:has-text("Logout")');
    await expect(page).toHaveURL(/\/login/);

    await page.goto('http://localhost:5173/');
    await expect(page).toHaveURL(/\/login/);
  });

});

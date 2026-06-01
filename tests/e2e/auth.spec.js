// @ts-check
const { test, expect } = require('@playwright/test');

test.describe('Authentication flows', () => {

  test('successful login redirects to home', async ({ page }) => {
    // Ensure a test user exists via the backend
    const apiBase = 'http://localhost:8000/api/v1';

    // Register a fresh user (ignore 409 if already exists)
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

    // Should redirect to home
    await expect(page).toHaveURL('http://localhost:5173/');
    await expect(page.locator('h1')).toHaveText('Welcome');
    await expect(page.locator('text=Hello,')).toBeVisible();

    // NavBar should show logout
    await expect(page.locator('text=Logout')).toBeVisible();
  });

  test('failed login shows error message', async ({ page }) => {
    await page.goto('http://localhost:5173/login');
    await expect(page.locator('h1')).toHaveText('Login');

    await page.fill('input[id="email"]', 'no-such-user@test.com');
    await page.fill('input[id="password"]', 'wrongpass');
    await page.click('button[type="submit"]');

    // Should show error message
    await expect(page.locator('[role="alert"]')).toBeVisible();
    // Should still be on login page
    await expect(page).toHaveURL(/\/login/);
  });

  test('logout clears session and redirects to login', async ({ page }) => {
    const apiBase = 'http://localhost:8000/api/v1';

    // Ensure user exists
    await page.request.post(`${apiBase}/auth/register`, {
      data: {
        name: 'E2E Logout User',
        email: 'e2e-logout@test.com',
        password: 'secret123',
        role: 'client',
      },
    }).catch(() => {});

    // Login
    await page.goto('http://localhost:5173/login');
    await page.fill('input[id="email"]', 'e2e-logout@test.com');
    await page.fill('input[id="password"]', 'secret123');
    await page.click('button[type="submit"]');
    await expect(page).toHaveURL('http://localhost:5173/');

    // Logout
    await page.click('button:has-text("Logout")');
    await expect(page).toHaveURL(/\/login/);

    // Should not be able to access home
    await page.goto('http://localhost:5173/');
    await expect(page).toHaveURL(/\/login/);
  });

});

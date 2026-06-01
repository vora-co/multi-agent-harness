import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000/api/v1';
const APP = 'http://localhost:5173';

async function getToken(page, email, password = 'secret123') {
  const res = await page.request.post(`${API}/auth/login`, { data: { email, password } });
  return (await res.json()).access_token;
}
async function getMe(page, token) {
  const res = await page.request.get(`${API}/auth/me`, { headers: { Authorization: `Bearer ${token}` } });
  return res.json();
}
async function loginViaUI(page, email, password = 'secret123') {
  await page.goto(`${APP}/login`);
  await page.fill('input[id="email"]', email);
  await page.fill('input[id="password"]', password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(`${APP}/`);
}

test.describe('Admin panel', () => {

  test('cliente no puede acceder a /admin — redirige', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Client NoAdmin', email: 'client-noadmin@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    await loginViaUI(page, 'client-noadmin@test.com');
    await page.goto(`${APP}/admin/sessions`);
    await expect(page).not.toHaveURL(/\/admin/);
  });

  test('admin ve enlace Admin en NavBar', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Nav', email: 'admin-nav@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    await loginViaUI(page, 'admin-nav@test.com');
    await expect(page.getByRole('link', { name: 'Admin' })).toBeVisible();
  });

  test('cliente NO ve enlace Admin en NavBar', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Client Nav', email: 'client-nav@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    await loginViaUI(page, 'client-nav@test.com');
    await expect(page.locator('a[href*="admin"]')).not.toBeVisible();
  });

  test('admin puede crear una sesión desde el panel', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Create', email: 'admin-create@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    await loginViaUI(page, 'admin-create@test.com');
    await page.goto(`${APP}/admin/sessions`);

    // Abrir modal de nueva sesión
    await page.getByRole('button', { name: 'Nueva sesión' }).click();
    await expect(page.locator('text=Nueva sesión').last()).toBeVisible();

    // Rellenar formulario usando atributo name
    await page.locator('input[name="title"]').fill('Sesión Admin Test');
    await page.locator('input[name="instructor"]').fill('Roberto');
    await page.locator('input[name="style"]').fill('Yin');
    await page.locator('input[name="duration_minutes"]').fill('45');
    await page.locator('input[name="capacity"]').fill('10');

    // Fecha futura — datetime-local usa YYYY-MM-DDTHH:MM
    const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 16);
    await page.locator('input[name="starts_at"]').fill(tomorrow);

    await page.getByRole('button', { name: 'Crear sesión' }).click();

    await expect(page.locator('text=Sesión Admin Test')).toBeVisible({ timeout: 5000 });
  });

  test('admin puede agregar créditos a un usuario', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Credits', email: 'admin-credits@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Target User', email: 'target-user@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});

    await loginViaUI(page, 'admin-credits@test.com');
    await page.goto(`${APP}/admin/users`);

    await expect(page.locator('text=target-user@test.com')).toBeVisible();

    // Click en agregar créditos para ese usuario
    const row = page.locator('tr', { hasText: 'target-user@test.com' });
    await row.getByRole('button', { name: 'Agregar créditos' }).click();

    await expect(page.locator('text=Agregar créditos').last()).toBeVisible();
    await page.locator('input[type="number"]').fill('5');
    await page.getByRole('button', { name: 'Agregar', exact: true }).click();

    await expect(page.getByText(/Se agregaron .* créditos/)).toBeVisible({ timeout: 5000 });
  });

});

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
async function addCredits(page, adminToken, userId, amount = 5) {
  await page.request.post(`${API}/users/${userId}/credits`, {
    data: { amount, reason: 'E2E credits' },
    headers: { Authorization: `Bearer ${adminToken}` },
  });
}
function isoFuture(daysAhead = 1) {
  const d = new Date(Date.now() + daysAhead * 86400000);
  return d.toISOString().split('.')[0];
}

async function createSession(page, adminToken, overrides = {}) {
  const res = await page.request.post(`${API}/sessions`, {
    data: {
      title: 'Test Session',
      instructor: 'Ana',
      style: 'Hatha',
      starts_at: isoFuture(),
      duration_minutes: 60,
      capacity: 5,
      ...overrides,
    },
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  return res.json();
}
async function loginViaUI(page, email, password = 'secret123') {
  await page.goto(`${APP}/login`);
  await page.fill('input[id="email"]', email);
  await page.fill('input[id="password"]', password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(`${APP}/`);
}

test.describe('My Bookings page', () => {

  test('muestra reservas del usuario', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin MB', email: 'admin-mb@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-mb@test.com');
    const session = await createSession(page, adminToken, { title: 'Sesión de Felipe' });

    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Client MB', email: 'client-mb@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const clientToken = await getToken(page, 'client-mb@test.com');
    const me = await getMe(page, clientToken);
    await addCredits(page, adminToken, me.id, 3);
    await page.request.post(`${API}/bookings`, {
      data: { session_id: session.id },
      headers: { Authorization: `Bearer ${clientToken}` },
    });

    await loginViaUI(page, 'client-mb@test.com');
    await page.goto(`${APP}/my-bookings`);

    await expect(page.locator('text=Sesión de Felipe')).toBeVisible({ timeout: 8000 });
    await expect(page.locator('text=confirmed').or(page.locator('text=Confirmada'))).toBeVisible();
  });

  test('cancelar una reserva confirmed con modal de confirmación', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Cancel', email: 'admin-cancel@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-cancel@test.com');
    const session = await createSession(page, adminToken, { title: 'Sesión Cancelable' });

    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Client Cancel', email: 'client-cancel@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const clientToken = await getToken(page, 'client-cancel@test.com');
    const me = await getMe(page, clientToken);
    await addCredits(page, adminToken, me.id, 3);
    await page.request.post(`${API}/bookings`, {
      data: { session_id: session.id },
      headers: { Authorization: `Bearer ${clientToken}` },
    });

    await loginViaUI(page, 'client-cancel@test.com');
    await page.goto(`${APP}/my-bookings`);

    // Click cancelar
    await page.locator('button', { hasText: /[Cc]ancelar/ }).first().click();

    // Modal de confirmación visible
    await expect(page.getByRole('heading', { name: 'Confirmar Cancelación' })).toBeVisible();

    // Confirmar cancelación
    await page.getByRole('button', { name: 'Confirmar Cancelar' }).click();

    await expect(page.locator('text=cancelled').or(page.locator('text=Cancelada'))).toBeVisible({ timeout: 5000 });
  });

  test('sesión llena aparece como waitlist en mis reservas', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin WL', email: 'admin-wl@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-wl@test.com');
    const session = await createSession(page, adminToken, { title: 'Sesión Llena WL', capacity: 1 });

    // Llenar la sesión
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Filler WL', email: 'filler-wl@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const fillerToken = await getToken(page, 'filler-wl@test.com');
    const filler = await getMe(page, fillerToken);
    await addCredits(page, adminToken, filler.id, 2);
    await page.request.post(`${API}/bookings`, {
      data: { session_id: session.id },
      headers: { Authorization: `Bearer ${fillerToken}` },
    });

    // Cliente va a waitlist
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Waitlist Client', email: 'waitlist-client@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const waitlistToken = await getToken(page, 'waitlist-client@test.com');
    const wlMe = await getMe(page, waitlistToken);
    await addCredits(page, adminToken, wlMe.id, 2);
    await page.request.post(`${API}/bookings`, {
      data: { session_id: session.id },
      headers: { Authorization: `Bearer ${waitlistToken}` },
    });

    await loginViaUI(page, 'waitlist-client@test.com');
    await page.goto(`${APP}/my-bookings`);

    await expect(page.locator('text=waitlist').or(page.locator('text=Lista de espera'))).toBeVisible();
  });

});

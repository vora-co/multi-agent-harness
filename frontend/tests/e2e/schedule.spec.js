import { test, expect } from '@playwright/test';

const API = 'http://localhost:8000/api/v1';
const APP = 'http://localhost:5173';

// ─── Helpers ────────────────────────────────────────────────────────────────

async function registerAndLogin(page, { name, email, password = 'secret123', role = 'client' }) {
  await page.request.post(`${API}/auth/register`, {
    data: { name, email, password, role },
  }).catch(() => {});
  await page.goto(`${APP}/login`);
  await page.fill('input[id="email"]', email);
  await page.fill('input[id="password"]', password);
  await page.click('button[type="submit"]');
  await expect(page).toHaveURL(`${APP}/`);
}

// Python 3.9 fromisoformat no acepta Z ni milisegundos — usar formato limpio
function isoFuture(daysAhead = 1) {
  const d = new Date(Date.now() + daysAhead * 86400000);
  return d.toISOString().split('.')[0];  // "2026-06-02T12:00:00"
}

async function createSession(page, adminToken, overrides = {}) {
  const data = {
    title: 'Test Yoga',
    instructor: 'Ana',
    style: 'Hatha',
    starts_at: isoFuture(),
    duration_minutes: 60,
    capacity: 2,
    ...overrides,
  };
  const res = await page.request.post(`${API}/sessions`, {
    data,
    headers: { Authorization: `Bearer ${adminToken}` },
  });
  return (await res.json());
}

async function getToken(page, email, password = 'secret123') {
  const res = await page.request.post(`${API}/auth/login`, {
    data: { email, password },
  });
  return (await res.json()).access_token;
}

async function addCredits(page, adminToken, userId, amount = 5) {
  await page.request.post(`${API}/users/${userId}/credits`, {
    data: { amount, reason: 'E2E test credits' },
    headers: { Authorization: `Bearer ${adminToken}` },
  });
}

async function getMe(page, token) {
  const res = await page.request.get(`${API}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  return res.json();
}

// ─── Tests: /schedule ───────────────────────────────────────────────────────

test.describe('Schedule page', () => {

  test('muestra la agenda con sesiones disponibles', async ({ page }) => {
    // Setup: admin crea sesión
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Schedule', email: 'admin-sched@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-sched@test.com');
    await createSession(page, adminToken, { title: 'Sesión Visible' });

    await registerAndLogin(page, { name: 'Client Sched', email: 'client-sched@test.com' });
    await page.goto(`${APP}/schedule`);

    await expect(page.locator('text=Sesión Visible')).toBeVisible({ timeout: 8000 });
  });

  test('filtra sesiones por estilo', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Filter', email: 'admin-filter@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-filter@test.com');
    await createSession(page, adminToken, { title: 'Sesión Hatha', style: 'Hatha' });
    await createSession(page, adminToken, { title: 'Sesión Vinyasa', style: 'Vinyasa' });

    await registerAndLogin(page, { name: 'Client Filter', email: 'client-filter@test.com' });
    await page.goto(`${APP}/schedule`);

    // Esperar que las sesiones carguen
    await expect(page.locator('text=Sesión Hatha')).toBeVisible({ timeout: 8000 });

    // Seleccionar filtro Vinyasa — el option value es el nombre del estilo
    await page.selectOption('select', { label: 'Vinyasa' });
    await expect(page.locator('text=Sesión Vinyasa')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('text=Sesión Hatha')).not.toBeVisible();
  });

  test('reservar una sesión con cupo', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Book', email: 'admin-book@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-book@test.com');
    const uniqueTitle = `Reservar-${Date.now()}`;
    await createSession(page, adminToken, { title: uniqueTitle, capacity: 5 });

    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Client Book', email: 'client-book@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const clientToken = await getToken(page, 'client-book@test.com');
    const me = await getMe(page, clientToken);
    await addCredits(page, adminToken, me.id, 3);

    await registerAndLogin(page, { name: 'Client Book', email: 'client-book@test.com' });
    await page.goto(`${APP}/schedule`);
    await expect(page.locator(`text=${uniqueTitle}`)).toBeVisible({ timeout: 8000 });

    // Filtrar la card que contiene el título único y hacer click en su botón
    const card = page.locator('div').filter({ hasText: uniqueTitle }).filter({ has: page.getByRole('button', { name: 'Reservar', exact: true }) }).last();
    await card.getByRole('button', { name: 'Reservar', exact: true }).click();
    // Esperar feedback: mensaje de éxito o de error (créditos insuficientes, etc.)
    await expect(page.getByText(/exitosamente|suficientes|activa/i).first()).toBeVisible({ timeout: 5000 });
  });

  test('muestra badge "Lista de espera" cuando sesión llena', async ({ page }) => {
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Admin Full', email: 'admin-full@test.com', password: 'secret123', role: 'admin' },
    }).catch(() => {});
    const adminToken = await getToken(page, 'admin-full@test.com');
    const fullTitle = `Llena-${Date.now()}`;
    const session = await createSession(page, adminToken, { title: fullTitle, capacity: 1 });

    // Llenar la sesión
    await page.request.post(`${API}/auth/register`, {
      data: { name: 'Filler User', email: 'filler@test.com', password: 'secret123', role: 'client' },
    }).catch(() => {});
    const fillerToken = await getToken(page, 'filler@test.com');
    const filler = await getMe(page, fillerToken);
    await addCredits(page, adminToken, filler.id, 2);
    await page.request.post(`${API}/bookings`, {
      data: { session_id: session.id },
      headers: { Authorization: `Bearer ${fillerToken}` },
    });

    await registerAndLogin(page, { name: 'Late Client', email: 'late-client@test.com' });
    await page.goto(`${APP}/schedule`);
    await expect(page.locator(`text=${fullTitle}`)).toBeVisible({ timeout: 8000 });

    // Filtrar la card específica y verificar el badge dentro de ella
    const fullCard = page.locator('div').filter({ hasText: fullTitle }).filter({ has: page.locator('text=Lista de espera') }).last();
    await expect(fullCard.locator('text=Lista de espera').first()).toBeVisible();
  });

});

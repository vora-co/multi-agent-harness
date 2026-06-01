/**
 * Thin wrapper around fetch() that prepends /api/v1 and injects auth header.
 * Returns { ok, status, data } — never throws.
 */

const BASE = '/api/v1';

export async function apiFetch(path, options = {}) {
  const token = localStorage.getItem('token');
  const headers = {
    'Content-Type': 'application/json',
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(options.headers || {}),
  };

  const res = await fetch(`${BASE}${path}`, { ...options, headers });
  let data = null;
  try {
    data = await res.json();
  } catch (_) {
    /* response may not be JSON */
  }
  return { ok: res.ok, status: res.status, data };
}

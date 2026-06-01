/**
 * Auth API functions using apiFetch from client.js
 */
import { apiFetch } from './client';

export function loginUser(email, password) {
  return apiFetch('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
}

export function registerUser(name, email, password, role = 'client') {
  return apiFetch('/auth/register', {
    method: 'POST',
    body: JSON.stringify({ name, email, password, role }),
  });
}

export function getMe() {
  return apiFetch('/auth/me');
}

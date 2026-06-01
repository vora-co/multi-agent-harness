/**
 * Admin API functions using apiFetch from client.js
 */
import { apiFetch } from './client';

export function adminGetSessions() {
  return apiFetch('/sessions');
}

export function adminCreateSession(data) {
  return apiFetch('/sessions', {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function adminUpdateSession(id, data) {
  return apiFetch(`/sessions/${id}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export function adminDeleteSession(id) {
  return apiFetch(`/sessions/${id}`, {
    method: 'DELETE',
  });
}

export function adminGetUsers() {
  return apiFetch('/users');
}

export function adminAddCredits(userId, amount) {
  return apiFetch(`/users/${userId}/credits`, {
    method: 'PUT',
    body: JSON.stringify({ credits: amount }),
  });
}

export function adminGetAttendees(sessionId) {
  return apiFetch(`/sessions/${sessionId}/attendees`);
}

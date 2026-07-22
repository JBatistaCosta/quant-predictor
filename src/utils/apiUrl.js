// src/utils/apiUrl.js
// Front-end (Firebase Hosting) e as funções em api/*.js (Vercel) agora vivem
// em domínios diferentes — todo fetch pra /api/... precisa virar URL absoluta
// pro domínio do Vercel, senão bate no próprio Firebase Hosting (404).
// Em dev (vite dev) e no build antigo do Vercel, VITE_API_BASE_URL fica vazia
// e o fetch continua relativo, como sempre foi.
const API_BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '');

export function apiUrl(path) {
  return `${API_BASE}${path}`;
}

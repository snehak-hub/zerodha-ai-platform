const TOKEN_KEY = 'zk_token';
const USER_KEY = 'zk_user';

export type AuthUser = { id: number; email: string; full_name: string };

export function getToken(): string | null {
  if (typeof window === 'undefined') return null;
  try { return window.localStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function getStoredUser(): AuthUser | null {
  if (typeof window === 'undefined') return null;
  try { return JSON.parse(window.localStorage.getItem(USER_KEY) || 'null'); } catch { return null; }
}

export function saveSession(token: string, user: AuthUser) {
  window.localStorage.setItem(TOKEN_KEY, token);
  window.localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  try {
    window.localStorage.removeItem(TOKEN_KEY);
    window.localStorage.removeItem(USER_KEY);
  } catch {}
}

export function logout() {
  clearSession();
  window.location.href = '/login';
}

/** fetch() that sends the JWT and sends the user to /login when the session is missing or expired. */
export async function authFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers || {});
  const token = getToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(url, { ...init, headers });
  if (res.status === 401 && typeof window !== 'undefined' && !window.location.pathname.startsWith('/login')) {
    clearSession();
    window.location.href = '/login';
  }
  return res;
}

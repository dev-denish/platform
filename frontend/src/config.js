/**
 * Central API configuration.
 *
 * Existing implementation (MVP): `const API_BASE = "http://localhost:8001"` was
 * hardcoded and duplicated across four components, so the build only ever worked
 * against a developer's laptop and changing it meant editing four files.
 *
 * Enterprise solution: one module reads the base URL from a build-time Vite env var
 * (VITE_API_BASE), defaulting to the same-origin "/api/v1" path that nginx/ingress
 * proxies to the backend. Every component imports from here.
 */
export const API_BASE = import.meta.env.VITE_API_BASE ?? "/api/v1";

const ACCESS_KEY = "dmrv.access_token";
const REFRESH_KEY = "dmrv.refresh_token";

export function getToken() {
  return sessionStorage.getItem(ACCESS_KEY);
}

export function setToken(token) {
  sessionStorage.setItem(ACCESS_KEY, token);
}

export function getRefreshToken() {
  return sessionStorage.getItem(REFRESH_KEY);
}

/** Store an {access_token, refresh_token} pair returned by /auth/login or /auth/refresh. */
export function setTokens({ access_token, refresh_token }) {
  sessionStorage.setItem(ACCESS_KEY, access_token);
  if (refresh_token) sessionStorage.setItem(REFRESH_KEY, refresh_token);
}

export function clearToken() {
  sessionStorage.removeItem(ACCESS_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
}

/**
 * NOTE (security): the MVP stored the JWT in localStorage, which is readable by any
 * XSS. sessionStorage is a marginal improvement; the planned end state is an
 * httpOnly, Secure, SameSite cookie set by the API so JS never touches the token
 * (tracked in the security workstream). This wrapper centralises that future change.
 */

let authExpiredListener = null;
/** AuthContext registers a callback here to react (e.g. redirect to /login) when
 * the refresh token itself is rejected or absent - the session is genuinely over. */
export function onAuthExpired(callback) {
  authExpiredListener = callback;
}

// Only one refresh should ever be in flight; concurrent 401s await the same promise
// instead of racing the backend's refresh-token rotation.
let refreshInFlight = null;

async function refreshAccessToken() {
  const refreshToken = getRefreshToken();
  if (!refreshToken) throw new Error("No refresh token available.");

  if (!refreshInFlight) {
    refreshInFlight = fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("Refresh token rejected.");
        return res.json();
      })
      .finally(() => {
        refreshInFlight = null;
      });
  }
  const pair = await refreshInFlight;
  setTokens(pair);
  return pair.access_token;
}

/**
 * Thin fetch wrapper that attaches the bearer token, transparently retries once
 * on a 401 by rotating the access token via the refresh token, and normalises the
 * API's `{ error: { code, message } }` envelope into thrown Errors.
 */
export async function apiFetch(path, options = {}) {
  const doFetch = async (token) => {
    const headers = new Headers(options.headers || {});
    if (token) headers.set("Authorization", `Bearer ${token}`);
    if (!(options.body instanceof FormData)) {
      headers.set("Content-Type", "application/json");
    }
    return fetch(`${API_BASE}${path}`, { ...options, headers });
  };

  let res = await doFetch(getToken());

  if (res.status === 401 && getRefreshToken()) {
    try {
      const newToken = await refreshAccessToken();
      res = await doFetch(newToken);
    } catch {
      clearToken();
      authExpiredListener?.();
      throw new Error("Your session has expired. Please sign in again.");
    }
  } else if (res.status === 401) {
    clearToken();
    authExpiredListener?.();
  }

  if (!res.ok) {
    let message = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      message = body?.error?.message ?? message;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(message);
  }
  return res.status === 204 ? null : res.json();
}

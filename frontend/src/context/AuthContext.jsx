import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { apiFetch, clearToken, getRefreshToken, getToken, onAuthExpired, setTokens } from "../config.js";

const AuthContext = createContext(null);

// "loading": checking for an existing session; "authenticated" / "anonymous" once resolved.
export function AuthProvider({ children }) {
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState("loading");

  const logout = useCallback(async () => {
    // Best-effort: revoke the session server-side (see POST /auth/logout) so the
    // access token can't be replayed for the rest of its TTL. Local state is
    // cleared either way - a network hiccup here must not leave the user stuck
    // looking "logged in" in the UI.
    try {
      await apiFetch("/auth/logout", {
        method: "POST",
        body: JSON.stringify({ refresh_token: getRefreshToken() }),
      });
    } catch {
      /* revoked or not, we're clearing the local session below regardless */
    }
    clearToken();
    setUser(null);
    setStatus("anonymous");
  }, []);

  useEffect(() => {
    onAuthExpired(() => {
      setUser(null);
      setStatus("anonymous");
    });
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function restore() {
      if (!getToken()) {
        setStatus("anonymous");
        return;
      }
      try {
        const me = await apiFetch("/auth/me");
        if (!cancelled) {
          setUser(me);
          setStatus("authenticated");
        }
      } catch {
        if (!cancelled) {
          clearToken();
          setStatus("anonymous");
        }
      }
    }
    restore();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (username, password) => {
    const pair = await apiFetch("/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setTokens(pair);
    const me = await apiFetch("/auth/me");
    setUser(me);
    setStatus("authenticated");
    return me;
  }, []);

  const value = useMemo(() => ({ user, status, login, logout }), [user, status, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within an AuthProvider");
  return ctx;
}

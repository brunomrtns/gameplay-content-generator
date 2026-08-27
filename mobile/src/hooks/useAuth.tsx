import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { getToken, getUser, clearAuth } from '../api/client';
import { authApi, dashboardApi } from '../api/endpoints';

interface AuthUser {
  id: number;
  email: string;
  name?: string;
  is_admin: boolean;
  is_active: boolean;
  has_youtube: boolean;
  channel_title?: string;
  channel_domain?: string;
}

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  isAuthenticated: boolean;
  refresh: () => Promise<void>;
  logout: () => Promise<void>;
  setAuthenticated: (user: AuthUser) => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    const token = await getToken();
    if (!token) {
      setUser(null);
      setLoading(false);
      return;
    }
    try {
      const me = await authApi.getMe();
      // Fetch channel domain from dashboard
      try {
        const dash = await dashboardApi.get();
        me.channel_domain = dash.channel_domain || 'games';
      } catch {}
      setUser(me);
    } catch {
      setUser(null);
    } finally {
      setLoading(false);
    }
  }, []);

  const logout = useCallback(async () => {
    await authApi.logout();
    setUser(null);
  }, []);

  const setAuthenticated = useCallback((u: AuthUser) => {
    setUser(u);
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return (
    <AuthContext.Provider
      value={{
        user,
        loading,
        isAuthenticated: user !== null,
        refresh,
        logout,
        setAuthenticated,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

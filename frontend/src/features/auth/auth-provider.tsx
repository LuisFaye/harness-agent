import { type PropsWithChildren, createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import {
  type AuthUser,
  bootstrapOwner as bootstrapOwnerRequest,
  getCurrentUser,
  getSetupStatus,
  login as loginRequest,
  logout as logoutRequest,
  registerWithInviteCode as registerRequest,
} from "./auth-api";

interface AuthContextValue {
  user: AuthUser | null;
  loading: boolean;
  setupRequired: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (code: string, email: string, displayName: string, password: string) => Promise<void>;
  bootstrapOwner: (email: string, displayName: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: PropsWithChildren) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const response = await getCurrentUser();
      setUser(response.user);
      setSetupRequired(false);
    } catch {
      setUser(null);
      const status = await getSetupStatus().catch(() => ({ setup_required: false }));
      setSetupRequired(status.setup_required);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const handleAuthenticationRequired = () => setUser(null);
    window.addEventListener("sb-agent-auth-required", handleAuthenticationRequired);
    return () => window.removeEventListener("sb-agent-auth-required", handleAuthenticationRequired);
  }, [refresh]);

  const login = useCallback(async (email: string, password: string) => {
    const response = await loginRequest(email, password);
    setUser(response.user);
    setSetupRequired(false);
  }, []);

  const register = useCallback(async (code: string, email: string, displayName: string, password: string) => {
    const response = await registerRequest(code, email, displayName, password);
    setUser(response.user);
    setSetupRequired(false);
  }, []);

  const bootstrapOwner = useCallback(async (email: string, displayName: string, password: string) => {
    const response = await bootstrapOwnerRequest(email, displayName, password);
    setUser(response.user);
    setSetupRequired(false);
  }, []);

  const logout = useCallback(async () => {
    await logoutRequest().catch(() => undefined);
    setUser(null);
  }, []);

  const value = useMemo<AuthContextValue>(() => ({
    user,
    loading,
    setupRequired,
    login,
    register,
    bootstrapOwner,
    logout,
    refresh,
  }), [bootstrapOwner, loading, login, logout, refresh, register, setupRequired, user]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) {
    throw new Error("useAuth 必须在 AuthProvider 内使用");
  }
  return value;
}

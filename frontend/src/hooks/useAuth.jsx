/**
 * Auth context and hook — stores token in state + localStorage,
 * fetches /auth/me for user info.
 */
import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { loginUser, registerUser, getMe } from '../api/auth';

const AuthContext = createContext(null);

export function AuthProvider({ children }) {
  const [token, setToken] = useState(localStorage.getItem('token'));
  const [user, setUser] = useState(null);
  const [loading, setLoading] = useState(true);

  /* On mount: if a token exists, validate it by calling /auth/me */
  useEffect(() => {
    if (!token) {
      setLoading(false);
      return;
    }
    getMe().then(({ ok, data }) => {
      if (ok) {
        setUser(data);
      } else {
        localStorage.removeItem('token');
        setToken(null);
        setUser(null);
      }
    }).finally(() => setLoading(false));
  }, []);

  const login = useCallback(async (email, password) => {
    const { ok, data } = await loginUser(email, password);
    if (!ok) throw new Error(data?.detail || 'Login failed');
    localStorage.setItem('token', data.access_token);
    setToken(data.access_token);
    const me = await getMe();
    if (me.ok) setUser(me.data);
    return data;
  }, []);

  const register = useCallback(async (name, email, password, role = 'client') => {
    const { ok, data } = await registerUser(name, email, password, role);
    if (!ok) throw new Error(data?.detail || 'Registration failed');
    localStorage.setItem('token', data.access_token);
    setToken(data.access_token);
    const me = await getMe();
    if (me.ok) setUser(me.data);
    return data;
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('token');
    setToken(null);
    setUser(null);
  }, []);

  const isAuthenticated = !!token && !!user;

  return (
    <AuthContext.Provider value={{ token, user, login, register, logout, isAuthenticated, loading }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

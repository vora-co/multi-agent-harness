# Spec — Feature #10: Frontend React con autenticación

## Archivos a crear o modificar

| Archivo | Acción |
|---|---|
| `frontend/package.json` | Nuevo — configuración del proyecto Vite + React |
| `frontend/vite.config.js` | Nuevo — proxy `/api` → `http://localhost:8000` |
| `frontend/tailwind.config.js` | Nuevo — content paths para purging Tailwind |
| `frontend/postcss.config.js` | Nuevo — plugins Tailwind + Autoprefixer |
| `frontend/index.html` | Nuevo — entry point HTML |
| `frontend/src/index.css` | Nuevo — directivas Tailwind (`@tailwind base/components/utilities`) |
| `frontend/src/main.jsx` | Nuevo — mount point React con BrowserRouter |
| `frontend/src/App.jsx` | Nuevo — rutas, AuthProvider, NavBar siempre visible |
| `frontend/src/hooks/useAuth.jsx` | Nuevo — Context API para autenticación con `login()`, `register()`, `logout()` |
| `frontend/src/api/client.js` | Nuevo — wrapper fetch con auth header (`apiFetch`) |
| `frontend/src/api/auth.js` | Nuevo — `loginUser`, `registerUser`, `getMe` |
| `frontend/src/components/PrivateRoute.jsx` | Nuevo — guard de rutas protegidas con spinner de carga |
| `frontend/src/components/NavBar.jsx` | Nuevo — barra de navegación responsive con hamburger |
| `frontend/src/pages/LoginPage.jsx` | Nuevo — página de login con validación |
| `frontend/src/pages/RegisterPage.jsx` | Nuevo — página de registro con auto-login |
| `frontend/src/pages/Home.jsx` | Nuevo — página home post-login |
| `frontend/playwright.config.js` | Nuevo — configuración Playwright con webServer auto-start |
| `frontend/tests/e2e/auth.spec.js` | Nuevo — tests E2E: login exitoso, login fallido, logout |

---

## Implementación

### `frontend/package.json`

Crear desde cero con el siguiente contenido exacto:

```json
{
  "name": "frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview",
    "test:e2e": "npx playwright test"
  },
  "dependencies": {
    "react": "^18.2.0",
    "react-dom": "^18.2.0",
    "react-router-dom": "^6.20.0"
  },
  "devDependencies": {
    "@vitejs/plugin-react": "^4.2.0",
    "autoprefixer": "^10.4.16",
    "postcss": "^8.4.32",
    "tailwindcss": "^3.4.0",
    "vite": "^5.0.0",
    "@playwright/test": "^1.45.0"
  }
}
```

### `frontend/vite.config.js`

```js
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
});
```

**Comportamiento del proxy:** Toda request a `/api/...` desde el frontend se reenvía a `http://localhost:8000`. El frontend llama `/api/v1/auth/login` y Vite lo rescribe como `http://localhost:8000/api/v1/auth/login`. Sin CORS en desarrollo.

### `frontend/tailwind.config.js`

```js
/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {},
  },
  plugins: [],
};
```

### `frontend/postcss.config.js`

```js
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
```

### `frontend/index.html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Yoga Sessions</title>
  </head>
  <body class="bg-gray-50 text-gray-900 min-h-screen">
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
```

### `frontend/src/index.css`

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### `frontend/src/main.jsx`

```jsx
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import App from './App';
import './index.css';

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>
);
```

### `frontend/src/api/client.js`

```js
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
```

**Comportamiento:**
- Toda request se envía a `/api/v1/<path>`.
- Inyecta header `Authorization: Bearer <token>` si hay token en localStorage.
- **Nunca lanza excepciones** — siempre retorna `{ ok, status, data }`.
- Si la respuesta no es JSON, `data` queda `null`.

### `frontend/src/api/auth.js`

```js
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
```

**Signaturas y tipos de retorno:**
- `loginUser(email: string, password: string): Promise<{ok: boolean, status: number, data: any}>`
- `registerUser(name: string, email: string, password: string, role?: string): Promise<{ok: boolean, status: number, data: any}>`
- `getMe(): Promise<{ok: boolean, status: number, data: any}>`

**Endpoints del backend referenciados (ver `src/api.py`):**
- `POST /api/v1/auth/login` → `{ access_token, token_type }` (200) o `{ detail: "Invalid credentials" }` (401)
- `POST /api/v1/auth/register` → `{ access_token, token_type, user }` (200) o `{ detail: "Email already registered" }` (409)
- `GET /api/v1/auth/me` → `{ id, name, email, role, credits, ... }` (200) o `{ detail: "Not authenticated" }` (401)

### `frontend/src/hooks/useAuth.jsx`

```jsx
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
```

**Context value expuesto (cada propiedad):**

| Propiedad | Tipo | Descripción |
|---|---|---|
| `token` | `string \| null` | JWT token desde estado + localStorage |
| `user` | `object \| null` | Datos del perfil (id, name, email, role, credits…) |
| `login` | `(email: string, password: string) => Promise<object>` | Llama `loginUser()`, guarda token, hace `getMe()`. **Lanza** `Error` si `ok === false` con mensaje `data.detail` o `"Login failed"`. Retorna `data` del backend. |
| `register` | `(name: string, email: string, password: string, role?: string) => Promise<object>` | Llama `registerUser()`, guarda token, hace `getMe()`. **Lanza** `Error` si `ok === false` con mensaje `data.detail` o `"Registration failed"`. Retorna `data` del backend. |
| `logout` | `() => void` | Borra token de localStorage, resetea `token` y `user` a `null`. |
| `isAuthenticated` | `boolean` | `true` si `!!token && !!user` |
| `loading` | `boolean` | `true` mientras se valida el token inicial con `getMe()` |

**Flujo de inicialización:**
1. Al montar, `token` se inicializa desde `localStorage.getItem('token')`.
2. Si no hay token → `loading = false` inmediatamente.
3. Si hay token → llama `getMe()`. Si responde `ok: true` → guarda `user`. Si falla → borra localStorage y resetea estado.
4. `loading` pasa a `false` al finalizar la validación (vía `.finally()`).

**Excepciones:**
- `useAuth()` lanza `Error('useAuth must be used within AuthProvider')` si se invoca fuera del provider.
- `login()` lanza `Error` con `data.detail` o `'Login failed'` si la API responde con error.
- `register()` lanza `Error` con `data.detail` o `'Registration failed'` si la API responde con error.

### `frontend/src/App.jsx`

```jsx
import { Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './hooks/useAuth';
import NavBar from './components/NavBar';
import PrivateRoute from './components/PrivateRoute';
import LoginPage from './pages/LoginPage';
import RegisterPage from './pages/RegisterPage';
import Home from './pages/Home';

export default function App() {
  return (
    <AuthProvider>
      <NavBar />
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route path="/register" element={<RegisterPage />} />
        <Route
          path="/"
          element={
            <PrivateRoute>
              <Home />
            </PrivateRoute>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
```

**Comportamiento de rutas:**
- `NavBar` se renderiza SIEMPRE (en todas las rutas). El propio `NavBar` decide qué mostrar según `isAuthenticated`.
- `/login` y `/register` son públicos. No redirigen si ya hay sesión (el NavBar muestra los links de Logout igualmente).
- `/` está protegido con `PrivateRoute` → si no hay sesión, redirige a `/login`.
- `*` (catch-all) redirige a `/`.

### `frontend/src/components/PrivateRoute.jsx`

```jsx
import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function PrivateRoute({ children }) {
  const { isAuthenticated, loading } = useAuth();

  if (loading) {
    return (
      <div className="flex justify-center items-center min-h-[60vh]">
        <div className="animate-spin h-10 w-10 border-4 border-indigo-600 border-t-transparent rounded-full" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return children;
}
```

**Estados:**
1. `loading === true` → spinner centrado (anillo Tailwind `animate-spin` con borde `border-t-transparent`).
2. `loading === false && !isAuthenticated` → `<Navigate to="/login" replace />`.
3. `loading === false && isAuthenticated` → renderiza `children`.

### `frontend/src/components/NavBar.jsx`

```jsx
import React, { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function NavBar() {
  const { token, user, logout } = useAuth();
  const navigate = useNavigate();
  const [mobileOpen, setMobileOpen] = useState(false);

  const handleLogout = () => {
    logout();
    navigate('/login');
    setMobileOpen(false);
  };

  return (
    <nav className="bg-white shadow">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          {/* Left: brand */}
          <div className="flex items-center">
            <Link to="/" className="text-xl font-bold text-indigo-600">
              Yoga Studio
            </Link>
          </div>

          {/* Right: desktop links */}
          <div className="hidden sm:flex sm:items-center sm:gap-4">
            {token ? (
              <>
                <span className="text-gray-700">Hello, {user?.name || 'user'}</span>
                <button
                  onClick={handleLogout}
                  className="bg-red-500 text-white px-3 py-1 rounded hover:bg-red-600"
                >
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link to="/login" className="text-indigo-600 hover:underline">
                  Login
                </Link>
                <Link to="/register" className="text-indigo-600 hover:underline">
                  Register
                </Link>
              </>
            )}
          </div>

          {/* Hamburger button — mobile only */}
          <div className="sm:hidden flex items-center">
            <button
              onClick={() => setMobileOpen(!mobileOpen)}
              className="text-gray-700 hover:text-indigo-600 focus:outline-none"
              aria-label="Toggle menu"
            >
              <svg className="h-6 w-6" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                {mobileOpen ? (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                ) : (
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                )}
              </svg>
            </button>
          </div>
        </div>
      </div>

      {/* Mobile menu */}
      {mobileOpen && (
        <div className="sm:hidden border-t border-gray-200">
          <div className="px-4 py-3 space-y-3">
            {token ? (
              <>
                <span className="block text-gray-700">Hello, {user?.name || 'user'}</span>
                <button
                  onClick={handleLogout}
                  className="block w-full text-left bg-red-500 text-white px-3 py-1 rounded hover:bg-red-600"
                >
                  Logout
                </button>
              </>
            ) : (
              <>
                <Link
                  to="/login"
                  onClick={() => setMobileOpen(false)}
                  className="block text-indigo-600 hover:underline"
                >
                  Login
                </Link>
                <Link
                  to="/register"
                  onClick={() => setMobileOpen(false)}
                  className="block text-indigo-600 hover:underline"
                >
                  Register
                </Link>
              </>
            )}
          </div>
        </div>
      )}
    </nav>
  );
}
```

**Comportamiento responsive:**
- **Desktop (≥640px, `sm:`):** Links inline a la derecha. Si `token` existe → "Hello, {name}" + botón Logout rojo. Si no → links Login y Register.
- **Mobile (<640px):** Hamburguer button. Al hacer click toggla `mobileOpen`. Muestra menú vertical debajo de la barra.
- Hamburger alterna entre icono de 3 barras (hamburger) y X (close).
- `handleLogout()` → llama `logout()` del contexto, navega a `/login`, cierra menú mobile.

**Estilos Tailwind usados:**
- `bg-white shadow` — fondo blanco con sombra
- `text-indigo-600` — links y brand
- `bg-red-500` / `hover:bg-red-600` — botón Logout
- `sm:flex`, `sm:hidden` — breakpoint responsive
- `space-y-3` — espaciado vertical en menú mobile

### `frontend/src/pages/LoginPage.jsx`

```jsx
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function LoginPage() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { login } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!email.trim() || !password.trim()) {
      setError('Email and password are required.');
      return;
    }
    setSubmitting(true);
    try {
      await login(email.trim(), password);
      navigate('/');
    } catch (err) {
      setError(err.message || 'Login failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8">
        <h1 className="text-3xl font-bold text-center text-indigo-600 mb-6">Login</h1>
        {error && (
          <div className="bg-red-50 border border-red-400 text-red-700 px-4 py-3 rounded mb-4" role="alert">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoComplete="email"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoComplete="current-password"
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-indigo-600 text-white py-2 rounded-lg font-semibold hover:bg-indigo-700 transition disabled:opacity-50"
          >
            {submitting ? 'Logging in...' : 'Login'}
          </button>
        </form>
        <p className="text-center text-sm text-gray-500 mt-4">
          Don&apos;t have an account?{' '}
          <Link to="/register" className="text-indigo-600 hover:underline font-medium">
            Register
          </Link>
        </p>
      </div>
    </div>
  );
}
```

**Flujo de submit:**
1. Previene default del form.
2. Valida: si `email` o `password` vacíos tras `.trim()` → setea error `"Email and password are required."`.
3. `submitting = true` → botón muestra "Logging in..." y se deshabilita (`disabled:opacity-50`).
4. Llama `login(email, password)` del hook `useAuth`. El hook internamente:
   - Llama `loginUser()` → si `ok === false`, **lanza** `Error`.
   - Guarda el token en localStorage y estado.
   - Llama `getMe()` para poblar el perfil.
5. Si `login()` lanza → catch captura y muestra `err.message` en el div `[role="alert"]`.
6. Si `login()` resuelve → `navigate('/')` redirige al home.
7. `finally` → `submitting = false`.

**IDs de inputs para tests E2E:** `id="email"`, `id="password"`. El div de error usa `role="alert"`.

### `frontend/src/pages/RegisterPage.jsx`

```jsx
import { useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';

export default function RegisterPage() {
  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState('client');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const { register } = useAuth();
  const navigate = useNavigate();

  async function handleSubmit(e) {
    e.preventDefault();
    setError('');
    if (!name.trim() || !email.trim() || !password.trim()) {
      setError('All fields are required.');
      return;
    }
    if (password.length < 4) {
      setError('Password must be at least 4 characters.');
      return;
    }
    setSubmitting(true);
    try {
      await register(name.trim(), email.trim(), password, role);
      navigate('/');
    } catch (err) {
      setError(err.message || 'Registration failed');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-md w-full bg-white rounded-xl shadow-lg p-8">
        <h1 className="text-3xl font-bold text-center text-indigo-600 mb-6">Register</h1>
        {error && (
          <div className="bg-red-50 border border-red-400 text-red-700 px-4 py-3 rounded mb-4" role="alert">
            {error}
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label htmlFor="name" className="block text-sm font-medium text-gray-700 mb-1">
              Name
            </label>
            <input
              id="name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoComplete="name"
            />
          </div>
          <div>
            <label htmlFor="email" className="block text-sm font-medium text-gray-700 mb-1">
              Email
            </label>
            <input
              id="email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoComplete="email"
            />
          </div>
          <div>
            <label htmlFor="password" className="block text-sm font-medium text-gray-700 mb-1">
              Password
            </label>
            <input
              id="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
              autoComplete="new-password"
            />
          </div>
          <div>
            <label htmlFor="role" className="block text-sm font-medium text-gray-700 mb-1">
              Role
            </label>
            <select
              id="role"
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-4 py-2 focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <option value="client">Client</option>
              <option value="admin">Admin</option>
            </select>
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-indigo-600 text-white py-2 rounded-lg font-semibold hover:bg-indigo-700 transition disabled:opacity-50"
          >
            {submitting ? 'Registering...' : 'Register'}
          </button>
        </form>
        <p className="text-center text-sm text-gray-500 mt-4">
          Already have an account?{' '}
          <Link to="/login" className="text-indigo-600 hover:underline font-medium">
            Login
          </Link>
        </p>
      </div>
    </div>
  );
}
```

**Flujo de submit:**
1. Valida: todos los campos requeridos → `"All fields are required."`.
2. Valida: `password.length < 4` → `"Password must be at least 4 characters."`.
3. `submitting = true` → botón "Registering..." deshabilitado.
4. Llama `register(name, email, password, role)` del hook `useAuth`. El hook:
   - Llama `registerUser()` → si `ok === false`, lanza `Error`.
   - Guarda el token (el backend ya devuelve `access_token` tras registro exitoso).
   - Llama `getMe()` para poblar perfil → **auto-login integrado en el hook**.
5. Si `register()` lanza → catch captura y muestra `err.message`.
6. Si `register()` resuelve → `navigate('/')`.
7. `finally` → `submitting = false`.

**IDs de inputs para tests E2E:** `id="name"`, `id="email"`, `id="password"`, `id="role"`.

**Nota:** El auto-login tras registro está implementado en el hook `useAuth`, no en el componente. La respuesta de `POST /api/v1/auth/register` ya incluye `access_token`, por lo que el hook simplemente lo guarda sin necesidad de una segunda llamada a `loginUser`.

### `frontend/src/pages/Home.jsx`

```jsx
import { useAuth } from '../hooks/useAuth';

export default function Home() {
  const { user } = useAuth();

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="max-w-lg w-full bg-white rounded-xl shadow-lg p-8 text-center">
        <h1 className="text-3xl font-bold text-indigo-600 mb-2">Welcome</h1>
        <p className="text-gray-600 text-lg">
          Hello, <span className="font-semibold">{user?.name || user?.email}</span>!
        </p>
        <p className="text-gray-500 text-sm mt-1">Role: {user?.role}</p>
      </div>
    </div>
  );
}
```

**Comportamiento:** Página simple que muestra nombre del usuario (o email como fallback) y su rol. Solo accesible vía `PrivateRoute`.

### `frontend/playwright.config.js`

```js
import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30000,
  retries: 0,
  use: {
    baseURL: 'http://localhost:5173',
    headless: true,
    screenshot: 'only-on-failure',
  },
  webServer: [
    {
      command: 'npm run dev',
      port: 5173,
      reuseExistingServer: true,
      timeout: 15000,
    },
  ],
});
```

**Detalles:**
- `testDir: './tests/e2e'` — los tests están dentro de `frontend/tests/e2e/`.
- `webServer` inicia `npm run dev` automáticamente antes de los tests (vite dev server en puerto 5173).
- `reuseExistingServer: true` — si el dev server ya está corriendo, no lo reinicia.
- `timeout: 30000` — 30 segundos por test.
- `baseURL` en `use` permite rutas relativas aunque los tests usan URLs absolutas.

---

## Tests a escribir

### `frontend/tests/e2e/auth.spec.js`

Archivo Playwright (`@playwright/test`). Se ejecuta con `npx playwright test` desde el directorio `frontend/`.

#### Precondiciones generales:
- Backend corriendo en `http://localhost:8000`
- Frontend (Vite dev server) corriendo en `http://localhost:5173` (lo inicia automáticamente `webServer` en playwright.config.js)

---

### Test 1: `test('successful login redirects to home', ...)`

**Precondición:**
- Usuario de prueba `e2e-login@test.com` / `secret123` registrado vía API (`POST /api/v1/auth/register`). Se ignora 409 si ya existe.

**Acción:**
1. `page.request.post('http://localhost:8000/api/v1/auth/register', { data: { name: 'E2E Login User', email: 'e2e-login@test.com', password: 'secret123', role: 'client' } }).catch(() => {})`
2. `page.goto('http://localhost:5173/login')`
3. `await expect(page.locator('h1')).toHaveText('Login')`
4. `page.fill('input[id="email"]', 'e2e-login@test.com')`
5. `page.fill('input[id="password"]', 'secret123')`
6. `page.click('button[type="submit"]')`

**Assertions:**
- `await expect(page).toHaveURL('http://localhost:5173/')`
- `await expect(page.locator('h1')).toHaveText('Welcome')`
- `await expect(page.getByText('Hello,').first()).toBeVisible()`
- `await expect(page.locator('text=Logout')).toBeVisible()`

---

### Test 2: `test('failed login shows error message', ...)`

**Precondición:** Ninguna (el usuario no debe existir).

**Acción:**
1. `page.goto('http://localhost:5173/login')`
2. `await expect(page.locator('h1')).toHaveText('Login')`
3. `page.fill('input[id="email"]', 'no-such-user@test.com')`
4. `page.fill('input[id="password"]', 'wrongpass')`
5. `page.click('button[type="submit"]')`

**Assertions:**
- `await expect(page.locator('[role="alert"]')).toBeVisible()` — el div de error debe mostrarse
- `await expect(page).toHaveURL(/\/login/)` — la URL aún contiene `/login` (no hubo redirect)

---

### Test 3: `test('logout clears session and redirects to login', ...)`

**Precondición:**
- Usuario `e2e-logout@test.com` / `secret123` registrado vía API. Se ignora 409 si ya existe.

**Acción:**
1. `page.request.post(...)` para registrar `e2e-logout@test.com` (ignorar 409)
2. `page.goto('http://localhost:5173/login')`, llenar email y password, submit
3. `await expect(page).toHaveURL('http://localhost:5173/')` — verificar que llegó al home
4. `page.click('button:has-text("Logout")')` — click en botón Logout

**Assertions:**
- `await expect(page).toHaveURL(/\/login/)` — redirigió a login
- `page.goto('http://localhost:5173/')` — intentar acceder al home
- `await expect(page).toHaveURL(/\/login/)` — PrivateRoute redirige a login

---

## Dependencias

**Instalación en `frontend/`:**

```bash
cd frontend
npm install react@^18.2.0 react-dom@^18.2.0 react-router-dom@^6.20.0
npm install -D vite@^5.0.0 @vitejs/plugin-react@^4.2.0 tailwindcss@^3.4.0 postcss@^8.4.32 autoprefixer@^10.4.16 @playwright/test@^1.45.0
npx tailwindcss init -p   # genera tailwind.config.js y postcss.config.js (si no existen)
npx playwright install chromium   # instala el navegador para tests E2E
```

---

## Notas de implementación

1. **IDs de inputs para tests E2E:** Los inputs usan `id="email"`, `id="password"`, `id="name"`, `id="role"`. Los selectores de Playwright dependen de estos IDs. No deben cambiarse sin actualizar los tests.

2. **Flujo de auth centralizado en el hook:** `useAuth().login(email, password)` y `useAuth().register(name, email, password, role)` encapsulan toda la lógica: llamada a la API, almacenamiento de token, y fetch del perfil (`getMe()`). Los componentes de página solo llaman estas funciones y manejan el error vía try/catch.

3. **`login()` y `register()` lanzan excepciones:** A diferencia de `apiFetch` que nunca lanza, estas funciones del hook sí lanzan `Error` cuando la API responde con `ok: false`. Los componentes usan try/catch para manejar estos errores. Esto es intencional: el hook traduce respuestas de error HTTP en excepciones para simplificar el código del componente.

4. **NavBar siempre visible:** A diferencia de otros patrones comunes, `NavBar` se renderiza en todas las rutas (incluso `/login` y `/register`). El propio `NavBar` decide qué mostrar basado en `token` del contexto. Esto permite que un usuario autenticado vea "Hello, X" + Logout incluso en `/login`.

5. **Proxy de Vite:** El proxy `/api` → `http://localhost:8000` permite que el frontend haga requests a `/api/v1/...` sin problemas de CORS en desarrollo. En producción se necesitaría configurar el servidor web (nginx, etc.) para el mismo comportamiento.

6. **PrivateRoute con estado de carga:** El spinner (`animate-spin`) se muestra mientras `loading === true` (validación inicial del token). Sin esto, usuarios con token válido verían un flash de redirect a `/login` antes de que `getMe()` responda.

7. **`apiFetch` nunca lanza:** Todas las funciones en `api/auth.js` retornan `{ ok, status, data }`. Es el hook `useAuth` quien decide si lanzar o no basado en `ok`.

8. **Auto-login tras registro:** `useAuth().register()` guarda directamente el `access_token` que devuelve el backend en la respuesta de registro. No hace falta una segunda llamada a login. El backend devuelve `{ access_token, token_type, user }` en el POST /auth/register exitoso.

9. **Rutas del backend referenciadas:**
   - `POST /api/v1/auth/login` — definido en `src/api.py` (JWT + bcrypt). Respuesta 200: `{ access_token, token_type }`. Respuesta 401: `{ detail: "Invalid credentials" }`.
   - `POST /api/v1/auth/register` — definido en `src/api.py`. Respuesta 200: `{ access_token, token_type, user }`. Respuesta 409: `{ detail: "Email already registered" }`.
   - `GET /api/v1/auth/me` — definido en `src/api.py`. Respuesta 200: `{ id, name, email, role, credits, ... }`. Respuesta 401: `{ detail: "Not authenticated" }`.
   - Ver `src/auth.py` para los detalles de JWT (python-jose) y bcrypt (passlib), y `src/storage.py` para persistencia JSON.

10. **Configuración de Playwright en `frontend/`:** El archivo `playwright.config.js` está en `frontend/`, no en la raíz del proyecto. Incluye `webServer` que levanta `npm run dev` automáticamente. Los tests se ejecutan con `cd frontend && npx playwright test` o `npm run test:e2e`.

11. **Directorios de la estructura frontend:**
    - `frontend/src/api/` — capa de comunicación HTTP (`client.js`, `auth.js`)
    - `frontend/src/hooks/` — hooks de React (`useAuth.jsx`)
    - `frontend/src/components/` — componentes reutilizables (`NavBar.jsx`, `PrivateRoute.jsx`)
    - `frontend/src/pages/` — páginas de la app (`LoginPage.jsx`, `RegisterPage.jsx`, `Home.jsx`)
    - `frontend/tests/e2e/` — tests E2E Playwright

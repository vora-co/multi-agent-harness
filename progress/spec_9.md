# Spec — Feature #9: Boilerplate Vue 3 SPA con Vite

## Archivos a crear o modificar

| Archivo | Acción |
|---|---|
| `src/frontend/package.json` | NUEVO — configuración del proyecto Vite + Vue 3 |
| `src/frontend/vite.config.js` | NUEVO — configuración de Vite con proxy al backend |
| `src/frontend/index.html` | NUEVO — HTML de entrada |
| `src/frontend/tailwind.config.js` | NUEVO — configuración Tailwind CSS |
| `src/frontend/postcss.config.js` | NUEVO — configuración PostCSS (requerido por Tailwind) |
| `src/frontend/src/main.js` | NUEVO — entry point de Vue 3, monta la app y el router |
| `src/frontend/src/App.vue` | NUEVO — layout raíz con `<NavBar />` + `<router-view />` |
| `src/frontend/src/router/index.js` | NUEVO — configuración de vue-router 4 |
| `src/frontend/src/composables/useAuth.js` | NUEVO — composable para manejo de token JWT |
| `src/frontend/src/pages/HomePage.vue` | NUEVO — página de bienvenida condicional |
| `src/frontend/src/pages/LoginPage.vue` | NUEVO — formulario de login |
| `src/frontend/src/pages/RegisterPage.vue` | NUEVO — formulario de registro |
| `src/frontend/src/pages/DashboardPage.vue` | NUEVO — panel simple post-login |
| `src/frontend/src/components/NavBar.vue` | NUEVO — barra de navegación con branding "PlaySession" |
| `src/frontend/src/components/AppButton.vue` | NUEVO — botón reutilizable con variantes Tailwind |
| `src/frontend/src/style.css` | NUEVO — directivas base de Tailwind |
| `tests/e2e/test_feature_9.py` | MODIFICACIÓN — añadir tests de navegador Playwright para el SPA |

---

## Implementación

### src/frontend/package.json

Crear desde cero con `npm create vite@latest` o manualmente. Debe contener:

```json
{
  "name": "playsession-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "vue": "^3.4.0",
    "vue-router": "^4.3.0"
  },
  "devDependencies": {
    "@vitejs/plugin-vue": "^5.0.0",
    "autoprefixer": "^10.4.0",
    "postcss": "^8.4.0",
    "tailwindcss": "^3.4.0",
    "vite": "^5.4.0"
  }
}
```

### src/frontend/vite.config.js

```javascript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
```

### src/frontend/index.html

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>PlaySession</title>
  </head>
  <body class="bg-gray-50 min-h-screen">
    <div id="app"></div>
    <script type="module" src="/src/main.js"></script>
  </body>
</html>
```

### src/frontend/tailwind.config.js

```javascript
/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{vue,js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {},
  },
  plugins: [],
}
```

### src/frontend/postcss.config.js

```javascript
export default {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
}
```

### src/frontend/src/style.css

```css
@tailwind base;
@tailwind components;
@tailwind utilities;
```

### src/frontend/src/main.js

```javascript
import { createApp } from 'vue'
import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)
app.use(router)
app.mount('#app')
```

### src/frontend/src/router/index.js

Define 4 rutas. `HomePage` es la ruta raíz. `LoginPage` y `RegisterPage` son públicas. `DashboardPage` requiere autenticación (se usa `beforeEnter` con guard que redirige a `/login` si no hay token).

```javascript
import { createRouter, createWebHistory } from 'vue-router'
import HomePage from '../pages/HomePage.vue'
import LoginPage from '../pages/LoginPage.vue'
import RegisterPage from '../pages/RegisterPage.vue'
import DashboardPage from '../pages/DashboardPage.vue'

function getToken() {
  return localStorage.getItem('playsession_token')
}

const routes = [
  {
    path: '/',
    name: 'Home',
    component: HomePage,
  },
  {
    path: '/login',
    name: 'Login',
    component: LoginPage,
    meta: { guest: true },
  },
  {
    path: '/register',
    name: 'Register',
    component: RegisterPage,
    meta: { guest: true },
  },
  {
    path: '/dashboard',
    name: 'Dashboard',
    component: DashboardPage,
    meta: { requiresAuth: true },
    beforeEnter: (to, from, next) => {
      if (!getToken()) {
        next({ name: 'Login' })
      } else {
        next()
      }
    },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

export default router
```

### src/frontend/src/composables/useAuth.js

Composable que expone estado reactivo del token JWT. Lee/escribe `localStorage` bajo la clave `'playsession_token'`. Expone: `token` (ref), `isAuthenticated` (computed), `user` (ref, cargado desde `GET /api/v1/auth/me`), `login(email, password)`, `register(name, email, password)`, `logout()`, `fetchMe()`.

```javascript
import { ref, computed } from 'vue'

const TOKEN_KEY = 'playsession_token'

// Singleton state (shared across all components)
const token = ref(localStorage.getItem(TOKEN_KEY) || null)
const user = ref(null)

export function useAuth() {
  const isAuthenticated = computed(() => !!token.value)

  function setToken(t) {
    token.value = t
    if (t) {
      localStorage.setItem(TOKEN_KEY, t)
    } else {
      localStorage.removeItem(TOKEN_KEY)
    }
  }

  async function login(email, password) {
    const res = await fetch('/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Login failed')
    }
    const data = await res.json()
    setToken(data.access_token)
    await fetchMe()
    return data
  }

  async function register(name, email, password) {
    const res = await fetch('/api/v1/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, email, password, role: 'client' }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail || 'Registration failed')
    }
    const data = await res.json()
    setToken(data.access_token)
    await fetchMe()
    return data
  }

  function logout() {
    setToken(null)
    user.value = null
  }

  async function fetchMe() {
    if (!token.value) {
      user.value = null
      return null
    }
    const res = await fetch('/api/v1/auth/me', {
      headers: { 'Authorization': `Bearer ${token.value}` },
    })
    if (!res.ok) {
      // Token expired or invalid
      setToken(null)
      user.value = null
      return null
    }
    const data = await res.json()
    user.value = data
    return data
  }

  return {
    token,
    user,
    isAuthenticated,
    login,
    register,
    logout,
    fetchMe,
  }
}
```

### src/frontend/src/App.vue

Layout raíz: `<NavBar />` arriba, `<router-view />` debajo. Al montar el componente, llama a `fetchMe()` si hay token.

```vue
<template>
  <div class="min-h-screen flex flex-col">
    <NavBar />
    <main class="flex-1 container mx-auto px-4 py-8">
      <router-view />
    </main>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import NavBar from './components/NavBar.vue'
import { useAuth } from './composables/useAuth'

const { fetchMe } = useAuth()

onMounted(() => {
  fetchMe()
})
</script>
```

### src/frontend/src/pages/HomePage.vue

Página de bienvenida. Si hay token (`isAuthenticated === true`), muestra mensaje de bienvenida y enlace al Dashboard. Si no hay token, muestra Hero section con título "Welcome to PlaySession", subtítulo explicativo y dos botones: "Login" y "Register". Usa `useAuth()`.

```vue
<template>
  <div>
    <!-- Authenticated view -->
    <div v-if="isAuthenticated" class="text-center py-16">
      <h1 class="text-3xl font-bold text-green-700 mb-4">
        Welcome back, {{ user?.name || 'User' }}!
      </h1>
      <p class="text-gray-600 mb-6">You are logged in.</p>
      <router-link
        to="/dashboard"
        class="inline-block bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 transition"
        data-testid="go-dashboard-btn"
      >
        Go to Dashboard
      </router-link>
    </div>

    <!-- Guest view -->
    <div v-else class="text-center py-16">
      <h1 class="text-4xl font-bold text-gray-800 mb-4" data-testid="hero-title">
        Welcome to PlaySession
      </h1>
      <p class="text-lg text-gray-600 mb-8 max-w-md mx-auto">
        Book yoga and wellness sessions with ease.
        Join our community and start your wellness journey today.
      </p>
      <div class="flex gap-4 justify-center">
        <router-link
          to="/login"
          class="inline-block bg-blue-600 text-white px-6 py-3 rounded-lg hover:bg-blue-700 transition"
          data-testid="login-btn"
        >
          Login
        </router-link>
        <router-link
          to="/register"
          class="inline-block bg-green-600 text-white px-6 py-3 rounded-lg hover:bg-green-700 transition"
          data-testid="register-btn"
        >
          Register
        </router-link>
      </div>
    </div>
  </div>
</template>

<script setup>
import { useAuth } from '../composables/useAuth'

const { isAuthenticated, user } = useAuth()
</script>
```

### src/frontend/src/pages/LoginPage.vue

Formulario de login con email y password. Al enviar, llama a `useAuth().login()`. En éxito redirige a `/dashboard`. Muestra errores del backend en un elemento con clase `text-red-600` y `data-testid="login-error"`. Los inputs tienen `data-testid="login-email"` y `data-testid="login-password"`. El botón submit tiene `data-testid="login-submit"`.

```vue
<template>
  <div class="max-w-md mx-auto mt-10">
    <h1 class="text-2xl font-bold mb-6 text-center">Login</h1>
    <form @submit.prevent="handleLogin" class="bg-white shadow-md rounded-lg p-6 space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
        <input
          v-model="email"
          type="email"
          required
          data-testid="login-email"
          class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input
          v-model="password"
          type="password"
          required
          data-testid="login-password"
          class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
        />
      </div>
      <p v-if="error" class="text-red-600 text-sm" data-testid="login-error">{{ error }}</p>
      <button
        type="submit"
        data-testid="login-submit"
        class="w-full bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 transition"
        :disabled="loading"
      >
        {{ loading ? 'Logging in...' : 'Login' }}
      </button>
      <p class="text-center text-sm text-gray-500">
        Don't have an account?
        <router-link to="/register" class="text-blue-600 hover:underline">Register</router-link>
      </p>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { login } = useAuth()

const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  error.value = ''
  loading.value = true
  try {
    await login(email.value, password.value)
    router.push('/dashboard')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
```

### src/frontend/src/pages/RegisterPage.vue

Formulario de registro con name, email, password. Similar a LoginPage pero crea cuenta. `data-testid`: `register-name`, `register-email`, `register-password`, `register-submit`, `register-error`. En éxito redirige a `/dashboard`.

```vue
<template>
  <div class="max-w-md mx-auto mt-10">
    <h1 class="text-2xl font-bold mb-6 text-center">Register</h1>
    <form @submit.prevent="handleRegister" class="bg-white shadow-md rounded-lg p-6 space-y-4">
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Name</label>
        <input
          v-model="name"
          type="text"
          required
          data-testid="register-name"
          class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-green-500"
        />
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Email</label>
        <input
          v-model="email"
          type="email"
          required
          data-testid="register-email"
          class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-green-500"
        />
      </div>
      <div>
        <label class="block text-sm font-medium text-gray-700 mb-1">Password</label>
        <input
          v-model="password"
          type="password"
          required
          data-testid="register-password"
          class="w-full border border-gray-300 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-green-500"
        />
      </div>
      <p v-if="error" class="text-red-600 text-sm" data-testid="register-error">{{ error }}</p>
      <button
        type="submit"
        data-testid="register-submit"
        class="w-full bg-green-600 text-white py-2 rounded-lg hover:bg-green-700 transition"
        :disabled="loading"
      >
        {{ loading ? 'Creating account...' : 'Register' }}
      </button>
      <p class="text-center text-sm text-gray-500">
        Already have an account?
        <router-link to="/login" class="text-blue-600 hover:underline">Login</router-link>
      </p>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { register } = useAuth()

const name = ref('')
const email = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleRegister() {
  error.value = ''
  loading.value = true
  try {
    await register(name.value, email.value, password.value)
    router.push('/dashboard')
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>
```

### src/frontend/src/pages/DashboardPage.vue

Página simple post-autenticación. Muestra nombre del usuario (de `useAuth().user`), rol, créditos, y un botón "Logout" con `data-testid="logout-btn"`. El botón logout llama a `logout()` y redirige a `/`.

```vue
<template>
  <div class="max-w-2xl mx-auto">
    <h1 class="text-3xl font-bold mb-6" data-testid="dashboard-title">Dashboard</h1>
    <div class="bg-white shadow-md rounded-lg p-6 space-y-3" data-testid="dashboard-card">
      <p><span class="font-semibold">Name:</span> <span data-testid="dashboard-name">{{ user?.name }}</span></p>
      <p><span class="font-semibold">Email:</span> <span data-testid="dashboard-email">{{ user?.email }}</span></p>
      <p><span class="font-semibold">Role:</span> <span data-testid="dashboard-role">{{ user?.role }}</span></p>
      <p><span class="font-semibold">Credits:</span> <span data-testid="dashboard-credits">{{ user?.credits }}</span></p>
    </div>
    <button
      @click="handleLogout"
      data-testid="logout-btn"
      class="mt-6 bg-red-600 text-white px-6 py-2 rounded-lg hover:bg-red-700 transition"
    >
      Logout
    </button>
  </div>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { user, logout } = useAuth()

function handleLogout() {
  logout()
  router.push('/')
}
</script>
```

### src/frontend/src/components/NavBar.vue

Barra de navegación responsive. Muestra "PlaySession" como branding a la izquierda (con enlace a `/`). A la derecha: si está autenticado, muestra el nombre del usuario y botón "Dashboard" + "Logout". Si no, muestra botones "Login" y "Register". Usa `useAuth()`.

```vue
<template>
  <nav class="bg-white shadow-sm border-b border-gray-200" data-testid="navbar">
    <div class="container mx-auto px-4 py-3 flex items-center justify-between">
      <!-- Branding -->
      <router-link to="/" class="text-xl font-bold text-blue-700 tracking-tight" data-testid="navbar-brand">
        PlaySession
      </router-link>

      <!-- Right side -->
      <div class="flex items-center gap-4">
        <template v-if="isAuthenticated">
          <span class="text-sm text-gray-600 hidden sm:inline" data-testid="navbar-user-name">
            {{ user?.name }}
          </span>
          <router-link
            to="/dashboard"
            class="text-sm text-blue-600 hover:underline"
            data-testid="navbar-dashboard-link"
          >
            Dashboard
          </router-link>
          <button
            @click="handleLogout"
            class="text-sm text-red-600 hover:underline"
            data-testid="navbar-logout-btn"
          >
            Logout
          </button>
        </template>
        <template v-else>
          <router-link
            to="/login"
            class="text-sm text-blue-600 hover:underline"
            data-testid="navbar-login-link"
          >
            Login
          </router-link>
          <router-link
            to="/register"
            class="text-sm bg-green-600 text-white px-3 py-1 rounded hover:bg-green-700 transition"
            data-testid="navbar-register-link"
          >
            Register
          </router-link>
        </template>
      </div>
    </div>
  </nav>
</template>

<script setup>
import { useRouter } from 'vue-router'
import { useAuth } from '../composables/useAuth'

const router = useRouter()
const { isAuthenticated, user, logout } = useAuth()

function handleLogout() {
  logout()
  router.push('/')
}
</script>
```

### src/frontend/src/components/AppButton.vue

Componente botón reutilizable con variantes de Tailwind. Props: `variant` (`'primary'` | `'secondary'` | `'danger'`, default `'primary'`), `disabled` (boolean, default `false`), `type` (string, default `'button'`). Emite `click`. Usa slots para el contenido.

```vue
<template>
  <button
    :type="type"
    :disabled="disabled"
    :class="buttonClasses"
    @click="$emit('click')"
  >
    <slot />
  </button>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: {
    type: String,
    default: 'primary',
    validator: (v) => ['primary', 'secondary', 'danger'].includes(v),
  },
  disabled: {
    type: Boolean,
    default: false,
  },
  type: {
    type: String,
    default: 'button',
  },
})

defineEmits(['click'])

const buttonClasses = computed(() => {
  const base = 'inline-block px-6 py-3 rounded-lg font-medium transition focus:outline-none focus:ring-2'
  const variants = {
    primary: 'bg-blue-600 text-white hover:bg-blue-700 focus:ring-blue-300',
    secondary: 'bg-gray-200 text-gray-800 hover:bg-gray-300 focus:ring-gray-400',
    danger: 'bg-red-600 text-white hover:bg-red-700 focus:ring-red-300',
  }
  const disabledClasses = props.disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'
  return `${base} ${variants[props.variant]} ${disabledClasses}`
})
</script>
```

---

## Tests a escribir

### tests/e2e/test_feature_9.py

**IMPORTANTE**: El archivo `tests/e2e/test_feature_9.py` ya existe con tests E2E de API para cancelación de sesiones y promoción de waitlist. Los tests del frontend SPA deben **añadirse** al mismo archivo (nuevas clases al final), NO reemplazar el contenido existente.

Los tests del SPA usan `page: Page` (navegador real de Playwright), a diferencia de los tests existentes que usan `api` (APIRequestContext). Ambos coexisten en el mismo archivo.

---

#### Configuración y fixtures compartidos

El archivo ya define `BASE_URL` y `SCREENSHOT_DIR`. Los nuevos tests del SPA deben apuntar al frontend Vite (puerto 5173 en dev). Se debe definir una nueva constante:

```python
FRONTEND_URL = os.environ.get("FRONTEND_URL", "http://localhost:5173")
```

Y una función helper `screen(page, name)` para screenshots PNG (similar a la que usan `test_feature_1.py` y `test_feature_3.py`):

```python
def screen(page: Page, name: str):
    """Take a full-page PNG screenshot."""
    ensure_screenshot_dir()
    path = os.path.join(SCREENSHOT_DIR, f"feat9_spa_{name}.png")
    page.screenshot(path=path, full_page=True)
    return path
```

---

#### Test 1: `test_homepage_shows_login_and_register_when_no_token`

- **Precondición**: Navegador limpio (sin token en localStorage). Servidor Vite corriendo en `FRONTEND_URL`.
- **Acción**: `page.goto(FRONTEND_URL + "/")`
- **Assertions**:
  - `expect(page).to_have_title("PlaySession")`
  - El hero title `[data-testid="hero-title"]` contiene "Welcome to PlaySession"
  - El botón Login `[data-testid="login-btn"]` es visible
  - El botón Register `[data-testid="register-btn"]` es visible
  - El Navbar `[data-testid="navbar"]` es visible
  - El branding `[data-testid="navbar-brand"]` contiene "PlaySession"
  - El enlace Login del Navbar `[data-testid="navbar-login-link"]` es visible
  - El enlace Register del Navbar `[data-testid="navbar-register-link"]` es visible
  - **NO** es visible el botón Dashboard / Logout en el Navbar
- **Screenshot**: `feat9_spa_happy_01_homepage_guest.png`

---

#### Test 2: `test_login_successful_redirects_to_dashboard`

- **Precondición**: Un usuario existe en el backend (crear vía `api` — el fixture `api` ya existe en el archivo, registrar un usuario fresco con `api.post` a `/api/v1/auth/register`).
- **Acción**:
  1. `page.goto(FRONTEND_URL + "/login")`
  2. Llenar `[data-testid="login-email"]` con el email del usuario
  3. Llenar `[data-testid="login-password"]` con la password del usuario
  4. Click en `[data-testid="login-submit"]`
  5. Esperar a que la URL sea `/dashboard` (`page.waitForURL("**/dashboard")`)
- **Assertions**:
  - La URL final es `/dashboard`
  - `[data-testid="dashboard-title"]` contiene "Dashboard"
  - `[data-testid="dashboard-name"]` contiene el nombre del usuario
  - `[data-testid="dashboard-email"]` contiene el email
  - El Navbar muestra el nombre del usuario `[data-testid="navbar-user-name"]`
  - El Navbar muestra el enlace Dashboard `[data-testid="navbar-dashboard-link"]`
  - El Navbar muestra el botón Logout `[data-testid="navbar-logout-btn"]`
  - El Navbar **NO** muestra Login / Register links
- **Screenshot**: `feat9_spa_happy_02_dashboard.png`

---

#### Test 3: `test_login_failed_shows_error`

- **Precondición**: Ninguna.
- **Acción**:
  1. `page.goto(FRONTEND_URL + "/login")`
  2. Llenar `[data-testid="login-email"]` con `"no_such_user@example.com"`
  3. Llenar `[data-testid="login-password"]` con `"WrongPass1!"`
  4. Click en `[data-testid="login-submit"]`
- **Assertions**:
  - El elemento `[data-testid="login-error"]` se vuelve visible
  - Contiene texto de error (no vacío)
  - La URL sigue siendo `/login` (no hubo redirect)
- **Screenshot**: `feat9_spa_sad_01_login_error.png`

---

#### Test 4: `test_register_successful_redirects_to_dashboard`

- **Precondición**: Backend corriendo. Email único generado con timestamp.
- **Acción**:
  1. `page.goto(FRONTEND_URL + "/register")`
  2. Llenar `[data-testid="register-name"]` con `"New User"`
  3. Llenar `[data-testid="register-email"]` con `f"e2e_spa_{int(time.time())}@example.com"`
  4. Llenar `[data-testid="register-password"]` con `"Str0ng!Pass"`
  5. Click en `[data-testid="register-submit"]`
  6. Esperar `page.waitForURL("**/dashboard")`
- **Assertions**:
  - URL final es `/dashboard`
  - `[data-testid="dashboard-title"]` es visible
  - `[data-testid="dashboard-name"]` contiene `"New User"`
  - El Navbar refleja estado autenticado
- **Screenshot**: `feat9_spa_happy_03_register_success.png`

---

#### Test 5: `test_logout_clears_session_and_shows_home`

- **Precondición**: Test 2 o 4 ya ejecutado (o se registra/login inline). Usuario autenticado en `/dashboard`.
- **Acción**:
  1. Asegurarse de estar en `/dashboard` tras login exitoso
  2. Click en `[data-testid="logout-btn"]`
  3. Esperar navegación a `/`
- **Assertions**:
  - URL final es `/`
  - `[data-testid="hero-title"]` es visible (contiene "Welcome to PlaySession")
  - `[data-testid="login-btn"]` es visible
  - `[data-testid="register-btn"]` es visible
  - El Navbar muestra links de guest (Login, Register) y NO muestra info de usuario autenticado
- **Screenshot**: `feat9_spa_happy_04_after_logout.png`

---

#### Test 6: `test_dashboard_redirects_to_login_if_not_authenticated`

- **Precondición**: Navegador limpio (sin token en localStorage).
- **Acción**: `page.goto(FRONTEND_URL + "/dashboard")`
- **Assertions**:
  - El navegador es redirigido a `/login` (o la URL contiene `/login`)
  - El formulario de login es visible (`[data-testid="login-email"]`)
- **Screenshot**: `feat9_spa_sad_02_redirect_to_login.png`

---

#### Test 7: `test_navbar_branding_links_to_home`

- **Precondición**: Navegador en cualquier página autenticada o no.
- **Acción**:
  1. `page.goto(FRONTEND_URL + "/login")`
  2. Click en `[data-testid="navbar-brand"]`
- **Assertions**:
  - URL es `/`
  - HomePage se muestra (hero title visible)
- **Screenshot**: `feat9_spa_happy_05_navbar_branding.png`

---

## Dependencias

Ninguna librería Python nueva. Las dependencias de Node.js se instalan con `npm install` dentro de `src/frontend/`:

- `vue` ^3.4.0
- `vue-router` ^4.3.0
- `vite` ^5.4.0
- `@vitejs/plugin-vue` ^5.0.0
- `tailwindcss` ^3.4.0
- `postcss` ^8.4.0
- `autoprefixer` ^10.4.0

---

## Notas de implementación

1. **Ubicación**: Todo el frontend vive en `src/frontend/`, NO en `frontend/` como dice `docs/architecture.md`. La especificación del feature lo pide explícitamente: "Estructura: src/frontend/". La arquitectura existente usa `src/` como raíz de todo el código fuente.

2. **Vite vs React**: Aunque `docs/architecture.md` y `docs/conventions.md` mencionan React, esta feature #9 usa **Vue 3**. Es una decisión explícita de la especificación. El resto del documento de arquitectura puede necesitar actualización posterior.

3. **Proxy de Vite**: La configuración de proxy (`/api` → `localhost:8000`) es esencial para desarrollo. Sin ella, el frontend haría requests CORS que fallarían. En producción el build se serviría desde FastAPI como archivos estáticos, pero eso está fuera del scope de esta feature.

4. **Coexistencia de tests en test_feature_9.py**: Los tests existentes de API (clases `TestWaitlistPromotionE2E`, `TestSessionCancelE2E`, `TestSessionCancelSadPaths`) deben preservarse intactos. Los nuevos tests del SPA se añaden al final como clases nuevas (`TestHomePageGuest`, `TestAuthFlow`, `TestNavBar`, etc.).

5. **Variables de entorno para tests E2E**: Los tests del SPA necesitan `FRONTEND_URL` (default `http://localhost:5173`). Los tests de API existentes necesitan `BASE_URL` (default `http://localhost:8000`). Ambos servidores deben estar corriendo para los tests SPA, ya que el frontend hace fetch al backend vía proxy.

6. **localStorage**: La clave usada es `'playsession_token'`. Esto es deliberado para evitar colisiones con otras aplicaciones en el mismo dominio.

7. **Composable useAuth como singleton**: El estado (`token`, `user`) se define a nivel de módulo (fuera de la función `useAuth()`), lo que significa que todas las instancias comparten el mismo estado reactivo. Esto es intencional para Vue 3 composables.

8. **Estados de carga**: LoginPage y RegisterPage muestran texto de carga en el botón y lo deshabilitan mientras la request está en vuelo (`loading = true`).

9. **No se usa TypeScript**: Aunque Vite lo soporta, esta feature usa JavaScript plano para mantener simplicidad y consistencia con el backend Python.

10. **El orden de npm install**: Primero crear `package.json`, luego ejecutar `npm install` dentro de `src/frontend/`. Esto genera `node_modules/` y `package-lock.json`. Asegurar que `src/frontend/node_modules/` está en `.gitignore`.

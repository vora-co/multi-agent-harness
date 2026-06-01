"""Tests for Feature #9: Vue 3 SPA boilerplate with Vite.

SPATest uses unittest + Playwright (library mode) for e2e verification.
"""

import unittest
import subprocess
import time
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


FRONTEND_DIR = Path(__file__).resolve().parent.parent / "src" / "frontend"
VITE_PORT = "5199"
BASE_URL = f"http://localhost:{VITE_PORT}"


class SPATest(unittest.TestCase):
    """E2E tests for the Vue 3 SPA boilerplate."""

    @classmethod
    def setUpClass(cls):
        """Install dependencies and build the SPA once."""
        # npm install
        result = subprocess.run(
            ["npm", "install"],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"npm install failed (rc={result.returncode}): "
                f"{result.stderr[:500]}"
            )

        # npx vite build
        result = subprocess.run(
            ["npx", "vite", "build"],
            cwd=str(FRONTEND_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"vite build failed (rc={result.returncode}): "
                f"{result.stderr[:500]}"
            )

    def setUp(self):
        """Start Vite dev server and launch Playwright browser."""
        # Ensure no previous vite is occupying the port
        self._kill_vite_on_port()

        self._vite_proc = subprocess.Popen(
            ["npx", "vite", "--port", VITE_PORT, "--strictPort"],
            cwd=str(FRONTEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for Vite to be ready (poll until HTTP 200 or timeout)
        deadline = time.time() + 10
        ready = False
        while time.time() < deadline:
            try:
                check = subprocess.run(
                    ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                     BASE_URL],
                    capture_output=True,
                    text=True,
                    timeout=2,
                )
                if check.stdout.strip() == "200":
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)

        if not ready:
            self._kill_vite_process()
            raise RuntimeError("Vite dev server did not become ready within 10s")

        # Launch Playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=True)
        self._page = self._browser.new_page()

    def tearDown(self):
        """Close Playwright and kill Vite."""
        if hasattr(self, "_page") and self._page:
            self._page.close()
        if hasattr(self, "_browser") and self._browser:
            self._browser.close()
        if hasattr(self, "_pw") and self._pw:
            self._pw.stop()
        self._kill_vite_process()
        self._kill_vite_on_port()

    # ------------------------------------------------------------------
    # Helper methods
    # ------------------------------------------------------------------

    def _kill_vite_process(self):
        """Terminate/kill the tracked Vite subprocess."""
        proc = getattr(self, "_vite_proc", None)
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=5)
            except Exception:
                pass
        except Exception:
            pass

    def _kill_vite_on_port(self):
        """Brute-force kill anything listening on VITE_PORT."""
        # pkill is cross-platform enough on macOS/Linux CI
        try:
            subprocess.run(
                ["pkill", "-f", f"vite.*{VITE_PORT}"],
                capture_output=True,
                timeout=5,
            )
        except Exception:
            pass
        time.sleep(0.3)

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_static_build_existe(self):
        """Verify dist/index.html exists after vite build."""
        index_path = FRONTEND_DIR / "dist" / "index.html"
        self.assertTrue(
            index_path.is_file(),
            f"Expected {index_path} to exist after build",
        )

    def test_vite_dev_sirve_home(self):
        """Home page shows branding and Login/Register buttons."""
        self._page.goto(BASE_URL, wait_until="networkidle")
        text = self._page.text_content("body") or ""

        # Branding "PlaySession" visible
        self.assertIn(
            "PlaySession",
            text,
            "Home page should contain 'PlaySession' branding",
        )

        # Login button
        login_btn = self._page.locator('[data-testid="login-btn"]')
        self.assertTrue(login_btn.is_visible(), "Login button should be visible")

        # Register button
        register_btn = self._page.locator('[data-testid="register-btn"]')
        self.assertTrue(
            register_btn.is_visible(), "Register button should be visible"
        )

    def test_vite_dev_sirve_login(self):
        """Login page has email, password fields and a submit button."""
        self._page.goto(f"{BASE_URL}/login", wait_until="networkidle")

        email_input = self._page.locator('[data-testid="login-email"]')
        self.assertTrue(email_input.is_visible(), "Email field should be visible")

        password_input = self._page.locator('[data-testid="login-password"]')
        self.assertTrue(
            password_input.is_visible(), "Password field should be visible"
        )

        submit_btn = self._page.locator('[data-testid="login-submit"]')
        self.assertTrue(
            submit_btn.is_visible(), "Login submit button should be visible"
        )

    def test_vite_dev_sirve_register_navegacion(self):
        """Clicking Register on Home navigates to /register."""
        self._page.goto(BASE_URL, wait_until="networkidle")

        register_btn = self._page.locator('[data-testid="register-btn"]')
        register_btn.click()
        self._page.wait_for_url("**/register", timeout=5000)

        self.assertIn(
            "/register",
            self._page.url,
            "URL should contain /register after clicking Register",
        )

    def test_vite_dev_dashboard_redirige_sin_token(self):
        """Navigating to /dashboard without a token redirects to /login."""
        # Clear any leftover token
        self._page.goto(BASE_URL, wait_until="networkidle")
        self._page.evaluate("() => localStorage.removeItem('playsession_token')")

        # Now navigate to dashboard
        self._page.goto(f"{BASE_URL}/dashboard", wait_until="networkidle")

        # Should end up on /login
        self.assertIn(
            "/login",
            self._page.url,
            "Should be redirected to /login when no token is present",
        )

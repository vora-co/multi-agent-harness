"""
E2E tests for authentication flows using Playwright.
Requires: pip install pytest-playwright playwright && python3 -m playwright install chromium
"""
import pytest
import subprocess
import time
import os
import signal
import httpx


@pytest.fixture(scope="module")
def backend_server():
    """Start the FastAPI backend on port 8000 for the test module."""
    # Build the frontend first so Vite serves fresh assets
    frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')
    
    # Start uvicorn
    proc = subprocess.Popen(
        ['python3', '-m', 'uvicorn', 'src.main:app', '--host', '127.0.0.1', '--port', '8000'],
        cwd=os.path.join(os.path.dirname(__file__), '..'),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait for server to be ready
    for _ in range(20):
        try:
            resp = httpx.get('http://127.0.0.1:8000/api/v1/health', timeout=1)
            if resp.status_code == 200:
                break
        except Exception:
            pass
        time.sleep(0.5)
    else:
        proc.terminate()
        proc.wait()
        pytest.fail('Backend did not start')
    
    yield proc
    
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture(scope="module")
def frontend_server():
    """Start the Vite dev server on port 5173."""
    frontend_dir = os.path.join(os.path.dirname(__file__), '..', 'frontend')
    
    # Install deps if needed
    subprocess.run(['npm', 'install'], cwd=frontend_dir, capture_output=True, timeout=30)
    
    proc = subprocess.Popen(
        ['npx', 'vite', '--port', '5173', '--strictPort'],
        cwd=frontend_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(3)
    
    yield proc
    
    proc.send_signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


@pytest.fixture
def page(browser, frontend_server):
    """Create a new page for each test."""
    context = browser.new_context()
    page = context.new_page()
    yield page
    context.close()


def test_login_successful_redirects_to_home(page, backend_server):
    """Login with valid credentials should redirect to home page showing welcome."""
    # First register a user (or try login directly; register to be safe)
    page.goto('http://localhost:5173/register')
    page.wait_for_load_state('networkidle')
    
    # Register e2e_test_user
    page.fill('#username', 'e2e_test_user')
    page.fill('#password', 'testpass123')
    page.select_option('#role', 'student')
    page.click('button[type="submit"]')
    
    # After registration, auto-login should redirect to home
    page.wait_for_url('http://localhost:5173/')
    page.wait_for_load_state('networkidle')
    
    # Verify welcome message
    assert page.is_visible('text=Hello')
    assert page.is_visible('text=e2e_test_user')


def test_login_failed_shows_error(page, backend_server):
    """Login with invalid credentials should show an error message."""
    page.goto('http://localhost:5173/login')
    page.wait_for_load_state('networkidle')
    
    page.fill('#username', 'nonexistent_user')
    page.fill('#password', 'wrongpassword')
    page.click('button[type="submit"]')
    
    # Wait for error message
    page.wait_for_selector('[role="alert"]', timeout=5000)
    
    error_el = page.locator('[role="alert"]')
    assert error_el.is_visible()
    error_text = error_el.inner_text()
    assert len(error_text) > 0
    
    # Should still be on login page
    assert '/login' in page.url


def test_logout_redirects_to_login(page, backend_server):
    """After logout, user should be redirected to login page."""
    # First login: register then logout
    page.goto('http://localhost:5173/register')
    page.wait_for_load_state('networkidle')
    page.fill('#username', 'e2e_logout_user')
    page.fill('#password', 'testpass456')
    page.select_option('#role', 'student')
    page.click('button[type="submit"]')
    page.wait_for_url('http://localhost:5173/')
    page.wait_for_load_state('networkidle')
    
    # Now logout
    page.click('button:has-text("Logout")')
    page.wait_for_url('http://localhost:5173/login')
    
    # Verify on login page
    assert '/login' in page.url
    assert page.is_visible('text=Login')

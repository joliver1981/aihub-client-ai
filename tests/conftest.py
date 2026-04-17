"""
Playwright Test Configuration for AI Hub
=========================================

This file contains shared fixtures and configuration used across all e2e tests,
including role-based authentication fixtures for RBAC testing.

Test Users:
    - admin / admin         - Role 3 (Admin) - Full access
    - developer / developer - Role 2 (Developer) - Developer + User access
    - test / test           - Role 1 (User) - Basic access only

Usage:
    1. Start your Flask application: python app.py
    2. Run tests: pytest tests/e2e/ -v
    3. Run with visible browser: pytest tests/e2e/ -v --headed
    4. Run specific test: pytest tests/e2e/test_smoke.py -v --headed
    5. Run RBAC tests: pytest tests/e2e/test_rbac.py -v
"""

from __future__ import annotations

import pytest
from typing import Generator, TYPE_CHECKING
import os

try:
    from playwright.sync_api import Page, BrowserContext, Browser, expect
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    # Provide stubs so type annotations don't break at module level
    Page = None
    BrowserContext = None
    Browser = None

# =============================================================================
# CONFIGURATION
# =============================================================================

# Base URL for your application - adjust if running on different port
BASE_URL = "http://10.0.0.7:5001"  #os.environ.get("TEST_BASE_URL", "http://10.0.0.7:5001")

# Test user credentials - default user for backward compatibility
TEST_USER_EMAIL = os.environ.get("TEST_USER_EMAIL", "admin")
TEST_USER_PASSWORD = os.environ.get("TEST_USER_PASSWORD", "admin")

# Role-specific test users for RBAC testing
RBAC_TEST_USERS = {
    "admin": {
        "username": "admin",
        "password": "admin",
        "role": 3,
        "description": "Full admin access"
    },
    "developer": {
        "username": "developer",
        "password": "developer",
        "role": 2,
        "description": "Developer tools + user features"
    },
    "user": {
        "username": "test",
        "password": "test",
        "role": 1,
        "description": "Basic user features only"
    },
}

# Timeouts (in milliseconds)
DEFAULT_TIMEOUT = 30000  # 30 seconds for page loads
NAVIGATION_TIMEOUT = 15000  # 15 seconds for navigation


# =============================================================================
# PYTEST CONFIGURATION
# =============================================================================

def pytest_configure(config):
    """Add custom markers for test categorization."""
    config.addinivalue_line("markers", "smoke: Quick smoke tests for basic functionality")
    config.addinivalue_line("markers", "auth: Tests that require authentication")
    config.addinivalue_line("markers", "slow: Tests that take longer to run")
    config.addinivalue_line("markers", "rbac: Role-based access control tests")
    config.addinivalue_line("markers", "admin_only: Tests requiring admin role")
    config.addinivalue_line("markers", "developer_only: Tests requiring developer role")
    config.addinivalue_line("markers", "user_accessible: Tests accessible by regular users")
    config.addinivalue_line("markers", "ui: UI functional tests (buttons, forms, modals)")
    config.addinivalue_line("markers", "crud: Create/read/update/delete lifecycle tests")
    config.addinivalue_line("markers", "workflow: Workflow builder/execution tests")
    config.addinivalue_line("markers", "environments: Agent environment end-to-end tests")


# =============================================================================
# BROWSER CONFIGURATION FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def browser_context_args(browser_context_args):
    """Configure browser context settings for all tests."""
    return {
        **browser_context_args,
        "viewport": {"width": 1280, "height": 800},
        "ignore_https_errors": True,  # Useful for local dev with self-signed certs
    }


# =============================================================================
# AUTHENTICATION HELPER FUNCTIONS
# =============================================================================

def perform_login(page: Page, base_url: str, username: str, password: str) -> bool:
    """
    Perform login with specific credentials.
    
    Args:
        page: Playwright page object
        base_url: Application base URL
        username: Username to login with
        password: Password to login with
        
    Returns:
        True if login successful, False otherwise
    """
    url = base_url if base_url else BASE_URL
    
    try:
        # Navigate to login
        page.goto(f"{url}/login", timeout=NAVIGATION_TIMEOUT)
        page.wait_for_load_state("networkidle")
        
        # Check if already logged in (redirected to home)
        if "/login" not in page.url:
            # Already logged in - might be wrong user, logout first
            try:
                page.goto(f"{url}/logout", timeout=NAVIGATION_TIMEOUT)
                page.wait_for_load_state("networkidle")
                page.goto(f"{url}/login", timeout=NAVIGATION_TIMEOUT)
                page.wait_for_load_state("networkidle")
            except Exception:
                pass
        
        # Fill in login form
        username_field = page.locator('input[name="username"], input[name="email"], #username, #email').first
        password_field = page.locator('input[name="password"], input[type="password"], #password').first
        
        username_field.fill(username)
        password_field.fill(password)
        
        # Submit the form
        submit_button = page.locator('button[type="submit"], input[type="submit"], .btn-login').first
        submit_button.click()
        
        # Wait for navigation away from login page
        page.wait_for_url(lambda url: "/login" not in url, timeout=DEFAULT_TIMEOUT)
        page.wait_for_load_state("networkidle")
        
        return True
        
    except Exception as e:
        print(f"Login failed for {username}: {e}")
        return False


# =============================================================================
# DEFAULT AUTHENTICATION FIXTURE (backward compatible)
# =============================================================================

@pytest.fixture
def login_page(page: Page, base_url: str) -> Page:
    """Navigate to the login page."""
    url = base_url if base_url else BASE_URL
    page.goto(f"{url}/login", timeout=NAVIGATION_TIMEOUT)
    page.wait_for_load_state("networkidle")
    return page


@pytest.fixture
def logged_in_page(page: Page, base_url: str) -> Generator[Page, None, None]:
    """
    Provides a page with an authenticated user session.
    
    This fixture:
    1. Navigates to the login page
    2. Fills in credentials
    3. Submits the form
    4. Waits for successful redirect to dashboard
    5. Returns the authenticated page for use in tests
    
    Usage in tests:
        def test_something(logged_in_page, base_url):
            logged_in_page.goto(f"{base_url}/some-protected-page")
            # ... rest of test
    """
    url = base_url if base_url else BASE_URL
    
    success = perform_login(page, url, TEST_USER_EMAIL, TEST_USER_PASSWORD)
    if not success:
        pytest.skip(f"Could not log in as default user ({TEST_USER_EMAIL})")
    
    yield page


# =============================================================================
# ROLE-SPECIFIC PAGE FIXTURES (for RBAC testing)
# =============================================================================

@pytest.fixture
def admin_page(browser: Browser, base_url: str) -> Generator[Page, None, None]:
    """
    Provides a page logged in as admin user (role=3).
    
    Uses a SEPARATE browser context to avoid session conflicts with other role fixtures.
    
    Use for tests requiring full admin privileges:
    - User management
    - Group management
    - All developer and user features
    
    Usage:
        def test_admin_feature(admin_page, base_url):
            admin_page.goto(f"{base_url}/users")
            # Admin should have access
    """
    url = base_url if base_url else BASE_URL
    creds = RBAC_TEST_USERS["admin"]
    
    # Create a separate context for this role
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True
    )
    page = context.new_page()
    
    success = perform_login(page, url, creds["username"], creds["password"])
    if not success:
        context.close()
        pytest.skip(f"Could not log in as admin user ({creds['username']})")
    
    yield page
    
    context.close()


@pytest.fixture
def developer_page(browser: Browser, base_url: str) -> Generator[Page, None, None]:
    """
    Provides a page logged in as developer user (role=2).
    
    Uses a SEPARATE browser context to avoid session conflicts with other role fixtures.
    
    Use for tests requiring developer privileges:
    - Workflow builder
    - Connection management
    - Agent creation
    - Monitoring
    - All user features
    
    Usage:
        def test_developer_feature(developer_page, base_url):
            developer_page.goto(f"{base_url}/workflow_tool")
            # Developer should have access
    """
    url = base_url if base_url else BASE_URL
    creds = RBAC_TEST_USERS["developer"]
    
    # Create a separate context for this role
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True
    )
    page = context.new_page()
    
    success = perform_login(page, url, creds["username"], creds["password"])
    if not success:
        context.close()
        pytest.skip(f"Could not log in as developer user ({creds['username']})")
    
    yield page
    
    context.close()


@pytest.fixture
def user_page(browser: Browser, base_url: str) -> Generator[Page, None, None]:
    """
    Provides a page logged in as regular user (role=1).
    
    Uses a SEPARATE browser context to avoid session conflicts with other role fixtures.
    
    Use for tests with basic user privileges only:
    - Job scheduling
    - Using assistants
    - Dashboard access
    - Agent knowledge management
    
    Usage:
        def test_user_blocked(user_page, base_url):
            user_page.goto(f"{base_url}/users")
            # User should be blocked from admin pages
    """
    url = base_url if base_url else BASE_URL
    creds = RBAC_TEST_USERS["user"]
    
    # Create a separate context for this role
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True
    )
    page = context.new_page()
    
    success = perform_login(page, url, creds["username"], creds["password"])
    if not success:
        context.close()
        pytest.skip(f"Could not log in as regular user ({creds['username']})")
    
    yield page
    
    context.close()


@pytest.fixture
def anonymous_page(browser: Browser) -> Generator[Page, None, None]:
    """
    Provides a page without any authentication.
    
    Uses a SEPARATE browser context to ensure no session cookies.
    
    Use for tests verifying login requirements.
    
    Usage:
        def test_anonymous_redirect(anonymous_page, base_url):
            anonymous_page.goto(f"{base_url}/jobs")
            # Should be redirected to login
    """
    # Create a fresh context with no cookies
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        ignore_https_errors=True
    )
    page = context.new_page()
    
    yield page
    
    context.close()


# =============================================================================
# UTILITY FIXTURES
# =============================================================================

@pytest.fixture
def screenshot_on_failure(page: Page, request):
    """
    Automatically capture a screenshot when a test fails.
    Screenshots are saved to: tests/screenshots/
    """
    yield
    
    # Check if the test failed
    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        # Create screenshots directory if it doesn't exist
        screenshot_dir = os.path.join(os.path.dirname(__file__), "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        
        # Generate filename from test name
        test_name = request.node.name.replace("/", "_").replace(":", "_")
        screenshot_path = os.path.join(screenshot_dir, f"{test_name}.png")
        
        # Capture screenshot
        page.screenshot(path=screenshot_path)
        print(f"\nScreenshot saved: {screenshot_path}")


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Hook to make test result available to fixtures."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, f"rep_{rep.when}", rep)


# =============================================================================
# PAGE OBJECT HELPERS
# =============================================================================

class DashboardPage:
    """Page object for the AI Hub Dashboard."""
    
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url if base_url else BASE_URL
        
        # Define locators for key elements
        self.welcome_heading = page.locator(".welcome-section h1, .welcome-free h1")
        self.new_chat_button = page.locator('a:has-text("New Chat"), a:has-text("Chat with AI")')
        self.create_agent_button = page.locator('a:has-text("Create Agent"), a:has-text("Manage Agents")')
        self.quick_actions_section = page.locator(".action-grid")
        self.sidebar = page.locator(".new-sidebar, #newSidebar")
    
    def navigate(self):
        """Navigate to the dashboard."""
        self.page.goto(f"{self.base_url}/", timeout=NAVIGATION_TIMEOUT)
        self.page.wait_for_load_state("networkidle")
        return self
    
    def get_welcome_text(self) -> str:
        """Get the welcome message text."""
        return self.welcome_heading.text_content()
    
    def click_new_chat(self):
        """Click the New Chat button."""
        self.new_chat_button.first.click()
        self.page.wait_for_load_state("networkidle")
    
    def click_create_agent(self):
        """Click the Create Agent button."""
        self.create_agent_button.first.click()
        self.page.wait_for_load_state("networkidle")
    
    def is_sidebar_visible(self) -> bool:
        """Check if the sidebar is visible."""
        return self.sidebar.is_visible()


class AgentsPage:
    """Page object for the Agents/Assistants page."""
    
    def __init__(self, page: Page, base_url: str):
        self.page = page
        self.base_url = base_url if base_url else BASE_URL
    
    def navigate(self):
        """Navigate to the agents page."""
        self.page.goto(f"{self.base_url}/assistants", timeout=NAVIGATION_TIMEOUT)
        self.page.wait_for_load_state("networkidle")
        return self


@pytest.fixture
def dashboard_page(logged_in_page: Page, base_url: str) -> DashboardPage:
    """Provides a DashboardPage object for testing the dashboard."""
    return DashboardPage(logged_in_page, base_url if base_url else BASE_URL)


@pytest.fixture
def agents_page(logged_in_page: Page, base_url: str) -> AgentsPage:
    """Provides an AgentsPage object for testing the agents functionality."""
    return AgentsPage(logged_in_page, base_url if base_url else BASE_URL)

import pytest
from django.test import Client
from apps.accounts.models import Role, User


@pytest.mark.django_db
class TestBrowserNavigationAndSignoutGuard:
    """
    Validates browser history navigation, undo/redo controls, and signout confirmation guard.
    """

    def test_landing_page_never_cache_headers(self, client):
        """Anonymous landing page has never_cache headers to prevent bfcache retention."""
        response = client.get("/")
        assert response.status_code == 200
        # Check Cache-Control headers
        cache_control = response.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control or "no-store" in cache_control

    def test_authenticated_user_can_view_landing_page_with_dashboard_nav(self, client):
        """Authenticated users visiting '/' can view the landing page with 'Dashboard' and 'Sign Out' actions."""
        role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        user = User.objects.create_user(
            email="nav_test_requester@procuresphere.com",
            password="Password123!",
            role=role,
        )
        client.force_login(user)

        response = client.get("/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "ProcureSphere 360" in content
        assert "/dashboard/" in content or "Dashboard" in content

        cache_control = response.headers.get("Cache-Control", "")
        assert "no-cache" in cache_control or "no-store" in cache_control

    def test_dashboard_templates_contain_signout_modal_and_no_inpage_arrow_boxes(self, client):
        """Dashboard renders sign-out confirmation modal and avoids in-page undo/redo boxes (browser arrows used instead)."""
        role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        user = User.objects.create_user(
            email="nav_arrows_test@procuresphere.com",
            password="Password123!",
            role=role,
        )
        client.force_login(user)

        response = client.get("/requester/")
        assert response.status_code == 200
        content = response.content.decode("utf-8")

        # In-page navigation arrow boxes are removed (browser native arrows are used instead)
        assert "ps-nav-arrows" not in content
        assert "psNavBackBtn" not in content
        assert "psNavForwardBtn" not in content

        # Verify signout confirmation modal is rendered with auto-dismiss countdown elements
        assert 'id="signout-confirm-modal"' in content
        assert "Sign Out Confirmation" in content
        assert "Stay on Dashboard" in content
        assert 'href="/logout/"' in content
        assert 'id="signout-countdown-sec"' in content
        assert 'id="signout-progress-bar"' in content

    def test_sidebar_toggle_button_partial_has_no_inpage_arrow_boxes(self):
        """Verify sidebar_toggle_button.html renders clean sidebar toggle button without separate arrow boxes."""
        from django.template.loader import render_to_string

        rendered = render_to_string("partials/sidebar_toggle_button.html", {"sidebar_id": "testSidebar"})
        assert "sidebarToggleBtn" in rendered
        assert "ps-nav-arrows" not in rendered
        assert "psNavBackBtn" not in rendered
        assert "psNavForwardBtn" not in rendered

    def test_vendor_dashboard_and_subpages_guard_structure(self, client):
        """Vendor dashboard and subpages contain signout modal in layout without in-page arrow boxes."""
        from apps.vendors.models import Vendor, VendorCategory

        role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor Portal User"})
        cat, _ = VendorCategory.objects.get_or_create(code="IT_SERVICES", defaults={"name": "IT Services"})
        vendor, _ = Vendor.objects.get_or_create(
            vendor_number="VND-NAV-TEST-01",
            defaults={"legal_name": "Nav Test Vendor Inc", "category": cat, "email": "navtest@vendor.com"},
        )
        user = User.objects.create_user(
            email="nav_vendor_guard_test@procuresphere.com",
            password="Password123!",
            role=role,
        )
        user.vendor = vendor
        user.save()
        client.force_login(user)

        # Check vendor root dashboard and vendor subpages
        for subpath in ["/vendor/", "/vendor/profile/", "/vendor/sourcing/", "/vendor/bids/"]:
            response = client.get(subpath)
            assert response.status_code == 200
            content = response.content.decode("utf-8")
            assert "ps-nav-arrows" not in content
            assert "psNavBackBtn" not in content
            assert "psNavForwardBtn" not in content
            assert 'id="signout-confirm-modal"' in content

    def test_app_js_contains_subpage_safe_navigation_guard(self):
        """Static app.js has strict subpage bypass, section hash tracking, and sessionStorage subpage tracking."""
        with open("static/js/app.js", "r", encoding="utf-8") as f:
            app_js = f.read()

        # Strict root detection
        assert "isDashboardRoot" in app_js
        assert "'/vendor'" in app_js
        assert "'/vendor/dashboard'" in app_js

        # Subpage safety checks
        assert "ps_visited_subpage" in app_js
        assert "cameFromSubpage" in app_js
        assert "isSectionHash" in app_js
        assert "if (!currentlyOnRoot) {" in app_js
        assert "showSignoutModal()" in app_js
        assert "armDashboardGuard()" in app_js

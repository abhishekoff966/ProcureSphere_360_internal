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

    def test_authenticated_user_on_landing_page_redirects_with_never_cache(self, client):
        """Authenticated users visiting '/' are served dashboard or redirected with never_cache headers."""
        role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        user = User.objects.create_user(
            email="nav_test_requester@procuresphere.com",
            password="Password123!",
            role=role,
        )
        client.force_login(user)

        response = client.get("/")
        # In test mode, landing_home delegates directly to home_view (200), or redirects (302)
        assert response.status_code in (200, 302)
        if response.status_code == 302:
            assert response.url == "/requester/"

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

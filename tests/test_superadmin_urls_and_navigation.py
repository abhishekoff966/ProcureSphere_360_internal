import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.organization.models import Organization


@pytest.fixture
def superadmin_client(db):
    role_sa, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB")
    user = User.objects.create_superuser(
        email="superadmin.navtest@example.com",
        password="Password123!",
        role=role_sa,
    )
    user.organization = org
    user.save()
    client = Client()
    client.force_login(user)
    return client


@pytest.mark.django_db
class TestSuperadminURLsAndNavigation:
    """Verify that all superadmin endpoints and sidebar links use /superadmin/ correctly."""

    def test_dashboard_and_home_urls(self, superadmin_client):
        # /superadmin/
        resp_home = superadmin_client.get(reverse("superadmin:home"))
        assert resp_home.status_code == 200
        assert "Contact Enquiries" not in resp_home.content.decode("utf-8")

        # /superadmin/dashboard/
        resp_dash = superadmin_client.get(reverse("superadmin:dashboard"))
        assert resp_dash.status_code == 200

        # /admin/dashboard/ redirects to /superadmin/
        resp_admin_dash = superadmin_client.get("/admin/dashboard/")
        assert resp_admin_dash.status_code == 302
        assert resp_admin_dash.url == "/superadmin/"

    def test_superadmin_submodule_urls(self, superadmin_client):
        # /superadmin/notifications/
        resp_notif = superadmin_client.get(reverse("superadmin:notifications"))
        assert resp_notif.status_code == 200

        # /superadmin/reports/
        resp_rep = superadmin_client.get(reverse("superadmin:reports"))
        assert resp_rep.status_code == 200

        # /superadmin/vendors/
        resp_ven = superadmin_client.get(reverse("superadmin:vendors"))
        assert resp_ven.status_code == 200

        # /superadmin/rules/
        resp_rules = superadmin_client.get(reverse("superadmin:rules"))
        assert resp_rules.status_code == 200

    def test_sidebar_links_contain_superadmin_prefix(self, superadmin_client):
        resp = superadmin_client.get("/superadmin/organizations/")
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")

        # Dashboard link must be /superadmin/ (not /admin/dashboard/ or /admin/)
        assert 'href="/superadmin/"' in content
        assert 'href="/admin/dashboard/"' not in content

        # Notifications, Reports, Vendors, Rules must have /superadmin/
        assert 'href="/superadmin/notifications/"' in content
        assert 'href="/superadmin/reports/"' in content
        assert 'href="/superadmin/vendors/"' in content
        assert 'href="/superadmin/rules/"' in content

        # Contact Enquiries must NOT appear in Organization Admin dashboard/sidebar
        assert 'href="/superadmin/contact-enquiries/"' not in content
        assert "Contact Enquiries" not in content

        # Ensure old un-prefixed links are not in the sidebar
        assert 'href="/notifications/"' not in content
        assert 'href="/vendors/"' not in content
        assert 'href="/reports/hub/"' not in content
        assert 'href="/invoices/settings/tolerances/"' not in content


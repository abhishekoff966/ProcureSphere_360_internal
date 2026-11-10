import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.superadmin_forms import UserAdminForm


@pytest.mark.django_db
class TestAdminDashboardUserRoleVendorExclusion:
    """Verify that Vendor Portal User (VENDOR_USER) is excluded when adding users in admin dashboards."""

    def test_user_admin_form_excludes_vendor_user_role(self):
        for code, name in Role.ROLE_CHOICES:
            Role.objects.get_or_create(code=code, defaults={"name": name})

        form = UserAdminForm()
        role_choices = [c[1] for c in form.fields["role"].choices]
        role_codes = list(form.fields["role"].queryset.values_list("code", flat=True))

        assert Role.VENDOR_USER not in role_codes
        assert not any("VENDOR_USER" in str(c) for c in role_choices)
        assert not any("Vendor Portal User" in str(c) for c in role_choices)

    def test_user_admin_form_rejects_vendor_role_submission(self):
        vendor_role, _ = Role.objects.get_or_create(
            code=Role.VENDOR_USER, defaults={"name": "Vendor Portal User"}
        )
        form_data = {
            "email": "vendor_attempt@example.com",
            "role": str(vendor_role.id),
            "is_active": True,
        }
        form = UserAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "role" in form.errors
        assert any(
            "not one of the available choices" in str(e) or "Vendor" in str(e)
            for e in form.errors["role"]
        )

    def test_user_create_modal_endpoint_does_not_render_vendor_option(self):
        for code, name in Role.ROLE_CHOICES:
            Role.objects.get_or_create(code=code, defaults={"name": name})

        role_sa, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
        sa_user = User.objects.create_superuser(
            email="superadmin.vendorcheck@example.com",
            password="Password123!",
            role=role_sa,
        )
        client = Client()
        client.force_login(sa_user)

        url = reverse("superadmin:user_create")
        resp = client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")

        assert "VENDOR_USER" not in content
        assert "Vendor Portal User" not in content


import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.superadmin_forms import OrganizationAdminForm
from apps.organization.admin import OrganizationDjangoAdminForm
from apps.organization.models import Organization
from apps.organization.services import (
    create_organization_service,
    create_organization_with_admin_service,
)
from apps.organization.validators import (
    validate_org_address,
    validate_org_admin_email,
    validate_org_code,
    validate_org_name,
    validate_org_tax_identifier,
)


@pytest.mark.django_db
class TestOrganizationValidators:
    """Unit tests for organization input validation functions."""

    def test_org_code_validation_valid(self):
        assert validate_org_code("HPE-GLOBAL_01") == "HPE-GLOBAL_01"
        assert validate_org_code("acme-corp") == "ACME-CORP"
        assert validate_org_code("VPD123") == "VPD123"

    def test_org_code_validation_rejects_arbitrary_characters(self):
        with pytest.raises(ValidationError) as exc:
            validate_org_code("HPE<script>alert(1)</script>")
        assert "invalid characters" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_code("HPE GLOBAL")  # Space not allowed
        assert "invalid characters" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_code("HPE@CORP!")  # Punctuation not allowed
        assert "invalid characters" in str(exc.value)

    def test_org_code_validation_rejects_unbounded_lengths(self):
        with pytest.raises(ValidationError) as exc:
            validate_org_code("A")  # < 2 chars
        assert "at least 2 characters" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_code("A" * 51)  # > 50 chars
        assert "cannot exceed 50 characters" in str(exc.value)

    def test_org_name_validation_valid(self):
        assert validate_org_name("Acme Global, Inc.") == "Acme Global, Inc."
        assert validate_org_name("AT&T / Hewlett Packard (Enterprise)") == "AT&T / Hewlett Packard (Enterprise)"

    def test_org_name_validation_rejects_arbitrary_characters(self):
        with pytest.raises(ValidationError) as exc:
            validate_org_name("Acme <script>alert('xss')</script>")
        assert "HTML or script" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_name("Acme\x00Corp")  # Control character
        assert "control characters" in str(exc.value)

    def test_org_name_validation_rejects_unbounded_lengths(self):
        with pytest.raises(ValidationError) as exc:
            validate_org_name("A")  # < 2 chars
        assert "at least 2 characters" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_name("A" * 256)  # > 255 chars
        assert "cannot exceed 255 characters" in str(exc.value)

    def test_org_tax_identifier_validation(self):
        assert validate_org_tax_identifier("US-99-8877665") == "US-99-8877665"
        assert validate_org_tax_identifier("27AAAPL1234C1Z5") == "27AAAPL1234C1Z5"
        assert validate_org_tax_identifier("TECHNOVA001_tax") == "TECHNOVA001_tax"
        assert validate_org_tax_identifier("") == ""

        with pytest.raises(ValidationError) as exc:
            validate_org_tax_identifier("TAX<script>")
        assert "control characters" in str(exc.value) or "invalid characters" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_tax_identifier("T" * 101)
        assert "cannot exceed 100 characters" in str(exc.value)

    def test_org_address_validation(self):
        assert validate_org_address("100 Innovation Way, Suite 400\nSilicon Valley, CA") == (
            "100 Innovation Way, Suite 400\nSilicon Valley, CA"
        )
        assert validate_org_address("") == ""

        with pytest.raises(ValidationError) as exc:
            validate_org_address("Address with <script>evil()</script>")
        assert "script tags" in str(exc.value)

        with pytest.raises(ValidationError) as exc:
            validate_org_address("A" * 501)
        assert "cannot exceed 500 characters" in str(exc.value)

    def test_admin_email_validation(self):
        # Mandatory on create
        with pytest.raises(ValidationError) as exc:
            validate_org_admin_email("", is_create=True, admin_user=None)
        assert "Admin Email is mandatory" in str(exc.value)

        # Invalid format
        with pytest.raises(ValidationError) as exc:
            validate_org_admin_email("invalid-email-format", is_create=True)
        assert "valid email address" in str(exc.value)

        # Unbounded length > 254
        long_email = ("a" * 250) + "@example.com"
        with pytest.raises(ValidationError) as exc:
            validate_org_admin_email(long_email, is_create=True)
        assert "cannot exceed 254 characters" in str(exc.value)

        # Valid
        assert validate_org_admin_email("Admin@Example.COM", is_create=True) == "admin@example.com"


@pytest.mark.django_db
class TestSuperadminOrganizationFormValidation:
    """Integration tests for OrganizationAdminForm used in the Super Admin modal."""

    def test_superadmin_form_rejects_missing_mandatory_admin_email(self):
        form_data = {
            "code": "TEST-ORG-01",
            "name": "Test Organization",
            "tax_identifier": "US-12-3456789",
            "address": "123 Business St",
            "admin_email": "",  # Empty mandatory field
            "admin_first_name": "Test",
            "admin_last_name": "Admin",
        }
        form = OrganizationAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "admin_email" in form.errors
        assert any("mandatory" in str(e).lower() for e in form.errors["admin_email"])
        # Check visual warning highlight styling attached to widget
        assert "is-invalid" in form.fields["admin_email"].widget.attrs.get("class", "")
        assert "border: 2px solid" in form.fields["admin_email"].widget.attrs.get("style", "")

    def test_superadmin_form_rejects_arbitrary_characters_in_code_and_name(self):
        form_data = {
            "code": "ORG<script>alert(1)</script>",
            "name": "Evil Corp <script>",
            "admin_email": "admin@evilcorp.com",
        }
        form = OrganizationAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "code" in form.errors
        assert "name" in form.errors
        assert "is-invalid" in form.fields["code"].widget.attrs.get("class", "")
        assert "is-invalid" in form.fields["name"].widget.attrs.get("class", "")

    def test_superadmin_form_rejects_unbounded_lengths(self):
        form_data = {
            "code": "O" * 55,  # Exceeds max 50
            "name": "N" * 260,  # Exceeds max 255
            "tax_identifier": "T" * 105,  # Exceeds max 100
            "address": "A" * 505,  # Exceeds max 500
            "admin_email": ("a" * 250) + "@test.com",  # Exceeds max 254
        }
        form = OrganizationAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "code" in form.errors
        assert "name" in form.errors
        assert "tax_identifier" in form.errors
        assert "address" in form.errors
        assert "admin_email" in form.errors

    def test_superadmin_form_valid_submission(self):
        form_data = {
            "code": "VALID-ORG-99",
            "name": "Valid Organization Services, LLC",
            "tax_identifier": "US-99-1234567",
            "address": "456 Tech Park, Austin, TX",
            "admin_email": "admin@validorg99.com",
            "admin_first_name": "Sarah",
            "admin_last_name": "Connor",
            "is_active": True,
        }
        form = OrganizationAdminForm(data=form_data)
        assert form.is_valid() is True, form.errors


@pytest.mark.django_db
class TestDjangoAdminOrganizationFormValidation:
    """Integration tests for OrganizationDjangoAdminForm used in Django Admin."""

    def test_django_admin_form_rejects_missing_mandatory_admin_email(self):
        form_data = {
            "code": "ADMIN-ORG-01",
            "name": "Admin Org One",
            "admin_email": "",  # Missing mandatory
        }
        form = OrganizationDjangoAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "admin_email" in form.errors
        assert any("mandatory" in str(e).lower() for e in form.errors["admin_email"])
        assert "border: 2px solid" in form.fields["admin_email"].widget.attrs.get("style", "")

    def test_django_admin_form_rejects_arbitrary_characters(self):
        form_data = {
            "code": "BAD CODE WITH SPACES",
            "name": "Bad <script> Corp",
            "admin_email": "valid@email.com",
        }
        form = OrganizationDjangoAdminForm(data=form_data)
        assert form.is_valid() is False
        assert "code" in form.errors
        assert "name" in form.errors


@pytest.mark.django_db
class TestOrganizationServiceLayerValidation:
    """Tests that create_organization_service enforces input bounds and character rules."""

    def test_service_rejects_arbitrary_characters(self):
        with pytest.raises(ValidationError):
            create_organization_service(
                name="Injected <script> Org",
                code="INJECT_01",
            )

        with pytest.raises(ValidationError):
            create_organization_service(
                name="Valid Org Name",
                code="INVALID CODE!",
            )

    def test_service_rejects_unbounded_lengths(self):
        with pytest.raises(ValidationError):
            create_organization_service(
                name="V" * 300,
                code="VALID-CODE",
            )


@pytest.mark.django_db
def test_superadmin_modal_template_renders_visual_warning_highlights():
    """Verify that org_form.html contains visual warning elements and styles."""
    client = Client()
    # Create global superadmin without org
    role_sa, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    sa_user = User.objects.create_superuser(
        email="global.sa@test.local",
        password="Password123!",
        role=role_sa,
    )
    client.force_login(sa_user)

    url = reverse("superadmin:org_create")
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")

    # Check for visual warning elements
    assert "Admin Email <span class=\"text-danger fw-bold\">*</span>" in content
    assert "admin_email_warning_icon" in content
    assert "admin_email_client_error" in content
    assert "#modal-content-body .form-control.is-invalid" in content
    assert "border: 2px solid #dc3545" in content

    # Test POST with empty admin_email triggers server error highlight
    post_payload = {
        "code": "NEW-TEST-ORG",
        "name": "New Test Org",
        "admin_email": "",
    }
    resp_post = client.post(url, post_payload)
    assert resp_post.status_code == 200
    post_content = resp_post.content.decode("utf-8")
    assert "Admin Email is mandatory" in post_content
    assert "is-invalid" in post_content


@pytest.mark.django_db
def test_org_create_modal_renders_password_visibility_toggle_symbols():
    """Verify that org_form.html renders password visibility toggle symbols for Admin, Manager, and Auditor."""
    client = Client()
    role_sa, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    sa_user = User.objects.create_superuser(
        email="global.sa.pwdtest@test.local",
        password="Password123!",
        role=role_sa,
    )
    client.force_login(sa_user)

    url = reverse("superadmin:org_create")
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")

    # Admin Password toggle & eye symbol
    assert "toggleAdminPasswordBtn" in content
    assert "toggleAdminPasswordIcon" in content

    # Procurement Manager Password toggle & eye symbol
    assert "toggleProcMgrPasswordBtn" in content
    assert "toggleProcMgrPasswordIcon" in content

    # Compliance Auditor Password toggle & eye symbol
    assert "toggleAuditorPasswordBtn" in content
    assert "toggleAuditorPasswordIcon" in content

    # Verify eye icons
    assert content.count("fa-eye") >= 3


@pytest.mark.django_db
def test_org_modal_uses_innerhtml_and_admin_edit_preserves_modal_and_admin():
    """Verify org create/edit modals use innerHTML (never outerHTML destroying .modal-content) and /admin/ edit works."""
    client = Client()
    role_sa, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    sa_user = User.objects.create_superuser(
        email="global.sa.modaledit@test.local",
        password="Password123!",
        role=role_sa,
    )
    org = Organization.objects.create(
        name="TechNova Original",
        code="TECHNOVA001",
        tax_identifier="TECHNOVA001_tax",
        address="Old Address",
        is_active=True,
    )
    org_admin = User.objects.create_user(
        email="admin@technova.test",
        password="Password123!",
        role=role_sa,
        organization=org,
    )
    org.admin_user = org_admin
    org.save(update_fields=["admin_user"])

    client.force_login(sa_user)

    # /admin/ modal container and HTMX handlers
    dash_res = client.get("/admin/")
    assert dash_res.status_code == 200
    dash_html = dash_res.content.decode("utf-8")
    assert 'id="modal-content-body"' in dash_html
    assert "hidden.bs.modal" in dash_html

    # Create error renders neat error banner and keeps hx-swap="innerHTML"
    create_err = client.post(
        "/admin/organizations/create/",
        {
            "code": "BAD CODE!",
            "name": "Bad Org",
            "tax_identifier": "TECHNOVA001_tax",
            "admin_email": "newadmin@technova.test",
        },
    )
    assert create_err.status_code == 200
    create_html = create_err.content.decode("utf-8")
    assert 'hx-swap="innerHTML"' in create_html
    assert 'hx-swap="outerHTML"' not in create_html
    assert 'id="orgFormErrorAlert"' in create_html

    # Edit GET and POST under /admin/organizations/<id>/edit/
    edit_get = client.get(f"/admin/organizations/{org.pk}/edit/")
    assert edit_get.status_code == 200
    edit_get_html = edit_get.content.decode("utf-8")
    assert 'hx-swap="innerHTML"' in edit_get_html
    assert f'/admin/organizations/{org.pk}/edit/' in edit_get_html

    edit_post = client.post(
        f"/admin/organizations/{org.pk}/edit/",
        {
            "code": "TECHNOVA001",
            "name": "TechNova Solutions Pvt Ltd",
            "tax_identifier": "TECHNOVA001_tax",
            "address": "Updated Address",
            "is_active": "on",
            "admin_email": "",
        },
    )
    assert edit_post.status_code == 200
    org.refresh_from_db()
    assert org.name == "TechNova Solutions Pvt Ltd"
    assert org.address == "Updated Address"
    assert org.admin_user_id == org_admin.id


@pytest.mark.django_db
def test_admin_dashboard_does_not_render_duplicate_top_messagelist(client):
    role_sa, _ = Role.objects.get_or_create(
        code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"}
    )
    sa_user = User.objects.create_superuser(
        email="sa.msgcheck@example.com",
        password="Password123!",
        role=role_sa,
    )
    client.force_login(sa_user)

    create_ok = client.post(
        "/admin/organizations/create/",
        {
            "code": "TECHNOVA-ORG-002",
            "name": "TechNova Solutions Pvt Ltd",
            "tax_identifier": "TECHNOVA001_tax",
            "address": "123 Enterprise Way",
            "is_active": "on",
            "admin_email": "admin@technova.example.com",
            "admin_full_name": "TechNova Admin",
            "admin_password": "Password123!",
        },
        follow=False,
    )
    assert create_ok.status_code == 200

    dash_res = client.get("/admin/")
    assert dash_res.status_code == 200
    dash_html = dash_res.content.decode("utf-8")
    assert 'class="messagelist"' not in dash_html
    assert "TECHNOVA-ORG-002" in dash_html
    assert dash_html.count("created successfully.") == 1




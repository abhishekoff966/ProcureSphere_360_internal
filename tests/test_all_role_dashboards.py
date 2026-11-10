import pytest

from apps.accounts.models import Role, User
from apps.vendors.models import Vendor, VendorCategory


@pytest.mark.django_db
def test_all_role_dashboards_render_successfully(client):
    roles_to_test = [
        (Role.SUPER_ADMIN, "ProcureSphere 360"),
        (Role.REQUESTER, "Requester"),
        (Role.DEPT_APPROVER, "Approver"),
        (Role.PROC_MGR, "Manager"),
        (Role.PROC_EXEC, "Procurement"),
        (Role.STORES_RECEIVER, "Stores"),
        (Role.FINANCE_AP, "Finance"),
        (Role.LEGAL_MGR, "Legal"),
        (Role.AUDITOR, "Auditor"),
        (Role.VENDOR_USER, "Vendor"),
    ]

    for role_code, expected_snippet in roles_to_test:
        role, _ = Role.objects.get_or_create(code=role_code, defaults={"name": role_code})
        user = User.objects.create_user(
            email=f"user_{role_code.lower()}@hpe.com",
            password="Password123!",
            role=role,
        )

        if role_code == Role.VENDOR_USER:
            cat = VendorCategory.objects.create(name="Default Cat", code=f"CAT-{role_code}")
            vendor = Vendor.objects.create(
                legal_name="Test Vendor Inc",
                tax_identification_number=f"TAX-{role_code}",
                category=cat,
                email=user.email,
                status=Vendor.STATUS_ACTIVE,
            )
            user.vendor = vendor
            user.save()

        client.force_login(user)
        response = client.get("/dashboard/", follow=True)
        assert response.status_code == 200
        html = response.content.decode()
        assert expected_snippet.lower() in html.lower()
        # Verify standardized global navbar presence and branding
        assert "procuresphere_360_nav_brand.png" in html
        assert "global-enterprise-navbar" in html
        # Verify logo is positioned at the top of the sidebar and toggle is in navbar
        assert "sidebar-brand-box" in html
        assert "global-brand-logo" in html
        nav_start = html.find("<nav ")
        assert nav_start != -1
        nav_end = html.find("</nav>", nav_start)
        assert nav_end != -1
        navbar_html = html[nav_start:nav_end]

        # Verify toggle is in navbar and logo is NOT duplicated in top navbar
        assert "sidebar-toggle-btn" in navbar_html
        assert "global-brand-logo" not in navbar_html

        # Verify unwanted navbar elements are completely removed
        assert "global-context-badge" not in navbar_html
        assert "fa-magnifying-glass" not in navbar_html
        assert "fa-circle-question" not in navbar_html
        assert "fa-handshake" not in navbar_html

        # Verify Profile dropdown contains strictly user email and Sign Out
        assert user.email in navbar_html
        assert "/logout/" in navbar_html
        assert "Sign Out" in navbar_html
        assert "/accounts/change-password/" not in navbar_html
        assert "Audit History" not in navbar_html


@pytest.mark.django_db
def test_dept_approver_dashboard_handles_missing_department_and_org(client):
    from apps.organization.models import Department, Organization

    role, _ = Role.objects.get_or_create(
        code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"}
    )
    org = Organization.objects.create(name="Approver Render Org", code="APPR-ORG")
    Department.objects.create(organization=org, name="Operations", code="APPR-OPS")

    # Organization assigned but user.department is None: the dashboard resolves a
    # fallback department, exercising the former `user.department.name` crash path.
    org_only_user = User.objects.create_user(
        email="approver_org_only@hpe.com", role=role, organization=org
    )
    client.force_login(org_only_user)
    response = client.get("/approver/")
    assert response.status_code == 200

    # Neither organization nor department assigned: must still render with guidance.
    orphan_user = User.objects.create_user(
        email="approver_orphan@hpe.com", role=role
    )
    client.force_login(orphan_user)
    response = client.get("/approver/")
    assert response.status_code == 200
    assert "not linked to a department" in response.content.decode()

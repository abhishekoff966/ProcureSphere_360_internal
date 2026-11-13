from datetime import date
from decimal import Decimal
import pytest
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.models import Budget
from apps.core.superadmin_forms import (
    BudgetAdminForm,
    CostCenterAdminForm,
    DepartmentAdminForm,
    FiscalPeriodAdminForm,
    LocationAdminForm,
    UserAdminForm,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Location, Organization


@pytest.fixture
def test_org(db):
    org, _ = Organization.objects.get_or_create(
        code="TEST-VAL-ORG-01",
        defaults={
            "name": "Test Validation Org Ltd",
            "tax_identifier": "TESTVAL001",
            "is_active": True,
        },
    )
    return org


@pytest.fixture
def superadmin_user(db, test_org):
    user, _ = User.objects.get_or_create(
        email="test_superadmin_val@example.com",
        defaults={
            "username": "test_superadmin_val@example.com",
            "is_superuser": True,
            "is_staff": True,
            "is_active": True,
        },
    )
    return user


@pytest.mark.django_db
class TestInputValidation:
    """Requirement 1 & 5: Locations, Departments, Cost Centers, Fiscal Periods cannot be pure numbers or pure symbols."""

    def test_location_form_rejects_pure_numbers_and_symbols(self, test_org):
        # Pure numbers
        form = LocationAdminForm(
            data={
                "organization": test_org.id,
                "name": "11111111111111111111",
                "code": "11111111111111111111",
                "city": "11111111111111111111",
                "country": "India",
                "is_active": True,
            }
        )
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors
        assert "city" in form.errors

        # Pure symbols
        form_sym = LocationAdminForm(
            data={
                "organization": test_org.id,
                "name": "!@#$%^&*()",
                "code": "!@#$%^&*()",
                "city": "!@#$%^&*()",
                "country": "India",
                "is_active": True,
            }
        )
        assert not form_sym.is_valid()
        assert "name" in form_sym.errors
        assert "code" in form_sym.errors
        assert "city" in form_sym.errors

        # Valid alphanumeric with mixed letters
        form_valid = LocationAdminForm(
            data={
                "organization": test_org.id,
                "name": "Hyderabad Campus 2",
                "code": "LOC-HYD-02",
                "city": "Hyderabad",
                "country": "India",
                "is_active": True,
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_department_form_rejects_pure_numbers_and_symbols(self, test_org):
        # Pure numbers
        form = DepartmentAdminForm(
            data={
                "organization": test_org.id,
                "name": "99999999",
                "code": "99999999",
            }
        )
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors

        # Pure symbols
        form_sym = DepartmentAdminForm(
            data={
                "organization": test_org.id,
                "name": "!@#$%^&*()",
                "code": "!@#$%^&*()",
            }
        )
        assert not form_sym.is_valid()
        assert "name" in form_sym.errors
        assert "code" in form_sym.errors

        # Valid alphanumeric with letters
        form_valid = DepartmentAdminForm(
            data={
                "organization": test_org.id,
                "name": "Engineering 2",
                "code": "ENG-02",
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_cost_center_form_rejects_pure_numbers_and_symbols(self, test_org):
        dept, _ = Department.objects.get_or_create(
            organization=test_org,
            code="DEPT-CC-VAL",
            defaults={"name": "Dept for CC Validation"},
        )
        # Pure numbers
        form = CostCenterAdminForm(
            data={
                "department": dept.id,
                "name": "77777777",
                "code": "77777777",
            }
        )
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors

        # Valid mixed letters and numbers
        form_valid = CostCenterAdminForm(
            data={
                "department": dept.id,
                "name": "CC Operations 101",
                "code": "CC-OP-101",
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_fiscal_period_form_validation_and_start_date(self, test_org):
        today = timezone.localdate()
        form_init = FiscalPeriodAdminForm()
        assert form_init.fields["start_date"].initial == today
        assert form_init.fields["year"].initial == today.year

        # Year overflow and period number overflow
        form_overflow = FiscalPeriodAdminForm(
            data={
                "organization": test_org.id,
                "name": "!@#$%^&*()",
                "year": 2222222222222222,
                "period_number": 1222222222222222,
                "start_date": str(today),
                "end_date": str(today),
            }
        )
        assert not form_overflow.is_valid()
        assert "year" in form_overflow.errors
        assert "period_number" in form_overflow.errors
        assert "name" in form_overflow.errors

        # Valid fiscal period
        form_valid = FiscalPeriodAdminForm(
            data={
                "organization": test_org.id,
                "name": "FY2026-Q3",
                "year": 2026,
                "period_number": 3,
                "start_date": "2026-07-01",
                "end_date": "2026-09-30",
            }
        )
        assert form_valid.is_valid(), form_valid.errors


@pytest.mark.django_db
class TestRequesterCostCenterExclusivity:
    """Requirement 4: Once cost center is assigned to a requester, do not show it again in dropdown."""

    def test_assigned_cost_center_excluded_from_requester_dropdown(self, test_org):
        dept = Department.objects.create(
            organization=test_org,
            code="DEPT-EXCL-01",
            name="Exclusivity Dept",
        )
        cc1 = CostCenter.objects.create(
            department=dept,
            code="CC-EXCL-01",
            name="Cost Center Alpha",
        )
        cc2 = CostCenter.objects.create(
            department=dept,
            code="CC-EXCL-02",
            name="Cost Center Beta",
        )
        requester_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})

        # Assign cc1 to active Requester 1
        User.objects.create(
            email="requester1_excl@technova.example.com",
            username="requester1_excl@technova.example.com",
            role=requester_role,
            organization=test_org,
            department=dept,
            cost_center=cc1,
            is_active=True,
        )

        # Now when creating another user, cc1 should be excluded from UserAdminForm cost_center choices
        form = UserAdminForm()
        cc_ids_in_form = list(form.fields["cost_center"].queryset.values_list("id", flat=True))
        assert cc1.id not in cc_ids_in_form
        assert cc2.id in cc_ids_in_form


@pytest.mark.django_db
class TestCostCenterDepartmentExclusivity:
    """Requirement: Once a department is assigned to a Cost Center, do not show it again in CostCenter create dropdown."""

    def test_assigned_department_excluded_from_cost_center_create_dropdown(self, test_org):
        dept1 = Department.objects.create(
            organization=test_org,
            code="DEPT-CC-EXCL-01",
            name="Assigned Dept Alpha",
        )
        dept2 = Department.objects.create(
            organization=test_org,
            code="DEPT-CC-EXCL-02",
            name="Unassigned Dept Beta",
        )
        cc1 = CostCenter.objects.create(
            department=dept1,
            code="CC-DEPT-EXCL-01",
            name="Operations Center Alpha",
        )

        # In create mode (unbound GET), dept1 should be excluded while dept2 is available
        form = CostCenterAdminForm()
        dept_ids_in_form = list(form.fields["department"].queryset.values_list("id", flat=True))
        assert dept1.id not in dept_ids_in_form
        assert dept2.id in dept_ids_in_form

        # In edit mode for cc1, cc1's own department (dept1) MUST remain available
        edit_form = CostCenterAdminForm(instance=cc1)
        edit_dept_ids = list(edit_form.fields["department"].queryset.values_list("id", flat=True))
        assert dept1.id in edit_dept_ids

        # Verifies that already assigned departments are excluded from the choices in the dropdown
        assert dept1.id not in dept_ids_in_form
        assert dept2.id in dept_ids_in_form


@pytest.mark.django_db
class TestAdminIndexTemplateSyntax:
    """Test that /admin/ index template renders cleanly without TemplateSyntaxError."""

    def test_admin_index_forloop_syntax(self, client, test_org, superadmin_user):
        client.force_login(superadmin_user)
        # Create more than 5 organizations to trigger the {% if forloop.counter > 5 %} branch
        for i in range(7):
            Organization.objects.get_or_create(
                code=f"TEST-ORG-INDEX-{i}",
                defaults={"name": f"Index Org {i}", "is_active": True},
            )
        response = client.get("/admin/")
        assert response.status_code == 200


@pytest.mark.django_db
class TestBudgetFormSelectOption:
    """Requirement 4: Fiscal Period displays '-- Select Fiscal Period --' by default."""

    def test_budget_form_fiscal_period_empty_label(self, test_org):
        dept = Department.objects.create(
            organization=test_org,
            code="DEPT-BGT-01",
            name="Budget Dept",
        )
        cc = CostCenter.objects.create(
            department=dept,
            code="CC-BGT-01",
            name="Budget CC",
        )
        fp = FiscalPeriod.objects.create(
            organization=test_org,
            name="FY2026-Q1",
            year=2026,
            period_number=1,
            start_date=date(2026, 1, 1),
            end_date=date(2026, 3, 31),
        )
        form = BudgetAdminForm()
        assert form.fields["fiscal_period"].empty_label == "-- Select Fiscal Period --"


@pytest.mark.django_db
class TestDeletionViews:
    """Requirement 2: Deletion option for Users, Departments, and Cost Centers."""

    def test_department_and_cost_center_delete_views(self, client, superadmin_user, test_org):
        dept = Department.objects.create(
            organization=test_org,
            code="DEPT-DEL-01",
            name="Department to Delete",
        )
        cc = CostCenter.objects.create(
            department=dept,
            code="CC-DEL-01",
            name="Cost Center to Delete",
        )

        client.force_login(superadmin_user)

        # Delete Cost Center
        response = client.post(f"/superadmin/cost-centers/{cc.id}/delete/")
        assert response.status_code == 302
        assert not CostCenter.objects.filter(id=cc.id).exists()

        # Delete Department
        resp_dept = client.post(f"/superadmin/departments/{dept.id}/delete/")
        assert resp_dept.status_code == 302
        assert not Department.objects.filter(id=dept.id).exists()

    def test_user_delete_view(self, client, superadmin_user, test_org):
        target_user = User.objects.create(
            email="user_to_delete@technova.example.com",
            username="user_to_delete@technova.example.com",
            organization=test_org,
            is_active=True,
        )

        client.force_login(superadmin_user)
        response = client.post(f"/superadmin/users/{target_user.id}/delete/")
        assert response.status_code == 302
        assert not User.objects.filter(id=target_user.id).exists()


@pytest.mark.django_db
class TestDashboardAPIs:
    """Requirement 3: Dashboard APIs in /api/docs/ for superadmin, admin, and approver."""

    def test_dashboard_api_endpoints_accessible(self, rf, superadmin_user, test_org):
        from apps.core.dashboard_api_views import (
            AdminDashboardAPIView,
            DepartmentApproverDashboardAPIView,
            DepartmentApproverQueueAPIView,
            SuperAdminDashboardAPIView,
        )

        # Super Admin Dashboard API
        req_super = rf.get("/api/v1/superadmin/dashboard/")
        req_super.user = superadmin_user
        resp_super = SuperAdminDashboardAPIView.as_view()(req_super)
        assert resp_super.status_code == 200
        assert "total_organizations" in resp_super.data
        assert "active_tenants" in resp_super.data

        # Admin Dashboard API
        req_admin = rf.get("/api/v1/admin/dashboard/")
        req_admin.user = superadmin_user
        resp_admin = AdminDashboardAPIView.as_view()(req_admin)
        assert resp_admin.status_code == 200
        assert "organization_name" in resp_admin.data
        assert "total_users" in resp_admin.data

        # Approver Dashboard API
        approver_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
        approver_user = User.objects.create(
            email="test_dept_approver_api@technova.example.com",
            username="test_dept_approver_api@technova.example.com",
            role=approver_role,
            organization=test_org,
            is_active=True,
        )
        req_appr = rf.get("/api/v1/approver/dashboard/")
        req_appr.user = approver_user
        resp_appr = DepartmentApproverDashboardAPIView.as_view()(req_appr)
        assert resp_appr.status_code == 200
        assert "total_requisitions" in resp_appr.data
        assert "pending_approvals" in resp_appr.data

        # Approver Queue API
        req_queue = rf.get("/api/v1/approver/queue/")
        req_queue.user = approver_user
        resp_queue = DepartmentApproverQueueAPIView.as_view()(req_queue)
        assert resp_queue.status_code == 200
        assert isinstance(resp_queue.data, list)

    def test_openapi_schema_contains_dashboard_endpoints(self, client, superadmin_user):
        client.force_login(superadmin_user)
        resp = client.get("/api/schema/?format=json")
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "/api/v1/superadmin/dashboard/" in content
        assert "/api/v1/admin/dashboard/" in content
        assert "/api/v1/approver/dashboard/" in content
        assert "/api/v1/approver/queue/" in content
        assert "/api/v1/approver/decision/" in content


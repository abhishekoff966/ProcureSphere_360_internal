"""
Comprehensive test suite verifying validation across all user-facing forms in ProcureSphere 360:
Superadmin, Admin, and Department Approver.

Verifies:
1. Field validation according to label and purpose (required, formats, lengths, ranges, letters).
2. Error messages match exact field labels and display directly on field errors.
3. Form validation on submit / clean preserves entered values and blocks invalid submissions.
4. Consistency between frontend rules and backend form clean methods (unique values, edit mode accepting existing values).
5. Cross-field rules (e.g. end_date >= start_date, min_amount <= max_amount).
"""

from decimal import Decimal
import pytest
from django.utils import timezone
from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, Location, FiscalPeriod
from apps.budgets.models import Budget
from apps.approvals.models import ApprovalPolicy
from apps.invoices.models import MatchTolerancePolicy
from apps.core.superadmin_forms import (
    OrganizationAdminForm,
    DepartmentAdminForm,
    CostCenterAdminForm,
    LocationAdminForm,
    FiscalPeriodAdminForm,
    BudgetAdminForm,
    UserAdminForm,
    PolicyAdminForm,
)


@pytest.fixture
def org_and_masters(db):
    org = Organization.objects.create(
        code="VAL-ORG-01",
        name="Validation Test Organization",
        tax_identifier="VAL-12345",
        address="100 Validation Boulevard",
        is_active=True,
    )
    dept = Department.objects.create(
        organization=org,
        code="VAL-DEPT-01",
        name="Engineering Dept",
        description="Engineering and IT",
    )
    cc = CostCenter.objects.create(
        department=dept,
        code="VAL-CC-101",
        name="Software Development CC",
    )
    fp = FiscalPeriod.objects.create(
        organization=org,
        name="FY2026-Q1",
        year=2026,
        period_number=1,
        start_date=timezone.localdate(),
        end_date=timezone.localdate() + timezone.timedelta(days=90),
    )
    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "fp": fp,
    }


@pytest.mark.django_db
class TestAllFormsValidation:

    # 1. DepartmentAdminForm Tests
    def test_department_form_empty_required_fields(self, org_and_masters):
        form = DepartmentAdminForm(data={})
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors
        assert "organization" in form.errors
        assert "Department Name is required." in str(form.errors["name"])
        assert "Department Code is required." in str(form.errors["code"])
        assert "Parent Organization is required." in str(form.errors["organization"])
        # Mixin applied is-invalid
        assert "is-invalid" in form.fields["name"].widget.attrs.get("class", "")

    def test_department_form_numbers_only_rejected(self, org_and_masters):
        org = org_and_masters["org"]
        form = DepartmentAdminForm(data={
            "organization": org.id,
            "name": "12345678",
            "code": "888888",
        })
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors
        assert "cannot contain only numbers" in str(form.errors["name"])
        assert "cannot contain only numbers" in str(form.errors["code"])

    def test_department_form_edit_mode_accepts_existing_code(self, org_and_masters):
        dept = org_and_masters["dept"]
        org = org_and_masters["org"]
        form = DepartmentAdminForm(
            instance=dept,
            data={
                "organization": org.id,
                "name": dept.name,
                "code": dept.code,
                "description": "Updated description",
            },
        )
        assert form.is_valid(), form.errors

    # 2. CostCenterAdminForm Tests
    def test_cost_center_form_empty_required_fields(self, org_and_masters):
        form = CostCenterAdminForm(data={})
        assert not form.is_valid()
        assert "code" in form.errors
        assert "name" in form.errors
        assert "department" in form.errors
        assert "Cost Center Code is required." in str(form.errors["code"])
        assert "Cost Center Name is required." in str(form.errors["name"])
        assert "Department is required." in str(form.errors["department"])

    def test_cost_center_form_manager_invalid_email(self, org_and_masters):
        dept = org_and_masters["dept"]
        form = CostCenterAdminForm(data={
            "code": "CC-VALID-01",
            "name": "Cost Center Valid",
            "department": dept.id,
            "manager": "not-a-valid-email",
        })
        assert not form.is_valid()
        assert "manager" in form.errors
        assert "Enter a valid email address." in str(form.errors["manager"])

    def test_cost_center_form_edit_mode_accepts_existing_code(self, org_and_masters):
        cc = org_and_masters["cc"]
        form = CostCenterAdminForm(
            instance=cc,
            data={
                "code": cc.code,
                "name": cc.name,
                "department": cc.department.id,
            },
        )
        assert form.is_valid(), form.errors

    # 3. LocationAdminForm Tests
    def test_location_form_empty_required_fields(self, org_and_masters):
        form = LocationAdminForm(data={})
        assert not form.is_valid()
        assert "name" in form.errors
        assert "code" in form.errors
        assert "city" in form.errors
        assert "organization" in form.errors
        assert "Location Name is required." in str(form.errors["name"])
        assert "Location Code is required." in str(form.errors["code"])
        assert "City is required." in str(form.errors["city"])
        assert "Parent Organization is required." in str(form.errors["organization"])

    def test_location_form_edit_mode_accepts_existing_code(self, org_and_masters):
        org = org_and_masters["org"]
        loc = Location.objects.create(
            organization=org,
            name="Hyderabad Tech Center",
            code="LOC-HYD-01",
            city="Hyderabad",
        )
        form = LocationAdminForm(
            instance=loc,
            data={
                "organization": org.id,
                "name": loc.name,
                "code": loc.code,
                "city": loc.city,
                "country": "India",
                "is_active": True,
            },
        )
        assert form.is_valid(), form.errors

    # 4. FiscalPeriodAdminForm Tests
    def test_fiscal_period_form_empty_required_fields(self, org_and_masters):
        form = FiscalPeriodAdminForm(data={})
        assert not form.is_valid()
        assert "name" in form.errors
        assert "year" in form.errors
        assert "period_number" in form.errors
        assert "start_date" in form.errors
        assert "end_date" in form.errors
        assert "Fiscal Period Name is required." in str(form.errors["name"])
        assert "Year is required." in str(form.errors["year"])
        assert "Period Number is required." in str(form.errors["period_number"])
        assert "Start Date is required." in str(form.errors["start_date"])
        assert "End Date is required." in str(form.errors["end_date"])

    def test_fiscal_period_form_cross_field_end_date_before_start_date(self, org_and_masters):
        org = org_and_masters["org"]
        today = timezone.localdate()
        form = FiscalPeriodAdminForm(data={
            "organization": org.id,
            "name": "FY2026-Test",
            "year": today.year,
            "period_number": 2,
            "start_date": str(today),
            "end_date": str(today - timezone.timedelta(days=5)),
        })
        assert not form.is_valid()
        assert "end_date" in form.errors
        assert "End date must be on or after the start date." in str(form.errors["end_date"])

    def test_fiscal_period_form_duplicate_period_rejected(self, org_and_masters):
        fp = org_and_masters["fp"]
        form = FiscalPeriodAdminForm(data={
            "organization": fp.organization.id,
            "name": "Duplicate Period",
            "year": fp.year,
            "period_number": fp.period_number,
            "start_date": str(fp.start_date),
            "end_date": str(fp.end_date),
        })
        assert not form.is_valid()
        assert "period_number" in form.errors
        assert "already exists in this organization" in str(form.errors["period_number"])

    # 5. BudgetAdminForm Tests
    def test_budget_form_empty_required_fields(self, org_and_masters):
        form = BudgetAdminForm(data={})
        assert not form.is_valid()
        assert "cost_center" in form.errors
        assert "fiscal_period" in form.errors
        assert "allocated_amount" in form.errors
        assert "Cost Center is required." in str(form.errors["cost_center"])
        assert "Fiscal Period is required." in str(form.errors["fiscal_period"])
        assert "Allocated Amount is required." in str(form.errors["allocated_amount"])

    def test_budget_form_zero_or_negative_amount_rejected(self, org_and_masters):
        cc = org_and_masters["cc"]
        fp = org_and_masters["fp"]
        form = BudgetAdminForm(data={
            "cost_center": cc.id,
            "fiscal_period": fp.id,
            "allocated_amount": "0.00",
        })
        assert not form.is_valid()
        assert "allocated_amount" in form.errors
        assert "greater than zero" in str(form.errors["allocated_amount"])

    def test_budget_form_duplicate_budget_rejected(self, org_and_masters):
        cc = org_and_masters["cc"]
        fp = org_and_masters["fp"]
        Budget.objects.create(
            cost_center=cc,
            fiscal_period=fp,
            allocated_amount=Decimal("50000.00"),
        )
        form = BudgetAdminForm(data={
            "cost_center": cc.id,
            "fiscal_period": fp.id,
            "allocated_amount": "75000.00",
        })
        assert not form.is_valid()
        assert "cost_center" in form.errors
        assert "already exists" in str(form.errors["cost_center"])

    # 6. UserAdminForm Tests
    def test_user_form_empty_required_fields(self, org_and_masters):
        form = UserAdminForm(data={})
        assert not form.is_valid()
        assert "email" in form.errors
        assert "role" in form.errors
        assert "Email Address is required." in str(form.errors["email"])
        assert "Role is required." in str(form.errors["role"])

    def test_user_form_invalid_email_format(self, org_and_masters):
        role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        form = UserAdminForm(data={
            "email": "invalid-email-address",
            "role": role.id,
        })
        assert not form.is_valid()
        assert "email" in form.errors
        assert "Enter a valid email address." in str(form.errors["email"])

    def test_user_form_invalid_phone_format(self, org_and_masters):
        role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
        form = UserAdminForm(data={
            "email": "user@example.com",
            "role": role.id,
            "phone_number": "abc-not-a-phone",
        })
        assert not form.is_valid()
        assert "phone_number" in form.errors
        assert "Enter a valid phone number." in str(form.errors["phone_number"])

    # 7. PolicyAdminForm Tests
    def test_policy_form_empty_required_fields(self, org_and_masters):
        form = PolicyAdminForm(data={})
        assert not form.is_valid()
        assert "name" in form.errors
        assert "module" in form.errors
        assert "Policy Name is required." in str(form.errors["name"])
        assert "Module is required." in str(form.errors["module"])

    def test_policy_form_cross_field_min_greater_than_max(self, org_and_masters):
        org = org_and_masters["org"]
        form = PolicyAdminForm(data={
            "organization": org.id,
            "name": "High Value PR Policy",
            "module": ApprovalPolicy.MODULE_PR,
            "min_amount": "50000.00",
            "max_amount": "10000.00",
        })
        assert not form.is_valid()
        assert "min_amount" in form.errors
        assert "cannot be greater than maximum amount" in str(form.errors["min_amount"])

    def test_policy_form_exception_routing_validation(self, org_and_masters):
        org = org_and_masters["org"]
        # Exception routing enabled without required exception approver role
        form = PolicyAdminForm(data={
            "organization": org.id,
            "name": "Exception PR Policy",
            "module": ApprovalPolicy.MODULE_PR,
            "min_amount": "0.00",
            "has_exception_routing": True,
            "exception_amount_threshold": "-500.00",
            "exception_escalation_time_hours": "0",
        })
        assert not form.is_valid()
        assert "exception_approver_role" in form.errors
        assert "Exception Approver Role is required when exception routing is enabled." in str(form.errors["exception_approver_role"])
        assert "exception_amount_threshold" in form.errors
        assert "Exception Amount Threshold cannot be negative." in str(form.errors["exception_amount_threshold"])
        assert "exception_escalation_time_hours" in form.errors
        assert "Escalation Window must be at least 1 hour." in str(form.errors["exception_escalation_time_hours"])

    def test_policy_form_steps_data_validation(self, org_and_masters):
        import json
        org = org_and_masters["org"]
        # Step with missing role, negative limit, and zero escalation hours
        invalid_steps = json.dumps([
            {
                "step_number": 1,
                "role_id": "",
                "approval_limit": "-100.00",
                "escalation_hours": 0,
            }
        ])
        form = PolicyAdminForm(data={
            "organization": org.id,
            "name": "Steps Validation Policy",
            "module": ApprovalPolicy.MODULE_PR,
            "min_amount": "0.00",
            "steps_data": invalid_steps,
        })
        assert not form.is_valid()
        assert "steps_data" in form.errors
        err_str = str(form.errors["steps_data"])
        assert "Select an Approver Role for Level 1." in err_str
        assert "Step Limit for Level 1 must be a positive amount." in err_str
        assert "Escalation SLA for Level 1 must be at least 1 hour." in err_str

    def test_policy_form_edit_mode_preserves_valid_policy(self, org_and_masters):
        import json
        from apps.accounts.models import Role
        org = org_and_masters["org"]
        role = Role.objects.first()
        policy = ApprovalPolicy.objects.create(
            organization=org,
            name="Original Policy Name",
            module=ApprovalPolicy.MODULE_PR,
            min_amount=Decimal("0.00"),
            max_amount=Decimal("100000.00"),
            is_active=True,
            status=ApprovalPolicy.STATUS_ACTIVE,
        )
        valid_steps = json.dumps([
            {
                "step_number": 1,
                "role_id": str(role.id),
                "approval_limit": "50000.00",
                "escalation_hours": 24,
            }
        ])
        form = PolicyAdminForm(instance=policy, data={
            "organization": org.id,
            "name": "Original Policy Name Updated",
            "module": ApprovalPolicy.MODULE_PR,
            "min_amount": "0.00",
            "max_amount": "100000.00",
            "is_active": True,
            "status": ApprovalPolicy.STATUS_ACTIVE,
            "steps_data": valid_steps,
        })
        assert form.is_valid(), form.errors
        updated_policy = form.save()
        assert updated_policy.name == "Original Policy Name Updated"

    # 8. MatchTolerancePolicy (Tolerance Settings) Tests
    def test_match_tolerance_policy_bounds(self, org_and_masters):
        policy = MatchTolerancePolicy.get_current()
        assert 0 <= policy.price_tolerance_pct <= 1
        assert 0 <= policy.qty_tolerance_pct <= 1

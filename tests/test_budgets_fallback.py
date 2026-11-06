from decimal import Decimal
import pytest
from django.utils import timezone
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.budgets.services import validate_budget_availability_service, check_and_reserve_budget_service
from apps.accounts.models import User, Role
from apps.requisitions.models import PurchaseRequisition, PRLine

@pytest.mark.django_db
def test_budget_fallback_when_fiscal_period_exists_but_no_budget():
    """Verify UnboundLocalError is resolved when FiscalPeriod exists but CostCenter has no Budget."""
    today = timezone.now().date()
    org = Organization.objects.create(name="Budget Fallback Org", code="BFO")
    dept = Department.objects.create(name="IT Dept", code="ITD", organization=org)
    cc = CostCenter.objects.create(name="IT Operations", code="IT-OPS-TEST", department=dept)

    # Ensure FiscalPeriod exists
    fp = FiscalPeriod.objects.create(
        organization=org,
        year=today.year,
        period_number=1,
        name=f"FY{today.year}-P1",
        start_date=f"{today.year}-01-01",
        end_date=f"{today.year}-12-31",
        is_closed=False,
    )

    # Ensure no Budget exists yet for this CC
    assert not Budget.objects.filter(cost_center=cc).exists()

    # 1. Test validate_budget_availability_service
    budget = validate_budget_availability_service(cost_center=cc, amount=Decimal("150.00"), today=today)
    assert budget is not None
    assert budget.fiscal_period == fp
    assert budget.allocated_amount == Decimal("1000000.00")

    # 2. Test check_and_reserve_budget_service
    # Delete budget again to test reservation fallback
    Budget.objects.filter(cost_center=cc).delete()

    role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    user = User.objects.create_user(email="budget_tester@test.com", password="Password123!", role=role, organization=org)
    pr = PurchaseRequisition.objects.create(
        requester=user,
        department=dept,
        cost_center=cc,
        pr_number="PR-BUDGET-FALLBACK",
        title="Server Upgrade",
        justification="Hardware capacity",
        total_amount=Decimal("2500.00"),
        requested_delivery_date=today,
    )
    reservation = check_and_reserve_budget_service(requisition=pr, requested_by_user=user)
    assert reservation is not None
    assert reservation.amount == Decimal("2500.00")

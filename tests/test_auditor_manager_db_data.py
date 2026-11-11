import datetime
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.selectors import (
    clear_audit_selector_caches,
    get_audit_metrics,
    get_auditor_dashboard_data,
)
from apps.budgets.models import Budget, SpendLedger
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt, ReceiptLine, RejectionRecord
from apps.requisitions.models import PurchaseRequisition
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import SourcingEvent, VendorBid
from apps.vendors.models import Vendor, VendorCategory, VendorRiskRecord


@pytest.fixture
def org_and_users(db):
    org = Organization.objects.create(name="HPE Tech Org", code="HPE-ORG-TEST")
    dept = Department.objects.create(name="Global Procurement", code="PROC-TEST", organization=org)
    cost_center = CostCenter.objects.create(name="CC-Core", code="CC-001", department=dept)
    category = VendorCategory.objects.create(name="Hardware & Compute", code="CAT-HW")
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q1",
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
    )

    mgr_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    aud_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Compliance Auditor"})

    manager = User.objects.create_user(
        email="mgr_test@hpe.com",
        password="Password123!",
        role=mgr_role,
        organization=org,
        department=dept,
    )
    auditor = User.objects.create_user(
        email="aud_test@hpe.com",
        password="Password123!",
        role=aud_role,
        organization=org,
        department=dept,
    )

    clear_audit_selector_caches()

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "category": category,
        "fiscal_period": fiscal_period,
        "manager": manager,
        "auditor": auditor,
    }


@pytest.mark.django_db
def test_proc_manager_kpis_api_fetches_real_db_data(org_and_users):
    """
    Verifies that Manager KPIs API computes real figures directly from database models:
    Budgets, SpendLedger, GoodsReceipts, RejectionRecords, VendorScorecards, and MatchExceptions.
    """
    data = org_and_users
    org = data["org"]
    cost_center = data["cost_center"]
    category = data["category"]
    fiscal_period = data["fiscal_period"]
    manager = data["manager"]

    # 1. Seed Budget & Spend data
    budget = Budget.objects.create(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("100000.00"),
    )
    SpendLedger.objects.create(
        budget=budget,
        amount=Decimal("20000.00"),
        entry_type=SpendLedger.ENTRY_COMMITMENT,
        reference_number="PO-TEST-001",
        description="Commitment for hardware",
    )
    SpendLedger.objects.create(
        budget=budget,
        amount=Decimal("15000.00"),
        entry_type=SpendLedger.ENTRY_ACTUAL,
        reference_number="INV-TEST-001",
        description="Actual spend for servers",
    )

    # 2. Seed Vendor & Scorecard
    vendor = Vendor.objects.create(
        legal_name="Apex Global Supplier",
        vendor_number="VND-APEX-01",
        category=category,
        organization=org,
        status=Vendor.STATUS_ACTIVE,
    )
    VendorScorecard.objects.create(
        vendor=vendor,
        evaluation_period="2026-Q1",
        delivery_score=Decimal("92.00"),
        quality_score=Decimal("92.00"),
        price_score=Decimal("92.00"),
        compliance_score=Decimal("92.00"),
        evaluated_by=manager,
    )

    # 3. Seed Purchase Order & Goods Receipt & QC Rejection
    po = PurchaseOrder.objects.create(
        po_number="PO-APEX-001",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("20000.00"),
        created_by=manager,
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Compute Node",
        quantity=Decimal("10.00"),
        unit_price=Decimal("2000.00"),
        line_total=Decimal("20000.00"),
    )
    grn = GoodsReceipt.objects.create(
        grn_number="GRN-APEX-001",
        po=po,
        received_date=timezone.now(),
        received_by=manager,
    )
    line = ReceiptLine.objects.create(
        receipt=grn,
        po_line=po_line,
        quantity_received=Decimal("10.00"),
        quantity_accepted=Decimal("8.00"),
        quantity_rejected=Decimal("2.00"),
    )
    RejectionRecord.objects.create(
        receipt_line=line,
        rejected_quantity=Decimal("2.00"),
        rejection_reason="Damaged in transit",
        returned_to_vendor=True,
    )

    # 4. Request Manager KPIs API
    client = APIClient()
    client.force_authenticate(user=manager)
    response = client.get("/api/v1/manager/kpis/")

    assert response.status_code == 200
    res = response.json()

    # Verify real DB numbers
    assert res["allocated_budget"] == 100000.00
    assert res["committed_spend"] == 20000.00
    assert res["actual_spend"] == 15000.00
    assert res["available_budget"] == 65000.00  # 100000 - 35000
    assert res["budget_utilization_pct"] == 35.0  # 35000 / 100000 * 100
    assert res["avg_vendor_scorecard"] == 92.0
    assert res["total_grns_count"] == 1
    assert res["total_rejections_count"] == 1
    assert res["active_pos_count"] == 1
    assert res["active_pos_value"] == 20000.00


@pytest.mark.django_db
def test_manager_vendor_watchlist_exposure_counts_from_db(org_and_users):
    """
    Verifies that Manager Vendor Watchlist calculates open POs and open bids from DB.
    """
    data = org_and_users
    org = data["org"]
    cost_center = data["cost_center"]
    category = data["category"]
    manager = data["manager"]

    vendor = Vendor.objects.create(
        legal_name="Target High Risk Vendor",
        vendor_number="VND-RISK-01",
        category=category,
        organization=org,
        status=Vendor.STATUS_ON_HOLD,
    )
    VendorRiskRecord.objects.create(
        vendor=vendor,
        risk_level=VendorRiskRecord.RISK_LEVEL_HIGH,
        assessed_by=manager,
        assessment_notes="High financial exposure review",
    )

    # Add open PO
    PurchaseOrder.objects.create(
        po_number="PO-EXP-001",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("50000.00"),
        created_by=manager,
    )

    # Add open Bid
    now = timezone.now()
    event = SourcingEvent.objects.create(
        title="RFQ Hardware Components",
        event_number="RFQ-2026-EXP",
        event_type="RFQ",
        status=SourcingEvent.STATUS_BID_WINDOW,
        bid_start_date=now,
        bid_end_date=now + datetime.timedelta(days=14),
        created_by=manager,
    )
    VendorBid.objects.create(
        event=event,
        vendor=vendor,
        bid_number="BID-EXP-001",
        status=VendorBid.STATUS_SUBMITTED,
        total_bid_amount=Decimal("45000.00"),
    )

    client = APIClient()
    client.force_authenticate(user=manager)
    response = client.get("/api/v1/manager/vendor-watchlist/")

    assert response.status_code == 200
    res = response.json()
    assert res["count"] >= 1
    target = next((v for v in res["results"] if v["id"] == str(vendor.id)), None)
    assert target is not None
    assert target["current_risk_level"] == "HIGH"
    assert target["exposure"]["open_pos"] == 1
    assert target["exposure"]["open_bids"] == 1
    assert target["exposure"]["total_open_pos_amount"] == 50000.00


@pytest.mark.django_db
def test_auditor_dashboard_metrics_and_views_fetch_real_db_data(org_and_users):
    """
    Verifies that Auditor Dashboard Metrics and data selectors reflect real DB objects:
    AuditLog entries, open exceptions, suspended vendors, and PO / Contract changes.
    """
    data = org_and_users
    org = data["org"]
    dept = data["dept"]
    cost_center = data["cost_center"]
    auditor = data["auditor"]
    manager = data["manager"]

    # 1. Create scoped PRs in DB
    deliv_date = timezone.now().date() + datetime.timedelta(days=7)
    pr1 = PurchaseRequisition.objects.create(
        pr_number="PR-2026-AUD01",
        title="Audit Test Hardware",
        department=dept,
        cost_center=cost_center,
        requester=manager,
        status=PurchaseRequisition.STATUS_APPROVED,
        total_amount=Decimal("12000.00"),
        requested_delivery_date=deliv_date,
    )
    pr2 = PurchaseRequisition.objects.create(
        pr_number="PR-2026-AUD02",
        title="Audit Test Services",
        department=dept,
        cost_center=cost_center,
        requester=manager,
        status=PurchaseRequisition.STATUS_REJECTED,
        total_amount=Decimal("8000.00"),
        requested_delivery_date=deliv_date,
    )

    clear_audit_selector_caches()

    # 2. Create audit logs referencing real entities
    AuditLog.objects.create(
        actor=manager,
        action=AuditLog.ACTION_APPROVE,
        target_model="PurchaseRequisition",
        target_object_id=str(pr1.id),
        previous_state={"status": "SUBMITTED"},
        new_state={"status": "APPROVED"},
    )
    AuditLog.objects.create(
        actor=manager,
        action=AuditLog.ACTION_REJECT,
        target_model="PurchaseRequisition",
        target_object_id=str(pr2.id),
        previous_state={"status": "SUBMITTED"},
        new_state={"status": "REJECTED"},
    )
    AuditLog.objects.create(
        actor=auditor,
        action=AuditLog.ACTION_LOGIN,
        target_model="User",
        target_object_id=str(auditor.id),
    )

    # 3. Query auditor metrics
    metrics = get_audit_metrics(period_days=30, organization=org)
    assert metrics["total_logs_all_time"] == 3
    assert metrics["critical_events"]["approvals"] == 1
    assert metrics["critical_events"]["rejections"] == 1
    assert metrics["critical_events"]["logins"] == 1
    assert "APPROVE" in metrics["action_distribution"]

    # 4. Query auditor dashboard data bundle
    dashboard_data = get_auditor_dashboard_data(period_days=30, user=auditor, organization=org)
    assert dashboard_data["metrics"]["total_logs_all_time"] == 3
    assert len(dashboard_data["recent_logs"]) == 3

    # 5. Verify Auditor dashboard HTML view returns 200 with DB data in context
    client = APIClient()
    client.force_login(user=auditor)
    response = client.get("/audit/dashboard/")
    assert response.status_code == 200
    assert response.context["metrics"]["total_audit_logs"] == 3
    assert response.context["metrics"]["total_approvals"] == 1
    assert response.context["metrics"]["total_rejections"] == 1

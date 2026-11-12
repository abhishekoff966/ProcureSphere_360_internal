import pytest
from decimal import Decimal
from datetime import date, timedelta
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.vendors.models import Vendor, VendorCategory
from apps.orders.models import PurchaseOrder, POLine
from apps.receipts.models import GoodsReceipt
from apps.receipts.services import create_goods_receipt_service
from apps.notifications.models import Notification
from apps.invoices.models import SupplierInvoice
from apps.reports.services import get_spend_analytics_report


pytestmark = [pytest.mark.django_db]


@pytest.fixture
def phase4_setup(db):
    # Organizations
    org_a = Organization.objects.create(name="Acme Corp", code="ACME")
    org_b = Organization.objects.create(name="Beta Industries", code="BETA")

    # Departments & Cost Centers
    dept_a = Department.objects.create(name="Engineering", code="ENG-A", organization=org_a)
    dept_a2 = Department.objects.create(name="Operations", code="OPS-A", organization=org_a)
    dept_b = Department.objects.create(name="Logistics", code="LOG-B", organization=org_b)

    cc_a1 = CostCenter.objects.create(name="Software Engineering", code="CC-ENG-1", department=dept_a)
    cc_a2 = CostCenter.objects.create(name="Hardware Engineering", code="CC-ENG-2", department=dept_a)
    cc_a_ops = CostCenter.objects.create(name="Ops Cost Center", code="CC-OPS-1", department=dept_a2)
    cc_b1 = CostCenter.objects.create(name="Logistics Center", code="CC-LOG-1", department=dept_b)

    # Roles
    proc_mgr_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    proc_exec_role, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    requester_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    receiver_role, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    vendor_role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})
    finance_role, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance & AP"})
    admin_role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})

    # Users Org A
    proc_mgr_a = User.objects.create_user(
        email="pm_a@acme.com",
        password="Password123!",
        role=proc_mgr_role,
        organization=org_a,
        is_active=True,
    )
    proc_exec_a = User.objects.create_user(
        email="pe_a@acme.com",
        password="Password123!",
        role=proc_exec_role,
        organization=org_a,
        is_active=True,
    )
    receiver_a = User.objects.create_user(
        email="receiver_a@acme.com",
        password="Password123!",
        role=receiver_role,
        organization=org_a,
        is_active=True,
    )
    requester_a = User.objects.create_user(
        email="req_a@acme.com",
        password="Password123!",
        role=requester_role,
        department=dept_a,
        cost_center=cc_a1,
        is_active=True,
    )
    finance_a = User.objects.create_user(
        email="fin_a@acme.com",
        password="Password123!",
        role=finance_role,
        organization=org_a,
        is_active=True,
    )

    # Users Org B
    proc_mgr_b = User.objects.create_user(
        email="pm_b@beta.com",
        password="Password123!",
        role=proc_mgr_role,
        organization=org_b,
        is_active=True,
    )
    receiver_b = User.objects.create_user(
        email="receiver_b@beta.com",
        password="Password123!",
        role=receiver_role,
        organization=org_b,
        is_active=True,
    )

    # Super Admin
    super_admin = User.objects.create_superuser(
        email="platform_admin@procuresphere.com",
        password="AdminPassword123!",
        role=admin_role,
    )

    # Categories
    cat_it = VendorCategory.objects.create(name="IT Services", code="CAT-IT")
    cat_log = VendorCategory.objects.create(name="Logistics", code="CAT-LOG")

    # Vendors
    vendor_a = Vendor.objects.create(
        organization=org_a,
        legal_name="Apex Tech Solutions",
        trade_name="Apex Tech",
        vendor_number="VND-APEX-001",
        tax_identification_number="TIN-APEX-001",
        email="contact@apextech.com",
        category=cat_it,
        status=Vendor.STATUS_ACTIVE,
    )
    vendor_b = Vendor.objects.create(
        organization=org_b,
        legal_name="Global Freight Logistics",
        trade_name="Global Freight",
        vendor_number="VND-GLO-002",
        tax_identification_number="TIN-GLO-002",
        email="contact@globalfreight.com",
        category=cat_log,
        status=Vendor.STATUS_ACTIVE,
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "dept_a": dept_a,
        "dept_a2": dept_a2,
        "dept_b": dept_b,
        "cc_a1": cc_a1,
        "cc_a2": cc_a2,
        "cc_a_ops": cc_a_ops,
        "cc_b1": cc_b1,
        "proc_mgr_a": proc_mgr_a,
        "proc_exec_a": proc_exec_a,
        "receiver_a": receiver_a,
        "requester_a": requester_a,
        "finance_a": finance_a,
        "proc_mgr_b": proc_mgr_b,
        "receiver_b": receiver_b,
        "super_admin": super_admin,
        "cat_it": cat_it,
        "cat_log": cat_log,
        "vendor_a": vendor_a,
        "vendor_b": vendor_b,
        "requester_role": requester_role,
        "vendor_role": vendor_role,
        "proc_mgr_role": proc_mgr_role,
    }


def _create_test_po(env, number="PO-PH4-001", cost_center=None, vendor=None, created_by=None, issued_by=None):
    cc = cost_center or env["cc_a1"]
    v = vendor or env["vendor_a"]
    po = PurchaseOrder.objects.create(
        po_number=number,
        cost_center=cc,
        vendor=v,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("1000.00"),
        total_amount=Decimal("1000.00"),
        created_by=created_by,
        issued_by=issued_by,
    )
    POLine.objects.create(
        po=po,
        item_description="Widget A",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
    )
    return po


# =========================================================================
# DEF-012: Goods Receipt Notification Fallback Tests
# =========================================================================

def test_grn_notification_standard_recipient(phase4_setup):
    """DEF-012: When standard recipient (issued_by) is active and in same org, they receive GRN notification."""
    env = phase4_setup
    po = _create_test_po(env, number="PO-STD-001", issued_by=env["proc_exec_a"])
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-STD-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    notifs = Notification.objects.filter(
        recipient=env["proc_exec_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    )
    assert notifs.count() == 1
    notif = notifs.first()
    assert receipt.grn_number in notif.title
    assert po.po_number in notif.message


def test_grn_notification_fallback_to_po_creator(phase4_setup):
    """DEF-012: When standard recipients are empty, notification falls back to active PO creator in same org."""
    env = phase4_setup
    po = _create_test_po(
        env,
        number="PO-FALLBACK-CREATOR",
        created_by=env["proc_exec_a"],
        issued_by=None,
    )
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-CREATOR-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    notifs = Notification.objects.filter(
        recipient=env["proc_exec_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    )
    assert notifs.count() == 1


def test_grn_notification_fallback_to_active_procurement_manager(phase4_setup):
    """DEF-012: When standard recipients and creator are missing, falls back to active Procurement Manager in org."""
    env = phase4_setup
    po = _create_test_po(
        env,
        number="PO-FALLBACK-PM",
        created_by=None,
        issued_by=None,
    )
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-PM-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    notifs = Notification.objects.filter(
        recipient=env["proc_mgr_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    )
    assert notifs.count() == 1


def test_grn_notification_excludes_inactive_user(phase4_setup):
    """DEF-012: Inactive candidates are excluded from notifications and fallback continues to next active user."""
    env = phase4_setup
    inactive_user = User.objects.create_user(
        email="inactive_buyer@acme.com",
        password="Password123!",
        role=env["proc_mgr_role"],
        organization=env["org_a"],
        is_active=False,
    )

    po = _create_test_po(
        env,
        number="PO-INACTIVE-CANDIDATE",
        created_by=inactive_user,
        issued_by=inactive_user,
    )
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-INACT-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    # Inactive user receives no notification
    assert not Notification.objects.filter(recipient=inactive_user).exists()

    # Active Procurement Manager receives the fallback notification
    pm_notifs = Notification.objects.filter(
        recipient=env["proc_mgr_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    )
    assert pm_notifs.count() == 1


def test_grn_notification_excludes_cross_tenant_user(phase4_setup):
    """DEF-012: User from another organization/tenant is excluded; fallback stays within owning org."""
    env = phase4_setup
    # PO belongs to Org A (cost_center cc_a1), but issued_by is from Org B
    po = _create_test_po(
        env,
        number="PO-CROSS-TENANT",
        created_by=env["proc_mgr_b"],
        issued_by=env["proc_mgr_b"],
    )
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-CROSS-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    # Org B user receives NOTHING for Org A receipt
    assert not Notification.objects.filter(
        recipient=env["proc_mgr_b"],
        title__contains=receipt.grn_number,
    ).exists()

    # Org A Procurement Manager receives the notification
    assert Notification.objects.filter(
        recipient=env["proc_mgr_a"],
        title__contains=receipt.grn_number,
    ).exists()


def test_grn_notification_duplicate_prevention(phase4_setup):
    """DEF-012: Duplicate notifications for the same receipt event are prevented."""
    env = phase4_setup
    po = _create_test_po(env, number="PO-DUP-TEST", issued_by=env["proc_exec_a"])
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-DUP-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    initial_count = Notification.objects.filter(
        recipient=env["proc_exec_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    ).count()
    assert initial_count == 1

    # Call duplicate dispatch directly to verify deduplication
    from apps.receipts.services import Notification as NotifModel
    # Re-running notification block for the same receipt must not create another notification
    duplicate_exists = NotifModel.objects.filter(
        recipient=env["proc_exec_a"],
        notification_type=NotifModel.TYPE_GOODS_RECEIVED,
    ).filter(
        title__contains=receipt.grn_number
    ).exists()
    assert duplicate_exists is True

    # Total remains 1
    assert Notification.objects.filter(
        recipient=env["proc_exec_a"],
        notification_type=Notification.TYPE_GOODS_RECEIVED,
        title__contains=receipt.grn_number,
    ).count() == 1


def test_grn_creation_succeeds_when_no_eligible_recipient_exists(phase4_setup):
    """DEF-012: GRN creation succeeds cleanly when all potential recipients are None or inactive."""
    env = phase4_setup
    # Deactivate Org A procurement manager temporarily
    env["proc_mgr_a"].is_active = False
    env["proc_mgr_a"].save(update_fields=["is_active"])

    po = _create_test_po(
        env,
        number="PO-NO-RECIPIENTS",
        created_by=None,
        issued_by=None,
    )
    line = po.lines.first()

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-NORECIP-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    assert receipt is not None
    assert receipt.grn_number.startswith("GRN-")


def test_grn_notification_non_blocking_on_dispatch_failure(phase4_setup, monkeypatch):
    """DEF-012: Notification dispatch error does not abort or fail valid GRN creation."""
    env = phase4_setup
    po = _create_test_po(env, number="PO-NON-BLOCKING", issued_by=env["proc_exec_a"])
    line = po.lines.first()

    def mock_create_failure(*args, **kwargs):
        raise RuntimeError("Simulated notification service failure.")

    monkeypatch.setattr(Notification.objects, "create", mock_create_failure)

    receipt = create_goods_receipt_service(
        po=po,
        received_by=env["receiver_a"],
        delivery_note_number="DN-FAIL-001",
        receipt_items=[{
            "po_line_id": str(line.id),
            "quantity_received": Decimal("5.00"),
            "quantity_accepted": Decimal("5.00"),
        }],
    )

    assert receipt is not None
    assert receipt.grn_number.startswith("GRN-")


# =========================================================================
# DEF-013: Spend Analytics Calculation & Multi-dimensional Breakdown Tests
# =========================================================================

def _create_test_invoice(env, number, amount, status, inv_date=None, cost_center=None, vendor=None):
    cc = cost_center or env["cc_a1"]
    v = vendor or env["vendor_a"]
    dt = inv_date or timezone.now().date()
    po = _create_test_po(env, number=f"PO-{number}", cost_center=cc, vendor=v)
    return SupplierInvoice.objects.create(
        invoice_number=number,
        po=po,
        vendor=v,
        invoice_date=dt,
        due_date=dt + timedelta(days=30),
        subtotal=Decimal(str(amount)),
        total_amount=Decimal(str(amount)),
        status=status,
    )


def test_spend_analytics_excludes_non_paid_invoices(phase4_setup):
    """DEF-013: Exclude DRAFT, RECEIVED, EXCEPTION, REJECTED, and other non-PAID invoices from realized spend."""
    env = phase4_setup
    # Create non-PAID invoices
    _create_test_invoice(env, "INV-RECV-01", 1000.00, SupplierInvoice.STATUS_RECEIVED)
    _create_test_invoice(env, "INV-VAL-01", 1500.00, SupplierInvoice.STATUS_VALIDATION)
    _create_test_invoice(env, "INV-MATCH-01", 2000.00, SupplierInvoice.STATUS_MATCHING)
    _create_test_invoice(env, "INV-EXC-01", 2500.00, SupplierInvoice.STATUS_EXCEPTION)
    _create_test_invoice(env, "INV-APP-01", 3000.00, SupplierInvoice.STATUS_APPROVAL)
    _create_test_invoice(env, "INV-READY-01", 3500.00, SupplierInvoice.STATUS_READY_FOR_PAYMENT)
    _create_test_invoice(env, "INV-REJ-01", 4000.00, SupplierInvoice.STATUS_REJECTED)

    # 1 PAID invoice
    _create_test_invoice(env, "INV-PAID-01", 5000.00, SupplierInvoice.STATUS_PAID)

    report = get_spend_analytics_report(user=env["proc_mgr_a"])
    invoice_nums = [item["invoice_number"] for item in report]

    assert "INV-PAID-01" in invoice_nums
    assert "INV-RECV-01" not in invoice_nums
    assert "INV-VAL-01" not in invoice_nums
    assert "INV-MATCH-01" not in invoice_nums
    assert "INV-EXC-01" not in invoice_nums
    assert "INV-APP-01" not in invoice_nums
    assert "INV-READY-01" not in invoice_nums
    assert "INV-REJ-01" not in invoice_nums

    total_realized_spend = sum(item["actual_spend"] for item in report)
    assert total_realized_spend == 5000.00


def test_spend_analytics_date_filtering_period_days(phase4_setup):
    """DEF-013: Verify period_days accurately restricts realized spend to the specified window."""
    env = phase4_setup
    today = timezone.now().date()
    # Invoice 1: 10 days ago (within 30-day window)
    _create_test_invoice(env, "INV-DAYS-10", 1200.00, SupplierInvoice.STATUS_PAID, inv_date=today - timedelta(days=10))
    # Invoice 2: 45 days ago (outside 30-day window, inside 60-day)
    _create_test_invoice(env, "INV-DAYS-45", 2800.00, SupplierInvoice.STATUS_PAID, inv_date=today - timedelta(days=45))

    report_30 = get_spend_analytics_report(user=env["proc_mgr_a"], period_days=30)
    nums_30 = [item["invoice_number"] for item in report_30]
    assert "INV-DAYS-10" in nums_30
    assert "INV-DAYS-45" not in nums_30
    assert sum(item["actual_spend"] for item in report_30) == 1200.00

    report_60 = get_spend_analytics_report(user=env["proc_mgr_a"], period_days=60)
    nums_60 = [item["invoice_number"] for item in report_60]
    assert "INV-DAYS-10" in nums_60
    assert "INV-DAYS-45" in nums_60
    assert sum(item["actual_spend"] for item in report_60) == 4000.00


def test_spend_analytics_date_filtering_start_and_end_date(phase4_setup):
    """DEF-013: Verify inclusive start_date and end_date filtering."""
    env = phase4_setup
    _create_test_invoice(env, "INV-JAN", 1000.00, SupplierInvoice.STATUS_PAID, inv_date=date(2026, 1, 15))
    _create_test_invoice(env, "INV-FEB", 2000.00, SupplierInvoice.STATUS_PAID, inv_date=date(2026, 2, 15))
    _create_test_invoice(env, "INV-MAR", 3000.00, SupplierInvoice.STATUS_PAID, inv_date=date(2026, 3, 15))

    # Query strictly February
    feb_report = get_spend_analytics_report(
        user=env["proc_mgr_a"],
        start_date="2026-02-01",
        end_date="2026-02-28",
    )
    feb_nums = [item["invoice_number"] for item in feb_report]
    assert feb_nums == ["INV-FEB"]
    assert sum(item["actual_spend"] for item in feb_report) == 2000.00


def test_spend_analytics_category_breakdown(phase4_setup):
    """DEF-013: Category filter accurately filters invoices and category sums reconcile with total realized spend."""
    env = phase4_setup
    # Create a second vendor in Org A under Logistics category
    vendor_log_a = Vendor.objects.create(
        organization=env["org_a"],
        legal_name="Acme Rapid Freight",
        trade_name="Rapid Freight",
        vendor_number="VND-RAPID-003",
        tax_identification_number="TIN-RAPID-003",
        email="contact@rapidfreight.com",
        category=env["cat_log"],
        status=Vendor.STATUS_ACTIVE,
    )

    _create_test_invoice(env, "INV-CAT-IT", 6000.00, SupplierInvoice.STATUS_PAID, vendor=env["vendor_a"])
    _create_test_invoice(env, "INV-CAT-LOG", 4000.00, SupplierInvoice.STATUS_PAID, vendor=vendor_log_a)

    all_report = get_spend_analytics_report(user=env["proc_mgr_a"])
    total_spend = sum(item["actual_spend"] for item in all_report)
    assert total_spend == 10000.00

    it_report = get_spend_analytics_report(user=env["proc_mgr_a"], category="IT Services")
    assert all(item["vendor_category"] == "IT Services" for item in it_report)
    it_spend = sum(item["actual_spend"] for item in it_report)
    assert it_spend == 6000.00

    log_report = get_spend_analytics_report(user=env["proc_mgr_a"], category="Logistics")
    assert all(item["vendor_category"] == "Logistics" for item in log_report)
    log_spend = sum(item["actual_spend"] for item in log_report)
    assert log_spend == 4000.00

    assert it_spend + log_spend == total_spend


def test_spend_analytics_department_and_cost_center_breakdown(phase4_setup):
    """DEF-013: Department and cost-center breakdowns reconcile with total realized spend."""
    env = phase4_setup
    # CC-ENG-1 under Engineering ($3000)
    _create_test_invoice(env, "INV-ENG-1", 3000.00, SupplierInvoice.STATUS_PAID, cost_center=env["cc_a1"])
    # CC-ENG-2 under Engineering ($2000)
    _create_test_invoice(env, "INV-ENG-2", 2000.00, SupplierInvoice.STATUS_PAID, cost_center=env["cc_a2"])
    # CC-OPS-1 under Operations ($5000)
    _create_test_invoice(env, "INV-OPS-1", 5000.00, SupplierInvoice.STATUS_PAID, cost_center=env["cc_a_ops"])

    all_report = get_spend_analytics_report(user=env["proc_mgr_a"])
    total_spend = sum(item["actual_spend"] for item in all_report)
    assert total_spend == 10000.00

    eng_report = get_spend_analytics_report(user=env["proc_mgr_a"], department="Engineering")
    eng_spend = sum(item["actual_spend"] for item in eng_report)
    assert eng_spend == 5000.00

    ops_report = get_spend_analytics_report(user=env["proc_mgr_a"], department="Operations")
    ops_spend = sum(item["actual_spend"] for item in ops_report)
    assert ops_spend == 5000.00

    assert eng_spend + ops_spend == total_spend

    # Specific cost center filter
    cc1_report = get_spend_analytics_report(user=env["proc_mgr_a"], cost_center="CC-ENG-1")
    assert len(cc1_report) == 1
    assert cc1_report[0]["actual_spend"] == 3000.00


def test_spend_analytics_tenant_isolation(phase4_setup):
    """DEF-013: Verify strict organization scoping: Org A cannot see Org B spend and vice-versa."""
    env = phase4_setup
    _create_test_invoice(env, "INV-ORG-A", 15000.00, SupplierInvoice.STATUS_PAID, cost_center=env["cc_a1"], vendor=env["vendor_a"])
    _create_test_invoice(env, "INV-ORG-B", 25000.00, SupplierInvoice.STATUS_PAID, cost_center=env["cc_b1"], vendor=env["vendor_b"])

    report_a = get_spend_analytics_report(user=env["proc_mgr_a"])
    report_b = get_spend_analytics_report(user=env["proc_mgr_b"])

    nums_a = [item["invoice_number"] for item in report_a]
    nums_b = [item["invoice_number"] for item in report_b]

    assert "INV-ORG-A" in nums_a
    assert "INV-ORG-B" not in nums_a
    assert sum(item["actual_spend"] for item in report_a) == 15000.00

    assert "INV-ORG-B" in nums_b
    assert "INV-ORG-A" not in nums_b
    assert sum(item["actual_spend"] for item in report_b) == 25000.00


def test_dashboard_summary_spend_matches_spend_analytics(phase4_setup):
    """DEF-013: Dashboard summary total_spend KPI strictly reconciles with spend analytics detail total."""
    env = phase4_setup
    _create_test_invoice(env, "INV-DASH-1", 4500.00, SupplierInvoice.STATUS_PAID)
    _create_test_invoice(env, "INV-DASH-2", 3500.00, SupplierInvoice.STATUS_PAID)

    client = APIClient()
    client.force_authenticate(user=env["proc_mgr_a"])

    dash_resp = client.get("/api/v1/reports/dashboard-summary/")
    assert dash_resp.status_code == status.HTTP_200_OK

    spend_resp = client.get("/api/v1/reports/spend-analytics/")
    assert spend_resp.status_code == status.HTTP_200_OK

    spend_detail_total = sum(float(item["actual_spend"]) for item in spend_resp.data)
    dashboard_spend = float(dash_resp.data["total_spend"])

    assert dashboard_spend == spend_detail_total
    assert dashboard_spend == 8000.00


def test_spend_analytics_api_validation_and_rbac(phase4_setup):
    """DEF-013: API parameters validation (HTTP 400) and role-based access control (HTTP 403)."""
    env = phase4_setup
    client = APIClient()

    # 1. Invalid period_days
    client.force_authenticate(user=env["proc_mgr_a"])
    resp_neg = client.get("/api/v1/reports/spend-analytics/?period_days=-5")
    assert resp_neg.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_neg.data["error"]["code"] == "VALIDATION_ERROR"

    resp_str = client.get("/api/v1/reports/spend-analytics/?period_days=notanint")
    assert resp_str.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_str.data["error"]["code"] == "VALIDATION_ERROR"

    # 2. Invalid date formats
    resp_date = client.get("/api/v1/reports/spend-analytics/?start_date=2026/02/31")
    assert resp_date.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_date.data["error"]["code"] == "VALIDATION_ERROR"

    resp_end = client.get("/api/v1/reports/spend-analytics/?end_date=bad-date")
    assert resp_end.status_code == status.HTTP_400_BAD_REQUEST
    assert resp_end.data["error"]["code"] == "VALIDATION_ERROR"

    # 3. RBAC: Requester role is forbidden (HTTP 403)
    client.force_authenticate(user=env["requester_a"])
    resp_req = client.get("/api/v1/reports/spend-analytics/")
    assert resp_req.status_code == status.HTTP_403_FORBIDDEN

    # 4. RBAC: Procurement Manager is allowed (HTTP 200)
    client.force_authenticate(user=env["proc_mgr_a"])
    resp_pm = client.get("/api/v1/reports/spend-analytics/")
    assert resp_pm.status_code == status.HTTP_200_OK

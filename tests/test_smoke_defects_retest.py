import os
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APIRequestFactory

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalPolicy, ApprovalStep
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.services import create_contract_service
from apps.invoices.models import SupplierInvoice
from apps.invoices.services import run_3_way_match_service
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt
from apps.requisitions.models import PRLine, PurchaseRequisition
from apps.requisitions.services import (
    approve_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.sourcing.models import AwardDecision, SourcingEvent, VendorBid
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def base_setup(db):
    org = Organization.objects.create(name="HPE Enterprise", code="HPE-ORG")
    dept = Department.objects.create(name="Technology Procurement", code="TECH-01", organization=org)
    cc = CostCenter.objects.create(name="IT Infrastructure", code="CC-IT-01", department=dept)
    fp = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        name="FY2026-P1",
        period_number=1,
        start_date="2026-01-01",
        end_date="2026-12-31",
        is_closed=False,
    )
    budget = Budget.objects.create(
        cost_center=cc,
        fiscal_period=fp,
        allocated_amount=Decimal("5000.00"),
        reserved_amount=Decimal("0.00"),
        committed_amount=Decimal("0.00"),
        actual_amount=Decimal("0.00"),
        allow_overspend=False,
    )
    category = VendorCategory.objects.create(name="Hardware & Software", code="HW-SW")
    vendor = Vendor.objects.create(
        organization=org,
        legal_name="Cisco Systems Inc",
        trade_name="Cisco",
        vendor_number="VND-CISCO-01",
        status=Vendor.STATUS_ACTIVE,
        category=category,
    )

    r_requester, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_proc_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_proc_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    r_finance, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance / AP"})
    r_stores, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores / Receiver"})
    r_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    r_vendor_role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})
    r_super, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})

    u_requester = User.objects.create_user(
        email="requester@test.com", password="password123", role=r_requester, department=dept, organization=org
    )
    u_proc_mgr = User.objects.create_user(
        email="procmgr@test.com", password="password123", role=r_proc_mgr, organization=org
    )
    u_proc_exec = User.objects.create_user(
        email="procexec@test.com", password="password123", role=r_proc_exec, organization=org
    )
    u_finance = User.objects.create_user(
        email="finance@test.com", password="password123", role=r_finance, organization=org
    )
    u_stores = User.objects.create_user(
        email="stores@test.com", password="password123", role=r_stores, organization=org
    )
    u_auditor = User.objects.create_user(
        email="auditor@test.com", password="password123", role=r_auditor, organization=org
    )
    u_vendor = User.objects.create_user(
        email="vendor@test.com", password="password123", role=r_vendor_role, vendor=vendor
    )
    u_super = User.objects.create_user(
        email="admin@test.com", password="password123", role=r_super, is_superuser=True, is_staff=True
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "budget": budget,
        "vendor": vendor,
        "u_requester": u_requester,
        "u_proc_mgr": u_proc_mgr,
        "u_proc_exec": u_proc_exec,
        "u_finance": u_finance,
        "u_stores": u_stores,
        "u_auditor": u_auditor,
        "u_vendor": u_vendor,
        "u_super": u_super,
    }


# ==============================================================================
# DEF-001 (TC-WFL-008): Overspend blocked when allow_overspend=False
# ==============================================================================
@pytest.mark.django_db
def test_def_001_tc_wfl_008_overspend_blocked_when_allow_overspend_false(base_setup):
    data = base_setup
    budget = data["budget"]
    budget.allocated_amount = Decimal("100.00")
    budget.allow_overspend = False
    budget.save()

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-TEST-OVERSPEND",
        title="Excessive Requisition",
        requester=data["u_requester"],
        department=data["dept"],
        cost_center=data["cc"],
        total_amount=Decimal("2000.00"),
        status=PurchaseRequisition.STATUS_DRAFT,
        justification="Required hardware",
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Server Rack",
        quantity=Decimal("2.00"),
        estimated_unit_price=Decimal("1000.00"),
        estimated_total=Decimal("2000.00"),
    )

    # Submit PR -> moves to STATUS_BUDGET_REVIEW because 2000 > 100
    submit_purchase_requisition_service(requisition=pr, user=data["u_requester"])
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW

    # Approval by PROC_MGR must be blocked with ValidationError because allow_overspend is False
    with pytest.raises(Exception) as exc_info:
        approve_purchase_requisition_service(
            requisition=pr, approver=data["u_proc_mgr"], comments="Attempting overrun approval"
        )
    assert "Budget overspend is not permitted" in str(exc_info.value)

    # Verify budget was NOT over-reserved
    budget.refresh_from_db()
    assert budget.reserved_amount == Decimal("0.00")
    assert budget.available_amount == Decimal("100.00")


# ==============================================================================
# DEF-002 (TC-INV-004): Invoice subtotal + tax mismatch rejected with HTTP 400
# ==============================================================================
@pytest.mark.django_db
def test_def_002_tc_inv_004_invoice_subtotal_tax_mismatch_rejected(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_finance"])

    po = PurchaseOrder.objects.create(
        po_number="PO-INV-001",
        vendor=data["vendor"],
        cost_center=data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        created_by=data["u_proc_exec"],
    )

    # subtotal 100 + tax 10 but total_amount 500 -> must return HTTP 400
    res = client.post(
        "/api/v1/invoices/",
        {
            "po": str(po.id),
            "vendor": str(data["vendor"].id),
            "invoice_number": "INV-MISMATCH-001",
            "invoice_date": str(timezone.now().date()),
            "due_date": str(timezone.now().date() + timezone.timedelta(days=30)),
            "subtotal": "100.00",
            "tax_amount": "10.00",
            "total_amount": "500.00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "total_amount" in str(res.data) or "Total amount mismatch" in str(res.data)


# ==============================================================================
# DEF-003 (TC-RBAC-040): REQUESTER denied invoices list
# ==============================================================================
@pytest.mark.django_db
def test_def_003_tc_rbac_040_requester_denied_invoices(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_requester"])

    res = client.get("/api/v1/invoices/")
    assert res.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# DEF-004 (TC-SUP-005, TC-INT-008): Celery beat task paths import cleanly
# ==============================================================================
def test_def_004_tc_sup_005_tc_int_008_celery_beat_task_imports():
    from apps.contracts.tasks import scan_contract_expirations_and_milestones_task
    from apps.notifications.tasks import scan_overdue_approvals_and_escalate
    from apps.sourcing.tasks import sync_sourcing_bid_windows_task
    from apps.vendors.tasks import scan_vendor_document_expirations_task

    assert callable(scan_contract_expirations_and_milestones_task)
    assert callable(scan_overdue_approvals_and_escalate)
    assert callable(sync_sourcing_bid_windows_task)
    assert callable(scan_vendor_document_expirations_task)


# ==============================================================================
# DEF-005 (TC-APR-021, TC-APR-016): No repeated approval of same action
# ==============================================================================
@pytest.mark.django_db
def test_def_005_tc_apr_021_single_approval_and_no_double_approve(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_proc_mgr"])

    action = ApprovalAction.objects.create(
        target_object_id=str(data["vendor"].id),
        target_model_name="Vendor",
        actor=data["u_proc_mgr"],
        action=ApprovalAction.ACTION_APPROVE,
        previous_state="KYC_REVIEW",
        new_state="ACTIVE",
    )

    # Calling approve on an already approved action must return HTTP 400
    res = client.post(f"/api/v1/approvals/actions/{action.id}/approve/", {"comment": "Again"})
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "ALREADY_PROCESSED" in str(res.data)


# ==============================================================================
# DEF-006 (TC-WFL-001): PR budget reservation released upon PO conversion
# ==============================================================================
@pytest.mark.django_db
def test_def_006_tc_wfl_001_budget_reservation_released_on_po(base_setup):
    data = base_setup
    budget = data["budget"]

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-WFL-001",
        title="WFL Hardware PR",
        requester=data["u_requester"],
        department=data["dept"],
        cost_center=data["cc"],
        total_amount=Decimal("200.00"),
        status=PurchaseRequisition.STATUS_DRAFT,
        justification="Project equipment",
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Router",
        quantity=Decimal("2.00"),
        estimated_unit_price=Decimal("100.00"),
        estimated_total=Decimal("200.00"),
    )

    # Submit PR -> reserves 200.00 in budget
    submit_purchase_requisition_service(requisition=pr, user=data["u_requester"])
    budget.refresh_from_db()
    assert budget.reserved_amount == Decimal("200.00")

    # Approve PR -> completes approval chain
    approve_purchase_requisition_service(
        requisition=pr, approver=data["u_super"], comments="PR Approved"
    )
    pr.refresh_from_db()
    if pr.status != PurchaseRequisition.STATUS_APPROVED:
        approve_purchase_requisition_service(
            requisition=pr, approver=data["u_super"], comments="PR Approved Step 2"
        )
        pr.refresh_from_db()

    # Create PO from PR and issue it
    from apps.orders.services import generate_purchase_order_service, issue_purchase_order_service
    po = generate_purchase_order_service(
        vendor=data["vendor"],
        cost_center=data["cc"],
        line_items=[{"item_description": "Router", "quantity": Decimal("2.00"), "unit_price": Decimal("100.00")}],
        created_by_user=data["u_proc_exec"],
        requisition=pr,
        tax_rate=Decimal("0.10"),
    )
    po.status = PurchaseOrder.STATUS_APPROVED
    po.save()

    issue_purchase_order_service(po=po, actor=data["u_proc_exec"])

    # Budget reservation must be released (reserved=0) and committed must be 220.00 (200 + 20 tax)
    budget.refresh_from_db()
    assert budget.reserved_amount == Decimal("0.00")
    assert budget.committed_amount == Decimal("220.00")
    assert budget.available_amount == Decimal("4780.00")


# ==============================================================================
# DEF-007: Concurrent contract creation uniqueness
# ==============================================================================
@pytest.mark.django_db
def test_def_007_concurrent_contract_creation(base_setup):
    data = base_setup
    c1 = create_contract_service(
        title="Contract Alpha",
        vendor=data["vendor"],
        contract_value=Decimal("10000.00"),
        start_date=timezone.now().date(),
        end_date=timezone.now().date() + timezone.timedelta(days=365),
        contract_owner=data["u_super"],
    )
    c2 = create_contract_service(
        title="Contract Beta",
        vendor=data["vendor"],
        contract_value=Decimal("15000.00"),
        start_date=timezone.now().date(),
        end_date=timezone.now().date() + timezone.timedelta(days=365),
        contract_owner=data["u_super"],
    )
    assert c1.contract_number != c2.contract_number
    assert c1.contract_number.startswith("CON-")
    assert c2.contract_number.startswith("CON-")


# ==============================================================================
# DEF-008 & DEF-009: Receipt string UUID po_line accepted
# ==============================================================================
@pytest.mark.django_db
def test_def_008_009_receipt_string_po_line_uuid(base_setup):
    data = base_setup
    po = PurchaseOrder.objects.create(
        po_number="PO-RECV-01",
        vendor=data["vendor"],
        cost_center=data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        created_by=data["u_proc_exec"],
    )
    line = POLine.objects.create(
        po=po,
        item_description="Cisco Switch",
        quantity=Decimal("5.00"),
        unit_price=Decimal("20.00"),
        line_total=Decimal("100.00"),
        quantity_received=Decimal("0.00"),
    )

    client = APIClient()
    client.force_authenticate(user=data["u_stores"])

    # Pass lines with 'po_line' as a string UUID
    res = client.post(
        "/api/v1/receipts/",
        {
            "po": str(po.id),
            "carrier_name": "FedEx",
            "tracking_number": "TRK-12345",
            "lines": [
                {
                    "po_line": str(line.id),
                    "quantity_received": "2.00",
                    "quantity_accepted": "2.00",
                }
            ],
        },
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    line.refresh_from_db()
    assert line.quantity_received == Decimal("2.00")


# ==============================================================================
# DEF-010 (TC-INV-009): VENDOR_USER denied POST /api/v1/invoices/
# ==============================================================================
@pytest.mark.django_db
def test_def_010_tc_inv_009_vendor_denied_invoice_post(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_vendor"])

    res = client.post(
        "/api/v1/invoices/",
        {
            "vendor": str(data["vendor"].id),
            "invoice_number": "INV-VENDOR-DIRECT",
            "invoice_date": str(timezone.now().date()),
            "subtotal": "100.00",
            "total_amount": "100.00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN


# ==============================================================================
# DEF-011 (TC-REQ-003): PR without line items rejected
# ==============================================================================
@pytest.mark.django_db
def test_def_011_tc_req_003_pr_without_lines_rejected(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_requester"])

    res = client.post(
        "/api/v1/requisitions/",
        {
            "title": "PR Empty Lines",
            "justification": "Test justification",
            "department": str(data["dept"].id),
            "cost_center": str(data["cc"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=5)),
            "lines": [],
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "lines" in str(res.data)


# ==============================================================================
# DEF-012 (TC-SUP-031): Non-existent UI route returns custom 404 without debug info
# ==============================================================================
@pytest.mark.django_db
def test_def_012_tc_sup_031_custom_404_page(client):
    res = client.get("/this-page-does-not-exist/")
    assert res.status_code == 404
    content = res.content.decode("utf-8")
    assert "URLconf" not in content
    assert "DEBUG = True" not in content
    assert "Page Not Found" in content or "404" in content


# ==============================================================================
# DEF-013 (TC-VEN-042, TC-VEN-043): PROC_EXEC can record risk, blank notes rejected
# ==============================================================================
@pytest.mark.django_db
def test_def_013_tc_ven_042_043_proc_exec_risk_and_blank_notes(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_proc_exec"])

    # 1. Blank notes -> HTTP 400
    res_blank = client.post(
        f"/api/v1/vendors/{data['vendor'].id}/risk/",
        {"risk_level": "HIGH", "notes": "", "risk_flags": ["SANCTIONS"]},
        format="json",
    )
    assert res_blank.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Valid risk entry by PROC_EXEC -> HTTP 201
    res_valid = client.post(
        f"/api/v1/vendors/{data['vendor'].id}/risk/",
        {"risk_level": "HIGH", "notes": "Sanctions check alert", "risk_flags": ["SANCTIONS"]},
        format="json",
    )
    assert res_valid.status_code == status.HTTP_201_CREATED
    assert res_valid.data["risk_level"] == "HIGH"


# ==============================================================================
# DEF-014 (TC-APR-009): PATCH policy is_active=false persists
# ==============================================================================
@pytest.mark.django_db
def test_def_014_tc_apr_009_patch_policy_is_active_false(base_setup):
    data = base_setup
    r_approver = Role.objects.get(code=Role.DEPT_APPROVER)
    policy = ApprovalPolicy.objects.create(
        name="Test Policy Active",
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("0.00"),
        max_amount=Decimal("1000.00"),
        organization=data["org"],
        status=ApprovalPolicy.STATUS_ACTIVE,
        is_active=True,
    )
    ApprovalStep.objects.create(
        policy=policy,
        step_number=1,
        approver_role=r_approver,
    )

    client = APIClient()
    client.force_authenticate(user=data["u_super"])

    res = client.patch(
        f"/api/v1/approvals/policies/{policy.id}/",
        {"is_active": False},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["is_active"] is False

    policy.refresh_from_db()
    assert policy.is_active is False
    assert policy.status == ApprovalPolicy.STATUS_ARCHIVED


# ==============================================================================
# DEF-015 (TC-AUD-062, TC-SFP-010): Audit log list supports limit/offset envelope
# ==============================================================================
@pytest.mark.django_db
def test_def_015_tc_aud_062_tc_sfp_010_audit_limit_offset_envelope(base_setup):
    data = base_setup
    from apps.audit.models import AuditLog

    for i in range(10):
        AuditLog.objects.create(
            actor=data["u_super"],
            action=AuditLog.ACTION_CREATE,
            target_model="Organization",
            target_object_id=str(data["org"].id),
            new_state={"seq": i},
        )

    client = APIClient()
    client.force_authenticate(user=data["u_auditor"])

    res = client.get("/api/v1/audit/logs/?limit=5&offset=0")
    assert res.status_code == status.HTTP_200_OK
    assert "total_count" in res.data
    assert "limit" in res.data
    assert "offset" in res.data
    assert "results" in res.data
    assert len(res.data["results"]) <= 5


# ==============================================================================
# DEF-016 (TC-UPL-002, TC-UPL-003): KYC upload accepts PNG and JPEG
# ==============================================================================
@pytest.mark.django_db
def test_def_016_tc_upl_002_003_kyc_png_jpg_upload(client, base_setup):
    data = base_setup
    client.force_login(data["u_vendor"])

    png_file = SimpleUploadedFile("kyc_pan.png", b"\x89PNG\r\n\x1a\nFakePNGData", content_type="image/png")
    res = client.post(
        "/vendor/profile/",
        {
            "action": "upload_kyc",
            "document_type": "PAN",
            "title": "Vendor PAN Scan",
            "kyc_file": png_file,
        },
        follow=True,
    )
    assert res.status_code == 200
    assert data["vendor"].documents.filter(document_type="PAN").exists()


# ==============================================================================
# DEF-017 (TC-INV-018): Matched invoice with 0 exceptions reaches READY_FOR_PAYMENT
# ==============================================================================
@pytest.mark.django_db
def test_def_017_tc_inv_018_matched_invoice_ready_for_payment(base_setup):
    data = base_setup
    po = PurchaseOrder.objects.create(
        po_number="PO-MATCH-01",
        vendor=data["vendor"],
        cost_center=data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        created_by=data["u_proc_exec"],
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Monitor",
        quantity=Decimal("5.00"),
        unit_price=Decimal("20.00"),
        line_total=Decimal("100.00"),
        quantity_received=Decimal("5.00"),
    )
    grn = GoodsReceipt.objects.create(
        po=po,
        grn_number="GRN-MATCH-01",
        received_date=timezone.now(),
        received_by=data["u_stores"],
    )
    from apps.receipts.models import ReceiptLine
    ReceiptLine.objects.create(
        receipt=grn,
        po_line=po_line,
        quantity_received=Decimal("5.00"),
        quantity_accepted=Decimal("5.00"),
    )

    inv = SupplierInvoice.objects.create(
        po=po,
        vendor=data["vendor"],
        invoice_number="INV-MATCH-01",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("100.00"),
        tax_amount=Decimal("10.00"),
        total_amount=Decimal("110.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )
    from apps.invoices.models import InvoiceLine
    InvoiceLine.objects.create(
        invoice=inv,
        po_line=po_line,
        item_description="Monitor",
        quantity=Decimal("5.00"),
        unit_price=Decimal("20.00"),
        line_total=Decimal("100.00"),
    )

    client = APIClient()
    client.force_authenticate(user=data["u_finance"])
    res = client.post(f"/api/v1/invoices/{inv.id}/run-match/")
    assert res.status_code == status.HTTP_200_OK
    inv.refresh_from_db()
    assert inv.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT


# ==============================================================================
# DEF-018 (TC-RBAC-045): STORES_RECEIVER allowed GET /api/v1/invoices/
# ==============================================================================
@pytest.mark.django_db
def test_def_018_tc_rbac_045_stores_receiver_can_read_invoices(base_setup):
    data = base_setup
    client = APIClient()
    client.force_authenticate(user=data["u_stores"])

    res = client.get("/api/v1/invoices/")
    assert res.status_code == status.HTTP_200_OK


# ==============================================================================
# DEF-019 (TC-SRC-038, TC-SRC-039): Sourcing generate-po endpoint generates PO
# ==============================================================================
@pytest.mark.django_db
def test_def_019_tc_src_038_039_sourcing_generate_po(base_setup):
    data = base_setup
    event = SourcingEvent.objects.create(
        event_number="RFQ-2026-TEST",
        title="Hardware RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        created_by=data["u_proc_exec"],
        status=SourcingEvent.STATUS_AWARDED,
        bid_start_date=timezone.now() - timezone.timedelta(days=5),
        bid_end_date=timezone.now() - timezone.timedelta(days=1),
    )
    from apps.sourcing.models import BidLine
    bid = VendorBid.objects.create(
        event=event,
        vendor=data["vendor"],
        total_bid_amount=Decimal("1500.00"),
        status=VendorBid.STATUS_SUBMITTED,
    )
    BidLine.objects.create(
        bid=bid,
        item_description="Cisco Switch",
        quantity=Decimal("10.00"),
        quoted_unit_price=Decimal("150.00"),
    )
    AwardDecision.objects.create(
        event=event,
        winning_bid=bid,
        award_reason="Best commercial and technical score",
        status=AwardDecision.STATUS_APPROVED,
        approved_by=data["u_proc_mgr"],
    )

    client = APIClient()
    client.force_authenticate(user=data["u_proc_exec"])

    # 1. Awarded event -> HTTP 201 Created
    res = client.post(
        f"/api/v1/sourcing-events/events/{event.id}/generate-po/",
        {"cost_center_id": str(data["cc"].id)},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert "po_number" in res.data

    # 2. Non-awarded event -> HTTP 400 Bad Request
    unawarded_event = SourcingEvent.objects.create(
        event_number="RFQ-2026-UNAWARDED",
        title="Unawarded RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        created_by=data["u_proc_exec"],
        status=SourcingEvent.STATUS_PUBLISHED,
        bid_start_date=timezone.now(),
        bid_end_date=timezone.now() + timezone.timedelta(days=5),
    )
    res_unawarded = client.post(
        f"/api/v1/sourcing-events/events/{unawarded_event.id}/generate-po/",
        {"cost_center_id": str(data["cc"].id)},
        format="json",
    )
    assert res_unawarded.status_code == status.HTTP_400_BAD_REQUEST


# ==============================================================================
# DEF-020 (TC-SUP-025): /roles/ public marketing page returns HTTP 200
# ==============================================================================
@pytest.mark.django_db
def test_def_020_tc_sup_025_roles_marketing_page(client):
    res = client.get("/roles/")
    assert res.status_code == 200
    content = res.content.decode("utf-8")
    assert "ProcureSphere 360" in content
    assert "Enterprise Roles" in content

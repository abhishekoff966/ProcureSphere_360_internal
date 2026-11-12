import datetime
from decimal import Decimal
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.approvals.models import ApprovalAction, ApprovalPolicy, ApprovalStep
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.orders.models import PurchaseOrder, POLine
from apps.orders.services import approve_purchase_order_service
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.receipts.services import create_goods_receipt_service
from django.core.exceptions import ValidationError
from apps.invoices.models import SupplierInvoice, MatchException, PaymentStatus
from apps.invoices.services import resolve_match_exception_service, mark_invoice_paid_service, create_supplier_invoice_service
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.vendors.services import approve_vendor_service


@pytest.fixture
def base_test_data(db):
    org = Organization.objects.create(name="Report Verification Org", code="RVO1")
    dept = Department.objects.create(name="Engineering", code="ENG1", organization=org)
    dept2 = Department.objects.create(name="Marketing", code="MKT1", organization=org)
    cc = CostCenter.objects.create(name="Eng CC", code="CC-ENG", department=dept)
    cc2 = CostCenter.objects.create(name="Mkt CC", code="CC-MKT", department=dept2)

    super_role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    req_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    approver_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Dept Approver"})
    legal_role, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    proc_exec_role, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    proc_mgr_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    fin_role, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance / AP"})
    aud_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Compliance / Auditor"})
    stores_role, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})

    admin = User.objects.create_superuser(email="admin_report@example.com", password="Pass123!Secure", role=super_role)
    requester = User.objects.create_user(email="req_report@example.com", password="Pass123!Secure", role=req_role, department=dept)
    dept_appr = User.objects.create_user(email="dept_report@example.com", password="Pass123!Secure", role=approver_role, department=dept)
    legal = User.objects.create_user(email="legal_report@example.com", password="Pass123!Secure", role=legal_role)
    proc_exec = User.objects.create_user(email="procexec_report@example.com", password="Pass123!Secure", role=proc_exec_role)
    proc_mgr = User.objects.create_user(email="procmgr_report@example.com", password="Pass123!Secure", role=proc_mgr_role)
    finance = User.objects.create_user(email="fin_report@example.com", password="Pass123!Secure", role=fin_role, department=dept)
    auditor = User.objects.create_user(email="aud_report@example.com", password="Pass123!Secure", role=aud_role)
    stores = User.objects.create_user(email="stores_report@example.com", password="Pass123!Secure", role=stores_role)

    vcat = VendorCategory.objects.create(name="IT Services", code="IT_SERV")
    vendor = Vendor.objects.create(
        legal_name="Report Vendor Ltd",
        trade_name="Report Vendor",
        vendor_number="VEN-REP-001",
        tax_identification_number="TIN-REP-001",
        category=vcat,
        organization=org,
        status=Vendor.STATUS_SUBMITTED,
    )
    active_vendor = Vendor.objects.create(
        legal_name="Report Active Vendor Ltd",
        trade_name="Active Vendor",
        vendor_number="VEN-ACT-001",
        tax_identification_number="TIN-REP-002",
        category=vcat,
        organization=org,
        status=Vendor.STATUS_ACTIVE,
    )

    policy = ApprovalPolicy.objects.create(
        name="PR Policy",
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("0.00"),
        max_amount=Decimal("100000.00"),
        is_active=True,
    )
    ApprovalStep.objects.create(policy=policy, step_number=1, approver_role=approver_role)

    return {
        "org": org,
        "dept": dept,
        "dept2": dept2,
        "cc": cc,
        "cc2": cc2,
        "admin": admin,
        "requester": requester,
        "dept_appr": dept_appr,
        "legal": legal,
        "proc_exec": proc_exec,
        "proc_mgr": proc_mgr,
        "finance": finance,
        "auditor": auditor,
        "stores": stores,
        "vendor": vendor,
        "active_vendor": active_vendor,
    }


@pytest.mark.django_db
def test_def_001_brute_force_lockout_enforced(base_test_data):
    """DEF-001: Axes proxy handler blocks after failure limit, even with correct password."""
    client = APIClient()
    for _ in range(5):
        resp = client.post("/api/v1/auth/login/", {"email": "req_report@example.com", "password": "WrongPassword!"}, format="json")
    
    resp = client.post("/api/v1/auth/login/", {"email": "req_report@example.com", "password": "Pass123!Secure"}, format="json")
    assert resp.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert resp.data["error"]["code"] == "ACCOUNT_LOCKED"


@pytest.mark.django_db
def test_def_002_invoices_rbac_requester_dept_approver_legal_denied_403(base_test_data):
    """DEF-002: REQUESTER, DEPT_APPROVER, and LEGAL_MGR receive HTTP 403 on invoices list."""
    client = APIClient()
    for user in [base_test_data["requester"], base_test_data["dept_appr"], base_test_data["legal"]]:
        client.force_authenticate(user=user)
        resp = client.get("/api/v1/invoices/")
        assert resp.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_def_003_org_creation_writes_audit_entry(base_test_data):
    """DEF-003: Creating an organization writes an AuditLog entry."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["admin"])
    resp = client.post("/api/v1/organization/entities/", {"name": "New Corp Ltd", "code": "NCL001", "is_active": True}, format="json")
    assert resp.status_code == status.HTTP_201_CREATED
    org_id = resp.data["id"]
    assert AuditLog.objects.filter(target_model="Organization", target_object_id=str(org_id), action=AuditLog.ACTION_CREATE).exists()


@pytest.mark.django_db
def test_def_004_approval_actions_filter_target_object_id(base_test_data):
    """DEF-004: ?target_object_id=<id> filters approval actions strictly."""
    act1 = ApprovalAction.objects.create(
        actor=base_test_data["admin"],
        target_object_id="11111111-1111-1111-1111-111111111111",
        target_model_name="PurchaseRequisition",
        action=ApprovalAction.ACTION_SUBMIT,
    )
    act2 = ApprovalAction.objects.create(
        actor=base_test_data["admin"],
        target_object_id="22222222-2222-2222-2222-222222222222",
        target_model_name="PurchaseRequisition",
        action=ApprovalAction.ACTION_SUBMIT,
    )
    client = APIClient()
    client.force_authenticate(user=base_test_data["admin"])
    resp = client.get(f"/api/v1/approvals/actions/?target_object_id={act1.target_object_id}")
    assert resp.status_code == status.HTTP_200_OK
    results = resp.data.get("results", [])
    assert len(results) == 1
    assert results[0]["target_object_id"] == str(act1.target_object_id)


@pytest.mark.django_db
def test_def_005_vendor_kyc_approval_audited(base_test_data):
    """DEF-005: Vendor approval status transition to ACTIVE writes AuditLog."""
    vendor = base_test_data["vendor"]
    dummy_pdf = SimpleUploadedFile("cert.pdf", b"%PDF-1.4 dummy", content_type="application/pdf")
    VendorDocument.objects.create(vendor=vendor, document_type=VendorDocument.DOC_TYPE_TAX, file=dummy_pdf, status=VendorDocument.STATUS_VERIFIED)
    VendorDocument.objects.create(vendor=vendor, document_type=VendorDocument.DOC_TYPE_BANK, file=dummy_pdf, status=VendorDocument.STATUS_VERIFIED)
    VendorDocument.objects.create(vendor=vendor, document_type=VendorDocument.DOC_TYPE_CERT, file=dummy_pdf, status=VendorDocument.STATUS_VERIFIED)
    
    approve_vendor_service(vendor=vendor, manager=base_test_data["proc_mgr"], notes="KYC Approved")
    vendor.refresh_from_db()
    assert vendor.status == Vendor.STATUS_ACTIVE
    assert AuditLog.objects.filter(target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_APPROVE).exists()


@pytest.mark.django_db
def test_def_006_requisition_without_lines_rejected_400(base_test_data):
    """DEF-006: Requisition without line items returns HTTP 400."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["requester"])
    delivery_date = (timezone.now() + datetime.timedelta(days=10)).date().isoformat()
    payload = {
        "title": "PR Empty Lines",
        "justification": "Test requisition justification",
        "requested_delivery_date": delivery_date,
        "department": str(base_test_data["dept"].id),
        "cost_center": str(base_test_data["cc"].id),
        "lines": [],
    }
    resp = client.post("/api/v1/requisitions/", payload, format="json")
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "lines" in str(resp.data)


@pytest.mark.django_db
def test_def_007_requisition_cost_center_department_mismatch_rejected_400(base_test_data):
    """DEF-007: PR referencing cost center belonging to another department returns HTTP 400."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["requester"])
    delivery_date = (timezone.now() + datetime.timedelta(days=10)).date().isoformat()
    payload = {
        "title": "PR Mismatched Dept CC",
        "justification": "Testing mismatch",
        "requested_delivery_date": delivery_date,
        "department": str(base_test_data["dept"].id),
        "cost_center": str(base_test_data["cc2"].id),  # belongs to dept2
        "lines": [{"item_description": "Server", "quantity": 1, "estimated_unit_price": "500.00"}],
    }
    resp = client.post("/api/v1/requisitions/", payload, format="json")
    assert resp.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]


@pytest.mark.django_db
def test_def_008_pr_submission_writes_submit_audit(base_test_data):
    """DEF-008: Submitting a requisition writes SUBMIT audit entry."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["requester"])
    delivery_date = (timezone.now() + datetime.timedelta(days=10)).date()
    pr = PurchaseRequisition.objects.create(
        title="PR Audit Submit",
        justification="PR Submission Audit Test",
        requested_delivery_date=delivery_date,
        pr_number="PR-TEST-SUB-01",
        requester=base_test_data["requester"],
        department=base_test_data["dept"],
        cost_center=base_test_data["cc"],
        status=PurchaseRequisition.STATUS_DRAFT,
        total_amount=Decimal("100.00"),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Mouse",
        quantity=1,
        estimated_unit_price=Decimal("100.00"),
        estimated_total=Decimal("100.00"),
    )
    resp = client.post(f"/api/v1/requisitions/{pr.id}/submit/", format="json")
    assert resp.status_code == status.HTTP_200_OK
    assert AuditLog.objects.filter(target_model="PurchaseRequisition", target_object_id=str(pr.id), action=AuditLog.ACTION_SUBMIT).exists()


@pytest.mark.django_db
def test_def_009_po_approval_creates_approval_action_row(base_test_data):
    """DEF-009: Approving a purchase order leaves an ApprovalAction row."""
    po = PurchaseOrder.objects.create(
        po_number="PO-APPR-TEST-001",
        vendor=base_test_data["active_vendor"],
        cost_center=base_test_data["cc"],
        status=PurchaseOrder.STATUS_APPROVAL,
        total_amount=Decimal("250.00"),
    )
    approve_purchase_order_service(po=po, approver=base_test_data["proc_mgr"], comments="Approved by PM")
    assert ApprovalAction.objects.filter(target_object_id=po.id, target_model_name="PurchaseOrder", action=ApprovalAction.ACTION_APPROVE).exists()


@pytest.mark.django_db
def test_def_010_duplicate_po_number_returns_400_not_500(base_test_data):
    """DEF-010: Duplicate po_number returns HTTP 400 INTEGRITY_ERROR, not 500."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["proc_exec"])
    payload = {
        "po_number": "PO-DUP-TEST-001",
        "vendor": str(base_test_data["active_vendor"].id),
        "cost_center": str(base_test_data["cc"].id),
        "lines": [{"item_description": "Widget", "quantity": 1, "unit_price": "100.00"}],
    }
    resp1 = client.post("/api/v1/purchase-orders/", payload, format="json")
    assert resp1.status_code == status.HTTP_201_CREATED
    resp2 = client.post("/api/v1/purchase-orders/", payload, format="json")
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST
    assert resp2.data["error"]["code"] in ["INTEGRITY_ERROR", "VALIDATION_ERROR"]


@pytest.mark.django_db
def test_def_011_duplicate_grn_number_rejected_400(base_test_data):
    """DEF-011: Duplicate GRN number is refused (HTTP 400) and quantity not counted twice."""
    po = PurchaseOrder.objects.create(
        po_number="PO-GRN-TEST-001",
        vendor=base_test_data["active_vendor"],
        cost_center=base_test_data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("200.00"),
    )
    line = POLine.objects.create(po=po, item_description="Item 1", quantity=2, unit_price=Decimal("100.00"), quantity_received=0)
    client = APIClient()
    client.force_authenticate(user=base_test_data["stores"])
    payload = {
        "po": str(po.id),
        "grn_number": "GRN-DUP-001",
        "lines": [{"po_line_id": str(line.id), "quantity_received": 1}],
    }
    resp1 = client.post("/api/v1/receipts/", payload, format="json")
    assert resp1.status_code == status.HTTP_201_CREATED
    line.refresh_from_db()
    assert line.quantity_received == 1

    resp2 = client.post("/api/v1/receipts/", payload, format="json")
    assert resp2.status_code == status.HTTP_400_BAD_REQUEST
    line.refresh_from_db()
    assert line.quantity_received == 1  # Not double counted


@pytest.mark.django_db
def test_def_012_goods_receipt_creates_notification(base_test_data):
    """DEF-012: Goods receipt creation dispatches GOODS_RECEIVED notification."""
    po = PurchaseOrder.objects.create(
        po_number="PO-NOTIF-TEST-001",
        vendor=base_test_data["active_vendor"],
        cost_center=base_test_data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("100.00"),
        submitted_by=base_test_data["proc_exec"],
    )
    line = POLine.objects.create(po=po, item_description="Gadget", quantity=1, unit_price=Decimal("100.00"))
    
    count_before = Notification.objects.filter(notification_type=Notification.TYPE_GOODS_RECEIVED).count()
    create_goods_receipt_service(
        po=po,
        received_by=base_test_data["stores"],
        receipt_items=[{"po_line_id": str(line.id), "quantity_received": 1}],
    )
    count_after = Notification.objects.filter(notification_type=Notification.TYPE_GOODS_RECEIVED).count()
    assert count_after > count_before


@pytest.mark.django_db
def test_def_013_dashboard_total_spend_equals_paid_invoices(base_test_data):
    """DEF-013: Dashboard total_spend aggregates only paid invoices / actual spend."""
    po = PurchaseOrder.objects.create(
        po_number="PO-SPEND-TEST-001",
        vendor=base_test_data["active_vendor"],
        cost_center=base_test_data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
    )
    today = timezone.now().date()
    # 1 paid invoice of $300, 1 unpaid/pending invoice of $700
    SupplierInvoice.objects.create(
        invoice_number="INV-PAID-01",
        vendor=base_test_data["active_vendor"],
        po=po,
        invoice_date=today,
        due_date=today + datetime.timedelta(days=30),
        subtotal=Decimal("300.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("300.00"),
        status=SupplierInvoice.STATUS_PAID,
    )
    SupplierInvoice.objects.create(
        invoice_number="INV-UNPAID-01",
        vendor=base_test_data["active_vendor"],
        po=po,
        invoice_date=today,
        due_date=today + datetime.timedelta(days=30),
        subtotal=Decimal("700.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("700.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )
    client = APIClient()
    client.force_authenticate(user=base_test_data["finance"])
    resp = client.get("/api/v1/reports/dashboard-summary/")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["total_spend"] == 300.0  # Only paid invoice, not 1000.0


@pytest.mark.django_db
def test_def_014_audit_entries_store_ip_and_request_id(base_test_data):
    """DEF-014: Performing API action populates ip_address and request_id on AuditLog."""
    client = APIClient()
    client.force_authenticate(user=base_test_data["admin"])
    resp = client.post(
        "/api/v1/organization/entities/",
        {"name": "IP Test Org", "code": "IPORG1", "is_active": True},
        format="json",
        HTTP_X_REQUEST_ID="test-request-id-12345",
        REMOTE_ADDR="192.168.1.100",
    )
    assert resp.status_code == status.HTTP_201_CREATED
    org_id = resp.data["id"]
    audit = AuditLog.objects.filter(target_model="Organization", target_object_id=str(org_id)).first()
    assert audit is not None
    assert audit.ip_address is not None and len(audit.ip_address) > 0
    assert audit.request_id is not None and len(audit.request_id) > 0


@pytest.mark.django_db
def test_def_015_failed_login_creates_audit_log_visible_to_auditor(base_test_data):
    """DEF-015: Failed login creates LOGIN_FAILED AuditLog entry visible to auditor."""
    client = APIClient()
    client.post("/api/v1/auth/login/", {"email": "req_report@example.com", "password": "WrongPassword!"}, format="json")
    
    assert AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).exists()
    
    client.force_authenticate(user=base_test_data["auditor"])
    resp = client.get("/api/v1/audit/logs/?action=LOGIN_FAILED")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.data["count"] >= 1


@pytest.mark.django_db
def test_def_016_finance_dashboard_bug_fixes(client, base_test_data):
    """
    DEF-016: Verify fixes for:
    - BUG-001: 3-Way Match Tolerances label & heading alignment
    - BUG-002: Budget Overview dropdowns do not prematurely auto-submit onchange
    - BUG-003: Currency symbol (₹) on invoice creation and submission
    """
    finance_user = base_test_data["finance"]
    client.force_login(finance_user)

    # 1. BUG-001: Verify Settings page and sidebar contain "3-Way Match Tolerances"
    resp_settings = client.get("/invoices/settings/tolerances/")
    assert resp_settings.status_code == 200
    html_settings = resp_settings.content.decode("utf-8")
    assert "3-Way Match Tolerances" in html_settings

    # Verify Finance base sidebar has "3-Way Match Tolerances"
    resp_dash = client.get("/finance/")
    assert resp_dash.status_code == 200
    html_dash = resp_dash.content.decode("utf-8")
    assert "3-Way Match Tolerances" in html_dash
    assert "System Settings" not in html_dash

    # 2. BUG-002: Verify Budget list does NOT contain onchange="this.form.submit()"
    resp_budgets = client.get("/budgets/")
    assert resp_budgets.status_code == 200
    html_budgets = resp_budgets.content.decode("utf-8")
    assert 'onchange="this.form.submit()"' not in html_budgets

    # 3. BUG-003: Verify Invoice Create page has (₹) and no ($) on Unit Price
    resp_inv_create = client.get("/invoices/create/")
    assert resp_inv_create.status_code == 200
    html_inv_create = resp_inv_create.content.decode("utf-8")
    assert "Unit Price (₹)" in html_inv_create
    assert "Unit Price ($)" not in html_inv_create
    assert '<i class="fa-solid fa-dollar-sign"></i>' not in html_inv_create


@pytest.mark.django_db
def test_def_017_match_exception_resolution_notes_validation(base_test_data):
    """
    BUG-004: Validate that resolution notes require meaningful audit justification
    and reject repeated numbers (e.g. 111111111111111111) or special character strings.
    """
    vendor = base_test_data["vendor"]
    po = PurchaseOrder.objects.create(
        po_number="PO-EXC-01",
        vendor=vendor,
        cost_center=base_test_data["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
    )
    invoice = SupplierInvoice.objects.create(
        invoice_number="INV-EXC-001",
        vendor=vendor,
        po=po,
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        subtotal=Decimal("1000.00"),
        total_amount=Decimal("1100.00"),
        status=SupplierInvoice.STATUS_EXCEPTION,
    )
    exc = MatchException.objects.create(
        invoice=invoice,
        exception_type=MatchException.TYPE_PRICE_VARIANCE,
        description="Unit price variance exceeds allowed tolerance.",
        variance_amount=Decimal("100.00"),
        status=MatchException.STATUS_OPEN,
    )

    # 1. Repeated numeric gibberish (BUG-004 test input: 111111111111111111) must raise ValidationError
    with pytest.raises(ValidationError) as excinfo:
        resolve_match_exception_service(
            match_exception=exc,
            resolved_by_user=base_test_data["finance"],
            resolution_notes="111111111111111111",
            action="RESOLVE",
        )
    assert "meaningful" in str(excinfo.value).lower() or "repeated" in str(excinfo.value).lower()

    # 2. Special character gibberish must raise ValidationError
    with pytest.raises(ValidationError):
        resolve_match_exception_service(
            match_exception=exc,
            resolved_by_user=base_test_data["finance"],
            resolution_notes="!@#$%^&*()_+",
            action="RESOLVE",
        )

    # 3. Too short string (<10 chars) must raise ValidationError
    with pytest.raises(ValidationError):
        resolve_match_exception_service(
            match_exception=exc,
            resolved_by_user=base_test_data["finance"],
            resolution_notes="Approved",
            action="RESOLVE",
        )

    # 4. Meaningful audit justification must succeed
    resolve_match_exception_service(
        match_exception=exc,
        resolved_by_user=base_test_data["finance"],
        resolution_notes="Price variance override approved per vendor contract addendum C-102.",
        action="RESOLVE",
    )
    exc.refresh_from_db()
    assert exc.status == MatchException.STATUS_RESOLVED
    assert "C-102" in exc.resolution_notes


@pytest.mark.django_db
def test_def_018_payment_reference_utr_validation(base_test_data):
    """
    BUG-005: Validate that payment disbursement rejects invalid / malicious special-character
    UTR strings (e.g., 445546!@#$%^&*()) with a clear ValidationError.
    """
    vendor = base_test_data["vendor"]
    cc = base_test_data["cc"]
    today = timezone.localdate()
    period = FiscalPeriod.objects.create(
        organization=base_test_data["org"],
        year=today.year,
        period_number=1,
        name=f"FY{today.year}-Q1",
        start_date=today,
        end_date=today + datetime.timedelta(days=90),
    )
    Budget.objects.create(
        cost_center=cc,
        fiscal_period=period,
        allocated_amount=Decimal("50000.00"),
    )
    po = PurchaseOrder.objects.create(
        po_number="PO-PAY-01",
        vendor=vendor,
        cost_center=cc,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("500.00"),
    )
    invoice = SupplierInvoice.objects.create(
        invoice_number="INV-PAY-001",
        vendor=vendor,
        po=po,
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + datetime.timedelta(days=30),
        subtotal=Decimal("500.00"),
        total_amount=Decimal("550.00"),
        status=SupplierInvoice.STATUS_READY_FOR_PAYMENT,
    )

    # 1. Test input from bug report: 445546!@#$%^&*() must raise ValidationError
    with pytest.raises(ValidationError) as excinfo:
        mark_invoice_paid_service(
            invoice=invoice,
            user=base_test_data["finance"],
            payment_reference="445546!@#$%^&*()",
            payment_method="NEFT_RTGS",
        )
    assert "special characters" in str(excinfo.value).lower() or "invalid" in str(excinfo.value).lower()

    # 2. Too short reference (<6 chars)
    with pytest.raises(ValidationError):
        mark_invoice_paid_service(
            invoice=invoice,
            user=base_test_data["finance"],
            payment_reference="123",
            payment_method="NEFT_RTGS",
        )

    # 3. Valid UTR succeeds cleanly
    paid_inv = mark_invoice_paid_service(
        invoice=invoice,
        user=base_test_data["finance"],
        payment_reference="UTR-2026-981023",
        payment_method="NEFT_RTGS",
    )
    paid_inv.refresh_from_db()
    assert paid_inv.status == SupplierInvoice.STATUS_PAID
    assert PaymentStatus.objects.filter(payment_reference="UTR-2026-981023").exists()


def test_def_019_modal_overflow_hidden_on_body():
    """
    BUG-006: Verify that body.modal-open has overflow: hidden !important
    in CSS and layouts to prevent dual overlapping scrollbars and scroll trapping.
    """
    with open("templates/layouts/finance_base.html", "r", encoding="utf-8") as f:
        finance_css = f.read()
    assert "body.modal-open" in finance_css
    assert "overflow: hidden !important;" in finance_css
    assert "overflow: visible !important;" not in finance_css

    with open("static/css/style.css", "r", encoding="utf-8") as f:
        style_css = f.read()
    assert "body.modal-open" in style_css
    assert "overflow: visible !important;" not in style_css


@pytest.mark.django_db
def test_def_020_invoice_number_format_validation(client, base_test_data):
    """
    BUG-007: Verify supplier invoice number format validation:
    1. Rejects special characters/invalid format (e.g. INV!@#$, INV-2026-991*) with ValidationError.
    2. Rejects too short strings (< 3 chars).
    3. Accepts valid alphanumeric patterns (e.g. INV-2026-991, CISCO/INV/0012, INV10123).
    4. Frontend templates contain proper pattern attribute.
    5. Clean error messaging when submission fails (no raw internal exceptions exposed).
    """
    from apps.core.validators import validate_invoice_number

    # 1. Reject invalid characters and patterns
    with pytest.raises(ValidationError):
        validate_invoice_number("INV!@#$")

    with pytest.raises(ValidationError):
        validate_invoice_number("INV-2026-991*")

    # 2. Reject too short (< 3 chars)
    with pytest.raises(ValidationError):
        validate_invoice_number("IN")

    # 3. Accept valid formats
    assert validate_invoice_number("INV-2026-991") == "INV-2026-991"
    assert validate_invoice_number("CISCO/INV/0012") == "CISCO/INV/0012"
    assert validate_invoice_number("INV10123") == "INV10123"

    # 4. Check HTML template has pattern enforcement
    with open("templates/pages/invoices/create.html", "r", encoding="utf-8") as f:
        create_html = f.read()
    assert 'name="invoice_number"' in create_html
    assert 'pattern="' in create_html

    with open("templates/pages/vendor/invoice_submit.html", "r", encoding="utf-8") as f:
        vendor_html = f.read()
    assert 'name="invoice_number"' in vendor_html
    assert 'pattern="' in vendor_html

    # 5. Service rejects invalid invoice number
    vendor = base_test_data["active_vendor"]
    cc = base_test_data["cc"]
    po = PurchaseOrder.objects.create(
        po_number="PO-INV-TEST-01",
        vendor=vendor,
        cost_center=cc,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Test Service",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
        line_total=Decimal("1000.00"),
    )

    with pytest.raises(ValidationError) as excinfo:
        create_supplier_invoice_service(
            vendor=vendor,
            po=po,
            invoice_number="INV!@#$",
            invoice_date=timezone.now().date(),
            due_date=timezone.now().date() + datetime.timedelta(days=30),
            line_items=[{
                "po_line": po_line,
                "quantity": Decimal("2.00"),
                "unit_price": Decimal("100.00"),
                "item_description": "Test Service",
            }],
        )
    assert "alphanumeric" in str(excinfo.value).lower() or "special characters" in str(excinfo.value).lower()




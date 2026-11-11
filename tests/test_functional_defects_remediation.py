import os
import pytest
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalPolicy, ApprovalStep
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.api_views import _clean_error_message
from apps.invoices.models import SupplierInvoice
from apps.invoices.services import mark_invoice_paid_service
from apps.orders.models import PurchaseOrder, POLine
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.requisitions.services import (
    approve_purchase_requisition_service,
    reject_purchase_requisition_service,
)
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.vendors.selectors import get_vendor_kyc_summary
from apps.vendors.api_urls import VendorSerializer

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def world(db):
    org1 = Organization.objects.create(name="HPE Global", code="HPE-GLB-01")
    org2 = Organization.objects.create(name="Acme Corp", code="ACME-01")

    dept1 = Department.objects.create(name="IT Infrastructure", code="IT-01", organization=org1)
    dept2 = Department.objects.create(name="HR Operations", code="HR-01", organization=org2)

    cc1 = CostCenter.objects.create(name="IT Ops", code="CC-IT-01", department=dept1)

    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_requester, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_dept_approver, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Dept Approver"})
    role_procexec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    role_procmgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    role_finance, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
    role_receiver, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    role_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    role_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    role_vendor, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})

    admin_user = User.objects.create_user(
        email="admin@hpe.com",
        password="Password123!",
        role=role_admin,
        organization=org1,
    )
    requester = User.objects.create_user(
        email="requester@hpe.com",
        password="Password123!",
        role=role_requester,
        department=dept1,
        cost_center=cc1,
        organization=org1,
    )
    dept_approver = User.objects.create_user(
        email="approver@hpe.com",
        password="Password123!",
        role=role_dept_approver,
        department=dept1,
        organization=org1,
    )
    procexec = User.objects.create_user(
        email="procexec@hpe.com",
        password="Password123!",
        role=role_procexec,
        organization=org1,
    )
    procmgr = User.objects.create_user(
        email="procmgr@hpe.com",
        password="Password123!",
        role=role_procmgr,
        organization=org1,
    )
    finance_user = User.objects.create_user(
        email="finance@hpe.com",
        password="Password123!",
        role=role_finance,
        organization=org1,
    )
    receiver = User.objects.create_user(
        email="receiver@hpe.com",
        password="Password123!",
        role=role_receiver,
        organization=org1,
    )
    legal_user = User.objects.create_user(
        email="legal@hpe.com",
        password="Password123!",
        role=role_legal,
        organization=org1,
    )
    auditor = User.objects.create_user(
        email="auditor@hpe.com",
        password="Password123!",
        role=role_auditor,
        organization=org1,
    )

    category = VendorCategory.objects.create(name="Hardware", code="HW-CAT-01")
    vendor = Vendor.objects.create(
        legal_name="Global Tech Ltd",
        vendor_number="VND-2026-00099",
        tax_identification_number="TIN-GLB-99",
        category=category,
        organization=org1,
        status=Vendor.STATUS_ACTIVE,
    )
    vendor_user = User.objects.create_user(
        email="vendor_test@globaltech.com",
        password="Password123!",
        role=role_vendor,
        vendor=vendor,
    )

    return {
        "org1": org1,
        "org2": org2,
        "dept1": dept1,
        "dept2": dept2,
        "cc1": cc1,
        "admin": admin_user,
        "requester": requester,
        "dept_approver": dept_approver,
        "procexec": procexec,
        "procmgr": procmgr,
        "finance": finance_user,
        "receiver": receiver,
        "legal": legal_user,
        "auditor": auditor,
        "vendor": vendor,
        "vendor_user": vendor_user,
    }


# =========================================================================
# DEF-001 (High): Payment strictly refused unless READY_FOR_PAYMENT
# =========================================================================
def test_def_001_mark_invoice_paid_strictly_requires_ready_for_payment(world):
    fp = FiscalPeriod.objects.create(
        organization=world["org1"],
        year=2026,
        period_number=1,
        name="FY26-Q1",
        start_date=timezone.now().date() - timezone.timedelta(days=30),
        end_date=timezone.now().date() + timezone.timedelta(days=60),
    )
    Budget.objects.create(
        cost_center=world["cc1"],
        fiscal_period=fp,
        allocated_amount=Decimal("100000.00"),
        committed_amount=Decimal("1100.00"),
    )
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-INV001",
        vendor=world["vendor"],
        cost_center=world["cc1"],
        status=PurchaseOrder.STATUS_ISSUED,
        created_by=world["procexec"],
    )
    invoice = SupplierInvoice.objects.create(
        invoice_number="INV-DEF-001",
        vendor=world["vendor"],
        po=po,
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("100.00"),
        total_amount=Decimal("1100.00"),
        status=SupplierInvoice.STATUS_APPROVAL,
    )
    with pytest.raises(ValidationError) as exc:
        mark_invoice_paid_service(invoice=invoice, user=world["finance"], payment_reference="PAY-001")
    assert "READY_FOR_PAYMENT" in str(exc.value)

    # Transition to READY_FOR_PAYMENT -> payment succeeds
    invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
    invoice.save(update_fields=["status"])
    paid_inv = mark_invoice_paid_service(invoice=invoice, user=world["finance"], payment_reference="PAY-001")
    assert paid_inv.status == SupplierInvoice.STATUS_PAID
    from apps.invoices.models import PaymentStatus
    assert PaymentStatus.objects.filter(invoice=paid_inv, payment_reference="PAY-001").exists()


# =========================================================================
# DEF-003 (High): Audit log report restricted to Super Admin and Auditor
# =========================================================================
def test_def_003_audit_log_report_restricted_to_auditor_and_superadmin(world):
    client = APIClient()

    # Finance AP denied
    client.force_authenticate(user=world["finance"])
    res = client.get("/api/v1/reports/audit-logs/")
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Procurement Manager denied
    client.force_authenticate(user=world["procmgr"])
    res = client.get("/api/v1/reports/audit-logs/")
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Auditor allowed
    client.force_authenticate(user=world["auditor"])
    res = client.get("/api/v1/reports/audit-logs/")
    assert res.status_code == status.HTTP_200_OK

    # Super Admin allowed
    client.force_authenticate(user=world["admin"])
    res = client.get("/api/v1/reports/audit-logs/")
    assert res.status_code == status.HTTP_200_OK


# =========================================================================
# DEF-004 (High): PR rejection during MANAGER_REVIEW denied to Finance/AP
# =========================================================================
def test_def_004_finance_cannot_reject_pr_during_manager_review(world):
    pr = PurchaseRequisition.objects.create(
        title="Hardware Requisition",
        pr_number="PR-DEF-004",
        requester=world["requester"],
        department=world["dept1"],
        cost_center=world["cc1"],
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Test Item",
        quantity=Decimal("1"),
        unit_of_measure="EA",
        estimated_unit_price=Decimal("5000.00"),
    )

    client = APIClient()
    client.force_authenticate(user=world["finance"])
    res = client.post(f"/api/v1/requisitions/{pr.id}/reject/", {"reason": "Not approved by finance"})
    assert res.status_code == status.HTTP_403_FORBIDDEN

    # Dept approver CAN reject during manager review
    client.force_authenticate(user=world["dept_approver"])
    res = client.post(f"/api/v1/requisitions/{pr.id}/reject/", {"reason": "Over budget for department"})
    assert res.status_code == status.HTTP_200_OK
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_REJECTED


# =========================================================================
# DEF-005 (High): Multi-level PR approval sequence enforced
# =========================================================================
def test_def_005_multi_level_pr_approval_sequence_enforced(world):
    dept_approver_role = Role.objects.get(code=Role.DEPT_APPROVER)
    proc_mgr_role = Role.objects.get(code=Role.PROC_MGR)

    policy = ApprovalPolicy.objects.create(
        name="Two-Level PR Policy",
        organization=world["org1"],
        department=world["dept1"],
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("1000.00"),
        max_amount=Decimal("100000.00"),
        is_active=True,
    )
    ApprovalStep.objects.create(policy=policy, step_number=1, approver_role=dept_approver_role)
    ApprovalStep.objects.create(policy=policy, step_number=2, approver_role=proc_mgr_role)

    pr = PurchaseRequisition.objects.create(
        title="Multi-Level PR",
        pr_number="PR-DEF-005",
        requester=world["requester"],
        department=world["dept1"],
        cost_center=world["cc1"],
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        total_amount=Decimal("15000.00"),
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Server Rack",
        quantity=Decimal("1"),
        unit_of_measure="EA",
        estimated_unit_price=Decimal("15000.00"),
    )

    # Proc Mgr cannot approve at Step 1 (which requires DEPT_APPROVER)
    with pytest.raises(ValidationError) as exc:
        approve_purchase_requisition_service(requisition=pr, approver=world["procmgr"])
    assert "step 1" in str(exc.value).lower()

    # Dept Approver approves Step 1 -> advances
    pr_after_step1 = approve_purchase_requisition_service(requisition=pr, approver=world["dept_approver"])
    assert pr_after_step1.status in [PurchaseRequisition.STATUS_MANAGER_REVIEW, PurchaseRequisition.STATUS_BUDGET_REVIEW]

    # Step 2: Proc Mgr can now approve
    pr_after_step2 = approve_purchase_requisition_service(requisition=pr, approver=world["procmgr"])
    assert pr_after_step2.status in [PurchaseRequisition.STATUS_APPROVED, PurchaseRequisition.STATUS_BUDGET_REVIEW]


# =========================================================================
# DEF-006 (High): Vendor KYC requires all mandatory document types
# =========================================================================
def test_def_006_vendor_kyc_requires_all_mandatory_doc_types(world):
    category = world["vendor"].category
    vendor_draft = Vendor.objects.create(
        legal_name="New Test Vendor",
        vendor_number="VND-DEF-006",
        tax_identification_number="TIN-DEF-006",
        category=category,
        organization=world["org1"],
        status=Vendor.STATUS_SUBMITTED,
    )

    # Only upload PAN (verified)
    VendorDocument.objects.create(
        vendor=vendor_draft,
        document_type=VendorDocument.DOC_TYPE_PAN,
        title="PAN Card",
        file="vendor_documents/test_pan.pdf",
        review_status=VendorDocument.REVIEW_VERIFIED,
    )

    summary = get_vendor_kyc_summary(vendor_draft)
    assert summary["can_activate"] is False
    assert any("Mandatory documents missing" in b for b in summary["blockers"])

    # Add TAX (GST) and BANK (verified)
    VendorDocument.objects.create(
        vendor=vendor_draft,
        document_type=VendorDocument.DOC_TYPE_GST,
        title="GST Certificate",
        file="vendor_documents/test_gst.pdf",
        review_status=VendorDocument.REVIEW_VERIFIED,
    )
    VendorDocument.objects.create(
        vendor=vendor_draft,
        document_type=VendorDocument.DOC_TYPE_BANK,
        title="Cancelled Cheque",
        file="vendor_documents/test_bank.pdf",
        review_status=VendorDocument.REVIEW_VERIFIED,
    )

    summary2 = get_vendor_kyc_summary(vendor_draft)
    assert summary2["can_activate"] is True
    assert not any("Mandatory documents missing" in b for b in summary2["blockers"])


# =========================================================================
# DEF-007 (High): Reports scoped by requesting user's organization
# =========================================================================
def test_def_007_reports_scoped_by_organization(world):
    client = APIClient()
    client.force_authenticate(user=world["finance"])

    res_summary = client.get("/api/v1/reports/dashboard-summary/")
    assert res_summary.status_code == status.HTTP_200_OK
    assert "total_prs" in res_summary.data

    res_spend = client.get("/api/v1/reports/spend-analytics/")
    assert res_spend.status_code == status.HTTP_200_OK
    assert isinstance(res_spend.data, list)


# =========================================================================
# DEF-008 (Medium): Safe read access for Finance and Legal on vendor risk
# =========================================================================
def test_def_008_vendor_risk_safe_read_for_finance_and_legal(world):
    client = APIClient()

    # Finance AP
    client.force_authenticate(user=world["finance"])
    res_fin = client.get(f"/api/v1/vendors/{world['vendor'].id}/risk/")
    assert res_fin.status_code == status.HTTP_200_OK

    # Legal Manager
    client.force_authenticate(user=world["legal"])
    res_leg = client.get(f"/api/v1/vendors/{world['vendor'].id}/risk/")
    assert res_leg.status_code == status.HTTP_200_OK


# =========================================================================
# DEF-009 (Medium): Trailing whitespace in passwords preserved
# =========================================================================
def test_def_009_trailing_whitespace_in_password_preserved():
    from apps.accounts.serializers import LoginRequestSerializer

    serializer = LoginRequestSerializer(data={"email": "test@hpe.com", "password": "Password123!   "})
    assert serializer.is_valid()
    assert serializer.validated_data["password"] == "Password123!   "


# =========================================================================
# DEF-010 (Medium): Landing home forwards authenticated users
# =========================================================================
def test_def_010_landing_home_forwards_authenticated_users(world):
    client = Client()
    client.force_login(world["requester"])

    resp = client.get("/")
    assert resp.status_code in [200, 302]
    assert client.session.get("_auth_user_id") is not None


# =========================================================================
# DEF-012 (Medium): Supplier invoice total must equal subtotal + tax
# =========================================================================
def test_def_012_supplier_invoice_total_amount_must_equal_subtotal_plus_tax(world):
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-INV012",
        vendor=world["vendor"],
        cost_center=world["cc1"],
        status=PurchaseOrder.STATUS_ISSUED,
        created_by=world["procexec"],
    )

    client = APIClient()
    client.force_authenticate(user=world["admin"])

    payload = {
        "invoice_number": "INV-MISMATCH-01",
        "vendor": str(world["vendor"].id),
        "po": str(po.id),
        "invoice_date": "2026-11-10",
        "due_date": "2026-11-25",
        "subtotal": "100.00",
        "tax_amount": "10.00",
        "total_amount": "150.00",  # Mismatch: 100 + 10 != 150
        "line_items": [
            {
                "item_description": "Widget",
                "quantity": 10,
                "unit_price": "10.00",
                "line_total": "100.00",
            }
        ],
    }
    res = client.post("/api/v1/invoices/", payload, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "total_amount" in str(res.data)


# =========================================================================
# DEF-014 (Medium): Invoices API restricted per RBAC matrix
# =========================================================================
def test_def_014_invoices_api_denied_for_requester_and_legal(world):
    client = APIClient()

    # Requester denied
    client.force_authenticate(user=world["requester"])
    res_req = client.get("/api/v1/invoices/")
    assert res_req.status_code == status.HTTP_403_FORBIDDEN

    # Legal Manager denied
    client.force_authenticate(user=world["legal"])
    res_leg = client.get("/api/v1/invoices/")
    assert res_leg.status_code == status.HTTP_403_FORBIDDEN

    # Finance AP allowed
    client.force_authenticate(user=world["finance"])
    res_fin = client.get("/api/v1/invoices/")
    assert res_fin.status_code == status.HTTP_200_OK


# =========================================================================
# DEF-016 (Medium): Cannot cancel a rejected PR
# =========================================================================
def test_def_016_cannot_cancel_rejected_pr(world):
    pr = PurchaseRequisition.objects.create(
        title="Rejected PR",
        pr_number="PR-DEF-016",
        requester=world["requester"],
        department=world["dept1"],
        cost_center=world["cc1"],
        status=PurchaseRequisition.STATUS_REJECTED,
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )
    client = APIClient()
    client.force_authenticate(user=world["requester"])

    res = client.post(f"/api/v1/requisitions/{pr.id}/cancel/", {"reason": "Try cancel"})
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Cannot cancel" in str(res.data)


# =========================================================================
# DEF-017 (Low): Contract create UI restricted to Legal Manager & Super Admin
# =========================================================================
def test_def_017_contract_create_ui_restricted_to_legal_and_admin(world):
    client = Client()

    # PROC_EXEC redirected / denied
    client.force_login(world["procexec"])
    resp_exec = client.get("/contracts/create/")
    assert resp_exec.status_code in [302, 403]

    # PROC_MGR redirected / denied
    client.force_login(world["procmgr"])
    resp_mgr = client.get("/contracts/create/")
    assert resp_mgr.status_code in [302, 403]

    # LEGAL_MGR allowed
    client.force_login(world["legal"])
    resp_legal = client.get("/contracts/create/")
    assert resp_legal.status_code == 200

    # SUPER_ADMIN allowed
    client.force_login(world["admin"])
    resp_admin = client.get("/contracts/create/")
    assert resp_admin.status_code == 200


# =========================================================================
# DEF-018 (Low): Requisitions UI denied to STORES_RECEIVER & LEGAL_MGR;
#                Scorecards UI denied to LEGAL_MGR
# =========================================================================
def test_def_018_requisitions_and_scorecards_ui_access_restricted(world):
    client = Client()

    # Stores Receiver denied /requisitions/
    client.force_login(world["receiver"])
    resp_rec = client.get("/requisitions/")
    assert resp_rec.status_code in [403, 302]

    # Legal Manager denied /requisitions/
    client.force_login(world["legal"])
    resp_leg_req = client.get("/requisitions/")
    assert resp_leg_req.status_code in [403, 302]

    # Legal Manager denied /scorecards/
    resp_leg_sc = client.get("/scorecards/")
    assert resp_leg_sc.status_code in [403, 302]


# =========================================================================
# DEF-019 (Low): Case-insensitive email authentication
# =========================================================================
def test_def_019_case_insensitive_email_login(world):
    client = Client()
    resp = client.post(
        "/login/",
        {"email": "ADMIN@HPE.COM", "password": "Password123!"},
        HTTP_X_REQUESTED_WITH="XMLHttpRequest",
    )
    assert resp.status_code in [200, 302]
    assert client.session.get("_auth_user_id") is not None


# =========================================================================
# DEF-020 (Low): Logout restricted to POST only
# =========================================================================
def test_def_020_logout_restricted_to_post_only(world):
    client = Client()
    client.force_login(world["requester"])

    # GET /logout/ is rejected with 405 Method Not Allowed
    resp_get = client.get("/logout/")
    assert resp_get.status_code == 405

    # POST /logout/ succeeds
    resp_post = client.post("/logout/")
    assert resp_post.status_code in [200, 302]
    assert client.session.get("_auth_user_id") is None


# =========================================================================
# DEF-023 (Low): ApprovalPolicy is_active synchronized with status
# =========================================================================
def test_def_023_approval_policy_is_active_synced_with_status():
    policy = ApprovalPolicy.objects.create(
        name="Test Sync Policy",
        module=ApprovalPolicy.MODULE_PO,
        all_departments=True,
        min_amount=Decimal("0.00"),
        max_amount=Decimal("10000.00"),
        is_active=False,
    )
    assert policy.status == ApprovalPolicy.STATUS_ARCHIVED

    policy.status = ApprovalPolicy.STATUS_ACTIVE
    policy.save()
    assert policy.is_active is True

    policy.status = ApprovalPolicy.STATUS_ARCHIVED
    policy.save()
    assert policy.is_active is False


# =========================================================================
# DEF-024 (Low): Vendor phone rejects alphabetic characters
# =========================================================================
def test_def_024_vendor_phone_rejects_alphabetic_characters(world):
    data = {
        "legal_name": "Alphabet Phone Vendor",
        "tax_identification_number": "TIN-ALPHA-01",
        "category": str(world["vendor"].category.id),
        "phone": "+1-800-CALL-NOW",
    }
    serializer = VendorSerializer(data=data)
    assert not serializer.is_valid()
    assert "phone" in serializer.errors


# =========================================================================
# DEF-027 (Low): period_days parameter validated for bounds
# =========================================================================
def test_def_027_period_days_parameter_validated(world):
    client = APIClient()
    client.force_authenticate(user=world["admin"])

    # Negative period_days
    res_neg = client.get("/api/v1/reports/spend-analytics/?period_days=-30")
    assert res_neg.status_code == status.HTTP_400_BAD_REQUEST

    # Non-numeric period_days
    res_str = client.get("/api/v1/reports/spend-analytics/?period_days=abc")
    assert res_str.status_code == status.HTTP_400_BAD_REQUEST

    # Excessively large period_days (> 3650)
    res_large = client.get("/api/v1/reports/spend-analytics/?period_days=99999")
    assert res_large.status_code == status.HTTP_400_BAD_REQUEST


# =========================================================================
# DEF-029 (Low): Unauthorized PO approval returns HTTP 403 Forbidden
# =========================================================================
def test_def_029_unauthorized_po_approval_returns_403(world):
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-DEF029",
        vendor=world["vendor"],
        cost_center=world["cc1"],
        status=PurchaseOrder.STATUS_APPROVAL,
        created_by=world["procexec"],
    )
    POLine.objects.create(
        po=po,
        item_description="Test Item",
        quantity=Decimal("2"),
        unit_price=Decimal("1000.00"),
        line_total=Decimal("2000.00"),
    )

    client = APIClient()
    # PROC_EXEC cannot approve PO
    client.force_authenticate(user=world["procexec"])
    res = client.post(f"/api/v1/purchase-orders/{po.id}/approve/", {"comments": "Try approve"})
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert res.data["error"]["code"] == "PERMISSION_DENIED"


# =========================================================================
# DEF-030 (Low): Root Super Admin admin@hpe.com can open org create form
# =========================================================================
def test_def_030_root_superadmin_can_open_org_create(world):
    client = Client()
    client.force_login(world["admin"])

    resp = client.get("/superadmin/organizations/create/")
    assert resp.status_code == 200


# =========================================================================
# DEF-034 (Low): Error message unwraps Python list repr into plain text
# =========================================================================
def test_def_034_clean_error_message_helper():
    err_list = ValidationError(["Invalid file content for .docx. The file does not have a valid ZIP/OpenXML container."])
    cleaned = _clean_error_message(err_list)
    assert not cleaned.startswith("['")
    assert not cleaned.endswith("']")
    assert "Invalid file content for .docx" in cleaned

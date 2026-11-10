import uuid
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role
from apps.approvals.models import ApprovalAction
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.orders.models import PurchaseOrder
from apps.orders.services import generate_purchase_order_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt
from apps.receipts.services import create_goods_receipt_service
from apps.reports.services import get_spend_analytics_report
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service

User = get_user_model()


@pytest.fixture
def qa_setup(db):
    org = Organization.objects.create(name="QA Remediation Corp", code="QA-ORG")
    dept1 = Department.objects.create(organization=org, name="Engineering", code="ENG")
    dept2 = Department.objects.create(organization=org, name="Marketing", code="MKT")
    cc1 = CostCenter.objects.create(department=dept1, code="CC-ENG-01", name="Eng CC")
    cc2 = CostCenter.objects.create(department=dept2, code="CC-MKT-01", name="Mkt CC")

    today = timezone.now().date()
    fp = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        name="FY2026-Q1",
        start_date=today - timezone.timedelta(days=10),
        end_date=today + timezone.timedelta(days=90),
    )

    cat = VendorCategory.objects.create(name="QA Hardware", code="QA-HW")
    vendor = register_vendor_service(
        legal_name="QA Hardware Supplies",
        tax_identification_number="TAX-QA-999",
        category=cat,
        email="vendor@qahardware.com",
        address="123 QA Test Street",
    )
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.save(update_fields=["status"])

    admin_role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    req_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    proc_exec_role, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Proc Exec"})
    proc_mgr_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Proc Mgr"})
    stores_role, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores"})

    admin_user = User.objects.create_user(
        email="superadmin.qa@test.com", password="Password123!", role=admin_role, is_superuser=True
    )
    requester = User.objects.create_user(
        email="requester.qa@test.com", password="Password123!", role=req_role, department=dept1, cost_center=cc1
    )
    proc_exec = User.objects.create_user(
        email="procexec.qa@test.com", password="Password123!", role=proc_exec_role, department=dept1
    )
    proc_mgr = User.objects.create_user(
        email="procmgr.qa@test.com", password="Password123!", role=proc_mgr_role, department=dept1
    )
    stores_user = User.objects.create_user(
        email="stores.qa@test.com", password="Password123!", role=stores_role
    )

    return {
        "org": org,
        "dept1": dept1,
        "dept2": dept2,
        "cc1": cc1,
        "cc2": cc2,
        "fp": fp,
        "vendor": vendor,
        "admin_user": admin_user,
        "requester": requester,
        "proc_exec": proc_exec,
        "proc_mgr": proc_mgr,
        "stores_user": stores_user,
    }


@pytest.mark.django_db
def test_def_001_axes_brute_force_lockout_enforced(qa_setup):
    """DEF-001: Repeated wrong passwords trigger HTTP 429 and block correct password during lockout."""
    client = APIClient()
    user = qa_setup["requester"]
    email = user.email

    for _ in range(5):
        res = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")

    # Next attempt must be locked out
    res_wrong = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")
    assert res_wrong.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert res_wrong.json()["error"]["code"] == "ACCOUNT_LOCKED"

    # Correct password must ALSO be refused with 429 during lockout
    res_correct = client.post("/api/v1/auth/login/", {"email": email, "password": "Password123!"}, format="json")
    assert res_correct.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert res_correct.json()["error"]["code"] == "ACCOUNT_LOCKED"


@pytest.mark.django_db
def test_def_004_organization_crud_creates_audit_log(qa_setup):
    """DEF-004: Creating an organization writes an AuditLog entry."""
    client = APIClient()
    client.force_authenticate(user=qa_setup["admin_user"])

    res = client.post(
        "/api/v1/organization/entities/",
        {"name": "Audited Subsidiary Inc", "code": "SUB-AUDIT-01", "tax_identifier": "TX-998877"},
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    new_org_id = res.json()["id"]

    audit_entry = AuditLog.objects.filter(
        target_model="Organization", target_object_id=str(new_org_id), action=AuditLog.ACTION_CREATE
    ).first()
    assert audit_entry is not None
    assert audit_entry.actor == qa_setup["admin_user"]


@pytest.mark.django_db
def test_def_005_approval_actions_filter_by_target_object_id(qa_setup):
    """DEF-005: Filtering approval actions by target_object_id returns only target actions."""
    client = APIClient()
    client.force_authenticate(user=qa_setup["admin_user"])

    target_id_1 = str(uuid.uuid4())
    target_id_2 = str(uuid.uuid4())

    ApprovalAction.objects.create(
        actor=qa_setup["admin_user"],
        target_model_name="PurchaseRequisition",
        target_object_id=target_id_1,
        action=ApprovalAction.ACTION_APPROVE,
        comments="Approved PR 1",
    )
    ApprovalAction.objects.create(
        actor=qa_setup["admin_user"],
        target_model_name="PurchaseRequisition",
        target_object_id=target_id_2,
        action=ApprovalAction.ACTION_APPROVE,
        comments="Approved PR 2",
    )

    res = client.get(f"/api/v1/approvals/actions/?target_object_id={target_id_1}")
    assert res.status_code == status.HTTP_200_OK
    results = res.json().get("results", res.json())
    assert len(results) == 1
    assert results[0]["target_object_id"] == target_id_1


@pytest.mark.django_db
def test_def_008_pr_cost_center_mismatched_department_rejected(qa_setup):
    """DEF-008: PR cannot reference a cost center belonging to a different department."""
    client = APIClient()
    client.force_authenticate(user=qa_setup["requester"])

    res = client.post(
        "/api/v1/requisitions/",
        {
            "title": "Cross Department PR",
            "justification": "Invalid department/cost center pair",
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=30)),
            "department": qa_setup["dept1"].id,
            "cost_center": qa_setup["cc2"].id,  # cc2 belongs to dept2!
            "lines": [
                {
                    "item_description": "Network Cable",
                    "quantity": 10,
                    "estimated_unit_price": 5.00,
                }
            ],
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    data = res.json()
    assert "cost_center" in str(data) or "department" in str(data)


@pytest.mark.django_db
def test_def_010_purchase_order_approval_creates_approval_action(qa_setup):
    """DEF-010: Submitting and approving a PO writes ApprovalAction records."""
    from apps.orders.services import (
        approve_purchase_order_service,
        submit_purchase_order_for_approval_service,
    )

    po = generate_purchase_order_service(
        vendor=qa_setup["vendor"],
        cost_center=qa_setup["cc1"],
        line_items=[{"item_description": "Server Hardware", "quantity": 1, "unit_price": 500.00}],
        created_by_user=qa_setup["proc_exec"],
    )

    submit_purchase_order_for_approval_service(po=po, actor=qa_setup["proc_exec"])
    actions = ApprovalAction.objects.filter(target_object_id=str(po.id))
    assert actions.filter(action=ApprovalAction.ACTION_SUBMIT).exists()

    approve_purchase_order_service(po=po, approver=qa_setup["proc_mgr"], comments="PO Approved")
    actions = ApprovalAction.objects.filter(target_object_id=str(po.id))
    assert actions.filter(action=ApprovalAction.ACTION_APPROVE).exists()


@pytest.mark.django_db
def test_def_011_duplicate_po_number_returns_400_not_500(qa_setup):
    """DEF-011: Attempting to create duplicate PO numbers returns clean HTTP 400 error."""
    client = APIClient()
    client.force_authenticate(user=qa_setup["proc_exec"])

    payload = {
        "po_number": "PO-DUP-TEST-001",
        "vendor": qa_setup["vendor"].id,
        "cost_center": qa_setup["cc1"].id,
        "payment_terms_days": 30,
        "lines": [{"item_description": "Test Item", "quantity": 1, "unit_price": "100.00"}],
    }

    res1 = client.post("/api/v1/purchase-orders/", payload, format="json")
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post("/api/v1/purchase-orders/", payload, format="json")
    assert res2.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]
    assert res2.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR


@pytest.mark.django_db
def test_def_013_duplicate_grn_number_rejected_400(qa_setup):
    """DEF-013: Goods receipt with an already used GRN number returns HTTP 400."""
    po = generate_purchase_order_service(
        vendor=qa_setup["vendor"],
        cost_center=qa_setup["cc1"],
        line_items=[{"item_description": "Rack Unit", "quantity": 2, "unit_price": 50.00}],
        created_by_user=qa_setup["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save()

    client = APIClient()
    client.force_authenticate(user=qa_setup["stores_user"])

    payload = {
        "po": po.id,
        "grn_number": "GRN-CUSTOM-001",
        "delivery_note_number": "DN-001",
        "received_date": timezone.now().isoformat(),
        "lines": [{"po_line_id": str(po.lines.first().id), "quantity_received": 1, "quantity_accepted": 1}],
    }

    res1 = client.post("/api/v1/receipts/", payload, format="json")
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = client.post("/api/v1/receipts/", payload, format="json")
    assert res2.status_code == status.HTTP_400_BAD_REQUEST
    assert "already exists" in str(res2.json())


@pytest.mark.django_db
def test_def_014_goods_receipt_creates_notification(qa_setup):
    """DEF-014: Recording a goods receipt creates a GOODS_RECEIVED notification."""
    po = generate_purchase_order_service(
        vendor=qa_setup["vendor"],
        cost_center=qa_setup["cc1"],
        line_items=[{"item_description": "Storage Drive", "quantity": 1, "unit_price": 100.00}],
        created_by_user=qa_setup["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.issued_by = qa_setup["proc_exec"]
    po.save()

    count_before = Notification.objects.filter(notification_type=Notification.TYPE_GOODS_RECEIVED).count()

    create_goods_receipt_service(
        po=po,
        received_by=qa_setup["stores_user"],
        receipt_items=[{"po_line": po.lines.first(), "quantity_received": 1, "quantity_accepted": 1}],
    )

    count_after = Notification.objects.filter(notification_type=Notification.TYPE_GOODS_RECEIVED).count()
    assert count_after == count_before + 1


@pytest.mark.django_db
def test_def_015_swagger_invoices_schema_includes_required_fields():
    """DEF-015: OpenAPI schema documents vendor_id, po_id, and line_items for create-invoice."""
    client = APIClient()
    res = client.get("/api/schema/")
    assert res.status_code == 200
    schemas = res.data["components"]["schemas"]

    assert "InvoiceCreatePayload" in schemas
    fields = schemas["InvoiceCreatePayload"]["properties"]
    assert "vendor_id" in fields
    assert "po_id" in fields
    assert "line_items" in fields
    assert "invoice_number" in fields


@pytest.mark.django_db
def test_def_016_spend_analytics_only_aggregates_paid_invoices(qa_setup):
    """DEF-016: Spend analytics reports only PAID invoices as actual spend."""
    from apps.invoices.models import SupplierInvoice

    po = generate_purchase_order_service(
        vendor=qa_setup["vendor"],
        cost_center=qa_setup["cc1"],
        line_items=[{"item_description": "Blade Server", "quantity": 1, "unit_price": 5000.00}],
        created_by_user=qa_setup["proc_exec"],
    )

    # 1. Received invoice (not paid yet)
    SupplierInvoice.objects.create(
        vendor=qa_setup["vendor"],
        po=po,
        invoice_number="INV-UNPAID-01",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("5000.00"),
        total_amount=Decimal("5000.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )

    report_before = get_spend_analytics_report(user=qa_setup["admin_user"])
    assert not any(item["invoice_number"] == "INV-UNPAID-01" for item in report_before)

    # 2. Paid invoice
    paid_inv = SupplierInvoice.objects.create(
        vendor=qa_setup["vendor"],
        po=po,
        invoice_number="INV-PAID-01",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("5000.00"),
        total_amount=Decimal("5000.00"),
        status=SupplierInvoice.STATUS_PAID,
    )

    report_after = get_spend_analytics_report(user=qa_setup["admin_user"])
    paid_numbers = [item["invoice_number"] for item in report_after]
    assert "INV-PAID-01" in paid_numbers
    assert "INV-UNPAID-01" not in paid_numbers


@pytest.mark.django_db
def test_def_018_failed_login_writes_audit_log():
    """DEF-018: Failed login attempts write an AuditLog security entry."""
    client = APIClient()
    email = "failed_audit_user@example.com"
    User.objects.create_user(email=email, password="CorrectPassword123!")

    client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")

    failed_log = AuditLog.objects.filter(
        target_model="User", action=AuditLog.ACTION_LOGIN_FAILED
    ).first()
    assert failed_log is not None
    assert failed_log.new_state["email"] == email.lower()
    assert failed_log.new_state["event"] == "LOGIN_FAILED"


@pytest.mark.django_db
def test_def_019_swagger_sourcing_evaluation_does_not_expose_delete():
    """DEF-019: Sourcing evaluation endpoint does not document DELETE method in OpenAPI schema."""
    client = APIClient()
    res = client.get("/api/schema/")
    assert res.status_code == 200
    paths = res.data.get("paths", {})

    for path, methods in paths.items():
        if "evaluate" in path:
            assert "delete" not in methods, f"DELETE should not be exposed on {path}"


@pytest.mark.django_db
def test_def_021_creator_can_update_draft_purchase_order(qa_setup):
    """DEF-021: PO creator can update their own draft PO without HTTP 404."""
    client = APIClient()
    client.force_authenticate(user=qa_setup["proc_exec"])

    res = client.post(
        "/api/v1/purchase-orders/",
        {
            "vendor": qa_setup["vendor"].id,
            "cost_center": qa_setup["cc1"].id,
            "payment_terms_days": 30,
            "terms_and_conditions": "Initial terms",
            "lines": [{"item_description": "Draft Switch", "quantity": 1, "unit_price": "100.00"}],
        },
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    po_id = res.json()["id"]

    # PATCH by the same creator must succeed (200 OK)
    patch_res = client.patch(
        f"/api/v1/purchase-orders/{po_id}/",
        {"terms_and_conditions": "Updated terms and conditions"},
        format="json",
    )
    assert patch_res.status_code == status.HTTP_200_OK
    assert patch_res.json()["terms_and_conditions"] == "Updated terms and conditions"

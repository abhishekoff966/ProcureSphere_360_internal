import pytest
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.orders.models import PurchaseOrder, POLine
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.reports.models import ExportJob
from apps.sourcing.models import SourcingEvent
from apps.vendors.models import Vendor, VendorCategory

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def world(db):
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB-01")
    dept = Department.objects.create(name="IT Infrastructure", code="IT-01", organization=org)
    cc = CostCenter.objects.create(name="IT Ops", code="CC-IT-01", department=dept)

    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_procexec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    role_procmgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    role_stores, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    role_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})

    admin_user = User.objects.create_user(
        email="admin@hpe.com",
        password="Password123!",
        role=role_admin,
        organization=org,
        is_superuser=False,  # Same as Render deployment
    )
    procexec = User.objects.create_user(
        email="procexec@hpe.com",
        password="Password123!",
        role=role_procexec,
        organization=org,
    )
    procmgr = User.objects.create_user(
        email="procmgr@hpe.com",
        password="Password123!",
        role=role_procmgr,
        organization=org,
    )
    stores_user = User.objects.create_user(
        email="receiver@hpe.com",
        password="Password123!",
        role=role_stores,
        organization=org,
    )
    auditor = User.objects.create_user(
        email="auditor@hpe.com",
        password="Password123!",
        role=role_auditor,
        organization=org,
    )

    category = VendorCategory.objects.create(name="Hardware", code="HW-CAT-01")
    vendor = Vendor.objects.create(
        legal_name="Global Tech Ltd",
        vendor_number="VND-2026-00099",
        tax_identification_number="TIN-GLB-99",
        category=category,
        organization=org,
        status=Vendor.STATUS_ACTIVE,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "admin": admin_user,
        "procexec": procexec,
        "procmgr": procmgr,
        "stores": stores_user,
        "auditor": auditor,
        "vendor": vendor,
    }


# =========================================================================
# DEF-001: Concurrency-safe document numbering for SourcingEvent
# =========================================================================
def test_def_001_sourcing_event_document_numbering(world):
    from apps.sourcing.services import create_sourcing_event_service

    event1 = create_sourcing_event_service(
        title="Event 1",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=timezone.now() + timezone.timedelta(days=1),
        bid_end_date=timezone.now() + timezone.timedelta(days=5),
        description="Scope 1",
        created_by_user=world["procexec"],
    )
    event2 = create_sourcing_event_service(
        title="Event 2",
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=timezone.now() + timezone.timedelta(days=1),
        bid_end_date=timezone.now() + timezone.timedelta(days=5),
        description="Scope 2",
        created_by_user=world["procexec"],
    )
    assert event1.event_number != event2.event_number
    assert event1.event_number.startswith("RFQ-2026-")
    assert event2.event_number.startswith("RFQ-2026-")


# =========================================================================
# DEF-010: Manager Approval Desk accessible to Super Admin
# =========================================================================
def test_def_010_super_admin_can_use_manager_approval_desk(world):
    client = APIClient()
    client.force_authenticate(user=world["admin"])

    # Invalid action returns 400 Bad Request (not 403 Forbidden)
    res = client.post("/api/v1/manager/approvals/action/", {"action": "MAYBE", "item_id": ""})
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # Missing item_id returns 400 Bad Request
    res = client.post("/api/v1/manager/approvals/action/", {"action": "APPROVE", "item_id": ""})
    assert res.status_code == status.HTTP_400_BAD_REQUEST


# =========================================================================
# DEF-014: Procurement Executive permitted on vendor governance-status
# =========================================================================
def test_def_014_proc_exec_governance_status_permission(world):
    client = APIClient()
    client.force_authenticate(user=world["procexec"])

    # Invalid status should return 400 Bad Request (not 403 Forbidden)
    res = client.post(
        f"/api/v1/vendors/{world['vendor'].id}/governance-status/",
        {"status": "NOT-A-STATUS", "notes": "Testing invalid status"},
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


# =========================================================================
# DEF-019: Full update (PUT) of a goods receipt does not require grn_number
# =========================================================================
def test_def_019_goods_receipt_put_update_succeeds(world):
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-GRN001",
        vendor=world["vendor"],
        cost_center=world["cc"],
        status=PurchaseOrder.STATUS_ISSUED,
        created_by=world["procexec"],
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Server Rack",
        quantity=Decimal("5"),
        unit_price=Decimal("100.00"),
    )
    grn = GoodsReceipt.objects.create(
        grn_number="GRN-2026-00001",
        po=po,
        received_by=world["stores"],
        received_date=timezone.now(),
        delivery_note_number="DN-001",
        remarks="Original remark",
    )
    ReceiptLine.objects.create(
        receipt=grn,
        po_line=po_line,
        quantity_received=Decimal("5"),
        quantity_accepted=Decimal("5"),
        quantity_rejected=Decimal("0"),
    )

    client = APIClient()
    client.force_authenticate(user=world["stores"])

    payload = {
        "po": str(po.id),
        "received_date": timezone.now().isoformat(),
        "delivery_note_number": "DN-001-REV",
        "remarks": "Updated remark via PUT",
    }
    res = client.put(f"/api/v1/receipts/{grn.id}/", payload)
    assert res.status_code == status.HTTP_200_OK
    grn.refresh_from_db()
    assert grn.remarks == "Updated remark via PUT"
    assert grn.grn_number == "GRN-2026-00001"


# =========================================================================
# DEF-020: Report & audit endpoints return 400 when query params are missing
# =========================================================================
def test_def_020_missing_query_parameters_return_400(world):
    client = APIClient()
    client.force_authenticate(user=world["auditor"])

    # Audit lifecycle without identifier
    res = client.get("/api/v1/audit/logs/lifecycle/")
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    res = client.get("/api/v1/audit/lifecycle/")
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # Audit entity-trail without target_model and target_object_id
    res = client.get("/api/v1/audit/logs/entity-trail/")
    assert res.status_code == status.HTTP_400_BAD_REQUEST

    # Manager supplier compare without vendor_a and vendor_b
    client.force_authenticate(user=world["procmgr"])
    res = client.get("/api/v1/manager/performance/compare/")
    assert res.status_code == status.HTTP_400_BAD_REQUEST


# =========================================================================
# Additional: Token refresh with email/password login alias & audit created_at
# =========================================================================
def test_additional_token_refresh_login_alias(world):
    client = APIClient()
    res = client.post(
        "/api/v1/auth/token/refresh/",
        {"email": "procexec@hpe.com", "password": "Password123!"},
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data.get("status") == "ok"


def test_additional_audit_log_carries_created_at(world):
    AuditLog.objects.create(
        actor=world["admin"],
        action="CREATE",
        target_model="Vendor",
        target_object_id=str(world["vendor"].id),
    )
    client = APIClient()
    client.force_authenticate(user=world["auditor"])
    res = client.get("/api/v1/audit/logs/")
    assert res.status_code == status.HTTP_200_OK
    first_result = res.data["results"][0]
    assert "timestamp" in first_result
    assert "created_at" in first_result


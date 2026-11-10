import pytest
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.contracts.models import Contract, ContractMilestone
from apps.invoices.models import MatchException, SupplierInvoice
from apps.orders.models import PurchaseOrder, POLine
from apps.organization.models import CostCenter, Department, Organization
from apps.receipts.models import GoodsReceipt
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import SourcingEvent
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def api_world(db):
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB-01")
    dept = Department.objects.create(name="IT Infrastructure", code="IT-01", organization=org)
    cc = CostCenter.objects.create(name="IT Ops", code="CC-IT-01", department=dept)

    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_requester, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_procexec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    role_procmgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    role_finance, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
    role_receiver, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    role_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    role_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    role_vendor, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})

    admin_user = User.objects.create_user(email="admin_test@hpe.com", password="Password123!", role=role_admin)
    requester = User.objects.create_user(email="requester_test@hpe.com", password="Password123!", role=role_requester, department=dept)
    procexec = User.objects.create_user(email="procexec_test@hpe.com", password="Password123!", role=role_procexec)
    procmgr = User.objects.create_user(email="procmgr_test@hpe.com", password="Password123!", role=role_procmgr)
    finance_user = User.objects.create_user(email="finance_test@hpe.com", password="Password123!", role=role_finance)
    receiver = User.objects.create_user(email="receiver_test@hpe.com", password="Password123!", role=role_receiver)
    legal_user = User.objects.create_user(email="legal_test@hpe.com", password="Password123!", role=role_legal)
    auditor = User.objects.create_user(email="auditor_test@hpe.com", password="Password123!", role=role_auditor)

    category = VendorCategory.objects.create(name="Hardware", code="HW-CAT-01")
    vendor = Vendor.objects.create(
        legal_name="Global Tech Ltd",
        vendor_number="VND-2026-00099",
        tax_identification_number="TIN-GLB-99",
        category=category,
        status=Vendor.STATUS_ACTIVE,
    )
    vendor_user = User.objects.create_user(
        email="vendor_test@globaltech.com",
        password="Password123!",
        role=role_vendor,
        vendor=vendor,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "admin": admin_user,
        "requester": requester,
        "procexec": procexec,
        "procmgr": procmgr,
        "finance": finance_user,
        "receiver": receiver,
        "legal": legal_user,
        "auditor": auditor,
        "vendor": vendor,
        "vendor_user": vendor_user,
    }


@pytest.mark.django_db
def test_rt_001_axes_json_login_lockout(api_world):
    """RT-001: JSON login requests trigger account lockout after 5 failed attempts."""
    client = APIClient()
    email = "target_lockout@hpe.com"
    User.objects.create_user(email=email, password="RealPassword123!", role=Role.objects.get(code=Role.REQUESTER))

    for _ in range(5):
        res = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")
        assert res.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_429_TOO_MANY_REQUESTS]

    # The next attempt must be blocked with HTTP 429 ACCOUNT_LOCKED
    res_locked = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")
    assert res_locked.status_code == status.HTTP_429_TOO_MANY_REQUESTS
    assert res_locked.json()["error"]["code"] == "ACCOUNT_LOCKED"


@pytest.mark.django_db
def test_rt_006_vendor_user_denied_internal_invoice_and_receipt_actions(api_world):
    """RT-006: VENDOR_USER is strictly denied from internal create-invoice, exceptions, and retrieve receipt."""
    client = APIClient()
    vendor_user = api_world["vendor_user"]
    vendor = api_world["vendor"]
    cc = api_world["cc"]
    client.force_authenticate(user=vendor_user)

    po = PurchaseOrder.objects.create(
        po_number="PO-VEND-01",
        vendor=vendor,
        cost_center=cc,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("100.00"),
    )
    po_line = POLine.objects.create(
        po=po, item_description="Item 1", quantity=Decimal("1"), unit_price=Decimal("100.00")
    )
    grn = GoodsReceipt.objects.create(
        grn_number="GRN-VEND-01",
        po=po,
        received_by=api_world["receiver"],
        received_date=timezone.now(),
    )

    # 1. Vendor cannot call POST /api/v1/invoices/create-invoice/
    res_inv = client.post(
        "/api/v1/invoices/create-invoice/",
        {
            "vendor_id": str(vendor.id),
            "po_id": str(po.id),
            "invoice_number": "INV-TEST-001",
            "invoice_date": timezone.now().date().isoformat(),
            "line_items": [{"item_description": "Item 1", "quantity": 1, "unit_price": 100.0}],
        },
        format="json",
    )
    assert res_inv.status_code == status.HTTP_403_FORBIDDEN

    # 2. Vendor cannot create MatchException records via POST /api/v1/invoices/exceptions/
    res_exc = client.post(
        "/api/v1/invoices/exceptions/",
        {"exception_type": "PRICE_VARIANCE", "description": "Vendor attempt"},
        format="json",
    )
    assert res_exc.status_code == status.HTTP_403_FORBIDDEN

    # 3. Vendor cannot retrieve internal GoodsReceipt detail via GET /api/v1/receipts/{id}/
    res_grn = client.get(f"/api/v1/receipts/{grn.id}/")
    assert res_grn.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_rt_007_sourcing_event_patch_when_pr_in_sourcing_status(api_world):
    """RT-007: Updating/patching a DRAFT sourcing event whose linked PR is in SOURCING succeeds."""
    client = APIClient()
    procexec = api_world["procexec"]
    dept = api_world["dept"]
    cc = api_world["cc"]
    client.force_authenticate(user=procexec)

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-SRC-001",
        title="Hardware PR",
        requester=api_world["requester"],
        department=dept,
        cost_center=cc,
        status=PurchaseRequisition.STATUS_SOURCING,  # PR already moved to SOURCING
        total_amount=Decimal("5000.00"),
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=30),
    )
    now = timezone.now()
    event = SourcingEvent.objects.create(
        event_number="RFQ-2026-00099",
        title="RFQ Title",
        event_type="RFQ",
        status=SourcingEvent.STATUS_DRAFT,
        requisition=pr,
        bid_start_date=now + timezone.timedelta(days=1),
        bid_end_date=now + timezone.timedelta(days=15),
        technical_weight=50,
        commercial_weight=50,
    )

    # Patching description on the draft event must succeed
    res = client.patch(
        f"/api/v1/sourcing-events/events/{event.id}/",
        {"description": "Updated sourcing description"},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    event.refresh_from_db()
    assert event.description == "Updated sourcing description"


@pytest.mark.django_db
def test_rt_008_close_bidding_idempotent_when_already_in_technical_review(api_world):
    """RT-008: close-bidding returns 200 OK when event has auto-transitioned to TECHNICAL_REVIEW."""
    client = APIClient()
    procexec = api_world["procexec"]
    client.force_authenticate(user=procexec)

    now = timezone.now()
    event = SourcingEvent.objects.create(
        event_number="RFQ-2026-00100",
        title="Expired RFQ",
        event_type="RFQ",
        status=SourcingEvent.STATUS_TECHNICAL_REVIEW,
        bid_start_date=now - timezone.timedelta(days=10),
        bid_end_date=now - timezone.timedelta(days=1),
        technical_weight=50,
        commercial_weight=50,
    )

    res = client.post(f"/api/v1/sourcing-events/events/{event.id}/close-bidding/", format="json")
    assert res.status_code == status.HTTP_200_OK


@pytest.mark.django_db
def test_def_029_cannot_run_match_on_paid_invoice(api_world):
    """DEF-029: 3-way match cannot be re-run on a PAID invoice."""
    client = APIClient()
    finance = api_world["finance"]
    client.force_authenticate(user=finance)

    po = PurchaseOrder.objects.create(
        po_number="PO-PAID-01",
        vendor=api_world["vendor"],
        cost_center=api_world["cc"],
        status=PurchaseOrder.STATUS_COMPLETED,
        total_amount=Decimal("110.00"),
    )
    invoice = SupplierInvoice.objects.create(
        invoice_number="INV-PAID-001",
        vendor=api_world["vendor"],
        po=po,
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("100.00"),
        total_amount=Decimal("110.00"),
        status=SupplierInvoice.STATUS_PAID,
    )

    res = client.post(f"/api/v1/invoices/{invoice.id}/run-match/", format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    invoice.refresh_from_db()
    assert invoice.status == SupplierInvoice.STATUS_PAID  # Preserved as PAID


@pytest.mark.django_db
def test_def_036_def_037_terminated_contracts_immutable(api_world):
    """DEF-036 & DEF-037: Terminated contracts reject amendments and milestone creation."""
    client = APIClient()
    legal = api_world["legal"]
    client.force_authenticate(user=legal)

    today = timezone.now().date()
    contract = Contract.objects.create(
        title="Terminated SLA",
        contract_number="CON-TERM-001",
        vendor=api_world["vendor"],
        contract_owner=legal,
        contract_value=Decimal("50000.00"),
        start_date=today - timezone.timedelta(days=100),
        end_date=today + timezone.timedelta(days=200),
        status=Contract.STATUS_TERMINATED,
    )

    # 1. Amend must fail with 400
    res_amend = client.post(
        f"/api/v1/contracts/{contract.id}/amend/",
        {
            "amendment_summary": "Attempt amend",
            "contract_value": "60000.00",
            "start_date": str(today),
            "end_date": str(today + timezone.timedelta(days=300)),
        },
        format="json",
    )
    assert res_amend.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Add milestone must fail with 400
    res_ms = client.post(
        f"/api/v1/contracts/{contract.id}/add-milestone/",
        {"title": "Terminated Milestone", "due_date": str(today + timezone.timedelta(days=30))},
        format="json",
    )
    assert res_ms.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_def_042_duplicate_period_scorecard_rejected(api_world):
    """DEF-042: Scorecard creation for an existing (vendor, evaluation_period) returns 400."""
    client = APIClient()
    procmgr = api_world["procmgr"]
    vendor = api_world["vendor"]
    client.force_authenticate(user=procmgr)

    VendorScorecard.objects.create(
        vendor=vendor,
        evaluation_period="Q1-2026",
        evaluated_by=procmgr,
        delivery_score=Decimal("90.00"),
        quality_score=Decimal("90.00"),
        price_score=Decimal("90.00"),
        compliance_score=Decimal("90.00"),
        composite_score=Decimal("90.00"),
    )

    # Attempt to post a second scorecard for the same vendor and period
    res = client.post(
        "/api/v1/scorecards/",
        {
            "vendor": str(vendor.id),
            "evaluation_period": "Q1-2026",
            "delivery_score": "80.00",
            "quality_score": "80.00",
            "price_score": "80.00",
            "compliance_score": "80.00",
            "composite_score": "80.00",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
def test_rt_009_requester_can_delete_own_draft_requisition(api_world):
    """RT-009: Requester is permitted to soft-cancel their own DRAFT requisition (HTTP 204)."""
    client = APIClient()
    requester = api_world["requester"]
    dept = api_world["dept"]
    cc = api_world["cc"]
    client.force_authenticate(user=requester)

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-DRAFT-DEL-01",
        title="Draft PR to delete",
        requester=requester,
        department=dept,
        cost_center=cc,
        status=PurchaseRequisition.STATUS_DRAFT,
        total_amount=Decimal("500.00"),
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
    )

    res = client.delete(f"/api/v1/requisitions/{pr.id}/")
    assert res.status_code == status.HTTP_204_NO_CONTENT
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_CANCELLED


@pytest.mark.django_db
def test_rt_002_stores_receiver_and_legal_manager_safe_read_access(api_world):
    """RT-002: Stores Receiver reads invoices (200) and Legal Manager reads scorecards (200)."""
    client = APIClient()

    # 1. Stores Receiver reads invoices
    client.force_authenticate(user=api_world["receiver"])
    res_inv = client.get("/api/v1/invoices/")
    assert res_inv.status_code == status.HTTP_200_OK

    # 2. Legal Manager reads scorecards
    client.force_authenticate(user=api_world["legal"])
    res_sc = client.get("/api/v1/scorecards/")
    assert res_sc.status_code == status.HTTP_200_OK

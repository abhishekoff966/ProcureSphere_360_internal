import pytest
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from django.db import IntegrityError

from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.approvals.models import ApprovalPolicy, ApprovalStep
from apps.vendors.models import Vendor, VendorCategory
from apps.orders.models import PurchaseOrder, POLine
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.requisitions.services import submit_purchase_requisition_service


pytestmark = [pytest.mark.django_db]


@pytest.fixture
def phase2_env(db):
    org_a = Organization.objects.create(name="Acme Corp", code="ACME")
    org_b = Organization.objects.create(name="Beta Industries", code="BETA")

    dept_a = Department.objects.create(name="Engineering", code="ENG-A", organization=org_a)
    dept_b = Department.objects.create(name="Operations", code="OPS-A", organization=org_a)
    dept_cross = Department.objects.create(name="External Dept", code="EXT-B", organization=org_b)

    cc_a = CostCenter.objects.create(name="Cost Center A", code="CC-A", department=dept_a)
    cc_b = CostCenter.objects.create(name="Cost Center B", code="CC-B", department=dept_b)
    cc_cross = CostCenter.objects.create(name="Cost Center Cross", code="CC-EXT-B", department=dept_cross)

    year = timezone.now().year
    fp, _ = FiscalPeriod.objects.get_or_create(
        organization=org_a,
        year=year,
        period_number=1,
        defaults={
            "name": f"FY{year}-P1",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "is_closed": False,
        },
    )
    Budget.objects.get_or_create(
        cost_center=cc_a,
        fiscal_period=fp,
        defaults={
            "allocated_amount": Decimal("100000.00"),
            "committed_amount": Decimal("0.00"),
            "allow_overspend": True,
        },
    )
    Budget.objects.get_or_create(
        cost_center=cc_b,
        fiscal_period=fp,
        defaults={
            "allocated_amount": Decimal("100000.00"),
            "committed_amount": Decimal("0.00"),
            "allow_overspend": True,
        },
    )

    # Roles
    r_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    r_stores, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})

    # Users in Org A
    user_admin = User.objects.create_user(
        email="admin2@acme.test", password="Password123!", role=r_admin, organization=org_a, is_staff=True, is_superuser=True
    )
    user_req = User.objects.create_user(
        email="req2@acme.test", password="Password123!", role=r_req, organization=org_a, department=dept_a, cost_center=cc_a
    )
    user_mgr = User.objects.create_user(
        email="mgr2@acme.test", password="Password123!", role=r_mgr, organization=org_a
    )
    user_exec = User.objects.create_user(
        email="exec2@acme.test", password="Password123!", role=r_exec, organization=org_a
    )
    user_stores = User.objects.create_user(
        email="stores2@acme.test", password="Password123!", role=r_stores, organization=org_a
    )

    # Approval policy for PR submission
    policy = ApprovalPolicy.objects.create(
        module=ApprovalPolicy.MODULE_PR,
        name="Global PR Policy",
        min_amount=Decimal("0.00"),
        max_amount=Decimal("1000000.00"),
        department=dept_a,
        is_active=True,
    )
    ApprovalStep.objects.create(policy=policy, step_number=1, approver_role=r_mgr)

    cat, _ = VendorCategory.objects.get_or_create(name="Hardware", code="HW-01")
    vendor = Vendor.objects.create(
        organization=org_a,
        legal_name="Apex Hardware Ltd",
        vendor_number="VND-HW-001",
        tax_identification_number="TIN-HW-001",
        email="contact@apex.test",
        address="100 Tech Blvd",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )

    # Pre-existing issued PO for GRN tests
    po = PurchaseOrder.objects.create(
        po_number="PO-TEST-GRN-001",
        vendor=vendor,
        cost_center=cc_a,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
    )
    po_line = POLine.objects.create(
        po=po,
        item_description="Dell Monitors",
        quantity=Decimal("10.00"),
        quantity_received=Decimal("0.00"),
        unit_price=Decimal("100.00"),
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "dept_a": dept_a,
        "dept_b": dept_b,
        "dept_cross": dept_cross,
        "cc_a": cc_a,
        "cc_b": cc_b,
        "cc_cross": cc_cross,
        "admin": user_admin,
        "req": user_req,
        "mgr": user_mgr,
        "exec": user_exec,
        "stores": user_stores,
        "vendor": vendor,
        "po": po,
        "po_line": po_line,
    }


# =========================================================================
# 1. DEF-006: Empty Purchase Requisition Lines
# =========================================================================
class TestDef006EmptyPurchaseRequisitionLines:

    def test_create_pr_with_empty_lines_list_returns_400(self, phase2_env):
        """Scenario: Creating a PR with empty lines list is rejected with HTTP 400."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "PR with empty lines",
            "justification": "Testing empty lines validation",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        data = res.json()
        assert "lines" in str(data)

    def test_create_pr_without_lines_key_returns_400(self, phase2_env):
        """Scenario: Creating a PR without lines field is rejected with HTTP 400."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "PR with missing lines",
            "justification": "Testing missing lines validation",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "lines" in str(res.json())

    def test_create_pr_with_invalid_line_item_returns_400(self, phase2_env):
        """Scenario: Line item with zero or negative quantity is rejected with HTTP 400."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "PR with zero quantity line",
            "justification": "Testing zero quantity validation",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [
                {"item_description": "Monitors", "quantity": 0, "estimated_unit_price": "100.00"}
            ],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "lines" in str(res.json()) or "quantity" in str(res.json())

    def test_submit_pr_without_lines_returns_400(self, phase2_env):
        """Scenario: Submitting a PR that has no lines is rejected with HTTP 400."""
        # Create draft PR directly in DB without lines
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-DRAFT-NO-LINES",
            title="Draft PR without lines",
            justification="Testing submission guard",
            requester=phase2_env["req"],
            department=phase2_env["dept_a"],
            cost_center=phase2_env["cc_a"],
            status=PurchaseRequisition.STATUS_DRAFT,
            requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
            total_amount=Decimal("0.00"),
        )
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        res = api.post(f"/api/v1/requisitions/{pr.id}/submit/", {}, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        assert "line item" in str(res.json()).lower()

    def test_valid_pr_creation_and_submission_workflow_preserved(self, phase2_env):
        """Scenario: Valid PR with line items can be created, edited as DRAFT, and submitted."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "Valid PR Hardware",
            "justification": "Approved project supplies",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [
                {"item_description": "Laptops", "quantity": 5, "estimated_unit_price": "1000.00"}
            ],
        }
        res_create = api.post("/api/v1/requisitions/", payload, format="json")
        assert res_create.status_code == status.HTTP_201_CREATED
        pr_id = res_create.json()["id"]

        # Edit draft
        res_edit = api.patch(f"/api/v1/requisitions/{pr_id}/", {"title": "Updated Hardware PR"}, format="json")
        assert res_edit.status_code == status.HTTP_200_OK

        # Submit draft
        res_submit = api.post(f"/api/v1/requisitions/{pr_id}/submit/", {}, format="json")
        assert res_submit.status_code == status.HTTP_200_OK
        assert res_submit.json()["status"] in [
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
            PurchaseRequisition.STATUS_SUBMITTED,
        ]


# =========================================================================
# 2. DEF-007: Cost Center and Department Validation
# =========================================================================
class TestDef007CostCenterAndDepartmentValidation:

    def test_mismatched_cost_center_and_department_rejected_400(self, phase2_env):
        """Scenario: Cost center belonging to Dept B paired with Dept A returns HTTP 400."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "Mismatched Department PR",
            "justification": "Testing mismatch",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_b"].id),  # Belongs to dept_b, NOT dept_a
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [{"item_description": "Keyboards", "quantity": 2, "estimated_unit_price": "50.00"}],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == status.HTTP_400_BAD_REQUEST
        data = res.json()
        assert "cost_center" in data or "cost_center" in str(data)
        assert "belong" in str(data).lower()

    def test_cross_organization_department_or_cost_center_rejected(self, phase2_env):
        """Scenario: Selecting department or cost center from another organization violates tenant isolation."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])  # In Org A

        payload = {
            "title": "Cross-Org PR",
            "justification": "Testing tenant boundary",
            "department": str(phase2_env["dept_cross"].id),  # Org B
            "cost_center": str(phase2_env["cc_cross"].id),   # Org B
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [{"item_description": "Mouse", "quantity": 1, "estimated_unit_price": "20.00"}],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN]

    def test_matching_department_and_cost_center_succeeds(self, phase2_env):
        """Scenario: Valid matching department and cost center combination is accepted."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["req"])

        payload = {
            "title": "Matching Dept and CC PR",
            "justification": "Valid combination",
            "department": str(phase2_env["dept_a"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=7)),
            "lines": [{"item_description": "Server Cable", "quantity": 10, "estimated_unit_price": "15.00"}],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == status.HTTP_201_CREATED
        assert res.json()["cost_center"] == str(phase2_env["cc_a"].id)


# =========================================================================
# 3. DEF-010: Concurrent Duplicate Purchase Order Number
# =========================================================================
class TestDef010ConcurrentDuplicatePurchaseOrderNumber:

    def test_duplicate_po_number_returns_structured_400_or_409_no_500(self, phase2_env):
        """Scenario: Submitting an existing po_number returns structured client error, never HTTP 500."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["exec"])

        payload = {
            "po_number": "PO-UNIQUE-9901",
            "vendor": str(phase2_env["vendor"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "lines": [{"item_description": "Hard Drives", "quantity": 4, "unit_price": "150.00"}],
        }
        res1 = api.post("/api/v1/purchase-orders/", payload, format="json")
        assert res1.status_code == status.HTTP_201_CREATED

        # Second creation attempt with same po_number
        res2 = api.post("/api/v1/purchase-orders/", payload, format="json")
        assert res2.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]
        assert res2.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR
        assert "po_number" in str(res2.json()) or "DUPLICATE_RESOURCE" in str(res2.json())

    def test_concurrent_integrity_error_handled_with_409_conflict(self, phase2_env):
        """Scenario: When database integrity collision occurs concurrently, HTTP 409 Conflict is returned."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["exec"])

        # Create base PO
        PurchaseOrder.objects.create(
            po_number="PO-RACE-001",
            vendor=phase2_env["vendor"],
            cost_center=phase2_env["cc_a"],
            status=PurchaseOrder.STATUS_DRAFT,
            total_amount=Decimal("100.00"),
        )

        from unittest.mock import patch

        # Simulate concurrent collision where serializer validation passed, but DB throws IntegrityError on save
        with patch.object(
            PurchaseOrder, "save", side_effect=IntegrityError("duplicate key value violates unique constraint 'orders_purchaseorder_po_number_key'")
        ):
            payload = {
                "po_number": "PO-RACE-NEW",
                "vendor": str(phase2_env["vendor"].id),
                "cost_center": str(phase2_env["cc_a"].id),
                "lines": [{"item_description": "RAM Modules", "quantity": 2, "unit_price": "80.00"}],
            }
            res = api.post("/api/v1/purchase-orders/", payload, format="json")
            assert res.status_code == status.HTTP_409_CONFLICT
            assert res.json()["error"]["code"] == "DUPLICATE_RESOURCE"

    def test_po_rollback_preserves_database_atomicity_on_collision(self, phase2_env):
        """Scenario: When PO creation collides on duplicate PO number, no orphaned lines persist."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["exec"])

        po_num = "PO-ATOMIC-001"
        payload = {
            "po_number": po_num,
            "vendor": str(phase2_env["vendor"].id),
            "cost_center": str(phase2_env["cc_a"].id),
            "lines": [{"item_description": "SSD Drives", "quantity": 10, "unit_price": "200.00"}],
        }
        res1 = api.post("/api/v1/purchase-orders/", payload, format="json")
        assert res1.status_code == status.HTTP_201_CREATED
        lines_after_create = POLine.objects.count()

        # Colliding attempt
        res2 = api.post("/api/v1/purchase-orders/", payload, format="json")
        assert res2.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]

        # Lines count must not increase (no orphan lines created)
        assert POLine.objects.count() == lines_after_create


# =========================================================================
# 4. DEF-011: Duplicate Goods Receipt Note
# =========================================================================
class TestDef011DuplicateGoodsReceiptNote:

    def test_duplicate_grn_number_rejected_and_quantities_not_double_counted(self, phase2_env):
        """Scenario: Duplicate GRN number is rejected and POLine quantity_received is incremented only once."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["stores"])
        po = phase2_env["po"]
        po_line = phase2_env["po_line"]
        assert po_line.quantity_received == Decimal("0.00")

        payload = {
            "po": str(po.id),
            "grn_number": "GRN-CUSTOM-9901",
            "delivery_note_number": "DN-9901",
            "received_date": timezone.now().isoformat(),
            "lines": [
                {
                    "po_line_id": str(po_line.id),
                    "quantity_received": "3.00",
                    "quantity_accepted": "3.00",
                }
            ],
        }

        # First receipt
        res1 = api.post("/api/v1/receipts/", payload, format="json")
        assert res1.status_code == status.HTTP_201_CREATED
        po_line.refresh_from_db()
        assert po_line.quantity_received == Decimal("3.00")

        # Second receipt with identical grn_number
        res2 = api.post("/api/v1/receipts/", payload, format="json")
        assert res2.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]
        assert res2.status_code != status.HTTP_500_INTERNAL_SERVER_ERROR

        # Quantity received MUST remain exactly 3.00 (not incremented again)
        po_line.refresh_from_db()
        assert po_line.quantity_received == Decimal("3.00")

    def test_duplicate_delivery_note_for_same_po_rejected(self, phase2_env):
        """Scenario: Submitting the same delivery note number for the same PO is rejected as duplicate."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["stores"])
        po = phase2_env["po"]
        po_line = phase2_env["po_line"]

        payload1 = {
            "po": str(po.id),
            "grn_number": "GRN-DN-TEST-1",
            "delivery_note_number": "DN-IDENTICAL-01",
            "received_date": timezone.now().isoformat(),
            "lines": [{"po_line_id": str(po_line.id), "quantity_received": "1.00", "quantity_accepted": "1.00"}],
        }
        res1 = api.post("/api/v1/receipts/", payload1, format="json")
        assert res1.status_code == status.HTTP_201_CREATED

        # Different GRN number but SAME delivery note number on same PO
        payload2 = {
            "po": str(po.id),
            "grn_number": "GRN-DN-TEST-2",
            "delivery_note_number": "DN-IDENTICAL-01",
            "received_date": timezone.now().isoformat(),
            "lines": [{"po_line_id": str(po_line.id), "quantity_received": "1.00", "quantity_accepted": "1.00"}],
        }
        res2 = api.post("/api/v1/receipts/", payload2, format="json")
        assert res2.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT]
        assert "delivery note" in str(res2.json()).lower()

    def test_concurrent_grn_integrity_error_handled_with_409_conflict(self, phase2_env):
        """Scenario: Concurrent DB integrity collision on GRN number returns HTTP 409 and rolls back quantity."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["stores"])
        po = phase2_env["po"]
        po_line = phase2_env["po_line"]
        qty_before = po_line.quantity_received or Decimal("0.00")

        from unittest.mock import patch

        with patch(
            "apps.receipts.services.GoodsReceipt.objects.create",
            side_effect=IntegrityError("duplicate key value violates unique constraint 'receipts_goodsreceipt_grn_number_key'"),
        ):
            payload = {
                "po": str(po.id),
                "grn_number": "GRN-RACE-001",
                "delivery_note_number": "DN-RACE-001",
                "received_date": timezone.now().isoformat(),
                "lines": [{"po_line_id": str(po_line.id), "quantity_received": "2.00", "quantity_accepted": "2.00"}],
            }
            res = api.post("/api/v1/receipts/", payload, format="json")
            assert res.status_code == status.HTTP_409_CONFLICT
            assert res.json()["error"]["code"] == "DUPLICATE_RESOURCE"

        # Verify quantities rolled back completely
        po_line.refresh_from_db()
        assert po_line.quantity_received == qty_before

    def test_valid_partial_and_complete_receipt_workflow(self, phase2_env):
        """Scenario: Legitimate partial receipts increment quantities correctly until ordered limit is reached."""
        api = APIClient()
        api.force_authenticate(user=phase2_env["stores"])

        # Create fresh PO with 10 items
        po_fresh = PurchaseOrder.objects.create(
            po_number="PO-PARTIAL-WORKFLOW",
            vendor=phase2_env["vendor"],
            cost_center=phase2_env["cc_a"],
            status=PurchaseOrder.STATUS_ISSUED,
            total_amount=Decimal("1000.00"),
        )
        line_fresh = POLine.objects.create(
            po=po_fresh,
            item_description="Projectors",
            quantity=Decimal("10.00"),
            quantity_received=Decimal("0.00"),
            unit_price=Decimal("100.00"),
        )

        # 1. First partial receipt: 6 items
        res_part1 = api.post(
            "/api/v1/receipts/",
            {
                "po": str(po_fresh.id),
                "grn_number": "GRN-PARTIAL-01",
                "delivery_note_number": "DN-PARTIAL-01",
                "received_date": timezone.now().isoformat(),
                "lines": [{"po_line_id": str(line_fresh.id), "quantity_received": "6.00", "quantity_accepted": "6.00"}],
            },
            format="json",
        )
        assert res_part1.status_code == status.HTTP_201_CREATED
        po_fresh.refresh_from_db()
        line_fresh.refresh_from_db()
        assert line_fresh.quantity_received == Decimal("6.00")
        assert po_fresh.status == PurchaseOrder.STATUS_PARTIAL_RECEIPT

        # 2. Exceeding remaining quantity (5 items when remaining is 4) is rejected
        res_over = api.post(
            "/api/v1/receipts/",
            {
                "po": str(po_fresh.id),
                "grn_number": "GRN-OVER-01",
                "delivery_note_number": "DN-OVER-01",
                "received_date": timezone.now().isoformat(),
                "lines": [{"po_line_id": str(line_fresh.id), "quantity_received": "5.00", "quantity_accepted": "5.00"}],
            },
            format="json",
        )
        assert res_over.status_code == status.HTTP_400_BAD_REQUEST
        assert "cannot exceed remaining quantity" in str(res_over.json()).lower()

        # 3. Second partial receipt: exact remaining 4 items
        res_part2 = api.post(
            "/api/v1/receipts/",
            {
                "po": str(po_fresh.id),
                "grn_number": "GRN-PARTIAL-02",
                "delivery_note_number": "DN-PARTIAL-02",
                "received_date": timezone.now().isoformat(),
                "lines": [{"po_line_id": str(line_fresh.id), "quantity_received": "4.00", "quantity_accepted": "4.00"}],
            },
            format="json",
        )
        assert res_part2.status_code == status.HTTP_201_CREATED
        po_fresh.refresh_from_db()
        line_fresh.refresh_from_db()
        assert line_fresh.quantity_received == Decimal("10.00")
        assert po_fresh.status == PurchaseOrder.STATUS_COMPLETED

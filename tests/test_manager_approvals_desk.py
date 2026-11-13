from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction
from apps.orders.models import POAmendment, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def org_and_users(db):
    org = Organization.objects.create(name="Test Org", code="TORG")
    dept = Department.objects.create(organization=org, name="Procurement", code="PROC")
    cost_center = CostCenter.objects.create(
        department=dept, name="General Operations", code="CC-GEN"
    )

    manager_role, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    dept_appr_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    req_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})

    manager_user = User.objects.create_user(
        email="proc_mgr@test.com",
        password="ValidPassword123!",
        role=manager_role,
        organization=org,
        department=dept,
    )
    approver_user = User.objects.create_user(
        email="dept_appr@test.com",
        password="ValidPassword123!",
        role=dept_appr_role,
        organization=org,
        department=dept,
    )
    requester_user = User.objects.create_user(
        email="requester@test.com",
        password="ValidPassword123!",
        role=req_role,
        organization=org,
        department=dept,
    )

    # Create test PR in SUBMITTED state
    pr = PurchaseRequisition.objects.create(
        department=dept,
        cost_center=cost_center,
        requester=requester_user,
        pr_number="PR-TEST-001",
        title="Urgent Equipment",
        justification="Hardware upgrade",
        status=PurchaseRequisition.STATUS_SUBMITTED,
        total_amount=Decimal("15000.00"),
        requested_delivery_date=timezone.now().date(),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Industrial Server Rack",
        quantity=Decimal("2.00"),
        estimated_unit_price=Decimal("7500.00"),
    )

    # Create Vendor, PO, and POAmendment
    category, _ = VendorCategory.objects.get_or_create(code="IT_HW", defaults={"name": "IT Hardware"})
    vendor = Vendor.objects.create(
        legal_name="Global Tech Supplies Inc",
        trade_name="Global Tech",
        category=category,
        vendor_number="VND-TEST-001",
        tax_identification_number="US998877665",
        status=Vendor.STATUS_ACTIVE,
    )
    po = PurchaseOrder.objects.create(
        po_number="PO-TEST-001",
        vendor=vendor,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("50000.00"),
    )
    amendment = POAmendment.objects.create(
        po=po,
        amendment_number=1,
        previous_version_snapshot={"total_amount": "50000.00"},
        pending_line_items=[],
        status=POAmendment.STATUS_PENDING,
        reason="Market price adjustment",
        delta_amount=Decimal("2500.00"),
        requested_by=requester_user,
    )

    return {
        "org": org,
        "dept": dept,
        "manager": manager_user,
        "approver": approver_user,
        "requester": requester_user,
        "pr": pr,
        "po": po,
        "amendment": amendment,
    }


@pytest.mark.django_db
def test_manager_approvals_view_access(client, org_and_users):
    """Procurement manager can access /manager/approvals/ and renders dedicated template."""
    client.force_login(org_and_users["manager"])
    url = reverse("manager_approvals")
    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert "manager/approvals.html" in [t.name for t in response.templates]
    assert "Pending Approvals" in response.content.decode("utf-8")


@pytest.mark.django_db
def test_requester_denied_manager_approvals(client, org_and_users):
    """Requester role is denied access to /manager/approvals/."""
    client.force_login(org_and_users["requester"])
    url = reverse("manager_approvals")
    response = client.get(url)

    assert response.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_approvals_inbox_redirects_proc_manager(client, org_and_users):
    """When a procurement manager hits /approvals/inbox/, they redirect to /manager/approvals/."""
    client.force_login(org_and_users["manager"])
    url = reverse("approvals_inbox")
    response = client.get(url)

    assert response.status_code == status.HTTP_302_FOUND
    assert response.url == reverse("manager_approvals")


@pytest.mark.django_db
def test_manager_approvals_api_data(client, org_and_users):
    """GET /api/v1/manager/approvals/ returns DB-backed pending counts and items."""
    client.force_login(org_and_users["manager"])
    url = reverse("manager-approvals-api")
    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["summary"]["pr_pending_count"] >= 1
    assert data["summary"]["po_amendment_pending_count"] >= 1
    assert data["summary"]["total_pending_count"] >= 2
    assert len(data["results"]) >= 2
    ref_numbers = [item["reference_number"] for item in data["results"]]
    assert "PR-TEST-001" in ref_numbers
    assert all(item["can_approve"] is False for item in data["results"])


@pytest.mark.django_db
def test_manager_approval_action_api(client, org_and_users):
    """Procurement Manager has approval oversight only and cannot approve or reject."""
    client.force_login(org_and_users["manager"])
    url = reverse("manager-approval-action-api")

    # 1. Attempting to approve PO Amendment must be rejected with 403 Forbidden
    amendment = org_and_users["amendment"]
    payload_po = {
        "item_type": "PO_AMENDMENT",
        "item_id": str(amendment.id),
        "action": "APPROVE",
        "comments": "Attempting approval.",
    }
    response_po = client.post(url, data=payload_po, content_type="application/json")
    assert response_po.status_code == status.HTTP_403_FORBIDDEN
    assert "Permission Denied" in response_po.json()["error"]
    assert "approval oversight only" in response_po.json()["error"]

    amendment.refresh_from_db()
    assert amendment.status == POAmendment.STATUS_PENDING

    # 2. Attempting to approve PR must be rejected with 403 Forbidden
    pr = org_and_users["pr"]
    payload_pr = {
        "item_type": "PR",
        "item_id": str(pr.id),
        "action": "APPROVE",
        "comments": "Attempting PR approval.",
    }
    response_pr = client.post(url, data=payload_pr, content_type="application/json")
    assert response_pr.status_code == status.HTTP_403_FORBIDDEN
    assert "Permission Denied" in response_pr.json()["error"]
    assert "approval oversight only" in response_pr.json()["error"]

    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_SUBMITTED


@pytest.mark.django_db
def test_requisitions_list_uses_manager_base_layout(client, org_and_users):
    """Procurement manager viewing requisitions list uses manager/base_manager.html."""
    client.force_login(org_and_users["manager"])
    url = reverse("requisitions_list")
    response = client.get(url)

    assert response.status_code == status.HTTP_200_OK
    assert response.context["base_layout"] == "manager/base_manager.html"


@pytest.mark.django_db
def test_manager_out_of_budget_pr_can_reject_cannot_approve(client, org_and_users):
    """
    Business Rule Verification:
    - If PR is out of budget, Procurement Manager inspects and rejects.
    - Zero option to approve under any circumstance.
    - Within-budget PRs cannot be rejected by Manager (oversight only).
    """
    client.force_login(org_and_users["manager"])
    url = reverse("manager-approval-action-api")
    pr = org_and_users["pr"]

    # 1. Within-budget PR: Rejection by manager is blocked
    resp_within = client.post(url, data={
        "item_type": "PR",
        "item_id": str(pr.id),
        "action": "REJECT",
        "comments": "Should fail for within budget PR",
    }, content_type="application/json")
    assert resp_within.status_code == status.HTTP_403_FORBIDDEN

    # 2. Transition PR to STATUS_BUDGET_REVIEW (Out of Budget)
    pr.status = PurchaseRequisition.STATUS_BUDGET_REVIEW
    pr.save()

    # Verify GET /api/v1/manager/approvals/ reports can_approve=False, can_reject=True, is_out_of_budget=True
    get_resp = client.get(reverse("manager-approvals-api"))
    assert get_resp.status_code == status.HTTP_200_OK
    pr_items = [it for it in get_resp.json()["results"] if it["reference_number"] == pr.pr_number]
    assert len(pr_items) == 1
    assert pr_items[0]["can_approve"] is False
    assert pr_items[0]["can_reject"] is True
    assert pr_items[0]["is_out_of_budget"] is True

    # 3. Manager tries to APPROVE out-of-budget PR -> Strictly 403 Forbidden
    resp_approve = client.post(url, data={
        "item_type": "PR",
        "item_id": str(pr.id),
        "action": "APPROVE",
        "comments": "Manager trying to approve out of budget PR",
    }, content_type="application/json")
    assert resp_approve.status_code == status.HTTP_403_FORBIDDEN
    assert "do not have authority to approve Purchase Requisitions" in resp_approve.json()["error"]

    # 4. Manager REJECTS out-of-budget PR -> 200 OK and status updated
    resp_reject = client.post(url, data={
        "item_type": "PR",
        "item_id": str(pr.id),
        "action": "REJECT",
        "comments": "Requisition exceeds departmental budget allocation.",
    }, content_type="application/json")
    assert resp_reject.status_code == status.HTTP_200_OK
    assert resp_reject.json()["new_status"] == PurchaseRequisition.STATUS_REJECTED

    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_REJECTED


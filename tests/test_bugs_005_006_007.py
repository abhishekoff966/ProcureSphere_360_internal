from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition


@pytest.fixture
def setup_data(db):
    org = Organization.objects.create(name="BugFix Org", code="BFORG")
    dept = Department.objects.create(organization=org, name="Finance Dept", code="FIN")
    cc = CostCenter.objects.create(department=dept, name="General CC", code="CC-FIN")

    dept_appr_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    req_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})

    approver = User.objects.create_user(
        email="approver_bf@test.com",
        password="Password123!",
        role=dept_appr_role,
        organization=org,
        department=dept,
    )
    requester = User.objects.create_user(
        email="requester_bf@test.com",
        password="Password123!",
        role=req_role,
        organization=org,
        department=dept,
    )

    pr1 = PurchaseRequisition.objects.create(
        department=dept,
        cost_center=cc,
        requester=requester,
        pr_number="PR-2026-00034",
        title="Server Upgrade Q4",
        justification="Critical servers",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        total_amount=Decimal("25000.00"),
        requested_delivery_date=timezone.now().date(),
    )
    PRLine.objects.create(
        requisition=pr1,
        item_description="Server Rack",
        quantity=Decimal("1.00"),
        estimated_unit_price=Decimal("25000.00"),
        estimated_total=Decimal("25000.00"),
    )

    pr2 = PurchaseRequisition.objects.create(
        department=dept,
        cost_center=cc,
        requester=requester,
        pr_number="PR-2026-00035",
        title="Office Chairs",
        justification="Chairs needed",
        status=PurchaseRequisition.STATUS_APPROVED,
        total_amount=Decimal("1200.00"),
        requested_delivery_date=timezone.now().date(),
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "approver": approver,
        "requester": requester,
        "pr_manager_review": pr1,
        "pr_approved": pr2,
    }


def test_bug_005_no_duplicate_approve_reject_buttons_at_top(client, setup_data):
    """
    BUG-005 & User Request: Verify sidebar card only has decision comments,
    with no redundant approve/reject buttons inside the sidebar card.
    The primary action buttons are in the top header.
    """
    approver = setup_data["approver"]
    pr = setup_data["pr_manager_review"]

    client.force_login(approver)
    response = client.get(reverse("requisition_detail", kwargs={"pk": pr.pk}))
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # The sidebar card exists and only contains comments textarea
    assert 'id="approvalActionDecision"' in content
    assert "Decision Comments" in content
    assert "Approve Requisition" not in content
    assert "Reject Requisition" not in content

    # The action buttons are available at the top
    assert "Approve PR" in content
    assert "Reject PR" in content


def test_bug_006_status_filter_visible_directly_in_search_bar(client, setup_data):
    """
    BUG-006: Status filter dropdown is directly visible in the search bar layout on /requisitions/.
    """
    requester = setup_data["requester"]
    client.force_login(requester)

    response = client.get(reverse("requisitions_list"))
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Status dropdown is directly accessible in search bar without collapse/toggle
    assert 'id="statusFilterSelect"' in content
    assert 'name="status"' in content
    assert "Search &amp; Filter" in content
    assert "All Statuses" in content
    assert "Pending Review" in content
    assert "Manager Review" in content

    # Test filtering by status
    resp_filtered = client.get(reverse("requisitions_list") + "?status=MANAGER_REVIEW")
    assert resp_filtered.status_code == 200
    content_filtered = resp_filtered.content.decode("utf-8")
    assert "PR-2026-00034" in content_filtered
    assert "PR-2026-00035" not in content_filtered


def test_bug_007_approvals_history_filter_options_and_filtering(client, setup_data):
    """
    BUG-007: My Approvals History dropdown includes active workflow states like Manager Review,
    and accurately filters items in audit history.
    """
    approver = setup_data["approver"]
    pr_manager_rev = setup_data["pr_manager_review"]
    pr_appr = setup_data["pr_approved"]

    # Record that approver approved Step 1 on pr_manager_rev
    ApprovalAction.objects.create(
        actor=approver,
        target_model_name="PurchaseRequisition",
        target_object_id=pr_manager_rev.id,
        action=ApprovalAction.ACTION_APPROVE,
        previous_state=PurchaseRequisition.STATUS_SUBMITTED,
        new_state=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        comments="Step 1 approved",
    )

    client.force_login(approver)
    response = client.get(reverse("approvals_inbox") + "?tab=history")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Check that Manager Review and Pending Review options exist in history filter dropdown
    assert '<option value="MANAGER_REVIEW"' in content
    assert "Manager Review</option>" in content
    assert '<option value="PENDING"' in content
    assert "Pending Review</option>" in content
    assert '<option value="APPROVED"' in content
    assert '<option value="REJECTED"' in content

    # Both PRs appear in history because approver evaluated pr_manager_rev and pr_appr is in history_statuses
    assert "PR-2026-00034" in content

    # Filter by MANAGER_REVIEW in history
    resp_filtered = client.get(reverse("approvals_inbox") + "?tab=history&status=MANAGER_REVIEW")
    assert resp_filtered.status_code == 200
    content_filtered = resp_filtered.content.decode("utf-8")
    assert "PR-2026-00034" in content_filtered
    assert "PR-2026-00035" not in content_filtered

    # Filter by APPROVED in history
    resp_approved = client.get(reverse("approvals_inbox") + "?tab=history&status=APPROVED")
    assert resp_approved.status_code == 200
    content_approved = resp_approved.content.decode("utf-8")
    assert "PR-2026-00035" in content_approved
    assert "PR-2026-00034" not in content_approved


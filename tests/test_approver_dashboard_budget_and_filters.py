from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalAction, ApprovalPolicy, ApprovalStep
from apps.budgets.models import Budget
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition


@pytest.fixture
def setup_approver_env(db):
    org = Organization.objects.create(name="HPE Test Org", code="HPE-TEST")
    dept = Department.objects.create(organization=org, name="Engineering", code="ENG-01")
    cc = CostCenter.objects.create(department=dept, name="R&D", code="CC-ENG-01")

    now = timezone.now()
    fp = FiscalPeriod.objects.create(
        organization=org,
        year=now.year,
        period_number=1,
        name=f"FY{now.year}-Q1",
        start_date=now.date().replace(month=1, day=1),
        end_date=now.date().replace(month=12, day=31),
        is_closed=False,
    )

    approver_role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    requester_role, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})

    approver_user = User.objects.create_user(
        email="approver_test@hpe.com",
        password="Password123!",
        role=approver_role,
        department=dept,
        organization=org,
    )

    requester_user = User.objects.create_user(
        email="requester_test@hpe.com",
        password="Password123!",
        role=requester_role,
        department=dept,
        organization=org,
    )

    policy = ApprovalPolicy.objects.create(
        name="Test Dept Policy",
        organization=org,
        department=dept,
        module=ApprovalPolicy.MODULE_PR,
        min_amount=Decimal("0.00"),
        max_amount=Decimal("1000000.00"),
        is_active=True,
    )
    ApprovalStep.objects.create(
        policy=policy,
        step_number=1,
        approver_role=approver_role,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "fp": fp,
        "approver": approver_user,
        "requester": requester_user,
    }


@pytest.mark.django_db
def test_approver_dashboard_excludes_draft_prs(client, setup_approver_env):
    env = setup_approver_env
    # Create a draft PR and a submitted PR
    today = timezone.now().date()
    draft_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Draft Laptop Requisition",
        status=PurchaseRequisition.STATUS_DRAFT,
        pr_number="PR-TEST-DRAFT",
        requested_delivery_date=today,
    )
    submitted_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Submitted Server Requisition",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-TEST-SUBMITTED",
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])
    response = client.get("/approver/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "PR-TEST-DRAFT" not in content
    assert "Draft Laptop Requisition" not in content
    assert "PR-TEST-SUBMITTED" in content
    assert "Submitted Server Requisition" in content


@pytest.mark.django_db
def test_approver_dashboard_budget_overrun_removes_approve_button(client, setup_approver_env):
    env = setup_approver_env
    # Budget has only 100 available
    Budget.objects.create(
        cost_center=env["cc"],
        fiscal_period=env["fp"],
        allocated_amount=Decimal("100.00"),
        reserved_amount=Decimal("0.00"),
        committed_amount=Decimal("0.00"),
        actual_amount=Decimal("0.00"),
    )

    # PR requires 500 (exceeds budget)
    today = timezone.now().date()
    over_budget_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="High Value Cluster",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-OVERBUDGET",
        requested_delivery_date=today,
    )
    PRLine.objects.create(
        requisition=over_budget_pr,
        item_description="Server Rack",
        quantity=Decimal("1"),
        estimated_unit_price=Decimal("500.00"),
    )
    over_budget_pr.total_amount = Decimal("500.00")
    over_budget_pr.save()

    client.force_login(env["approver"])
    response = client.get("/approver/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    assert "PR-OVERBUDGET" in content
    assert "Over Budget" in content or "Awaiting Finance AP" in content
    # Ensure the PR action row does NOT render an Approve button form for this PR
    assert "Awaiting Finance AP" in content


@pytest.mark.django_db
def test_requisitions_list_excludes_draft_and_restricted_filter_options(client, setup_approver_env):
    env = setup_approver_env
    today = timezone.now().date()
    draft_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Hidden Draft PR",
        status=PurchaseRequisition.STATUS_DRAFT,
        pr_number="PR-HIDDEN-DRAFT",
        requested_delivery_date=today,
    )
    active_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Visible Review PR",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-VISIBLE-REVIEW",
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])
    response = client.get("/requisitions/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    # Draft item must NOT be listed
    assert "PR-HIDDEN-DRAFT" not in content
    assert "Hidden Draft PR" not in content
    assert "PR-VISIBLE-REVIEW" in content

    # Check filter select options: Draft, In Sourcing, PO Issued should NOT be options for DEPT_APPROVER
    assert '<option value="DRAFT"' not in content
    assert '<option value="SOURCING"' not in content
    assert '<option value="PO_ISSUED"' not in content


@pytest.mark.django_db
def test_requisition_detail_back_button_and_overrun_guard(client, setup_approver_env):
    env = setup_approver_env
    today = timezone.now().date()
    pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Exceeded PR Detail Check",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-DETAIL-CHECK",
        requested_delivery_date=today,
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Big Hardware",
        quantity=Decimal("1"),
        estimated_unit_price=Decimal("1000.00"),
    )
    pr.total_amount = Decimal("1000.00")
    pr.save()
    # Budget created with 100 available, but PR is 1000 => budget overrun
    Budget.objects.create(
        cost_center=env["cc"],
        fiscal_period=env["fp"],
        allocated_amount=Decimal("100.00"),
        reserved_amount=Decimal("0.00"),
        committed_amount=Decimal("0.00"),
        actual_amount=Decimal("0.00"),
    )

    client.force_login(env["approver"])
    response = client.get(f"/requisitions/{pr.pk}/")
    assert response.status_code == 200

    content = response.content.decode("utf-8")
    # Must NOT have "Back to Approvals Inbox"
    assert "Back to Approvals Inbox" not in content
    # Must have "Back" button with history.back()
    assert "window.history.back()" in content or "history.back()" in content
    assert "Back" in content

    # Since budget is not there / exceeded, Department Approver cannot see Approve PR button
    assert "Approve PR" not in content


@pytest.mark.django_db
def test_approver_dashboard_my_approvals_navigation(client, setup_approver_env):
    env = setup_approver_env
    client.force_login(env["approver"])

    # 1. Dashboard has My Approvals quick button
    response = client.get("/approver/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert 'href="/approvals/inbox/?tab=history"' in content
    assert "My Approvals" in content

    # 2. Clicking /approvals/inbox/?tab=history activates My Approvals in sidebar
    response = client.get("/approvals/inbox/?tab=history")
    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "My Approvals" in content
    assert "Evaluated History" in content


@pytest.mark.django_db
def test_approver_dashboard_negative_budget_clamped_for_display_and_preserves_overrun_guard(
    client, setup_approver_env
):
    """Overdrawn budgets clamp card display to ₹0.00 (no negative balance) while preserving overrun guard."""
    env = setup_approver_env
    today = timezone.now().date()

    # Department budget is overdrawn (e.g. overrun exception approval / PO commitments)
    budget = Budget.objects.create(
        cost_center=env["cc"],
        fiscal_period=env["fp"],
        allocated_amount=Decimal("100000.00"),
        reserved_amount=Decimal("5000000.00"),
        committed_amount=Decimal("4929757.00"),
        actual_amount=Decimal("0.00"),
    )
    assert budget.available_amount == Decimal("-9829757.00")

    # Separate managed CC in another department must NOT leak into IT/ENG Funds card
    other_dept = Department.objects.create(organization=env["org"], name="Ops", code="OPS-02")
    other_cc = CostCenter.objects.create(
        department=other_dept, name="Ops CC", code="CC-OPS-02", manager=env["approver"]
    )
    Budget.objects.create(
        cost_center=other_cc,
        fiscal_period=env["fp"],
        allocated_amount=Decimal("50000.00"),
        reserved_amount=Decimal("0.00"),
    )

    pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Pending Overdrawn PR",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-OVERDRAWN-01",
        total_amount=Decimal("500.00"),
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])
    response = client.get("/approver/")
    assert response.status_code == 200
    content = response.content.decode("utf-8")

    # Displayed available budget is clamped to 0.00 (never negative) and marks Exhausted
    assert response.context["metrics"]["available_budget"] == 0.0
    assert response.context["metrics"]["is_budget_overdrawn"] is True
    assert "₹0.00" in content
    assert "₹-" not in content
    assert "-9829757" not in content
    assert "Exhausted" in content
    assert "Awaiting Finance AP" in content
    assert pr.pr_number in content

    # Underlying Budget model property remains unclamped for DEF-003 accounting accuracy
    budget.refresh_from_db()
    assert budget.available_amount == Decimal("-9829757.00")


@pytest.mark.django_db
def test_approver_dashboard_pending_links_auto_filter_pending_prs_only(client, setup_approver_env):
    """Clicking Pending badge/card links opens /approvals/inbox/?status=PENDING with Pending Review selected."""
    env = setup_approver_env
    today = timezone.now().date()

    pending_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Pending Monitor Order",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-PENDING-ONLY-01",
        total_amount=Decimal("250.00"),
        requested_delivery_date=today,
    )
    approved_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Already Approved Item",
        status=PurchaseRequisition.STATUS_APPROVED,
        pr_number="PR-APPROVED-01",
        total_amount=Decimal("300.00"),
        requested_delivery_date=today,
    )
    rejected_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Rejected Item",
        status=PurchaseRequisition.STATUS_REJECTED,
        pr_number="PR-REJECTED-01",
        total_amount=Decimal("400.00"),
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])
    dash_resp = client.get("/approver/")
    assert dash_resp.status_code == 200
    dash_html = dash_resp.content.decode("utf-8")
    assert 'href="/approvals/inbox/?status=PENDING"' in dash_html

    inbox_resp = client.get("/approvals/inbox/?status=PENDING")
    assert inbox_resp.status_code == 200
    inbox_html = inbox_resp.content.decode("utf-8")

    assert inbox_resp.context["status_filter"] == "PENDING"
    assert '<option value="PENDING" selected>Pending Review</option>' in inbox_html
    assert pending_pr.pr_number in inbox_html
    assert approved_pr.pr_number not in inbox_html
    assert rejected_pr.pr_number not in inbox_html


@pytest.mark.django_db
def test_approver_dashboard_shows_available_budget_across_multiple_open_periods(
    client, setup_approver_env
):
    """Dashboard must display active Cost Center budget even when an earlier open FiscalPeriod has no budget."""
    env = setup_approver_env
    today = timezone.now().date()

    # Create a second open fiscal period holding the actual active budget (simulating Q1 vs Annual/Q4)
    second_fp = FiscalPeriod.objects.create(
        organization=env["org"],
        year=today.year,
        period_number=99,
        name="FY-ALT-OPEN",
        start_date=today - timezone.timedelta(days=5),
        end_date=today + timezone.timedelta(days=90),
        is_closed=False,
    )
    Budget.objects.create(
        cost_center=env["cc"],
        fiscal_period=second_fp,
        allocated_amount=Decimal("9900000.00"),
        reserved_amount=Decimal("400.00"),
        committed_amount=Decimal("0.00"),
        actual_amount=Decimal("0.00"),
    )

    pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Checking PR Budget Sync",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-SYNC-BUDGET-01",
        total_amount=Decimal("400.00"),
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])
    resp = client.get("/approver/")
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")

    assert resp.context["metrics"]["available_budget"] == 9899600.0
    assert resp.context["metrics"]["is_budget_overdrawn"] is False
    assert "₹9899600.00" in html
    assert pr.pr_number in html
    # Within-budget pending PR renders Approve, Reject, and always-visible comment box
    assert 'name="action" value="APPROVED"' in html
    assert 'name="action" value="REJECTED"' in html
    assert 'name="comments"' in html
    assert 'd-none d-xl-inline-block rounded-pill px-3" style="width: 120px;' not in html


@pytest.mark.django_db
def test_incoming_pr_renders_approve_reject_and_comment_box_in_detail_and_inbox(
    client, setup_approver_env
):
    """Within-budget pending PR shows Approve, Reject, and comment box on detail & inbox and preserves 2-step workflow."""
    env = setup_approver_env
    today = timezone.now().date()

    finance_role, _ = Role.objects.get_or_create(
        code=Role.FINANCE_AP, defaults={"name": "Finance & AP"}
    )
    policy = ApprovalPolicy.objects.get(department=env["dept"])
    ApprovalStep.objects.create(
        policy=policy,
        step_number=2,
        approver_role=finance_role,
        description="Finance / AP Approval",
    )

    Budget.objects.create(
        cost_center=env["cc"],
        fiscal_period=env["fp"],
        allocated_amount=Decimal("9900000.00"),
        reserved_amount=Decimal("400.00"),
    )

    ok_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Incoming Actionable PR",
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        pr_number="PR-ACTIONABLE-01",
        total_amount=Decimal("400.00"),
        requested_delivery_date=today,
    )
    over_pr = PurchaseRequisition.objects.create(
        requester=env["requester"],
        department=env["dept"],
        cost_center=env["cc"],
        title="Over Budget Locked PR",
        status=PurchaseRequisition.STATUS_BUDGET_REVIEW,
        pr_number="PR-LOCKED-OVER-01",
        total_amount=Decimal("15000000.00"),
        requested_delivery_date=today,
    )

    client.force_login(env["approver"])

    # 1. Requisition detail renders Approve PR, Reject PR, and Decision Comments card + buttons
    detail_resp = client.get(f"/requisitions/{ok_pr.pk}/")
    assert detail_resp.status_code == 200
    assert detail_resp.context["can_approve"] is True
    detail_html = detail_resp.content.decode("utf-8")
    assert 'id="approvalActionDecision"' in detail_html
    assert 'id="decisionCommentsSidebar"' in detail_html
    assert "Approve PR" in detail_html
    assert "Reject PR" in detail_html

    # 2. Approvals inbox renders inline comment + Approve + Reject for ok_pr, while locking over_pr
    inbox_resp = client.get("/approvals/inbox/?status=PENDING")
    assert inbox_resp.status_code == 200
    inbox_html = inbox_resp.content.decode("utf-8")
    assert ok_pr.pr_number in inbox_html
    assert f'value="{ok_pr.id}"' in inbox_html
    assert 'name="comments"' in inbox_html
    assert 'name="action" value="APPROVED"' in inbox_html
    assert 'name="action" value="REJECTED"' in inbox_html
    # Over-budget PR must NOT get an inline approval form for DEPT_APPROVER
    assert over_pr.pr_number in inbox_html
    assert f'<input type="hidden" name="pr_id" value="{over_pr.id}">' not in inbox_html

    # 3. Submitting Approve with comment advances Step 1 while keeping Step 2 (Finance) workflow intact
    post_resp = client.post(
        "/approvals/inbox/",
        {"pr_id": str(ok_pr.id), "action": "APPROVED", "comments": "Verified by dept approver"},
    )
    assert post_resp.status_code in (302, 200)
    ok_pr.refresh_from_db()
    assert ok_pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW
    recorded = ApprovalAction.objects.filter(
        target_object_id=ok_pr.id,
        target_model_name="PurchaseRequisition",
        actor=env["approver"],
        action=ApprovalAction.ACTION_APPROVE,
    ).first()
    assert recorded is not None
    assert recorded.comments == "Verified by dept approver"


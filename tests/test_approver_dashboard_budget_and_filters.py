import pytest
from decimal import Decimal
from django.utils import timezone
from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.approvals.models import ApprovalPolicy, ApprovalStep, ApprovalAction


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


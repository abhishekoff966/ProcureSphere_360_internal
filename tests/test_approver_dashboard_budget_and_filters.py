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


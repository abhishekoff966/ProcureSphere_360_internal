import datetime
from decimal import Decimal

import pytest
from django.test import Client
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.approvals.models import ApprovalPolicy, ApprovalStep
from apps.contracts.models import Contract
from apps.notifications.models import Notification
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def manager_test_env(db):
    org = Organization.objects.create(name="HPE Global", code="HPE_GLB")
    dept = Department.objects.create(organization=org, name="Procurement & Sourcing", code="PROC_SRC")
    cc = CostCenter.objects.create(department=dept, code="CC_PROC_01", name="Procurement Admin")

    fp = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=1,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        is_closed=False,
    )
    cat = VendorCategory.objects.create(name="Hardware", code="HW_CAT", description="Hardware Vendor")

    r_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_dept, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    r_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})

    req_user = User.objects.create_user(
        email="req.tester@hpe.local",
        password="Password123!",
        role=r_req,
        department=dept,
        cost_center=cc,
        organization=org,
    )
    dept_user = User.objects.create_user(
        email="dept.approver@hpe.local",
        password="Password123!",
        role=r_dept,
        department=dept,
        cost_center=cc,
        organization=org,
    )
    mgr_user = User.objects.create_user(
        email="proc.mgr@hpe.local",
        password="Password123!",
        role=r_mgr,
        department=dept,
        cost_center=cc,
        organization=org,
    )
    legal_user = User.objects.create_user(
        email="legal.mgr@hpe.local",
        password="Password123!",
        role=r_legal,
        department=dept,
        cost_center=cc,
        organization=org,
    )

    vendor = Vendor.objects.create(
        organization=org,
        legal_name="Apex Hardware Corp",
        vendor_number="VND-APEX-001",
        tax_identification_number="TIN-APEX-01",
        email="sales@apex.local",
        address="100 Tech Blvd",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-TEST-MGR-001",
        title="Procurement Manager Test PR",
        justification="Verify PR approval removal for manager",
        requester=req_user,
        department=dept,
        cost_center=cc,
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        total_amount=Decimal("1500.00"),
        requested_delivery_date=datetime.date(2026, 12, 31),
    )
    PRLine.objects.create(
        requisition=pr,
        item_description="Precision Tools",
        quantity=Decimal("3"),
        estimated_unit_price=Decimal("500.00"),
        estimated_total=Decimal("1500.00"),
    )

    contract = Contract.objects.create(
        contract_number="CON-TEST-001",
        title="Master Services Agreement",
        vendor=vendor,
        status=Contract.STATUS_ACTIVE,
        contract_value=Decimal("50000.00"),
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        renewal_notice_days=30,
        contract_owner=mgr_user,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "req_user": req_user,
        "dept_user": dept_user,
        "mgr_user": mgr_user,
        "legal_user": legal_user,
        "vendor": vendor,
        "pr": pr,
        "contract": contract,
    }


@pytest.mark.django_db
def test_procurement_manager_pr_detail_view_has_no_approval_authority(manager_test_env):
    """
    Procurement Manager viewing a PR receives can_approve=False and cannot approve or reject.
    """
    client = Client()
    client.force_login(manager_test_env["mgr_user"])

    response = client.get(f"/requisitions/{manager_test_env['pr'].id}/")
    assert response.status_code == 200
    assert response.context["can_approve"] is False
    content = response.content.decode("utf-8")
    assert "Decision: Approve or Reject Requisition" not in content


@pytest.mark.django_db
def test_procurement_manager_pr_approve_and_reject_views_are_blocked(manager_test_env):
    """
    Direct POST requests by Procurement Manager to approve/reject views are blocked.
    """
    client = Client()
    client.force_login(manager_test_env["mgr_user"])
    pr = manager_test_env["pr"]

    # Attempt to approve via web endpoint
    res_approve = client.post(f"/requisitions/{pr.id}/approve/", {"comments": "Mgr attempting approval"})
    assert res_approve.status_code == 302
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW

    # Attempt to reject via web endpoint
    res_reject = client.post(f"/requisitions/{pr.id}/reject/", {"reason": "Mgr attempting rejection"})
    assert res_reject.status_code == 302
    pr.refresh_from_db()
    assert pr.status == PurchaseRequisition.STATUS_MANAGER_REVIEW


@pytest.mark.django_db
def test_procurement_manager_api_pr_approve_is_forbidden(manager_test_env):
    """
    API POST to /api/v1/requisitions/<id>/approve/ returns 403 Forbidden for Procurement Manager.
    """
    api = APIClient()
    api.force_authenticate(user=manager_test_env["mgr_user"])
    pr = manager_test_env["pr"]

    res = api.post(f"/api/v1/requisitions/{pr.id}/approve/", {"comments": "Approve via API"}, format="json")
    assert res.status_code == 403


@pytest.mark.django_db
def test_procurement_manager_manager_action_api_is_forbidden(manager_test_env):
    """
    API POST to manager-approval-action-api returns 403 with explicit oversight message.
    """
    client = Client()
    client.force_login(manager_test_env["mgr_user"])
    url = reverse("manager-approval-action-api")
    pr = manager_test_env["pr"]

    res = client.post(
        url,
        data={
            "item_type": "PR",
            "item_id": str(pr.id),
            "action": "APPROVE",
            "comments": "Attempting manager approval",
        },
        content_type="application/json",
    )
    assert res.status_code == status.HTTP_403_FORBIDDEN
    assert "approval oversight only" in res.json()["error"]


@pytest.mark.django_db
def test_contract_detail_view_preserves_manager_layout_for_procurement_manager(manager_test_env):
    """
    When Procurement Manager opens contract detail (e.g. from notification "View Action"),
    the layout rendered is 'manager/base_manager.html' instead of 'layouts/legal_base.html',
    so the manager sidebar and breadcrumbs are preserved.
    """
    client = Client()
    client.force_login(manager_test_env["mgr_user"])
    contract = manager_test_env["contract"]

    response = client.get(f"/contracts/{contract.id}/")
    assert response.status_code == 200
    assert response.context["base_layout"] == "manager/base_manager.html"
    content = response.content.decode("utf-8")
    assert "LEGAL NAVIGATION" not in content
    assert "Procurement Manager Workspace" in content or "manager/base_manager.html" in [t.name for t in response.templates]
    assert "Apex Hardware Corp (VND-APEX-001)" in content
    assert "Apex Hardware Corp ()" not in content


@pytest.mark.django_db
def test_contract_detail_view_preserves_legal_layout_for_legal_manager(manager_test_env):
    """
    When Legal Manager opens contract detail, the layout remains 'layouts/legal_base.html'.
    """
    client = Client()
    client.force_login(manager_test_env["legal_user"])
    contract = manager_test_env["contract"]

    response = client.get(f"/contracts/{contract.id}/")
    assert response.status_code == 200
    assert response.context["base_layout"] == "layouts/legal_base.html"
    content = response.content.decode("utf-8")
    assert "LEGAL NAVIGATION" in content


@pytest.mark.django_db
def test_notification_view_action_points_to_contract_without_sidebar_hijacking(manager_test_env):
    """
    A notification for contract renewal has target_url=/contracts/<uuid>/, which
    renders within the manager workspace for a Procurement Manager.
    """
    mgr = manager_test_env["mgr_user"]
    contract = manager_test_env["contract"]

    notif = Notification.objects.create(
        recipient=mgr,
        notification_type=Notification.TYPE_CONTRACT_EXPIRATION,
        title=f"Renewal Notice Due: {contract.contract_number}",
        message=f"Contract {contract.contract_number} ({contract.title}) renewal notice window has arrived.",
        target_url=f"/contracts/{contract.id}/",
    )

    client = Client()
    client.force_login(mgr)
    response = client.get(notif.target_url)
    assert response.status_code == 200
    assert response.context["base_layout"] == "manager/base_manager.html"
    content = response.content.decode("utf-8")
    assert "LEGAL NAVIGATION" not in content


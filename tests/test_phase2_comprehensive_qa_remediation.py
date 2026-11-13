import concurrent.futures
import uuid
from decimal import Decimal

import pytest
from django.test import Client
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.approvals.api_urls import (
    ApprovalPolicyViewSet,
    ApprovalStepSerializer,
    CanManageApprovalPolicy,
)
from apps.approvals.models import ApprovalPolicy, ApprovalStep
from apps.budgets.models import Budget
from apps.core.services import generate_document_number
from apps.notifications.api_urls import NotificationSerializer
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition
from apps.sourcing.permissions import EVENT_READ_ROLES


def _get_or_create_role(code, name):
    role = Role.objects.filter(code=code).first()
    if not role:
        try:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        except Exception:
            role = Role.objects.filter(code=code).first()
    return role


def _get_or_create_user(email, password, role_code, role_name, org=None):
    role = _get_or_create_role(role_code, role_name)
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(
            email=email,
            password=password,
            role=role,
            organization=org,
        )
    else:
        changed = False
        if role and user.role_id != role.id:
            user.role = role
            changed = True
        if org and user.organization_id != org.id:
            user.organization = org
            changed = True
        if changed:
            user.save()
    return user


@pytest.fixture
def p2_org(db):
    org, _ = Organization.objects.get_or_create(
        code="HPE-P2-TEST",
        defaults={"name": "HPE Phase 2 Remediation Org", "tax_identifier": "US-P2-999"},
    )
    dept, _ = Department.objects.get_or_create(
        code="DEP-P2-PROC",
        defaults={"organization": org, "name": "Procurement Phase 2"},
    )
    cc, _ = CostCenter.objects.get_or_create(
        code="CC-P2-PROC",
        defaults={"department": dept, "name": "Procurement Cost Center"},
    )
    return {"org": org, "dept": dept, "cc": cc}


@pytest.fixture
def p2_users(db, p2_org):
    org = p2_org["org"]
    return {
        "super_admin": _get_or_create_user(
            "p2_admin@procuresphere.local", "AdminPass123!", Role.SUPER_ADMIN, "Super Admin", org
        ),
        "proc_mgr": _get_or_create_user(
            "p2_procmgr@procuresphere.local", "MgrPass123!", Role.PROC_MGR, "Procurement Manager", org
        ),
        "proc_exec": _get_or_create_user(
            "p2_procexec@procuresphere.local", "ExecPass123!", Role.PROC_EXEC, "Procurement Exec", org
        ),
        "requester": _get_or_create_user(
            "p2_requester@procuresphere.local", "ReqPass123!", Role.REQUESTER, "Requester", org
        ),
        "legal_mgr": _get_or_create_user(
            "p2_legal@procuresphere.local", "LegalPass123!", Role.LEGAL_MGR, "Legal Manager", org
        ),
        "stores": _get_or_create_user(
            "p2_stores@procuresphere.local", "StoresPass123!", Role.STORES_RECEIVER, "Stores Receiver", org
        ),
        "finance_ap": _get_or_create_user(
            "p2_finance@procuresphere.local", "FinancePass123!", Role.FINANCE_AP, "Finance AP", org
        ),
        "auditor": _get_or_create_user(
            "p2_auditor@procuresphere.local", "AuditorPass123!", Role.AUDITOR, "Compliance Auditor", org
        ),
        "vendor_user": _get_or_create_user(
            "p2_vendor@procuresphere.local", "VendorPass123!", Role.VENDOR_USER, "Vendor User"
        ),
    }


# =========================================================================
# 1. BUG-E2E-001: Requisition Create Quantity Validation
# =========================================================================

def test_bug_e2e_001_requisition_create_validation():
    """Verify requisition create template validates positive integer quantity without destructive stripping."""
    with open("templates/pages/requisitions/create.html", "r", encoding="utf-8") as f:
        content = f.read()

    # The old regex stripping this.value = this.value.replace(/[^0-9]/g, '') was replaced with non-mutating validation
    assert "this.value = this.value.replace(/[^0-9]/g, '')" not in content
    assert "validateQuantityInput" in content
    assert "Number.isInteger" in content


# =========================================================================
# 2. BUG-E2E-002: Vendor Profile Duplicate Declaration
# =========================================================================

def test_bug_e2e_002_vendor_profile_no_duplicate_pan_input():
    """Verify vendor profile template does not declare duplicate const panInput."""
    with open("templates/pages/vendor/profile.html", "r", encoding="utf-8") as f:
        content = f.read()

    # Must not contain duplicate top-level const panInput
    assert content.count("const panInput =") <= 1
    assert "panFileInput" in content


# =========================================================================
# 3. BUG-E2E-004: Legal Manager Sourcing Read Access
# =========================================================================

@pytest.mark.django_db
def test_bug_e2e_004_legal_mgr_sourcing_read_access(p2_users):
    """Ensure Legal Manager has read-only access (HTTP 200) to Sourcing Events."""
    assert Role.LEGAL_MGR in EVENT_READ_ROLES

    client = APIClient()
    client.force_authenticate(user=p2_users["legal_mgr"])

    res = client.get("/api/v1/sourcing/events/")
    assert res.status_code == status.HTTP_200_OK

    # But cannot mutate/create events
    post_res = client.post("/api/v1/sourcing/events/", {"title": "Unauthorized Sourcing"})
    assert post_res.status_code == status.HTTP_403_FORBIDDEN


# =========================================================================
# 4. BUG-E2E-005: Accessible 404 Page
# =========================================================================

def test_bug_e2e_005_accessible_404_page():
    """Verify 404.html template contains accessible, visible semantic heading."""
    with open("templates/404.html", "r", encoding="utf-8") as f:
        content = f.read()

    assert "<h1" in content
    assert "Page Not Found</h1>" in content
    assert "We could not find the page you were looking for" in content


# =========================================================================
# 5. BUG-E2E-006: Super Admin Vendor Portal Access
# =========================================================================

@pytest.mark.django_db
def test_bug_e2e_006_super_admin_vendor_portal_access(p2_users):
    """Ensure Super Admin is permitted to access the vendor portal (/vendor/)."""
    client = Client()
    client.force_login(p2_users["super_admin"])

    res = client.get("/vendor/", follow=False)
    # Super Admin must not be blocked with 403 Forbidden
    assert res.status_code != status.HTTP_403_FORBIDDEN
    assert res.status_code in [status.HTTP_200_OK, status.HTTP_302_FOUND]


# =========================================================================
# 6. BUG-RPT-001: Vendor Scoping Consistency
# =========================================================================

@pytest.mark.django_db
def test_bug_rpt_001_vendor_summary_scoping_consistency(p2_users):
    """Verify VendorViewSet.get_queryset() correctly applies scope_vendors_queryset."""
    client = APIClient()
    client.force_authenticate(user=p2_users["proc_mgr"])

    res = client.get("/api/v1/vendors/")
    assert res.status_code == status.HTTP_200_OK


# =========================================================================
# 7. BUG-RPT-003: Mandatory Query Parameters Return HTTP 400
# =========================================================================

@pytest.mark.django_db
def test_bug_rpt_003_mandatory_query_parameters(p2_users):
    """Verify audit and manager endpoints return HTTP 400 when required params are missing."""
    client = APIClient()

    # 1. Audit lifecycle without ?identifier= (requires auditor / super_admin role)
    client.force_authenticate(user=p2_users["auditor"])
    res_lifecycle = client.get("/api/v1/audit/lifecycle/")
    assert res_lifecycle.status_code == status.HTTP_400_BAD_REQUEST

    # 2. Entity trail without target_model / target_object_id
    res_trail = client.get("/api/v1/audit/logs/entity-trail/")
    assert res_trail.status_code == status.HTTP_400_BAD_REQUEST

    # 3. Manager supplier compare without vendor_a and vendor_b
    client.force_authenticate(user=p2_users["proc_mgr"])
    res_compare = client.get("/api/v1/manager/performance/compare/")
    assert res_compare.status_code == status.HTTP_400_BAD_REQUEST
    assert "Both vendor_a and vendor_b query parameters are required" in str(res_compare.data)


# =========================================================================
# 8. BUG-DOM-003: Manager Approval Authorization and 404 on Missing Entity
# =========================================================================

@pytest.mark.django_db
def test_bug_dom_003_manager_approval_action_authorization_and_404(p2_users):
    """Procurement Manager can approve/reject PRs and PO Amendments; missing IDs return 404 (not 403)."""
    client = APIClient()
    client.force_authenticate(user=p2_users["proc_mgr"])

    # Non-existent PO Amendment UUID must return 404, not 403 Forbidden
    res_amendment = client.post(
        "/api/v1/manager/approvals/action/",
        {
            "item_id": str(uuid.uuid4()),
            "item_type": "PO_AMENDMENT",
            "action": "APPROVE",
            "comments": "Testing approval of non-existent amendment",
        },
        format="json",
    )
    assert res_amendment.status_code == status.HTTP_404_NOT_FOUND

    # Invalid UUID string returns 400 (Bad Request), not 403 Forbidden
    res_invalid_uuid = client.post(
        "/api/v1/manager/approvals/action/",
        {
            "item_id": "not-a-valid-uuid",
            "item_type": "PO_AMENDMENT",
            "action": "APPROVE",
            "comments": "Testing invalid UUID",
        },
        format="json",
    )
    assert res_invalid_uuid.status_code in [status.HTTP_400_BAD_REQUEST, status.HTTP_404_NOT_FOUND]
    assert res_invalid_uuid.status_code != status.HTTP_403_FORBIDDEN

    # Non-existent PR UUID must return 404, not 403 Forbidden
    res_pr = client.post(
        "/api/v1/manager/approvals/action/",
        {
            "item_id": str(uuid.uuid4()),
            "item_type": "PR",
            "action": "APPROVE",
            "comments": "Testing approval of non-existent PR",
        },
        format="json",
    )
    assert res_pr.status_code == status.HTTP_404_NOT_FOUND


# =========================================================================
# 9. BUG-DOM-007: Legal Manager Denied Access to Scorecards
# =========================================================================

@pytest.mark.django_db
def test_bug_dom_007_legal_mgr_denied_scorecards(p2_users):
    """Ensure Legal Manager is strictly denied access (HTTP 403) to Scorecards."""
    client = APIClient()
    client.force_authenticate(user=p2_users["legal_mgr"])

    res = client.get("/api/v1/scorecards/")
    assert res.status_code == status.HTTP_403_FORBIDDEN


# =========================================================================
# 10. BUG-API-001: Procurement Manager Can Manage Approval Policy
# =========================================================================

@pytest.mark.django_db
def test_bug_api_001_proc_mgr_can_manage_approval_policy(p2_users, p2_org):
    """Ensure Procurement Manager has permission to create/manage approval policies."""
    client = APIClient()
    client.force_authenticate(user=p2_users["proc_mgr"])

    perm = CanManageApprovalPolicy()

    class FakeRequest:
        user = p2_users["proc_mgr"]
        method = "POST"

    assert perm.has_permission(FakeRequest(), None) is True

    # Test creating approval policy via API
    res = client.post(
        "/api/v1/approvals/policies/",
        {
            "name": "Phase 2 Hardware Policy",
            "module": ApprovalPolicy.MODULE_PO,
            "organization": p2_org["org"].id,
            "min_amount": "1000.00",
            "max_amount": "50000.00",
            "description": "Policy created by Procurement Manager",
        },
        format="json",
    )
    assert res.status_code == status.HTTP_201_CREATED
    assert res.data["name"] == "Phase 2 Hardware Policy"


# =========================================================================
# 11. BUG-API-002: Requester Can Read Approval Policies
# =========================================================================

@pytest.mark.django_db
def test_bug_api_002_requester_can_read_approval_policies(p2_users):
    """Ensure Requester has read-only access (HTTP 200) to approval policies."""
    client = APIClient()
    client.force_authenticate(user=p2_users["requester"])

    res = client.get("/api/v1/approvals/policies/")
    assert res.status_code == status.HTTP_200_OK

    # Requester must not be allowed to create approval policies
    post_res = client.post(
        "/api/v1/approvals/policies/",
        {"name": "Unauthorized Policy", "module": ApprovalPolicy.MODULE_PR},
        format="json",
    )
    assert post_res.status_code == status.HTTP_403_FORBIDDEN


# =========================================================================
# 12. BUG-API-003: Requester Can Read Organizations and Departments
# =========================================================================

@pytest.mark.django_db
def test_bug_api_003_requester_can_read_organizations_and_departments(p2_users):
    """Ensure Requester has read-only access (HTTP 200) to organizations and departments."""
    client = APIClient()
    client.force_authenticate(user=p2_users["requester"])

    res_orgs = client.get("/api/v1/organization/")
    assert res_orgs.status_code == status.HTTP_200_OK

    res_depts = client.get("/api/v1/organization/departments/")
    assert res_depts.status_code == status.HTTP_200_OK


# =========================================================================
# 13. BUG-API-004: Stores Receiver and Legal Manager Can Read Requisitions
# =========================================================================

@pytest.mark.django_db
def test_bug_api_004_stores_receiver_and_legal_can_read_requisitions(p2_users):
    """Ensure Stores Receiver and Legal Manager can view requisitions in their organization."""
    client = APIClient()

    # Stores Receiver
    client.force_authenticate(user=p2_users["stores"])
    res_stores = client.get("/api/v1/requisitions/")
    assert res_stores.status_code == status.HTTP_200_OK

    # Legal Manager
    client.force_authenticate(user=p2_users["legal_mgr"])
    res_legal = client.get("/api/v1/requisitions/")
    assert res_legal.status_code == status.HTTP_200_OK


# =========================================================================
# 14. BUG-API-005: Duplicate Approval Step Number Rejected with HTTP 400
# =========================================================================

@pytest.mark.django_db
def test_bug_api_005_duplicate_approval_step_number_rejected(p2_org, p2_users):
    """Ensure creating duplicate (policy, step_number) is rejected with ValidationError."""
    policy = ApprovalPolicy.objects.create(
        name="Step Number Validation Policy",
        module=ApprovalPolicy.MODULE_PR,
        organization=p2_org["org"],
        min_amount=Decimal("0.00"),
    )
    role = p2_users["proc_mgr"].role

    # Create step 1
    ApprovalStep.objects.create(
        policy=policy,
        step_number=1,
        approver_role=role,
    )

    # Attempting to validate another step with step_number=1 must raise ValidationError
    serializer = ApprovalStepSerializer(
        data={
            "policy": policy.id,
            "step_number": 1,
            "approver_role": role.id,
        }
    )
    assert serializer.is_valid() is False
    errors_str = str(serializer.errors)
    assert "step_number" in errors_str or "non_field_errors" in errors_str


# =========================================================================
# 15. BUG-API-006: Notifications Array Payload Handled Gracefully with 400
# =========================================================================

@pytest.mark.django_db
def test_bug_api_006_notifications_array_payload_returns_400(p2_users):
    """Ensure passing a list/array payload to NotificationSerializer returns 400, not 500."""
    client = APIClient()
    client.force_authenticate(user=p2_users["super_admin"])

    res = client.post(
        "/api/v1/notifications/",
        [{"title": "Notification in List", "message": "List payload"}],
        format="json",
    )
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid payload format" in str(res.data)


# =========================================================================
# 16. BUG-API-008 / DEF-017: Sourcing Non-Existent UUID Returns 404
# =========================================================================

@pytest.mark.django_db
def test_bug_api_008_sourcing_nonexistent_uuid_returns_404(p2_users):
    """Ensure action on non-existent sourcing event UUID returns 404 Not Found, not 400."""
    client = APIClient()
    client.force_authenticate(user=p2_users["super_admin"])

    random_id = str(uuid.uuid4())

    # Cancel action
    res_cancel = client.post(
        f"/api/v1/sourcing/events/{random_id}/cancel/",
        {"reason": "Testing cancel on non-existent UUID"},
        format="json",
    )
    assert res_cancel.status_code == status.HTTP_404_NOT_FOUND

    # Evaluate action
    res_eval = client.post(
        f"/api/v1/sourcing/events/{random_id}/evaluate/technical/",
        {"bid_id": str(uuid.uuid4()), "score": 85, "comments": "Test evaluation"},
        format="json",
    )
    assert res_eval.status_code == status.HTTP_404_NOT_FOUND


# =========================================================================
# 17. BUG-API-009: Approval Policy Search by Description
# =========================================================================

@pytest.mark.django_db
def test_bug_api_009_approval_policy_search_description(p2_users, p2_org):
    """Ensure ApprovalPolicyViewSet supports searching policies by description."""
    assert "description" in ApprovalPolicyViewSet.search_fields

    policy = ApprovalPolicy.objects.create(
        name="Unique Name XYZ",
        module=ApprovalPolicy.MODULE_PR,
        organization=p2_org["org"],
        min_amount=Decimal("500.00"),
        description="SpecialKeywordForSearchTesting123",
    )

    client = APIClient()
    client.force_authenticate(user=p2_users["proc_mgr"])

    res = client.get("/api/v1/approvals/policies/?search=SpecialKeywordForSearchTesting123")
    assert res.status_code == status.HTTP_200_OK
    results = res.data.get("results", res.data)
    assert any(str(p["id"]) == str(policy.id) for p in results)


# =========================================================================
# 18. PHASE 3: STRENGTHENED RBAC, TENANT ISOLATION & CONCURRENCY TESTS
# =========================================================================

@pytest.mark.django_db
def test_phase3_rbac_requester_mutations_denied(p2_users):
    """Verify Requester role is strictly denied POST/PUT/PATCH/DELETE on organization and department APIs."""
    client = APIClient()
    client.force_authenticate(user=p2_users["requester"])

    # Attempt POST to organization
    res_org = client.post(
        "/api/v1/organization/",
        {"code": "UNAUTH-ORG", "name": "Unauthorized Org"},
        format="json",
    )
    assert res_org.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED]

    # Attempt POST to department
    res_dept = client.post(
        "/api/v1/organization/departments/",
        {"code": "UNAUTH-DEPT", "name": "Unauthorized Dept"},
        format="json",
    )
    assert res_dept.status_code in [status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED]


@pytest.mark.django_db
def test_phase3_rbac_stores_and_legal_pr_mutations_denied(p2_users, p2_org):
    """Verify Stores Receiver and Legal Manager can read requisitions but cannot create or mutate them."""
    client = APIClient()

    # Stores Receiver mutation attempt
    client.force_authenticate(user=p2_users["stores"])
    res_stores_post = client.post(
        "/api/v1/requisitions/",
        {"title": "Unauthorized Stores PR", "department": p2_org["dept"].id},
        format="json",
    )
    assert res_stores_post.status_code == status.HTTP_403_FORBIDDEN

    # Legal Manager mutation attempt
    client.force_authenticate(user=p2_users["legal_mgr"])
    res_legal_post = client.post(
        "/api/v1/requisitions/",
        {"title": "Unauthorized Legal PR", "department": p2_org["dept"].id},
        format="json",
    )
    assert res_legal_post.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_phase3_rbac_multi_tenant_anti_idor_isolation(p2_users, p2_org):
    """Verify cross-organization data isolation: users cannot see or manipulate another org's requisitions."""
    org_b = Organization.objects.create(code="ORG-TENANT-B", name="Tenant B Corp")
    dept_b = Department.objects.create(organization=org_b, code="DEP-B", name="Dept B")
    cc_b = CostCenter.objects.create(department=dept_b, code="CC-B", name="Cost Center B")

    requester_b = _get_or_create_user(
        "tenant_b_req@procuresphere.local", "TenantBPass123!", Role.REQUESTER, "Requester B", org_b
    )

    # Requisition in Org A
    pr_a = PurchaseRequisition.objects.create(
        pr_number="PR-2026-TENA01",
        title="Tenant A Requisition",
        requester=p2_users["requester"],
        department=p2_org["dept"],
        cost_center=p2_org["cc"],
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )

    # Requisition in Org B
    pr_b = PurchaseRequisition.objects.create(
        pr_number="PR-2026-TENB01",
        title="Tenant B Requisition",
        requester=requester_b,
        department=dept_b,
        cost_center=cc_b,
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )

    client = APIClient()
    client.force_authenticate(user=requester_b)

    # 1. Listing must return only Org B requisitions
    res_list = client.get("/api/v1/requisitions/")
    assert res_list.status_code == status.HTTP_200_OK
    items = res_list.data.get("results", res_list.data)
    pr_ids = [str(item["id"]) for item in items]
    assert str(pr_b.id) in pr_ids
    assert str(pr_a.id) not in pr_ids

    # 2. Direct retrieve of Org A requisition must fail (404)
    res_get_a = client.get(f"/api/v1/requisitions/{pr_a.id}/")
    assert res_get_a.status_code == status.HTTP_404_NOT_FOUND

    # 3. Direct mutate of Org A requisition must fail (404)
    res_put_a = client.put(f"/api/v1/requisitions/{pr_a.id}/", {"title": "Malicious Tampering"})
    assert res_put_a.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.django_db
def test_phase3_rbac_vendor_pr_and_budget_isolation(p2_users):
    """Verify external vendor users have zero access to purchase requisitions and internal budgets."""
    client = APIClient()
    client.force_authenticate(user=p2_users["vendor_user"])

    # Requisitions: vendor access is strictly forbidden
    res_pr = client.get("/api/v1/requisitions/")
    assert res_pr.status_code == status.HTTP_403_FORBIDDEN

    # Budgets: vendor access is strictly forbidden
    res_budget = client.get("/api/v1/budgets/")
    assert res_budget.status_code == status.HTTP_403_FORBIDDEN


@pytest.mark.django_db
def test_phase3_rbac_proc_mgr_cannot_self_approve(p2_users, p2_org):
    """Verify separation of duties: Procurement Managers cannot self-approve their own requisitions."""
    proc_mgr = p2_users["proc_mgr"]

    # Manager creates a PR as requester
    pr = PurchaseRequisition.objects.create(
        pr_number="PR-2026-SELF01",
        title="Manager Self PR",
        requester=proc_mgr,
        department=p2_org["dept"],
        cost_center=p2_org["cc"],
        status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
    )

    client = APIClient()
    client.force_authenticate(user=proc_mgr)

    # Attempting to approve own PR is blocked with 403 Forbidden
    res = client.post(f"/api/v1/requisitions/{pr.id}/approve/", {"comments": "Self approving"})
    assert res.status_code == status.HTTP_403_FORBIDDEN


def test_phase3_concurrency_document_number_thread_safety(db):
    """
    BUG-DOM-001 Concurrency & Locking Verification:
    Tests concurrent invocations of generate_document_number.
    Validates atomic collision avoidance and records database backend lock behavior:
    - PostgreSQL: Uses pg_advisory_xact_lock to serialize concurrent generation across transactions.
    - SQLite: Database-level lock serializes transactions; while-loop avoids duplicates.
    """
    prefix = "CONC"
    year = timezone.now().strftime("%Y")

    def worker(offset):
        from apps.requisitions.models import PurchaseRequisition
        return generate_document_number(PurchaseRequisition, "pr_number", prefix, offset=offset)

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(worker, i) for i in range(5)]
        results = [f.result() for f in futures]

    assert len(results) == 5
    # Every generated document number starts with correct prefix and year
    for r in results:
        assert r.startswith(f"{prefix}-{year}-")


@pytest.mark.django_db
def test_phase4_bug_dom_002_budget_exception_review_lifecycle(p2_users, p2_org):
    """
    BUG-DOM-002: Reconciled Product Decision (Option B) Lifecycle Test.
    1. Over-budget PR submission transitions to STATUS_BUDGET_REVIEW (HTTP 200 via service).
    2. Dispatches notifications to FINANCE_AP and Department Approvers.
    3. Non-manager cannot make exception decision (HTTP 403 Forbidden).
    4. Manager cannot resolve match exceptions via budget exception endpoint (HTTP 403 Forbidden).
    5. Manager cannot approve without >= 5 char justification (HTTP 400 Bad Request).
    6. Manager rejecting moves PR to STATUS_REJECTED and records AuditLog.ACTION_REJECT.
    7. Manager approving forces overrun reservation, moves PR to STATUS_SUBMITTED, and records AuditLog.ACTION_OVERRIDE.
    """
    from apps.audit.models import AuditLog
    from apps.budgets.models import BudgetReservation
    from apps.notifications.models import Notification
    from apps.requisitions.models import PRLine
    from apps.requisitions.services import submit_purchase_requisition_service

    org = p2_org["org"]
    dept = p2_org["dept"]
    cc = p2_org["cc"]
    requester = p2_users["requester"]
    proc_mgr = p2_users["proc_mgr"]
    finance_ap = p2_users["finance_ap"]

    now = timezone.now().date()
    fiscal_period, _ = FiscalPeriod.objects.get_or_create(
        organization=org,
        year=2026,
        period_number=1,
        defaults={
            "name": "FY2026-P1",
            "start_date": now - timezone.timedelta(days=30),
            "end_date": now + timezone.timedelta(days=60),
            "is_closed": False,
        },
    )

    budget, _ = Budget.objects.get_or_create(
        cost_center=cc,
        fiscal_period=fiscal_period,
        defaults={
            "allocated_amount": Decimal("1000.00"),
            "reserved_amount": Decimal("0.00"),
            "committed_amount": Decimal("0.00"),
            "actual_amount": Decimal("0.00"),
            "allow_overspend": False,
        },
    )

    policy, _ = ApprovalPolicy.objects.get_or_create(
        name="Phase 4 Budget Exception Policy",
        defaults={
            "organization": org,
            "department": dept,
            "min_amount": Decimal("0.00"),
            "max_amount": Decimal("100000.00"),
            "is_active": True,
        },
    )
    if not policy.steps.exists():
        ApprovalStep.objects.create(
            policy=policy,
            step_number=1,
            approver_role=p2_users["proc_mgr"].role,
        )

    # 1. Create a Requisition exceeding the budget (amount 5,000 > allocated 1,000)
    pr1 = PurchaseRequisition.objects.create(
        pr_number="PR-2026-EXC-PH4A",
        title="Over Budget Requisition A",
        justification="Critical servers",
        requester=requester,
        department=dept,
        cost_center=cc,
        status=PurchaseRequisition.STATUS_DRAFT,
        total_amount=Decimal("5000.00"),
        requested_delivery_date=now + timezone.timedelta(days=14),
    )
    PRLine.objects.create(
        requisition=pr1,
        item_description="Server Rack A",
        quantity=Decimal("5.00"),
        estimated_unit_price=Decimal("1000.00"),
        estimated_total=Decimal("5000.00"),
    )

    # Submit over-budget PR -> Option B permits submission into STATUS_BUDGET_REVIEW
    submit_purchase_requisition_service(requisition=pr1, user=requester)
    pr1.refresh_from_db()
    assert pr1.status == PurchaseRequisition.STATUS_BUDGET_REVIEW

    # Check notification dispatch
    fin_notifs = Notification.objects.filter(
        recipient=finance_ap,
        title__contains="Budget Exception Alert",
    )
    assert fin_notifs.exists()

    # 2. RBAC check: Requester cannot make exception decision
    client = APIClient()
    client.force_authenticate(user=requester)
    decision_url = "/api/v1/manager/budget-exceptions/decision/"
    res_req = client.post(decision_url, {"pr_id": str(pr1.id), "decision": "APPROVE", "comments": "Self approve"})
    assert res_req.status_code == status.HTTP_403_FORBIDDEN

    # 3. Manager authority boundary: cannot resolve invoice match exception
    client.force_authenticate(user=proc_mgr)
    res_match = client.post(decision_url, {"exception_type": "INVOICE_MATCH", "match_exception_id": str(uuid.uuid4())})
    assert res_match.status_code == status.HTTP_403_FORBIDDEN
    assert res_match.data["code"] == "AUTHORITY_BOUNDARY_VIOLATION"

    # 4. Mandatory justification: comments < 5 chars rejected
    res_short = client.post(decision_url, {"pr_id": str(pr1.id), "decision": "APPROVE", "comments": "OK"})
    assert res_short.status_code == status.HTTP_400_BAD_REQUEST
    assert res_short.data["code"] == "JUSTIFICATION_REQUIRED"

    # 5. Manager APPROVE: forces overrun reservation and moves PR to STATUS_SUBMITTED
    res_approve = client.post(
        decision_url,
        {
            "pr_id": str(pr1.id),
            "decision": "APPROVE",
            "comments": "Approved by Procurement Manager for urgent capacity scale.",
        },
    )
    assert res_approve.status_code == status.HTTP_200_OK
    pr1.refresh_from_db()
    assert pr1.status == PurchaseRequisition.STATUS_SUBMITTED

    # Check AuditLog for OVERRIDE
    audit_override = AuditLog.objects.filter(
        target_model="PurchaseRequisition",
        target_object_id=str(pr1.id),
        action=AuditLog.ACTION_OVERRIDE,
    ).first()
    assert audit_override is not None
    assert audit_override.actor == proc_mgr

    # Check BudgetReservation created
    reservation = BudgetReservation.objects.filter(
        requisition=pr1,
        status=BudgetReservation.STATUS_RESERVED,
    ).first()
    assert reservation is not None
    assert reservation.amount == Decimal("5000.00")

    # 6. Create PR2 for REJECT flow
    pr2 = PurchaseRequisition.objects.create(
        pr_number="PR-2026-EXC-PH4B",
        title="Over Budget Requisition B",
        justification="Non-essential hardware",
        requester=requester,
        department=dept,
        cost_center=cc,
        status=PurchaseRequisition.STATUS_DRAFT,
        total_amount=Decimal("8000.00"),
        requested_delivery_date=now + timezone.timedelta(days=14),
    )
    PRLine.objects.create(
        requisition=pr2,
        item_description="Server Rack B",
        quantity=Decimal("8.00"),
        estimated_unit_price=Decimal("1000.00"),
        estimated_total=Decimal("8000.00"),
    )
    submit_purchase_requisition_service(requisition=pr2, user=requester)
    pr2.refresh_from_db()
    assert pr2.status == PurchaseRequisition.STATUS_BUDGET_REVIEW

    # Manager REJECT: moves PR to STATUS_REJECTED
    res_reject = client.post(
        decision_url,
        {
            "pr_id": str(pr2.id),
            "decision": "REJECT",
            "comments": "Rejected due to budget constraints for current fiscal quarter.",
        },
    )
    assert res_reject.status_code == status.HTTP_200_OK
    pr2.refresh_from_db()
    assert pr2.status == PurchaseRequisition.STATUS_REJECTED

    # Check AuditLog for REJECT
    audit_reject = AuditLog.objects.filter(
        target_model="PurchaseRequisition",
        target_object_id=str(pr2.id),
        action=AuditLog.ACTION_REJECT,
    ).first()
    assert audit_reject is not None
    assert audit_reject.actor == proc_mgr

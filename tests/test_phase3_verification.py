import uuid
from decimal import Decimal
from datetime import timedelta
import pytest
from django.utils import timezone
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import RequestFactory
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.vendors.models import Vendor, VendorCategory, VendorDocument
from apps.orders.models import PurchaseOrder, POLine
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.approvals.models import ApprovalAction, ApprovalPolicy, ApprovalStep
from apps.audit.models import AuditLog
from apps.audit.selectors import get_security_events_audit, get_scoped_audit_logs_queryset
from apps.audit.services import create_audit_log_service
from apps.core.middleware import (
    get_client_ip,
    get_current_request_id,
    set_client_ip,
    set_current_request_id,
    clear_request_context,
)
from apps.organization.services import create_organization_service, create_organization_with_admin_service
from apps.vendors.services import approve_vendor_service, set_vendor_status_governance_service
from apps.requisitions.services import submit_purchase_requisition_service, create_purchase_requisition_service
from apps.orders.services import (
    generate_purchase_order_service,
    submit_purchase_order_for_approval_service,
    approve_purchase_order_service,
)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def phase3_env(db):
    clear_request_context()

    # Roles
    r_super, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_proc, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_dept, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    r_aud, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})

    # Organizations
    org_a = Organization.objects.create(name="Alpha Corp", code="ALPHA", tax_identifier="TAX-ALPHA-01")
    org_b = Organization.objects.create(name="Beta Corp", code="BETA", tax_identifier="TAX-BETA-02")

    dept_a = Department.objects.create(name="Procurement A", code="DP-A", organization=org_a)
    dept_b = Department.objects.create(name="Procurement B", code="DP-B", organization=org_b)

    cc_a = CostCenter.objects.create(name="Cost Center Alpha", code="CC-ALPHA", department=dept_a)
    cc_b = CostCenter.objects.create(name="Cost Center Beta", code="CC-BETA", department=dept_b)

    year = timezone.now().year
    fp_a, _ = FiscalPeriod.objects.get_or_create(
        organization=org_a,
        year=year,
        period_number=1,
        defaults={"name": f"FY{year}-P1", "start_date": f"{year}-01-01", "end_date": f"{year}-12-31", "is_closed": False},
    )
    Budget.objects.get_or_create(
        cost_center=cc_a,
        fiscal_period=fp_a,
        defaults={"allocated_amount": Decimal("1000000.00"), "committed_amount": Decimal("0.00"), "allow_overspend": True},
    )

    # Users
    admin_user = User.objects.create_user(
        email="superadmin@procure.com",
        password="SuperPassword123!",
        role=r_super,
        is_superuser=True,
        is_staff=True,
    )
    proc_mgr_a = User.objects.create_user(
        email="procmgr.a@alpha.com",
        password="ProcMgrPass123!",
        role=r_proc,
        organization=org_a,
        department=dept_a,
    )
    requester_a = User.objects.create_user(
        email="requester.a@alpha.com",
        password="RequesterPass123!",
        role=r_req,
        organization=org_a,
        department=dept_a,
    )
    dept_app_a = User.objects.create_user(
        email="deptapp.a@alpha.com",
        password="DeptAppPass123!",
        role=r_dept,
        organization=org_a,
        department=dept_a,
    )
    auditor_a = User.objects.create_user(
        email="auditor.a@alpha.com",
        password="AuditorPass123!",
        role=r_aud,
        organization=org_a,
    )

    proc_mgr_b = User.objects.create_user(
        email="procmgr.b@beta.com",
        password="ProcMgrPassB123!",
        role=r_proc,
        organization=org_b,
        department=dept_b,
    )

    # Vendor Category & Vendor for Org A
    cat_a, _ = VendorCategory.objects.get_or_create(name="IT Services", code="IT-SRV")
    vendor_a = Vendor.objects.create(
        legal_name="Alpha Vendor Services Ltd",
        trade_name="AlphaTech",
        vendor_number="VEND-ALPHA-01",
        pan_number="ABCDE1234F",
        gstin="27ABCDE1234F1Z5",
        status=Vendor.STATUS_SUBMITTED,
        organization=org_a,
        category=cat_a,
    )

    # Approval policy for Org A PRs
    policy_pr, _ = ApprovalPolicy.objects.get_or_create(
        name="Alpha PR Policy",
        module=ApprovalPolicy.MODULE_PR,
        organization=org_a,
        department=dept_a,
        defaults={"min_amount": Decimal("0.00"), "max_amount": Decimal("500000.00"), "status": ApprovalPolicy.STATUS_ACTIVE},
    )
    ApprovalStep.objects.get_or_create(
        policy=policy_pr,
        step_number=1,
        defaults={"approver_role": r_dept, "is_mandatory": True},
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "dept_a": dept_a,
        "dept_b": dept_b,
        "cc_a": cc_a,
        "cc_b": cc_b,
        "admin_user": admin_user,
        "proc_mgr_a": proc_mgr_a,
        "proc_mgr_b": proc_mgr_b,
        "requester_a": requester_a,
        "dept_app_a": dept_app_a,
        "auditor_a": auditor_a,
        "vendor_a": vendor_a,
    }


# =========================================================================
# 1. DEF-003: Organization Creation Audit
# =========================================================================
class TestDef003OrganizationCreationAudit:
    def test_successful_org_creation_generates_one_action_create_audit(self, phase3_env):
        actor = phase3_env["admin_user"]
        org = create_organization_service(
            name="New Enterprise Org",
            code="NEW-ENT",
            tax_identifier="TAX-ENT-99",
            actor=actor,
        )
        logs = AuditLog.objects.filter(target_model="Organization", target_object_id=str(org.id))
        assert logs.count() == 1
        log = logs.first()
        assert log.action == AuditLog.ACTION_CREATE
        assert log.actor == actor
        assert log.new_state["code"] == "NEW-ENT"

    def test_failed_org_creation_does_not_generate_false_audit(self, phase3_env):
        initial_count = AuditLog.objects.filter(target_model="Organization").count()
        with pytest.raises(ValidationError):
            create_organization_service(
                name="",  # Invalid name
                code="BAD",
            )
        assert AuditLog.objects.filter(target_model="Organization").count() == initial_count

    def test_api_org_creation_audit_uses_create_audit_service(self, phase3_env):
        client = APIClient()
        client.force_authenticate(user=phase3_env["admin_user"])
        res = client.post(
            "/api/v1/organization/entities/",
            {"name": "API Enterprise", "code": "API-ENT", "tax_identifier": "API-TAX-10"},
            format="json",
            HTTP_X_REQUEST_ID="test-req-org-create-001",
        )
        assert res.status_code == status.HTTP_201_CREATED
        created_id = res.json()["id"]
        log = AuditLog.objects.filter(target_model="Organization", target_object_id=str(created_id)).first()
        assert log is not None
        assert log.action == AuditLog.ACTION_CREATE
        assert log.request_id == "test-req-org-create-001"


# =========================================================================
# 2. DEF-004: Approval Action Object Filtering
# =========================================================================
class TestDef004ApprovalActionObjectFiltering:
    def test_matching_target_object_id_returns_correct_records(self, phase3_env):
        client = APIClient()
        client.force_authenticate(user=phase3_env["proc_mgr_a"])

        target_1 = uuid.uuid4()
        target_2 = uuid.uuid4()

        act1 = ApprovalAction.objects.create(
            actor=phase3_env["proc_mgr_a"],
            target_model_name="PurchaseOrder",
            target_object_id=target_1,
            action=ApprovalAction.ACTION_APPROVE,
            comments="Approved 1",
            previous_state="APPROVAL",
            new_state="APPROVED",
        )
        act2 = ApprovalAction.objects.create(
            actor=phase3_env["proc_mgr_a"],
            target_model_name="PurchaseOrder",
            target_object_id=target_2,
            action=ApprovalAction.ACTION_APPROVE,
            comments="Approved 2",
            previous_state="APPROVAL",
            new_state="APPROVED",
        )

        res = client.get(f"/api/v1/approvals/actions/?target_object_id={target_1}")
        assert res.status_code == status.HTTP_200_OK
        results = res.json().get("results", res.json())
        assert len(results) == 1
        assert results[0]["id"] == str(act1.id)
        assert results[0]["target_object_id"] == str(target_1)

    def test_different_target_object_id_returns_no_unrelated_records(self, phase3_env):
        client = APIClient()
        client.force_authenticate(user=phase3_env["proc_mgr_a"])

        unrelated_uuid = uuid.uuid4()
        res = client.get(f"/api/v1/approvals/actions/?target_object_id={unrelated_uuid}")
        assert res.status_code == status.HTTP_200_OK
        results = res.json().get("results", res.json())
        assert len(results) == 0

    def test_invalid_target_object_id_handled_safely(self, phase3_env):
        client = APIClient()
        client.force_authenticate(user=phase3_env["proc_mgr_a"])

        res = client.get("/api/v1/approvals/actions/?target_object_id=invalid-not-a-uuid")
        assert res.status_code == status.HTTP_200_OK
        results = res.json().get("results", res.json())
        assert len(results) == 0

    def test_cross_tenant_approval_records_remain_inaccessible(self, phase3_env):
        target_b = uuid.uuid4()
        ApprovalAction.objects.create(
            actor=phase3_env["proc_mgr_b"],
            target_model_name="PurchaseOrder",
            target_object_id=target_b,
            action=ApprovalAction.ACTION_APPROVE,
            comments="Beta approval",
            previous_state="APPROVAL",
            new_state="APPROVED",
        )

        # User in Org A querying target_b
        client = APIClient()
        client.force_authenticate(user=phase3_env["proc_mgr_a"])
        res = client.get(f"/api/v1/approvals/actions/?target_object_id={target_b}")
        assert res.status_code == status.HTTP_200_OK
        results = res.json().get("results", res.json())
        assert len(results) == 0


# =========================================================================
# 3. DEF-005: Vendor KYC Approval Audit
# =========================================================================
class TestDef005VendorKycApprovalAudit:
    def test_successful_kyc_approval_creates_one_action_approve_record(self, phase3_env):
        vendor = phase3_env["vendor_a"]
        manager = phase3_env["admin_user"]

        # Seed mandatory KYC documents as verified
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=VendorDocument.DOC_TYPE_PAN,
            title="PAN Card",
            file="pan.pdf",
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=VendorDocument.DOC_TYPE_GST,
            title="GST Certificate",
            file="gst.pdf",
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )
        VendorDocument.objects.create(
            vendor=vendor,
            document_type=VendorDocument.DOC_TYPE_BANK,
            title="Bank Statement",
            file="bank.pdf",
            status=VendorDocument.STATUS_VERIFIED,
            is_verified=True,
        )

        initial_audits = AuditLog.objects.filter(target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_APPROVE).count()
        approve_vendor_service(vendor=vendor, manager=manager, notes="All KYC verified.")

        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_ACTIVE

        audits = AuditLog.objects.filter(target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_APPROVE)
        assert audits.count() == initial_audits + 1
        log = audits.last()
        assert log.actor == manager
        assert log.target_object_id == str(vendor.id)

    def test_rejected_kyc_does_not_generate_successful_approval_event(self, phase3_env):
        vendor = phase3_env["vendor_a"]
        manager = phase3_env["admin_user"]

        set_vendor_status_governance_service(
            vendor=vendor,
            actor=manager,
            new_status=Vendor.STATUS_REJECTED,
            notes="KYC document fraudulent",
        )

        vendor.refresh_from_db()
        assert vendor.status == Vendor.STATUS_REJECTED

        # Ensure no ACTION_APPROVE record was written
        assert not AuditLog.objects.filter(
            target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_APPROVE
        ).exists()
        # Verify ACTION_REJECT was written
        assert AuditLog.objects.filter(
            target_model="Vendor", target_object_id=str(vendor.id), action=AuditLog.ACTION_REJECT
        ).exists()


# =========================================================================
# 4. DEF-008: Purchase Requisition Submission Audit
# =========================================================================
class TestDef008PurchaseRequisitionSubmissionAudit:
    def test_successful_pr_submission_creates_action_submit_audit(self, phase3_env):
        requester = phase3_env["requester_a"]
        pr = create_purchase_requisition_service(
            title="Laptops PR",
            justification="Engineering refresh",
            requester=requester,
            department=phase3_env["dept_a"],
            cost_center=phase3_env["cc_a"],
            requested_delivery_date=timezone.now().date() + timedelta(days=14),
            line_items=[{"item_description": "Dev Laptop", "quantity": 2, "estimated_unit_price": Decimal("2000.00")}],
        )
        assert pr.status == PurchaseRequisition.STATUS_DRAFT

        initial_submits = AuditLog.objects.filter(
            target_model="PurchaseRequisition", target_object_id=str(pr.id), action=AuditLog.ACTION_SUBMIT
        ).count()

        submit_purchase_requisition_service(requisition=pr, user=requester)
        pr.refresh_from_db()
        assert pr.status in [PurchaseRequisition.STATUS_MANAGER_REVIEW, PurchaseRequisition.STATUS_SUBMITTED]

        logs = AuditLog.objects.filter(
            target_model="PurchaseRequisition", target_object_id=str(pr.id), action=AuditLog.ACTION_SUBMIT
        )
        assert logs.count() == initial_submits + 1
        log = logs.last()
        assert log.actor == requester
        assert log.target_object_id == str(pr.id)

    def test_draft_saving_does_not_create_false_submission_record(self, phase3_env):
        requester = phase3_env["requester_a"]
        pr = create_purchase_requisition_service(
            title="Draft Only PR",
            justification="Pending specs",
            requester=requester,
            department=phase3_env["dept_a"],
            cost_center=phase3_env["cc_a"],
            requested_delivery_date=timezone.now().date() + timedelta(days=14),
            line_items=[{"item_description": "Monitor", "quantity": 1, "estimated_unit_price": Decimal("300.00")}],
        )
        assert not AuditLog.objects.filter(
            target_model="PurchaseRequisition", target_object_id=str(pr.id), action=AuditLog.ACTION_SUBMIT
        ).exists()

    def test_failed_pr_submission_does_not_create_submit_audit(self, phase3_env):
        requester = phase3_env["requester_a"]
        pr = create_purchase_requisition_service(
            title="Empty PR",
            justification="Will fail",
            requester=requester,
            department=phase3_env["dept_a"],
            cost_center=phase3_env["cc_a"],
            requested_delivery_date=timezone.now().date() + timedelta(days=14),
            line_items=[{"item_description": "Initial", "quantity": 1, "estimated_unit_price": Decimal("100.00")}],
        )
        # Remove lines so submission fails
        pr.lines.all().delete()
        with pytest.raises(ValidationError):
            submit_purchase_requisition_service(requisition=pr, user=requester)

        assert not AuditLog.objects.filter(
            target_model="PurchaseRequisition", target_object_id=str(pr.id), action=AuditLog.ACTION_SUBMIT
        ).exists()


# =========================================================================
# 5. DEF-009: Purchase Order Approval History
# =========================================================================
class TestDef009PurchaseOrderApprovalHistory:
    def test_successful_po_approval_creates_expected_approval_action_and_audit(self, phase3_env):
        vendor = phase3_env["vendor_a"]
        vendor.status = Vendor.STATUS_ACTIVE
        vendor.save()

        proc_mgr = phase3_env["proc_mgr_a"]
        requester = phase3_env["requester_a"]

        po = generate_purchase_order_service(
            vendor=vendor,
            cost_center=phase3_env["cc_a"],
            line_items=[{"item_description": "Test Goods", "quantity": 5, "unit_price": Decimal("100.00")}],
            created_by_user=requester,
        )
        submit_purchase_order_for_approval_service(po=po, actor=requester)
        po.refresh_from_db()
        assert po.status == PurchaseOrder.STATUS_APPROVAL

        # Approve PO
        approve_purchase_order_service(po=po, approver=proc_mgr, comments="PO fully approved.")
        po.refresh_from_db()
        assert po.status == PurchaseOrder.STATUS_APPROVED

        # Verify ApprovalAction
        actions = ApprovalAction.objects.filter(
            target_model_name="PurchaseOrder", target_object_id=po.id, action=ApprovalAction.ACTION_APPROVE
        )
        assert actions.count() == 1
        action_row = actions.first()
        assert action_row.actor == proc_mgr
        assert action_row.comments == "PO fully approved."

        # Verify AuditLog
        audits = AuditLog.objects.filter(
            target_model="PurchaseOrder", target_object_id=str(po.id), action=AuditLog.ACTION_APPROVE
        )
        assert audits.count() == 1
        audit_row = audits.first()
        assert audit_row.actor == proc_mgr

    def test_unauthorized_po_approval_rejected(self, phase3_env):
        vendor = phase3_env["vendor_a"]
        vendor.status = Vendor.STATUS_ACTIVE
        vendor.save()

        requester = phase3_env["requester_a"]
        po = generate_purchase_order_service(
            vendor=vendor,
            cost_center=phase3_env["cc_a"],
            line_items=[{"item_description": "Hardware", "quantity": 1, "unit_price": Decimal("500.00")}],
            created_by_user=requester,
        )
        submit_purchase_order_for_approval_service(po=po, actor=requester)

        # Requester cannot approve PO
        with pytest.raises(ValidationError) as exc_info:
            approve_purchase_order_service(po=po, approver=requester, comments="Self approval")
        assert "Permission Denied" in str(exc_info.value) or "Separation of duties" in str(exc_info.value)

        assert not ApprovalAction.objects.filter(
            target_model_name="PurchaseOrder", target_object_id=po.id, action=ApprovalAction.ACTION_APPROVE
        ).exists()


# =========================================================================
# 6. DEF-014: Audit Request Context
# =========================================================================
class TestDef014AuditRequestContext:
    def test_http_request_audit_contains_ip_and_request_id(self, phase3_env):
        rf = RequestFactory()
        req = rf.post("/dummy-url/", HTTP_X_REQUEST_ID="correlation-id-9988", REMOTE_ADDR="198.51.100.42")
        req.user = phase3_env["admin_user"]

        log = create_audit_log_service(
            actor=phase3_env["admin_user"],
            action=AuditLog.ACTION_UPDATE,
            target_model="Organization",
            target_object_id="org-test-uuid",
            request=req,
        )
        assert log.ip_address == "198.51.100.42"
        assert log.request_id == "correlation-id-9988"

    def test_headless_service_audit_works_without_request_context(self, phase3_env):
        clear_request_context()
        log = create_audit_log_service(
            actor=phase3_env["admin_user"],
            action=AuditLog.ACTION_UPDATE,
            target_model="SystemConfig",
            target_object_id="config-headless",
            ip_address=None,
            request_id=None,
            request=None,
        )
        assert log is not None
        assert log.target_object_id == "config-headless"
        assert log.request_id == ""

    def test_background_task_audit_works_without_request_context(self, phase3_env):
        clear_request_context()
        # Task runs without any request or user
        log = create_audit_log_service(
            actor=None,
            action=AuditLog.ACTION_UPDATE,
            target_model="CeleryJob",
            target_object_id="job-101",
        )
        assert log.actor is None
        assert log.target_object_id == "job-101"

    def test_context_is_cleared_between_requests_no_leakage(self, phase3_env):
        clear_request_context()
        set_current_request_id("transient-req-id-1")
        set_client_ip("192.0.2.1")
        assert get_current_request_id() == "transient-req-id-1"
        assert get_client_ip() == "192.0.2.1"

        clear_request_context()
        assert get_current_request_id() is None
        assert get_client_ip() is None


# =========================================================================
# 7. DEF-015: Failed Login Security Events
# =========================================================================
class TestDef015FailedLoginSecurityEvents:
    def test_incorrect_login_creates_failed_login_security_event(self, phase3_env):
        client = APIClient()
        initial_failed_count = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).count()

        res = client.post(
            "/api/v1/auth/login/",
            {"email": "requester.a@alpha.com", "password": "WrongPassword!"},
            format="json",
            HTTP_X_REQUEST_ID="test-login-fail-req",
            REMOTE_ADDR="203.0.113.195",
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

        new_failed_count = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).count()
        assert new_failed_count == initial_failed_count + 1

        log = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).last()
        assert log.new_state["email"] == "requester.a@alpha.com"
        assert "password" not in str(log.new_state).lower()

    def test_failed_login_event_visible_in_security_events_query(self, phase3_env):
        client = APIClient()
        client.post(
            "/api/v1/auth/login/",
            {"email": "procmgr.a@alpha.com", "password": "WrongPassword!"},
            format="json",
        )

        events = get_security_events_audit(organization=phase3_env["org_a"])
        results = events["results"]
        actions = [r["action"] for r in results]
        assert AuditLog.ACTION_LOGIN_FAILED in actions

    def test_null_actor_events_included_correctly_for_target_org(self, phase3_env):
        # Failed login attempt targeting a user in Org A, where actor is null
        user_a = phase3_env["requester_a"]
        create_audit_log_service(
            actor=None,
            action=AuditLog.ACTION_LOGIN_FAILED,
            target_model="User",
            target_object_id=str(user_a.id),
            new_state={"event": "LOGIN_FAILED", "email": user_a.email, "reason": "Invalid credentials"},
        )

        events = get_security_events_audit(organization=phase3_env["org_a"])
        matching = [r for r in events["results"] if r["target_object_id"] == str(user_a.id)]
        assert len(matching) >= 1
        assert matching[0]["actor_id"] is None
        assert matching[0]["action"] == AuditLog.ACTION_LOGIN_FAILED

    def test_organization_filter_does_not_leak_other_tenant_failed_logins(self, phase3_env):
        user_b = phase3_env["proc_mgr_b"]
        create_audit_log_service(
            actor=None,
            action=AuditLog.ACTION_LOGIN_FAILED,
            target_model="User",
            target_object_id=str(user_b.id),
            new_state={"event": "LOGIN_FAILED", "email": user_b.email, "reason": "Invalid credentials"},
        )

        # Org A auditor/admin query must NOT see Org B's failed login
        events_a = get_security_events_audit(organization=phase3_env["org_a"])
        matching_in_a = [r for r in events_a["results"] if r["target_object_id"] == str(user_b.id)]
        assert len(matching_in_a) == 0

        # Org B auditor/admin query DOES see Org B's failed login
        events_b = get_security_events_audit(organization=phase3_env["org_b"])
        matching_in_b = [r for r in events_b["results"] if r["target_object_id"] == str(user_b.id)]
        assert len(matching_in_b) >= 1

    def test_successful_login_and_phase1_lockout_behavior_preserved(self, phase3_env):
        client = APIClient()
        # Legitimate login succeeds (200 OK)
        res = client.post(
            "/api/v1/auth/login/",
            {"email": "requester.a@alpha.com", "password": "RequesterPass123!"},
            format="json",
        )
        assert res.status_code == status.HTTP_200_OK


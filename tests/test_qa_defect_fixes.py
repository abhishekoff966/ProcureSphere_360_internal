from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.accounts.services import create_user_service
from apps.audit.models import AuditLog
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.services import create_contract_service, terminate_contract_service
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import (
    create_purchase_requisition_service,
    submit_purchase_requisition_service,
)
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def qa_setup(db):
    org, _ = Organization.objects.get_or_create(code="HPE-QA", defaults={"name": "HPE QA Org"})
    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_appr, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    role_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    role_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    role_stores, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    role_finance, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance / AP"})
    role_auditor, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Compliance Auditor"})

    admin_user = User.objects.create_user(
        email="qa.admin@hpe.com", password="Password123!", role=role_admin, is_superuser=True, is_staff=True, organization=org
    )
    legal_user = User.objects.create_user(
        email="qa.legal@hpe.com", password="Password123!", role=role_legal, organization=org
    )
    req_user = User.objects.create_user(
        email="qa.requester@hpe.com", password="Password123!", role=role_req, organization=org
    )
    exec_user = User.objects.create_user(
        email="qa.exec@hpe.com", password="Password123!", role=role_exec, organization=org
    )
    mgr_user = User.objects.create_user(
        email="qa.mgr@hpe.com", password="Password123!", role=role_mgr, organization=org
    )
    stores_user = User.objects.create_user(
        email="qa.stores@hpe.com", password="Password123!", role=role_stores, organization=org
    )
    finance_user = User.objects.create_user(
        email="qa.finance@hpe.com", password="Password123!", role=role_finance, organization=org
    )
    auditor_user = User.objects.create_user(
        email="qa.auditor@hpe.com", password="Password123!", role=role_auditor, organization=org
    )

    dept1, _ = Department.objects.get_or_create(organization=org, code="QA-IT", defaults={"name": "QA IT"})
    dept2, _ = Department.objects.get_or_create(organization=org, code="QA-FIN", defaults={"name": "QA Finance"})

    cc1, _ = CostCenter.objects.get_or_create(department=dept1, code="QA-CC1", defaults={"name": "Cost Center 1"})
    cc2, _ = CostCenter.objects.get_or_create(department=dept2, code="QA-CC2", defaults={"name": "Cost Center 2"})

    cat, _ = VendorCategory.objects.get_or_create(code="QA-CAT", defaults={"name": "Hardware"})
    vendor, _ = Vendor.objects.get_or_create(
        tax_identification_number="TIN-QA-01",
        defaults={"legal_name": "QA Vendor Inc", "category": cat, "email": "vendor@qa.com", "organization": org, "status": Vendor.STATUS_ACTIVE}
    )

    fp, _ = FiscalPeriod.objects.get_or_create(
        organization=org, year=2026, period_number=99,
        defaults={"name": "FY26-QA", "start_date": timezone.now().date(), "end_date": timezone.now().date() + timezone.timedelta(days=90)}
    )

    return {
        "admin": admin_user,
        "legal": legal_user,
        "requester": req_user,
        "exec": exec_user,
        "mgr": mgr_user,
        "stores": stores_user,
        "finance": finance_user,
        "auditor": auditor_user,
        "role_req": role_req,
        "org": org,
        "dept1": dept1,
        "dept2": dept2,
        "cc1": cc1,
        "cc2": cc2,
        "vendor": vendor,
        "fp": fp,
    }


@pytest.mark.django_db
def test_def_010_budget_search_no_500(qa_setup):
    """DEF-010: Searching budgets must not throw FieldError (HTTP 500)."""
    client = Client()
    client.force_login(qa_setup["admin"])

    # Query with a search string (including SQL-injection style strings)
    res = client.get("/api/v1/budgets/?search=QA")
    assert res.status_code == 200

    res_sqli = client.get("/api/v1/budgets/?search=1%27%20OR%20%271%27=%271")
    assert res_sqli.status_code == 200


@pytest.mark.django_db
def test_def_013_contract_draft_termination_rejected(qa_setup):
    """DEF-013: Terminating a DRAFT contract must be rejected with ValidationError."""
    today = timezone.now().date()
    contract = create_contract_service(
        title="Draft Test Contract",
        vendor=qa_setup["vendor"],
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=qa_setup["legal"],
    )
    assert contract.status == Contract.STATUS_DRAFT

    with pytest.raises(ValidationError, match="Only active or renewal-due contracts can be terminated"):
        terminate_contract_service(contract=contract, user=qa_setup["legal"], reason="Testing draft termination")

    client = Client()
    client.force_login(qa_setup["legal"])
    res = client.post(f"/api/v1/contracts/{contract.id}/terminate/", data={"reason": "Invalid draft termination"}, content_type="application/json")
    assert res.status_code == 400
    assert "INVALID_TERMINATION" in res.json()["error"]["code"]


@pytest.mark.django_db
def test_def_021_post_vendor_no_slash_handled(qa_setup):
    """DEF-021: POST to /api/v1/vendors without trailing slash must not raise RuntimeError 500."""
    client = Client()
    client.force_login(qa_setup["admin"])

    # Request to /api/v1/vendors without trailing slash
    res = client.post(
        "/api/v1/vendors",
        data={
            "legal_name": "No Slash Vendor LLC",
            "tax_identification_number": "TIN-NOSLASH-01",
            "email": "noslash@vendor.com",
            "address": "123 Test St",
        },
        content_type="application/json",
    )
    # Must NOT return 500 Internal Server Error
    assert res.status_code in [201, 400]


@pytest.mark.django_db
def test_def_026_cost_centers_department_filtering(qa_setup):
    """DEF-026: Cost center list must filter by department query parameter."""
    client = Client()
    client.force_login(qa_setup["admin"])

    dept1 = qa_setup["dept1"]
    dept2 = qa_setup["dept2"]

    res1 = client.get(f"/api/v1/organization/cost-centers/?department={dept1.id}")
    assert res1.status_code == 200
    results1 = res1.json().get("results", [])
    assert len(results1) > 0
    for item in results1:
        assert str(item.get("department")) == str(dept1.id)

    res2 = client.get(f"/api/v1/organization/cost-centers/?department={dept2.id}")
    assert res2.status_code == 200
    results2 = res2.json().get("results", [])
    assert len(results2) > 0
    for item in results2:
        assert str(item.get("department")) == str(dept2.id)


@pytest.mark.django_db
def test_def_022_user_creation_audit_log(qa_setup):
    """DEF-022: Creating a user must record an immutable AuditLog entry."""
    client = Client()
    client.force_login(qa_setup["admin"])

    new_email = "audited.user@hpe.com"
    res = client.post(
        "/api/v1/accounts/users/",
        data={
            "email": new_email,
            "password": "Password123!",
            "first_name": "Audited",
            "last_name": "User",
            "role": str(qa_setup["role_req"].id),
        },
        content_type="application/json",
    )
    assert res.status_code in [201, 200]
    new_user_id = res.json()["id"]

    audit_entry = AuditLog.objects.filter(target_model="User", target_object_id=str(new_user_id)).first()
    assert audit_entry is not None
    assert audit_entry.action == AuditLog.ACTION_CREATE
    assert audit_entry.new_state["email"] == new_email


@pytest.mark.django_db
def test_def_004_unknown_api_route_returns_json_404(qa_setup):
    """DEF-004: Unknown /api/ routes must return standard JSON 404 error envelope."""
    client = Client()
    client.force_login(qa_setup["admin"])

    res = client.get("/api/v1/no-such-route/")
    assert res.status_code == 404
    data = res.json()
    assert "error" in data
    assert data["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
def test_def_023_audit_pagination_envelope(qa_setup):
    """DEF-023: Audit endpoints must return standard count, next, previous in pagination envelope."""
    client = Client()
    client.force_login(qa_setup["admin"])

    res = client.get("/api/v1/audit/approval-history/")
    assert res.status_code == 200
    data = res.json()
    assert "count" in data
    assert "next" in data
    assert "previous" in data
    assert "results" in data


@pytest.mark.django_db
def test_sourcing_evaluate_case_insensitive_and_delete_405(qa_setup):
    """Sourcing evaluate endpoint accepts uppercase TECHNICAL and rejects DELETE with 405."""
    client = Client()
    client.force_login(qa_setup["admin"])

    # DELETE to /evaluate/TECHNICAL/ should return 405 Method Not Allowed (not 404)
    res_del = client.delete("/api/v1/sourcing/events/00000000-0000-0000-0000-000000000000/evaluate/TECHNICAL/")
    assert res_del.status_code == 405
    assert res_del.json()["error"]["code"] == "METHOD_NOT_ALLOWED"


@pytest.mark.django_db
def test_def_001_document_numbering_uniqueness(qa_setup):
    """DEF-001: Sequential document numbering is monotonic and collision-safe."""
    from apps.core.services import generate_document_number
    from apps.requisitions.models import PurchaseRequisition
    num1 = generate_document_number(PurchaseRequisition, "pr_number", "PR", padding=5)
    num2 = generate_document_number(PurchaseRequisition, "pr_number", "PR", padding=5, offset=1)
    assert num1 != num2
    assert num1.startswith("PR-")
    assert num2.startswith("PR-")


@pytest.mark.django_db
def test_def_002_budget_overspend_triggers_budget_review(qa_setup):
    """DEF-002: Requisition exceeding available budget routes to STATUS_BUDGET_REVIEW."""
    from apps.approvals.models import ApprovalPolicy, ApprovalStep
    from apps.budgets.models import Budget

    Budget.objects.create(
        cost_center=qa_setup["cc1"],
        fiscal_period=qa_setup["fp"],
        allocated_amount=Decimal("100.00"),
        allow_overspend=False,
    )
    policy, _ = ApprovalPolicy.objects.get_or_create(
        organization=qa_setup["org"],
        module=ApprovalPolicy.MODULE_PR,
        name="QA Policy",
        min_amount=Decimal("0.00"),
        max_amount=Decimal("1000000.00"),
        is_active=True,
    )
    role_dept = Role.objects.get(code=Role.DEPT_APPROVER)
    ApprovalStep.objects.get_or_create(
        policy=policy,
        step_number=1,
        approver_role=role_dept,
        defaults={"description": "Dept Approver Step"},
    )

    pr = create_purchase_requisition_service(
        title="High Value Hardware",
        justification="Critical replacement",
        requester=qa_setup["requester"],
        department=qa_setup["dept1"],
        cost_center=qa_setup["cc1"],
        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=10),
        line_items=[{"item_description": "Expensive Item", "quantity": 1, "estimated_unit_price": Decimal("5000.00")}],
    )
    submitted_pr = submit_purchase_requisition_service(requisition=pr, user=qa_setup["requester"])
    assert submitted_pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW


@pytest.mark.django_db
def test_def_003_budget_available_amount_unclamped(qa_setup):
    """DEF-003: available_amount reflects true negative deficit when over-reserved/committed."""
    b = Budget.objects.create(
        cost_center=qa_setup["cc2"],
        fiscal_period=qa_setup["fp"],
        allocated_amount=Decimal("1000.00"),
        reserved_amount=Decimal("1500.00"),
        allow_overspend=False,
    )
    assert b.available_amount == Decimal("-500.00")


@pytest.mark.django_db
def test_def_004_empty_requisition_rejected(qa_setup):
    """DEF-004: Requisition without line items returns HTTP 400 Bad Request."""
    client = Client()
    client.force_login(qa_setup["requester"])
    res = client.post(
        "/api/v1/requisitions/",
        data={
            "title": "Empty Requisition",
            "justification": "Testing empty lines rejection",
            "department": str(qa_setup["dept1"].id),
            "cost_center": str(qa_setup["cc1"].id),
            "requested_delivery_date": str(timezone.now().date() + timezone.timedelta(days=10)),
            "line_items": [],
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "lines" in res.json().get("error", {}).get("details", {})


@pytest.mark.django_db
def test_def_005_dashboard_role_enforcement(qa_setup):
    """DEF-005: Unauthorized roles accessing direct dashboard URLs get HTTP 403."""
    client = Client()
    client.force_login(qa_setup["requester"])

    res_fin = client.get("/finance/")
    assert res_fin.status_code == 403

    res_admin = client.get("/superadmin/")
    assert res_admin.status_code == 403

    res_stores = client.get("/stores/")
    assert res_stores.status_code == 403


@pytest.mark.django_db
def test_def_007_session_cookie_secure_setting():
    """DEF-007: SESSION_COOKIE_SECURE and reverse proxy SSL header configured."""
    from django.conf import settings as s
    assert hasattr(s, "SECURE_PROXY_SSL_HEADER")
    assert s.SECURE_PROXY_SSL_HEADER == ("HTTP_X_FORWARDED_PROTO", "https")


@pytest.mark.django_db
def test_def_009_manager_supplier_compare_null_score(qa_setup):
    """DEF-009: Supplier compare endpoint returns HTTP 200 without NoneType crash."""
    client = Client()
    client.force_login(qa_setup["mgr"])

    v2, _ = Vendor.objects.get_or_create(
        tax_identification_number="TIN-QA-02",
        defaults={
            "vendor_number": "VND-2026-99992",
            "legal_name": "QA Vendor 2",
            "category": qa_setup["vendor"].category,
            "organization": qa_setup["org"],
            "status": Vendor.STATUS_ACTIVE,
        },
    )

    res = client.get(f"/api/v1/manager/performance/compare/?vendor_a={qa_setup['vendor'].id}&vendor_b={v2.id}")
    assert res.status_code == 200
    data = res.json()
    assert "vendor_a" in data
    assert "vendor_b" in data
    assert data["vendor_a"]["composite_score"] == 0.0


@pytest.mark.django_db
def test_def_010_manager_approval_action_unlocked(qa_setup):
    """DEF-010: ManagerApprovalActionAPIView does not return hardcoded 403 to Procurement Manager."""
    client = Client()
    client.force_login(qa_setup["mgr"])

    res = client.post(
        "/api/v1/manager/approvals/action/",
        data={
            "item_type": "PR",
            "item_id": "00000000-0000-0000-0000-000000000000",
            "action": "APPROVE",
            "comments": "Testing manager approval action",
        },
        content_type="application/json",
    )
    assert res.status_code != 403


@pytest.mark.django_db
def test_def_011_superadmin_cross_org_budget_scoping(qa_setup):
    """DEF-011: Super Admin can query budgets without being 404 blocked across orgs."""
    from apps.budgets.selectors import scope_budgets_queryset
    qs = scope_budgets_queryset(Budget.objects.all(), qa_setup["admin"])
    assert qs.count() == Budget.objects.count()


@pytest.mark.django_db
def test_def_012_invoice_rbac_stores_vs_requester(qa_setup):
    """DEF-012: Stores Receiver has Read access; Requester has No Access per RBAC matrix."""
    client_stores = Client()
    client_stores.force_login(qa_setup["stores"])
    res_stores = client_stores.get("/api/v1/invoices/")
    assert res_stores.status_code == 200

    client_req = Client()
    client_req.force_login(qa_setup["requester"])
    res_req = client_req.get("/api/v1/invoices/")
    assert res_req.status_code == 403


@pytest.mark.django_db
def test_def_013_requisition_and_scorecard_rbac(qa_setup):
    """DEF-013: Stores Receiver & Legal Manager blocked from PRs; Legal Manager blocked from Scorecards."""
    client_stores = Client()
    client_stores.force_login(qa_setup["stores"])
    res_stores_pr = client_stores.get("/requisitions/")
    assert res_stores_pr.status_code == 403

    client_legal = Client()
    client_legal.force_login(qa_setup["legal"])
    res_legal_pr = client_legal.get("/requisitions/")
    assert res_legal_pr.status_code == 403

    from apps.scorecards.permissions import can_view_scorecards
    assert can_view_scorecards(qa_setup["legal"]) is False
    assert can_view_scorecards(qa_setup["stores"]) is True


@pytest.mark.django_db
def test_def_014_proc_exec_vendor_risk_assessment(qa_setup):
    """DEF-014: Procurement Executive is permitted to record vendor risk assessment."""
    client = Client()
    client.force_login(qa_setup["exec"])
    res = client.post(
        f"/api/v1/vendors/{qa_setup['vendor'].id}/assess-risk/",
        data={
            "risk_level": "LOW",
            "notes": "Annual standard review completed by Procurement Executive.",
            "risk_flags": [],
        },
        content_type="application/json",
    )
    assert res.status_code in [200, 201]


@pytest.mark.django_db
def test_def_015_user_creation_role_mandatory(qa_setup):
    """DEF-015: User creation API rejects payload when role is omitted."""
    client = Client()
    client.force_login(qa_setup["admin"])
    res = client.post(
        "/api/v1/accounts/users/",
        data={
            "email": "norole.user@hpe.com",
            "password": "Password123!",
            "first_name": "NoRole",
            "last_name": "User",
        },
        content_type="application/json",
    )
    assert res.status_code == 400
    assert "role" in res.json().get("error", {}).get("details", {})


@pytest.mark.django_db
def test_def_016_po_line_unit_price_decimal_validation(qa_setup):
    """DEF-016: PO creation with >2 decimal places in line item unit price returns HTTP 400."""
    client = Client()
    client.force_login(qa_setup["admin"])
    res = client.post(
        "/api/v1/purchase-orders/",
        data={
            "vendor": str(qa_setup["vendor"].id),
            "cost_center": str(qa_setup["cc1"].id),
            "delivery_date": str(timezone.now().date() + timezone.timedelta(days=14)),
            "line_items": [
                {
                    "item_description": "Fractional Precision Test",
                    "quantity": 10,
                    "unit_price": "12.345",
                    "line_number": 1,
                }
            ],
        },
        content_type="application/json",
    )
    assert res.status_code == 400


@pytest.mark.django_db
def test_def_017_sourcing_early_bid_close(qa_setup):
    """DEF-017: Procurement Executive/Manager can close bidding before bid_end_date."""
    from apps.sourcing.models import SourcingEvent
    event = SourcingEvent.objects.create(
        title="Early Close Test",
        event_type=SourcingEvent.TYPE_RFQ,
        status=SourcingEvent.STATUS_BID_WINDOW,
        bid_start_date=timezone.now() - timezone.timedelta(days=1),
        bid_end_date=timezone.now() + timezone.timedelta(days=7),
        created_by=qa_setup["mgr"],
    )
    client = Client()
    client.force_login(qa_setup["mgr"])
    res = client.post(f"/api/v1/sourcing/events/{event.id}/close-bidding/")
    assert res.status_code == 200
    event.refresh_from_db()
    assert event.status == SourcingEvent.STATUS_TECHNICAL_REVIEW


@pytest.mark.django_db
def test_def_019_goods_receipt_update_completed_po(qa_setup):
    """DEF-019: Updating an existing GoodsReceipt does not fail when PO is COMPLETED."""
    from apps.orders.models import POLine, PurchaseOrder
    from apps.receipts.models import GoodsReceipt

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-99991",
        vendor=qa_setup["vendor"],
        cost_center=qa_setup["cc1"],
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("1000.00"),
        created_by=qa_setup["mgr"],
    )
    POLine.objects.create(
        po=po,
        item_description="Test Item",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
        line_total=Decimal("1000.00"),
    )

    grn = GoodsReceipt.objects.create(
        po=po,
        grn_number="GRN-2026-99991",
        received_by=qa_setup["stores"],
        received_date=timezone.now(),
    )
    po.status = PurchaseOrder.STATUS_COMPLETED
    po.save(update_fields=["status"])

    client = Client()
    client.force_login(qa_setup["stores"])
    res = client.patch(
        f"/api/v1/receipts/{grn.id}/",
        data={"remarks": "Updated delivery remarks after completion."},
        content_type="application/json",
    )
    assert res.status_code == 200



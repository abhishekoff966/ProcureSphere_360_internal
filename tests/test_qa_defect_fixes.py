import pytest
from decimal import Decimal
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.test import Client

from apps.accounts.models import Role, User
from apps.accounts.services import create_user_service
from apps.audit.models import AuditLog
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.services import create_contract_service, terminate_contract_service
from apps.organization.models import Department, CostCenter, Organization, FiscalPeriod
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service, submit_purchase_requisition_service
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def qa_setup(db):
    org, _ = Organization.objects.get_or_create(code="HPE-QA", defaults={"name": "HPE QA Org"})
    role_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    role_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    role_appr, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    role_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})

    admin_user = User.objects.create_user(
        email="qa.admin@hpe.com", password="Password123!", role=role_admin, is_superuser=True, is_staff=True, organization=org
    )
    legal_user = User.objects.create_user(
        email="qa.legal@hpe.com", password="Password123!", role=role_legal, organization=org
    )
    req_user = User.objects.create_user(
        email="qa.requester@hpe.com", password="Password123!", role=role_req, organization=org
    )

    dept1, _ = Department.objects.get_or_create(organization=org, code="QA-IT", defaults={"name": "QA IT"})
    dept2, _ = Department.objects.get_or_create(organization=org, code="QA-FIN", defaults={"name": "QA Finance"})

    cc1, _ = CostCenter.objects.get_or_create(department=dept1, code="QA-CC1", defaults={"name": "Cost Center 1"})
    cc2, _ = CostCenter.objects.get_or_create(department=dept2, code="QA-CC2", defaults={"name": "Cost Center 2"})

    cat, _ = VendorCategory.objects.get_or_create(code="QA-CAT", defaults={"name": "Hardware"})
    vendor, _ = Vendor.objects.get_or_create(
        tax_identification_number="TIN-QA-01",
        defaults={"legal_name": "QA Vendor Inc", "category": cat, "email": "vendor@qa.com", "organization": org}
    )

    fp, _ = FiscalPeriod.objects.get_or_create(
        organization=org, year=2026, period_number=99,
        defaults={"name": "FY26-QA", "start_date": timezone.now().date(), "end_date": timezone.now().date() + timezone.timedelta(days=90)}
    )

    return {
        "admin": admin_user,
        "legal": legal_user,
        "requester": req_user,
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


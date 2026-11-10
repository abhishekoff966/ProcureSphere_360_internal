"""
Tests for Legal Manager Workspace:
- Sidebar navigation: Removal of Audit Trail Log (/audit/logs/) and Sourcing Events (/sourcing-events/).
- Contract Register: Database fetching, pagination (previous/next controls), and status/search filtering.
- Business approval to Active transition lifecycle verification.
"""

from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contracts.models import Contract
from apps.contracts.services import (
    approve_business_service,
    approve_legal_review_service,
    create_contract_service,
    submit_for_legal_review_service,
)
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service

pytestmark = [pytest.mark.django_db]


def _get_or_create_role(code, name):
    role = Role.objects.filter(code=code).first()
    if not role:
        try:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        except Exception:
            role = Role.objects.filter(code=code).first()
    return role


def _get_or_create_user(email, password, role):
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(email=email, password=password, role=role)
    else:
        if role:
            user.role = role
            user.save(update_fields=["role"])
        user.refresh_from_db()
    return user


@pytest.fixture
def legal_world(db):
    legal_role = _get_or_create_role(Role.LEGAL_MGR, "Legal / Contract Manager")
    proc_mgr_role = _get_or_create_role(Role.PROC_MGR, "Procurement Manager")

    legal_user = _get_or_create_user("legal.test@hpe.com", "Password123!", legal_role)
    proc_mgr_user = _get_or_create_user("proc.mgr.test@hpe.com", "Password123!", proc_mgr_role)

    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-LEGAL-01", defaults={"name": "Legal & Cloud Services"}
    )
    vendor = register_vendor_service(
        legal_name="Global Legal Tech Corp",
        tax_identification_number="TAX-GLT-100",
        category=category,
        email="compliance@gltcorp.com",
        address="100 Enterprise Way",
    )

    return {
        "legal_user": legal_user,
        "proc_mgr_user": proc_mgr_user,
        "vendor": vendor,
    }


def test_legal_sidebar_navigation_removes_audit_log_and_sourcing_events(client, legal_world):
    """
    Legal Manager workspace sidebar must NOT contain:
    - Sourcing Events (/sourcing-events/)
    - Audit Trail Log (/audit/logs/) which was yielding 403 Access Denied
    It must preserve Contract Register, New Contract Draft, Vendor KYC Review, Approvals Inbox, Vendor Directory, Reports Hub.
    """
    legal_user = legal_world["legal_user"]
    client.force_login(legal_user)

    for url in ["/legal/", "/contracts/"]:
        resp = client.get(url)
        assert resp.status_code == 200
        content = resp.content.decode()

        # Removed links must NOT appear
        assert "/audit/logs/" not in content
        assert "Audit Trail Log" not in content
        assert "/sourcing-events/" not in content
        assert "Sourcing Events" not in content

        # Required links MUST appear
        assert "/contracts/" in content
        assert "Contract Register" in content
        assert "/contracts/create/" in content
        assert "New Contract Draft" in content
        assert "/vendors/onboarding/" in content
        assert "Vendor KYC Review" in content
        assert "/approvals/inbox/" in content
        assert "Approvals Inbox" in content
        assert "/vendors/" in content
        assert "Vendor Directory" in content
        assert "/reports/hub/" in content
        assert "Reports Hub" in content


def test_contract_register_fetches_from_db_with_pagination(client, legal_world):
    """
    Contract Register must fetch live data from the database and paginate records
    with Previous / Next controls (10 items per page).
    """
    legal_user = legal_world["legal_user"]
    vendor = legal_world["vendor"]
    client.force_login(legal_user)

    today = timezone.now().date()

    # Create 15 contracts in the database
    created_contracts = []
    for i in range(15):
        c = create_contract_service(
            title=f"Enterprise Software License #{i+1:02d}",
            vendor=vendor,
            contract_value=Decimal(f"{10000 + i * 1000}.00"),
            start_date=today,
            end_date=today + timezone.timedelta(days=365),
            renewal_notice_days=30,
            contract_owner=legal_user,
        )
        created_contracts.append(c)

    # 1. Page 1 (default)
    resp = client.get("/contracts/")
    assert resp.status_code == 200
    page_obj = resp.context["page_obj"]
    assert page_obj.paginator.count == 15
    assert page_obj.paginator.num_pages == 2
    assert page_obj.number == 1
    assert page_obj.has_next()
    assert not page_obj.has_previous()
    assert len(resp.context["items"]) == 10

    content_p1 = resp.content.decode()
    assert "Showing 1–10 of 15" in content_p1
    assert "Next" in content_p1
    assert "page=2" in content_p1

    # 2. Page 2
    resp_p2 = client.get("/contracts/?page=2")
    assert resp_p2.status_code == 200
    page_obj_p2 = resp_p2.context["page_obj"]
    assert page_obj_p2.number == 2
    assert not page_obj_p2.has_next()
    assert page_obj_p2.has_previous()
    assert len(resp_p2.context["items"]) == 5

    content_p2 = resp_p2.content.decode()
    assert "Showing 11–15 of 15" in content_p2
    assert "Previous" in content_p2
    assert "page=1" in content_p2


def test_contract_register_filtering_and_query_preservation(client, legal_world):
    """
    Contract Register filters by status and search query correctly from DB,
    preserving query string params in pagination links.
    """
    legal_user = legal_world["legal_user"]
    proc_mgr = legal_world["proc_mgr_user"]
    vendor = legal_world["vendor"]
    client.force_login(legal_user)

    today = timezone.now().date()

    # Create contracts in various statuses
    c_draft = create_contract_service(
        title="Draft Alpha Agreement",
        vendor=vendor,
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_user,
    )
    c_active = create_contract_service(
        title="Active Beta Service Level Agreement",
        vendor=vendor,
        contract_value=Decimal("75000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_user,
    )
    submit_for_legal_review_service(contract=c_active, user=legal_user)
    approve_legal_review_service(contract=c_active, user=legal_user)
    approve_business_service(contract=c_active, user=proc_mgr)
    c_active.refresh_from_db()
    assert c_active.status == Contract.STATUS_ACTIVE

    # Filter by status=ACTIVE
    resp_active = client.get("/contracts/?status=ACTIVE")
    assert resp_active.status_code == 200
    items_active = resp_active.context["items"]
    assert c_active in items_active
    assert c_draft not in items_active

    # Filter by search q=Alpha
    resp_search = client.get("/contracts/?q=Alpha")
    assert resp_search.status_code == 200
    items_search = resp_search.context["items"]
    assert c_draft in items_search
    assert c_active not in items_search

    # Filter by search q=Beta and status=ACTIVE
    resp_both = client.get("/contracts/?status=ACTIVE&q=Beta")
    assert resp_both.status_code == 200
    assert c_active in resp_both.context["items"]


def test_business_approval_to_active_lifecycle_flow(client, legal_world):
    """
    Lifecycle flow:
    1. DRAFT -> submit legal review -> LEGAL_REVIEW
    2. Legal Manager approves -> BUSINESS_APPROVAL (Legal Manager alone cannot activate)
    3. Procurement Manager / Business Owner approves -> ACTIVE with audit log and notification
    """
    legal_user = legal_world["legal_user"]
    proc_mgr = legal_world["proc_mgr_user"]
    vendor = legal_world["vendor"]
    today = timezone.now().date()

    contract = create_contract_service(
        title="Lifecycle Validation Agreement",
        vendor=vendor,
        contract_value=Decimal("120000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=legal_user,
    )
    assert contract.status == Contract.STATUS_DRAFT

    # Step 1: Submit for legal review
    contract = submit_for_legal_review_service(contract=contract, user=legal_user)
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Step 2: Legal Manager approves legal review -> converts to BUSINESS_APPROVAL
    contract = approve_legal_review_service(
        contract=contract, user=legal_user, notes="SLA and liability clauses verified"
    )
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # In BUSINESS_APPROVAL, Legal Manager viewing contract cannot approve business (button hidden)
    client.force_login(legal_user)
    detail_resp = client.get(reverse("contract_detail", kwargs={"contract_id": contract.id}))
    assert detail_resp.status_code == 200
    assert not detail_resp.context["is_proc_mgr"]

    # Step 3: Procurement Manager / Business Approver approves -> converts to ACTIVE
    client.force_login(proc_mgr)
    approve_resp = client.post(
        reverse("contract_business_approve", kwargs={"contract_id": contract.id}),
        {"notes": "Commercial rates and scope verified against project budget"},
    )
    assert approve_resp.status_code == 302
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_ACTIVE

    # Verify audit log entry
    audit = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id)
    ).order_by("-timestamp").first()
    assert audit is not None
    assert audit.action == AuditLog.ACTION_APPROVE
    assert audit.previous_state == {"status": Contract.STATUS_BUSINESS_APPROVAL}
    assert audit.new_state["status"] == Contract.STATUS_ACTIVE


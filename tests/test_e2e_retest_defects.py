import datetime
from decimal import Decimal

import pytest
from rest_framework import status
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contracts.models import Contract
from apps.contracts.services import create_contract_service
from apps.notifications.models import Notification
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PRLine, PurchaseRequisition
from apps.scorecards.models import VendorScorecard
from apps.sourcing.models import BidInvite, SourcingEvent
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def e2e_setup(db):
    org = Organization.objects.create(name="HPE Tech India", code="HPE_IND")
    dept = Department.objects.create(organization=org, name="Engineering Procurement", code="ENG_PROC")
    cc = CostCenter.objects.create(department=dept, name="Cloud Computing", code="CC_CLOUD")
    fp = FiscalPeriod.objects.create(
        organization=org,
        name="FY2026-Q1",
        year=2026,
        period_number=1,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
    )
    cat = VendorCategory.objects.create(name="IT Hardware", code="IT_HW")

    r_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_requester, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_proc_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    r_proc_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})

    u_admin = User.objects.create_user(
        email="e2e.admin@hpe.local", password="Password123!", role=r_admin, is_superuser=True, is_staff=True
    )
    u_req = User.objects.create_user(
        email="e2e.req@hpe.local", password="Password123!", role=r_requester, department=dept, cost_center=cc, organization=org
    )
    u_exec = User.objects.create_user(
        email="e2e.exec@hpe.local", password="Password123!", role=r_proc_exec, organization=org
    )
    u_legal = User.objects.create_user(
        email="e2e.legal@hpe.local", password="Password123!", role=r_legal, organization=org
    )

    vendors = []
    for i in range(1, 4):
        v = Vendor.objects.create(
            organization=org,
            legal_name=f"Vendor {i} Ltd",
            vendor_number=f"VND-E2E-{i:03d}",
            tax_identification_number=f"TAX-ID-E2E-{i:03d}",
            email=f"vendor{i}@test.local",
            address=f"Street {i}",
            category=cat,
            status=Vendor.STATUS_ACTIVE,
            is_event_eligible=True,
            event_eligibility_status=Vendor.ELIGIBILITY_ELIGIBLE,
        )
        vendors.append(v)

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "fp": fp,
        "admin": u_admin,
        "req": u_req,
        "exec": u_exec,
        "legal": u_legal,
        "vendors": vendors,
    }


# ==============================================================================
# 1. E2E-RT-053 (TC_PR_041): Requisition Resolved Route & approval-route endpoint
# ==============================================================================
@pytest.mark.django_db
def test_tc_pr_041_resolved_approval_route_and_endpoint(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["req"])

    # Create PR with lines
    payload = {
        "title": "Cloud Infrastructure Hardware",
        "justification": "Data center capacity expansion",
        "department": str(data["dept"].id),
        "cost_center": str(data["cc"].id),
        "requested_delivery_date": (datetime.date.today() + datetime.timedelta(days=14)).isoformat(),
        "lines": [
            {
                "item_description": "Blade Server Enclosure",
                "quantity": 2,
                "unit_of_measure": "EA",
                "estimated_unit_price": "225000.00",
            }
        ],
    }
    res = client.post("/api/v1/requisitions/", payload, format="json")
    assert res.status_code == status.HTTP_201_CREATED
    assert "resolved_route" in res.data
    assert "steps" in res.data["resolved_route"]
    steps = res.data["resolved_route"]["steps"]
    assert len(steps) >= 1
    assert any("Department" in s["role"] for s in steps)

    pr_id = res.data["id"]

    # Verify dedicated GET /api/v1/requisitions/{id}/approval-route/ endpoint
    route_res = client.get(f"/api/v1/requisitions/{pr_id}/approval-route/")
    assert route_res.status_code == status.HTTP_200_OK
    assert "steps" in route_res.data
    assert len(route_res.data["steps"]) == len(steps)


# ==============================================================================
# 2. E2E-RT-058 (TC_PR_053): Malformed PR payload error envelope & request_id
# ==============================================================================
@pytest.mark.django_db
def test_tc_pr_053_malformed_payload_error_envelope_and_request_id(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["req"])

    # Post invalid payload
    res = client.post("/api/v1/requisitions/", {"title": ""}, format="json")
    assert res.status_code == status.HTTP_400_BAD_REQUEST
    assert "error" in res.data
    error = res.data["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "request_id" in error
    assert isinstance(error["request_id"], str) and len(error["request_id"]) > 0
    assert "fields" in error or "details" in error


# ==============================================================================
# 3. E2E-RT-083 & 084 (TC_SRC_011 & TC_SRC_015): Sourcing 3-Vendor Publish Rule & published_at
# ==============================================================================
@pytest.mark.django_db
def test_tc_src_011_015_publish_validation_and_audit(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["exec"])

    # Create sourcing event in DRAFT
    event = SourcingEvent.objects.create(
        event_number="RFQ-E2E-TEST-001",
        title="Server Procurement Event",
        event_type=SourcingEvent.TYPE_RFQ,
        status=SourcingEvent.STATUS_DRAFT,
        bid_start_date=datetime.datetime.now(datetime.timezone.utc),
        bid_end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7),
        description="Requirement for enterprise servers",
        technical_weight=Decimal("50.00"),
        commercial_weight=Decimal("50.00"),
    )

    # 1 invite only -> publish must be blocked with HTTP 422
    BidInvite.objects.create(event=event, vendor=data["vendors"][0])
    res1 = client.post(f"/api/v1/sourcing-events/{event.id}/publish/")
    assert res1.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert res1.data["error"]["code"] == "min_vendors_required"
    event.refresh_from_db()
    assert event.status == SourcingEvent.STATUS_DRAFT

    # Add 2 more active vendors -> 3 active invites total
    BidInvite.objects.create(event=event, vendor=data["vendors"][1])
    BidInvite.objects.create(event=event, vendor=data["vendors"][2])

    # Publish succeeds with 200
    res2 = client.post(f"/api/v1/sourcing-events/{event.id}/publish/")
    assert res2.status_code == status.HTTP_200_OK
    assert res2.data["status"] in [SourcingEvent.STATUS_PUBLISHED, SourcingEvent.STATUS_BID_WINDOW]
    assert res2.data["published_at"] is not None

    # Verify AuditLog recorded ACTION_PUBLISH
    log = AuditLog.objects.filter(target_model="SourcingEvent", target_object_id=str(event.id), action="PUBLISH")
    assert log.exists()


# ==============================================================================
# 4. E2E-RT-079 (TC_SRC_001): Sourcing Event copies lines from PR
# ==============================================================================
@pytest.mark.django_db
def test_tc_src_001_sourcing_event_copies_pr_lines_and_pr_id(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["exec"])

    pr = PurchaseRequisition.objects.create(
        pr_number="PR-E2E-3001",
        title="Network Switches",
        requester=data["req"],
        department=data["dept"],
        cost_center=data["cc"],
        status=PurchaseRequisition.STATUS_APPROVED,
        total_amount=Decimal("3000.00"),
        requested_delivery_date=datetime.date.today() + datetime.timedelta(days=7),
    )
    for i in range(1, 4):
        PRLine.objects.create(
            requisition=pr,
            item_description=f"Switch Port Module {i}",
            quantity=Decimal("5.00"),
            estimated_unit_price=Decimal("200.00"),
            estimated_total=Decimal("1000.00"),
        )

    event = SourcingEvent.objects.create(
        event_number="RFQ-E2E-3001",
        title="Switches RFQ",
        event_type=SourcingEvent.TYPE_RFQ,
        requisition=pr,
        status=SourcingEvent.STATUS_DRAFT,
        bid_start_date=datetime.datetime.now(datetime.timezone.utc),
        bid_end_date=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=7),
        description="Sourcing event from approved PR",
    )

    res = client.get(f"/api/v1/sourcing-events/{event.id}/")
    assert res.status_code == status.HTTP_200_OK
    assert res.data["pr_id"] == "PR-E2E-3001"
    assert "lines" in res.data
    assert len(res.data["lines"]) == 3


# ==============================================================================
# 5. E2E-RT-149 (TC_CON_038): Contract termination reason & timestamp
# ==============================================================================
@pytest.mark.django_db
def test_tc_con_038_contract_termination_stores_reason_and_timestamp(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["legal"])

    contract = create_contract_service(
        title="Enterprise SLA Support",
        vendor=data["vendors"][0],
        contract_value=Decimal("50000.00"),
        start_date=datetime.date.today(),
        end_date=datetime.date.today() + datetime.timedelta(days=365),
        contract_owner=data["legal"],
    )
    contract.status = Contract.STATUS_ACTIVE
    contract.save(update_fields=["status"])

    res = client.post(
        f"/api/v1/contracts/{contract.id}/terminate/",
        {"reason": "Vendor consistently missed SLA targets"},
        format="json",
    )
    assert res.status_code == status.HTTP_200_OK
    assert res.data["status"] == Contract.STATUS_TERMINATED
    assert res.data["termination_reason"] == "Vendor consistently missed SLA targets"
    assert res.data["terminated_at"] is not None

    # GET also returns termination details
    get_res = client.get(f"/api/v1/contracts/{contract.id}/")
    assert get_res.status_code == status.HTTP_200_OK
    assert get_res.data["termination_reason"] == "Vendor consistently missed SLA targets"
    assert get_res.data["terminated_at"] is not None


# ==============================================================================
# 6. E2E-RT-201 (TC_API_002): Vendor Object exposes vendor_code
# ==============================================================================
@pytest.mark.django_db
def test_tc_api_002_vendor_code_and_status(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["exec"])

    res = client.get("/api/v1/vendors/")
    assert res.status_code == status.HTTP_200_OK
    results = res.data.get("results", res.data)
    assert len(results) >= 3
    first = results[0]
    assert "vendor_code" in first
    assert "vendor_number" in first
    assert first["vendor_code"] == first["vendor_number"]
    assert first["status"] == "ACTIVE"


# ==============================================================================
# 7. E2E-RT-203 (TC_API_009): OpenAPI Schema & Swagger UI
# ==============================================================================
@pytest.mark.django_db
def test_tc_api_009_schema_and_swagger_endpoints():
    client = APIClient()

    # Default /api/v1/schema/ serves Swagger UI HTML
    res_html = client.get("/api/v1/schema/")
    assert res_html.status_code == status.HTTP_200_OK
    assert "text/html" in res_html.headers.get("Content-Type", "")

    # ?format=json serves OpenAPI JSON
    res_json = client.get("/api/v1/schema/?format=json")
    assert res_json.status_code == status.HTTP_200_OK
    assert res_json.data.get("openapi", "").startswith("3.")
    assert "ProcureSphere" in res_json.data.get("info", {}).get("title", "")
    paths = res_json.data.get("paths", {})
    assert any("/vendors/" in p for p in paths)
    assert any("/requisitions/" in p for p in paths)
    assert any("/sourcing/" in p or "/sourcing-events/" in p for p in paths)


# ==============================================================================
# 8. E2E-RT-170 & 171 (TC_SUP_008 & TC_SUP_009): Scorecard score ranges
# ==============================================================================
@pytest.mark.django_db
def test_tc_sup_008_009_scorecard_score_ranges(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["exec"])
    vendor = data["vendors"][0]

    # Rejects 101
    res_high = client.post(
        "/api/v1/scorecards/",
        {"vendor": str(vendor.id), "period": "2026-Q4", "overall_score": 101},
        format="json",
    )
    assert res_high.status_code == status.HTTP_400_BAD_REQUEST

    # Rejects -1
    res_low = client.post(
        "/api/v1/scorecards/",
        {"vendor": str(vendor.id), "period": "2026-Q4", "overall_score": -1},
        format="json",
    )
    assert res_low.status_code == status.HTTP_400_BAD_REQUEST

    # Accepts boundary 0
    res_zero = client.post(
        "/api/v1/scorecards/",
        {"vendor": str(vendor.id), "period": "2026-Q4", "overall_score": 0},
        format="json",
    )
    assert res_zero.status_code == status.HTTP_201_CREATED
    assert Decimal(str(res_zero.data["overall_score"])) == Decimal("0.00")
    sc_id = res_zero.data["id"]

    # Accepts boundary 100 via PUT/PATCH
    res_hundred = client.patch(
        f"/api/v1/scorecards/{sc_id}/",
        {"overall_score": 100},
        format="json",
    )
    assert res_hundred.status_code == status.HTTP_200_OK
    assert Decimal(str(res_hundred.data["overall_score"])) == Decimal("100.00")


# ==============================================================================
# 9. E2E-RT-177 & 179 (TC_NTF_014 & TC_NTF_028): Notification read toggles & unknown fields
# ==============================================================================
@pytest.mark.django_db
def test_tc_ntf_014_028_notification_patch_and_unknown_fields(e2e_setup):
    data = e2e_setup
    client = APIClient()
    client.force_authenticate(user=data["req"])

    notif = Notification.objects.create(
        recipient=data["req"],
        notification_type=Notification.TYPE_APPROVAL_REQUIRED,
        title="Test PR Pending",
        message="Please review requisition.",
        is_read=False,
    )

    # Unknown field returns 400 with field error and request_id
    res_err = client.patch(f"/api/v1/notifications/{notif.id}/", {"status": "invalid"}, format="json")
    assert res_err.status_code == status.HTTP_400_BAD_REQUEST
    assert "error" in res_err.data
    assert "status" in res_err.data["error"].get("fields", res_err.data["error"].get("details", {}))
    assert "request_id" in res_err.data["error"]

    # PATCH read=True sets read=True and read_at timestamp
    res_read = client.patch(f"/api/v1/notifications/{notif.id}/", {"read": True}, format="json")
    assert res_read.status_code == status.HTTP_200_OK
    assert res_read.data["read"] is True
    assert res_read.data["read_at"] is not None

    # PATCH read=False resets read=False and clears read_at
    res_unread = client.patch(f"/api/v1/notifications/{notif.id}/", {"read": False}, format="json")
    assert res_unread.status_code == status.HTTP_200_OK
    assert res_unread.data["read"] is False
    assert res_unread.data["read_at"] is None

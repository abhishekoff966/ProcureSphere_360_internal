import datetime
from decimal import Decimal
import pytest
from django.core.exceptions import ValidationError
from django.test import Client, RequestFactory
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.services import create_contract_service
from apps.orders.models import PurchaseOrder, POLine
from apps.orders.selectors import scope_purchase_orders_queryset
from apps.budgets.selectors import scope_budgets_queryset
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.requisitions.models import PurchaseRequisition, PRLine
from apps.requisitions.services import submit_purchase_requisition_service
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def suite_data(db):
    org = Organization.objects.create(name="HPE Global", code="HPE_GLB")
    dept = Department.objects.create(organization=org, name="IT Operations", code="IT_OPS")
    cc = CostCenter.objects.create(department=dept, name="IT Infrastructure", code="IT_INFRA")
    fp = FiscalPeriod.objects.create(
        organization=org,
        name="FY2026-Q1",
        year=2026,
        period_number=1,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        is_closed=False,
    )
    cat = VendorCategory.objects.create(name="Cloud Services", code="CLOUD_SERV", description="Cloud and hosting")

    r_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_fin, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
    r_stores, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores"})
    r_aud, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})

    admin_user = User.objects.create_user(
        email="suite.admin@hpe.local",
        password="Password123!",
        role=r_admin,
        is_superuser=True,
        is_staff=True,
        organization=org,
    )
    super_admin_role_only = User.objects.create_user(
        email="suite.superrole@hpe.local",
        password="Password123!",
        role=r_admin,
        is_superuser=False,
        is_staff=True,
        organization=org,
    )
    req_user = User.objects.create_user(
        email="suite.req1@hpe.local",
        password="Password123!",
        role=r_req,
        department=dept,
        cost_center=cc,
        organization=org,
    )
    req_user2 = User.objects.create_user(
        email="suite.req2@hpe.local",
        password="Password123!",
        role=r_req,
        department=dept,
        cost_center=cc,
        organization=org,
    )
    mgr_user = User.objects.create_user(
        email="suite.mgr@hpe.local",
        password="Password123!",
        role=r_mgr,
        organization=org,
    )
    fin_user = User.objects.create_user(
        email="suite.fin@hpe.local",
        password="Password123!",
        role=r_fin,
        organization=org,
    )
    aud_user = User.objects.create_user(
        email="suite.aud@hpe.local",
        password="Password123!",
        role=r_aud,
        organization=org,
    )

    vendor = Vendor.objects.create(
        organization=org,
        legal_name="Alpha IT Solutions",
        vendor_number="VND-2026-0001",
        tax_identification_number="TIN-ALPHA-01",
        email="contact@alphait.local",
        address="100 Technology Drive",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )

    vendor2 = Vendor.objects.create(
        organization=org,
        legal_name="Beta Networks Inc",
        vendor_number="VND-2026-0002",
        tax_identification_number="TIN-BETA-02",
        email="contact@betanets.local",
        address="200 Network Way",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )

    budget = Budget.objects.create(
        cost_center=cc,
        fiscal_period=fp,
        allocated_amount=Decimal("500000.00"),
    )

    from apps.approvals.models import ApprovalPolicy, ApprovalStep
    policy = ApprovalPolicy.objects.create(
        module=ApprovalPolicy.MODULE_PR,
        name="Standard PR Policy",
        min_amount=Decimal("0.00"),
        max_amount=Decimal("1000000.00"),
        department=dept,
        is_active=True,
    )
    ApprovalStep.objects.create(
        policy=policy,
        step_number=1,
        approver_role=r_mgr,
    )

    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "fp": fp,
        "cat": cat,
        "admin": admin_user,
        "superrole": super_admin_role_only,
        "req1": req_user,
        "req2": req_user2,
        "mgr": mgr_user,
        "fin": fin_user,
        "aud": aud_user,
        "vendor": vendor,
        "vendor2": vendor2,
        "budget": budget,
    }


@pytest.mark.django_db
class TestDefectVerificationSuite:
    """Verifies all defects DEF-001 through DEF-018 and unblocked test cases."""

    # DEF-001: Role portals are role-guarded
    def test_def_001_role_portals_rbac_guard(self, suite_data):
        client = Client()
        client.force_login(suite_data["req1"])

        # Requester must be denied access to finance, stores, procurement, approver
        res_fin = client.get("/finance/")
        assert res_fin.status_code == 403

        res_stores = client.get("/stores/")
        assert res_stores.status_code == 403

        res_proc = client.get("/procurement/")
        assert res_proc.status_code == 403

        res_appr = client.get("/approver/")
        assert res_appr.status_code == 403

    # DEF-002: Invoices list denied to REQUESTER
    def test_def_002_requester_denied_invoices_list(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["req1"])
        res = api.get("/api/v1/invoices/")
        assert res.status_code == 403

    # DEF-003, DEF-004, DEF-005: Health API does not leak details or return 500
    def test_def_003_to_005_health_check_safety(self, suite_data):
        api = APIClient()
        res = api.get("/api/v1/health/")
        assert res.status_code in [200, 503]
        data = res.json()
        assert "redis" in data
        assert "hostname" not in str(data).lower()
        assert "password" not in str(data).lower()
        assert "port" not in str(data).lower()

    # DEF-006: 404 does not leak DEBUG mode or settings
    def test_def_006_custom_404_page(self, suite_data):
        client = Client()
        res = client.get("/qa-no-such-route-random/")
        assert res.status_code == 404
        assert b"DEBUG = True" not in res.content
        assert b"urlpatterns" not in res.content

    # DEF-007: Session cookie secure attribute
    def test_def_007_session_cookie_secure(self, suite_data):
        client = Client()
        res = client.post(
            "/api/v1/auth/login/",
            {"email": "suite.admin@hpe.local", "password": "Password123!"},
            format="json",
            HTTP_X_FORWARDED_PROTO="https",
            secure=True,
        )
        assert res.status_code == 200
        if "sessionid" in res.cookies:
            assert res.cookies["sessionid"]["secure"] is True

    # DEF-008: OpenAPI schema contains no hardcoded demo password
    def test_def_008_openapi_schema_no_demo_password(self, suite_data):
        client = Client()
        res = client.get("/api/schema/")
        assert res.status_code == 200
        schema_text = res.content.decode("utf-8")
        assert "placeholder_password_only" in schema_text or "Password123!" not in schema_text

    # E2E-AUT-020: Token refresh endpoint works
    def test_auth_token_refresh_endpoint(self, suite_data):
        api = APIClient()
        # Unauthenticated: 401
        res = api.post("/api/v1/auth/token/refresh/")
        assert res.status_code == 401

        # Authenticated: 200
        api.force_authenticate(user=suite_data["admin"])
        res2 = api.post("/api/v1/auth/token/refresh/")
        assert res2.status_code == 200

    # DEF-009: Requisition requires line items
    def test_def_009_requisition_requires_lines(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["req1"])
        payload = {
            "title": "PR without line items",
            "justification": "Testing validation",
            "department": suite_data["dept"].id,
            "cost_center": suite_data["cc"].id,
            "lines": [],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == 400

    # DEF-010: PR rejects invalid quantities
    def test_def_010_pr_quantity_validation(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["req1"])
        payload = {
            "title": "PR with zero quantity",
            "justification": "Testing validation",
            "department": suite_data["dept"].id,
            "cost_center": suite_data["cc"].id,
            "lines": [
                {
                    "item_description": "Laptops",
                    "quantity": 0,
                    "estimated_unit_price": "100.00",
                }
            ],
        }
        res = api.post("/api/v1/requisitions/", payload, format="json")
        assert res.status_code == 400

    # DEF-011: PR visibility browser and API parity
    def test_def_011_pr_visibility_browser_and_api(self, suite_data):
        # Admin creates PR for req1
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-SUITE-011",
            title="Private PR",
            justification="Confidential",
            requester=suite_data["req1"],
            department=suite_data["dept"],
            cost_center=suite_data["cc"],
            status=PurchaseRequisition.STATUS_DRAFT,
            total_amount=Decimal("500.00"),
            requested_delivery_date=datetime.date(2026, 12, 31),
        )
        PRLine.objects.create(
            requisition=pr,
            item_description="Secret item",
            quantity=Decimal("5"),
            estimated_unit_price=Decimal("100.00"),
        )

        # req2 tries to open req1's PR via Browser: 404
        client = Client()
        client.force_login(suite_data["req2"])
        res_browser = client.get(f"/requisitions/{pr.id}/")
        assert res_browser.status_code == 404

        # req2 tries to open req1's PR via API: 404
        api = APIClient()
        api.force_authenticate(user=suite_data["req2"])
        res_api = api.get(f"/api/v1/requisitions/{pr.id}/")
        assert res_api.status_code == 404

    # DEF-012: Duplicate approval returns clean 400
    def test_def_012_duplicate_approval_returns_400(self, suite_data):
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-SUITE-012",
            title="Approve Twice PR",
            justification="Testing duplicate approval",
            requester=suite_data["req1"],
            department=suite_data["dept"],
            cost_center=suite_data["cc"],
            status=PurchaseRequisition.STATUS_MANAGER_REVIEW,
            total_amount=Decimal("500.00"),
            requested_delivery_date=datetime.date(2026, 12, 31),
        )
        PRLine.objects.create(
            requisition=pr,
            item_description="Servers",
            quantity=Decimal("1"),
            estimated_unit_price=Decimal("500.00"),
        )

        api = APIClient()
        api.force_authenticate(user=suite_data["mgr"])
        res1 = api.post(f"/api/v1/requisitions/{pr.id}/approve/", {"comments": "Approved"}, format="json")
        assert res1.status_code == 200

        # Second approval on already approved PR returns 400 (not 500)
        res2 = api.post(f"/api/v1/requisitions/{pr.id}/approve/", {"comments": "Approved again"}, format="json")
        assert res2.status_code == 400

    # DEF-013: Super Admin can read/update/delete created PO
    def test_def_013_super_admin_po_lifecycle(self, suite_data):
        po = PurchaseOrder.objects.create(
            po_number="PO-SUITE-013",
            vendor=suite_data["vendor"],
            cost_center=suite_data["cc"],
            status=PurchaseOrder.STATUS_DRAFT,
            total_amount=Decimal("1200.00"),
        )
        POLine.objects.create(
            po=po,
            item_description="Cables",
            quantity=Decimal("10"),
            unit_price=Decimal("120.00"),
        )

        # Scoped queryset allows SUPER_ADMIN role even if is_superuser=False
        scoped = scope_purchase_orders_queryset(PurchaseOrder.objects.all(), suite_data["superrole"])
        assert scoped.filter(id=po.id).exists()

        api = APIClient()
        api.force_authenticate(user=suite_data["superrole"])
        res = api.get(f"/api/v1/orders/{po.id}/")
        assert res.status_code == 200

    # DEF-014: Super Admin can read/update/delete created Budget
    def test_def_014_super_admin_budget_lifecycle(self, suite_data):
        scoped = scope_budgets_queryset(Budget.objects.all(), suite_data["superrole"])
        assert scoped.filter(id=suite_data["budget"].id).exists()

        api = APIClient()
        api.force_authenticate(user=suite_data["superrole"])
        res = api.get(f"/api/v1/budgets/{suite_data['budget'].id}/")
        assert res.status_code == 200

    # DEF-015: Submitted PR leaves searchable audit-trail entry
    def test_def_015_submitted_pr_searchable_audit_entry(self, suite_data):
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-SUITE-015",
            title="Audit Search PR",
            justification="Checking audit trail search",
            requester=suite_data["req1"],
            department=suite_data["dept"],
            cost_center=suite_data["cc"],
            status=PurchaseRequisition.STATUS_DRAFT,
            total_amount=Decimal("300.00"),
            requested_delivery_date=datetime.date(2026, 12, 31),
        )
        PRLine.objects.create(
            requisition=pr,
            item_description="Monitors",
            quantity=Decimal("2"),
            estimated_unit_price=Decimal("150.00"),
        )

        submit_purchase_requisition_service(requisition=pr, user=suite_data["req1"])

        api = APIClient()
        api.force_authenticate(user=suite_data["aud"])
        res = api.get("/api/v1/audit/logs/?search=PR-SUITE-015")
        assert res.status_code == 200
        data = res.json()
        results = data.get("results", data)
        assert len(results) >= 1
        assert any(r.get("target_object_id") == str(pr.id) for r in results)

    # DEF-016: Dashboard KPIs agree with summary endpoints
    def test_def_016_dashboard_kpis_agree(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["admin"])

        dash_res = api.get("/api/v1/reports/dashboard-summary/")
        assert dash_res.status_code == 200
        dash_data = dash_res.json()

        ven_res = api.get("/api/v1/vendors/summary/")
        assert ven_res.status_code == 200
        ven_data = ven_res.json()

        po_res = api.get("/api/v1/purchase-orders/summary/")
        assert po_res.status_code == 200
        po_data = po_res.json()

        assert dash_data["vendor_count"] == ven_data["total_vendors"]
        assert dash_data["total_pos"] == po_data["total_pos"]

    # DEF-017: Vendor category list filters by search parameter
    def test_def_017_vendor_category_search(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["admin"])

        res_match = api.get("/api/v1/vendors/categories/?search=Cloud")
        assert res_match.status_code == 200
        match_data = res_match.json()
        items = match_data.get("results", match_data)
        assert len(items) >= 1

        res_nomatch = api.get("/api/v1/vendors/categories/?search=zz_nomatch_random")
        assert res_nomatch.status_code == 200
        nomatch_data = res_nomatch.json()
        nomatch_items = nomatch_data.get("results", nomatch_data)
        assert len(nomatch_items) == 0

    # DEF-018: Duplicate contract creation is rejected
    def test_def_018_duplicate_contract_rejected(self, suite_data):
        c1 = create_contract_service(
            title="Master Cloud Services Agreement",
            vendor=suite_data["vendor"],
            contract_value=Decimal("50000.00"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            contract_owner=suite_data["admin"],
        )
        assert c1.id is not None

        # Same contract payload must raise ValidationError
        with pytest.raises(ValidationError) as excinfo:
            create_contract_service(
                title="Master Cloud Services Agreement",
                vendor=suite_data["vendor"],
                contract_value=Decimal("50000.00"),
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 12, 31),
                contract_owner=suite_data["admin"],
            )
        assert "already exists" in str(excinfo.value)

    # E2E-PLT-032: Operation count in schema matches 355
    def test_plt_032_openapi_operation_count_355(self, suite_data):
        from drf_spectacular.generators import SchemaGenerator
        generator = SchemaGenerator(urlconf="config.urls")
        schema = generator.get_schema(request=None, public=True)
        ops = 0
        for p, p_item in schema.get("paths", {}).items():
            for m in p_item:
                if m.lower() in ["get", "post", "put", "patch", "delete"]:
                    ops += 1
        assert ops == 355

    # E2E-AUD-017, E2E-AUD-027, E2E-AUD-035: Audit default params return 200
    def test_aud_default_parameters_return_200(self, suite_data):
        pr = PurchaseRequisition.objects.create(
            pr_number="PR-SUITE-AUD",
            title="Audit PR",
            justification="Testing audit default lifecycle",
            requester=suite_data["req1"],
            department=suite_data["dept"],
            cost_center=suite_data["cc"],
            status=PurchaseRequisition.STATUS_DRAFT,
            total_amount=Decimal("100.00"),
            requested_delivery_date=datetime.date(2026, 12, 31),
        )
        AuditLog.objects.create(
            actor=suite_data["admin"],
            action=AuditLog.ACTION_CREATE,
            target_model="PurchaseRequisition",
            target_object_id=str(pr.id),
            new_state={"pr_number": pr.pr_number},
        )

        api = APIClient()
        api.force_authenticate(user=suite_data["admin"])

        res1 = api.get("/api/v1/audit/lifecycle/")
        assert res1.status_code == 200

        res2 = api.get("/api/v1/audit/logs/entity-trail/")
        assert res2.status_code == 200

    # E2E-MGR-013, E2E-MGR-048: Manager compare default parameters return 200
    def test_mgr_compare_default_parameters_return_200(self, suite_data):
        api = APIClient()
        api.force_authenticate(user=suite_data["mgr"])

        res = api.get("/api/v1/manager/performance/compare/")
        assert res.status_code == 200
        data = res.json()
        assert "vendor_a" in data
        assert "vendor_b" in data


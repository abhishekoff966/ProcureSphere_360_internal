import pytest
from django.db import connection
from django.test import Client
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contracts.models import Contract
from apps.orders.models import PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.vendors.models import Vendor, VendorCategory


@pytest.mark.django_db
def test_auditor_dashboard_queries():
    org = Organization.objects.create(name="HPE Global", code="HPE_GLB")
    dept = Department.objects.create(organization=org, name="Internal Audit", code="IA")
    r_aud, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    aud_user = User.objects.create_user(
        email="auditor.perf@hpe.local",
        password="Password123!",
        role=r_aud,
        department=dept,
        organization=org,
    )
    cc = CostCenter.objects.create(department=dept, code="CC-IA", name="Internal Audit Cost Center")
    cat = VendorCategory.objects.create(name="Tech", code="TECH")
    vendor = Vendor.objects.create(organization=org, legal_name="V1", vendor_number="VND-001", category=cat)
    po = PurchaseOrder.objects.create(po_number="PO-001", vendor=vendor, cost_center=cc, total_amount=100)
    contract = Contract.objects.create(contract_number="CON-001", vendor=vendor, contract_owner=aud_user, start_date="2026-01-01", end_date="2026-12-31")

    # Seed 10 audit logs
    for i in range(10):
        AuditLog.objects.create(
            actor=aud_user,
            action=AuditLog.ACTION_CREATE,
            target_model="PurchaseOrder",
            target_object_id=str(po.id),
        )

    client = Client()
    client.force_login(aud_user)

    # Measure dashboard queries
    with CaptureQueriesContext(connection) as ctx_dash:
        res_dash = client.get("/audit/dashboard/")
    print(f"\n[DASHBOARD] Status: {res_dash.status_code}, Query Count: {len(ctx_dash.captured_queries)}")
    from collections import Counter
    sql_prefixes = Counter([q['sql'].split()[0] + ' ' + (q['sql'].split()[1] if len(q['sql'].split()) > 1 else '') for q in ctx_dash.captured_queries])
    print(f"Query types: {dict(sql_prefixes)}")
    # Measure logs queries
    with CaptureQueriesContext(connection) as ctx_logs:
        res_logs = client.get("/audit/logs/")
    print(f"[LOGS] Status: {res_logs.status_code}, Query Count: {len(ctx_logs.captured_queries)}")
    assert res_logs.context["limit"] == 10
    assert len(res_logs.context["logs"]) <= 10

    # Measure approvals queries
    with CaptureQueriesContext(connection) as ctx_appr:
        res_appr = client.get("/audit/approvals/")
    print(f"[APPROVALS] Status: {res_appr.status_code}, Query Count: {len(ctx_appr.captured_queries)}")

    # Measure vendors queries
    with CaptureQueriesContext(connection) as ctx_vnd:
        res_vnd = client.get("/audit/vendors/")
    print(f"[VENDORS] Status: {res_vnd.status_code}, Query Count: {len(ctx_vnd.captured_queries)}")

    # Measure PO changes queries
    with CaptureQueriesContext(connection) as ctx_po:
        res_po = client.get("/audit/po-changes/")
    print(f"[PO CHANGES] Status: {res_po.status_code}, Query Count: {len(ctx_po.captured_queries)}")

    # Assert query counts are optimized and prevent regression
    assert len(ctx_dash.captured_queries) <= 45, f"Dashboard query count too high: {len(ctx_dash.captured_queries)}"
    assert len(ctx_logs.captured_queries) <= 12, f"Logs query count too high: {len(ctx_logs.captured_queries)}"
    assert len(ctx_appr.captured_queries) <= 10, f"Approvals query count too high: {len(ctx_appr.captured_queries)}"
    assert len(ctx_vnd.captured_queries) <= 15, f"Vendors query count too high: {len(ctx_vnd.captured_queries)}"
    assert len(ctx_po.captured_queries) <= 12, f"PO changes query count too high: {len(ctx_po.captured_queries)}"


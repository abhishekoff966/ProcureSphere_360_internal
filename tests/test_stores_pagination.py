import datetime
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.budgets.services import allocate_budget_service
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def stores_pagination_world(db, db_roles):
    org = Organization.objects.create(name="HPE Stores Pagination Org", code="HPE-PG-ORG")
    dept = Department.objects.create(organization=org, name="Warehouse Ops", code="DEPT-PG-WH")
    cost_center = CostCenter.objects.create(department=dept, code="CC-PG-WH", name="WH Dock")
    fiscal_period = FiscalPeriod.objects.create(
        organization=org,
        year=2026,
        period_number=2,
        name="FY2026-PG-Q2",
        start_date=timezone.now().date() - datetime.timedelta(days=5),
        end_date=timezone.now().date() + datetime.timedelta(days=85),
    )
    allocate_budget_service(
        cost_center=cost_center,
        fiscal_period=fiscal_period,
        allocated_amount=Decimal("500000.00"),
    )

    cat = VendorCategory.objects.create(name="Hardware Supply", code="CAT-HW-PG")
    vendor_a = Vendor.objects.create(
        legal_name="Alpha Logistics Hardware Ltd",
        vendor_number="VND-PG-001",
        tax_identification_number="TIN-ALH-PG-01",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
        organization=org,
    )
    vendor_b = Vendor.objects.create(
        legal_name="Beta Precision Cables Ltd",
        vendor_number="VND-PG-002",
        tax_identification_number="TIN-BPC-PG-02",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
        organization=org,
    )

    receiver = User.objects.create_user(
        email="stores_pg@hpe.com",
        password="Password123!",
        role=db_roles[Role.STORES_RECEIVER],
        organization=org,
        department=dept,
    )
    requester = User.objects.create_user(
        email="req_pg@hpe.com",
        password="Password123!",
        role=db_roles[Role.REQUESTER],
        organization=org,
        department=dept,
    )

    pos = []
    po_lines = []
    # 13 receivable POs -> spans 2 pages (10 + 3)
    for idx in range(1, 14):
        status = (
            PurchaseOrder.STATUS_ISSUED
            if idx % 3 == 1
            else (PurchaseOrder.STATUS_ACKNOWLEDGED if idx % 3 == 2 else PurchaseOrder.STATUS_PARTIAL_RECEIPT)
        )
        po = PurchaseOrder.objects.create(
            po_number=f"PO-PG-2026-{idx:03d}",
            version=1,
            vendor=vendor_a if idx <= 8 else vendor_b,
            cost_center=cost_center,
            status=status,
            total_amount=Decimal("1000.00"),
        )
        line = POLine.objects.create(
            po=po,
            item_description=f"Pagination Rack Unit #{idx:02d}",
            quantity=Decimal("10.00"),
            unit_of_measure="EA",
            unit_price=Decimal("100.00"),
            quantity_received=Decimal("0.00"),
        )
        pos.append(po)
        po_lines.append(line)

    # Completed PO should be excluded from STORES_RECEIVER receivable deliveries list
    PurchaseOrder.objects.create(
        po_number="PO-PG-COMPLETED-999",
        version=1,
        vendor=vendor_a,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_COMPLETED,
        total_amount=Decimal("500.00"),
    )

    # Create 12 GoodsReceipt + ReceiptLine entries -> spans 2 pages (10 + 2)
    receipt_lines = []
    now = timezone.now()
    for idx in range(1, 13):
        po = pos[idx - 1]
        po_line = po_lines[idx - 1]
        grn = GoodsReceipt.objects.create(
            grn_number=f"GRN-PG-2026-{idx:03d}",
            po=po,
            received_by=receiver,
            received_date=now - datetime.timedelta(minutes=idx),
            delivery_note_number=f"DN-PG-{idx:03d}",
        )
        qty_acc = Decimal("10.00") if idx % 2 == 1 else Decimal("7.00")
        rl = ReceiptLine.objects.create(
            receipt=grn,
            po_line=po_line,
            quantity_received=Decimal("10.00"),
            quantity_accepted=qty_acc,
            notes="" if idx % 2 == 1 else "Damaged casing in transit",
        )
        receipt_lines.append(rl)

    return {
        "org": org,
        "receiver": receiver,
        "requester": requester,
        "vendor_a": vendor_a,
        "vendor_b": vendor_b,
        "pos": pos,
        "receipt_lines": receipt_lines,
    }


@pytest.mark.django_db
def test_stores_po_deliveries_pagination_preserves_tab_and_navigates_pages(client, stores_pagination_world):
    receiver = stores_pagination_world["receiver"]
    client.force_login(receiver)

    # Page 1 under PO Deliveries tab (?tab=deliveries)
    resp1 = client.get("/purchase-orders/?tab=deliveries")
    assert resp1.status_code == 200
    html1 = resp1.content.decode()

    assert len(resp1.context["items"]) == 10
    assert resp1.context["page_obj"].number == 1
    assert resp1.context["paginator"].num_pages == 2
    assert "Showing 1–10 of 13" in html1
    assert '<li class="page-item disabled"><span class="page-link">Previous</span></li>' in html1
    assert 'href="?tab=deliveries&page=2">Next</a>' in html1
    assert "PO-PG-COMPLETED-999" not in html1
    # Ordered by -created_at: PO-PG-2026-013..004 on page 1, PO-PG-2026-003..001 on page 2
    assert "PO-PG-2026-013" in html1
    assert "PO-PG-2026-001" not in html1

    # Page 2 preserves ?tab=deliveries and links back via Previous
    resp2 = client.get("/purchase-orders/?tab=deliveries&page=2")
    assert resp2.status_code == 200
    html2 = resp2.content.decode()

    assert len(resp2.context["items"]) == 3
    assert resp2.context["page_obj"].number == 2
    assert "Showing 11–13 of 13" in html2
    assert 'href="?tab=deliveries&page=1">Previous</a>' in html2
    assert '<li class="page-item disabled"><span class="page-link">Next</span></li>' in html2
    assert "PO-PG-2026-003" in html2
    assert "PO-PG-2026-001" in html2
    assert "PO-PG-2026-013" not in html2

    # Base /purchase-orders/ (Receive PO Goods) also paginates cleanly
    resp_base = client.get("/purchase-orders/?page=2")
    assert resp_base.status_code == 200
    assert len(resp_base.context["items"]) == 3
    assert 'href="?page=1">Previous</a>' in resp_base.content.decode()

    # Out-of-range page gracefully clamps to last page
    resp_oob = client.get("/purchase-orders/?tab=deliveries&page=99")
    assert resp_oob.status_code == 200
    assert resp_oob.context["page_obj"].number == 2


@pytest.mark.django_db
def test_stores_delivery_and_rejection_report_pagination_and_full_export(client, stores_pagination_world):
    receiver = stores_pagination_world["receiver"]
    requester = stores_pagination_world["requester"]

    # Unauthorized role stays blocked from rendering report
    client.force_login(requester)
    unauth_resp = client.get("/receipts/reports/delivery-performance/")
    assert unauth_resp.status_code == 302
    assert unauth_resp.url == "/receipts/"

    client.force_login(receiver)

    # Page 1: 10 rows of 12 total, Next -> ?page=2, KPI summary reflects full 12 rows
    resp1 = client.get("/receipts/reports/delivery-performance/")
    assert resp1.status_code == 200
    html1 = resp1.content.decode()

    assert len(resp1.context["report_lines"]) == 10
    assert resp1.context["page_obj"].number == 1
    assert resp1.context["paginator"].num_pages == 2
    assert resp1.context["summary"]["total_lines"] == 12
    assert "Showing 1–10 of 12" in html1
    assert '<li class="page-item disabled"><span class="page-link">Previous</span></li>' in html1
    assert 'href="?page=2">Next</a>' in html1

    # Page 2 via both /receipts/reports/delivery-performance/ and /reports/delivery-performance/
    for url in (
        "/receipts/reports/delivery-performance/?page=2",
        "/reports/delivery-performance/?page=2",
    ):
        resp2 = client.get(url)
        assert resp2.status_code == 200
        html2 = resp2.content.decode()
        assert len(resp2.context["report_lines"]) == 2
        assert resp2.context["page_obj"].number == 2
        assert "Showing 11–12 of 12" in html2
        assert 'href="?page=1">Previous</a>' in html2
        assert '<li class="page-item disabled"><span class="page-link">Next</span></li>' in html2

    # Filters preserved in querystring when paging (status=REJECTED has 6 items -> 1 page; verify filter works & shows count)
    resp_filtered = client.get("/receipts/reports/delivery-performance/?status=REJECTED")
    assert resp_filtered.status_code == 200
    assert resp_filtered.context["paginator"].count == 6
    assert "Showing 6 of 6 records" in resp_filtered.content.decode()

    # Multi-page filter preservation: vendor_a has 8 POs -> wait, verify filter keeps query params on ?page=2 when > 10 rows match
    resp_filter_all = client.get("/receipts/reports/delivery-performance/?po_number=PO-PG-2026&page=2")
    assert resp_filter_all.status_code == 200
    assert 'href="?po_number=PO-PG-2026&page=1">Previous</a>' in resp_filter_all.content.decode()

    # CSV and XLSX exports still return ALL 12 rows regardless of page parameter
    csv_resp = client.get("/receipts/reports/delivery-performance/?export=csv&page=2")
    assert csv_resp.status_code == 200
    csv_lines = [line for line in csv_resp.content.decode("utf-8").splitlines() if line.strip()]
    # Header row + 12 data rows
    assert len(csv_lines) == 13

    xlsx_resp = client.get("/receipts/reports/delivery-performance/?export=xlsx")
    assert xlsx_resp.status_code == 200
    assert "spreadsheetml" in xlsx_resp["Content-Type"]


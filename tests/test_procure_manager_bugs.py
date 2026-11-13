from datetime import timedelta

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PRAttachment, PurchaseRequisition


@pytest.fixture
def proc_manager_user(db):
    org = Organization.objects.create(name="HPE Org", code="HPE-MGR")
    dept = Department.objects.create(name="Procurement", code="PROC-MGR", organization=org)
    cc = CostCenter.objects.create(name="Core Ops", code="CC-MGR-01", department=dept)
    role, _ = Role.objects.get_or_create(
        code="PROC_MGR",
        defaults={"name": "Procurement Manager"},
    )
    user = User.objects.create_user(
        email="procmgr_test@hpe.com",
        password="TestPassword123!",
        role=role,
        organization=org,
        department=dept,
    )
    return user, org, dept, cc


@pytest.mark.django_db
@pytest.mark.parametrize(
    "path",
    [
        "/manager/awards/",
        "/manager/pr-aging/",
        "/manager/purchase-orders/",
        "/manager/vendor-risk/",
        "/manager/policy-exceptions/",
        "/manager/spend/",
        "/manager/performance/",
        "/manager/sourcing/",
        "/manager/pipeline/",
        "/manager/approvals/",
    ],
)
def test_bug002_007_manager_drilldown_pages_include_back_to_dashboard(client, proc_manager_user, path):
    """BUG-002 / BUG-007: Every manager drill-down page provides an explicit Back to Dashboard link."""
    user, _, _, _ = proc_manager_user
    client.force_login(user)
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert 'href="/manager/"' in html
    assert "Back to Dashboard" in html


@pytest.mark.django_db
def test_bug003_pending_awards_page_and_sidebar_label_match_card(client, proc_manager_user):
    """BUG-003: /manager/awards/ and sidebar label use 'Pending Awards' matching the dashboard card."""
    user, _, _, _ = proc_manager_user
    client.force_login(user)

    dash_html = client.get("/manager/").content.decode("utf-8")
    assert 'href="/manager/awards/"' in dash_html

    awards_html = client.get("/manager/awards/").content.decode("utf-8")
    assert ">Pending Awards</h1>" in awards_html
    assert "<span>Pending Awards</span>" in awards_html


@pytest.mark.django_db
def test_bug004_watchlist_table_allows_horizontal_scroll_without_clipping(client, proc_manager_user):
    """BUG-004: Supplier Governance & Sanctions Watchlist table uses overflow-x: auto, not overflow-hidden."""
    user, _, _, _ = proc_manager_user
    client.force_login(user)
    html = client.get("/manager/").content.decode("utf-8")
    assert 'class="table-responsive border rounded-3" style="overflow-x: auto;"' in html
    assert 'class="table-responsive border rounded-3 overflow-hidden"' not in html


@pytest.mark.django_db
def test_bug005_sidebar_includes_invoice_and_policy_exceptions_link(client, proc_manager_user):
    """BUG-005: Left sidebar includes a persistent link to Invoice & Policy Exceptions."""
    user, _, _, _ = proc_manager_user
    client.force_login(user)
    html = client.get("/manager/").content.decode("utf-8")
    assert 'href="/manager/policy-exceptions/?tab=invoices"' in html
    assert "Invoice &amp; Policy Exceptions" in html


@pytest.mark.django_db
def test_bug006_and_008_approvals_desk_header_hierarchy_and_pagination(client, proc_manager_user):
    """BUG-006 & BUG-008: Subtext sits below H1 and approvals table renders pagination + row summary."""
    user, _, _, _ = proc_manager_user
    client.force_login(user)
    html = client.get("/manager/approvals/").content.decode("utf-8")

    h1_pos = html.index('<h1 class="h3 fw-bold text-dark mb-1 font-heading">Pending Approvals Oversight Desk</h1>')
    subtext_pos = html.index("Enterprise pipeline oversight, tier progression, and bottleneck tracking")
    assert subtext_pos > h1_pos

    assert "approvalsPaginationBar" in html
    assert "approvalsRowSummary" in html
    assert "APPROVALS_PAGE_SIZE" in html
    assert "goToApprovalPage" in html


@pytest.mark.django_db
def test_bug009_pr_supporting_document_view_opens_in_app_preview_modal(client, proc_manager_user):
    """BUG-009: Clicking View on PR supporting documents opens an in-app preview modal instead of forcing download."""
    user, _, dept, cc = proc_manager_user
    pr = PurchaseRequisition.objects.create(
        pr_number="PR-2026-99001",
        title="Doc Preview PR",
        justification="Testing inline document preview modal",
        department=dept,
        cost_center=cc,
        requester=user,
        requested_delivery_date=timezone.now().date() + timedelta(days=10),
    )
    PRAttachment.objects.create(
        requisition=pr,
        title="Medilink.doc.docx",
        file=SimpleUploadedFile("Medilink.doc.docx", b"PK\x03\x04dummy-docx"),
        file_size=12,
        uploaded_by=user,
    )

    client.force_login(user)
    resp = client.get(reverse("requisition_detail", args=[pr.pk]))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    head_end = html.index("</head>")
    head_html = html[:head_end]
    body_html = html[head_end:]
    assert "prDocumentPreviewModal" not in head_html
    assert "openPrDocPreview" not in head_html
    assert 'id="prDocumentPreviewModal"' in body_html
    assert 'onclick="openPrDocPreview(this)"' in body_html
    assert "function openPrDocPreview(" in body_html
    assert '?action=view" target="_blank"' not in body_html


@pytest.mark.django_db
def test_bug007_invoice_detail_includes_back_link_for_proc_manager(client, proc_manager_user):
    """BUG-007: /invoices/<id>/ renders a Back link for Procurement Manager."""
    from apps.invoices.models import SupplierInvoice
    from apps.orders.models import PurchaseOrder
    from apps.vendors.models import Vendor, VendorCategory

    user, org, dept, cc = proc_manager_user
    cat = VendorCategory.objects.create(name="IT Equipment", code="IT-EQ-MGR")
    vendor = Vendor.objects.create(
        vendor_number="VND-2026-99002",
        legal_name="Invoice Back Vendor Ltd",
        tax_identification_number="TAX-99002990",
        category=cat,
        email="invvendor@example.com",
        address="100 Main St",
        organization=org,
    )
    po = PurchaseOrder.objects.create(
        po_number="PO-2026-99002",
        vendor=vendor,
        cost_center=cc,
        created_by=user,
    )
    inv = SupplierInvoice.objects.create(
        invoice_number="INV-2026-99002",
        vendor=vendor,
        po=po,
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timedelta(days=30),
        subtotal="100.00",
        tax_amount="0.00",
        total_amount="100.00",
    )

    client.force_login(user)
    resp = client.get(reverse("invoice_detail", args=[inv.pk]))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert 'href="/manager/notifications/"' in html
    assert "Back to Notifications" in html

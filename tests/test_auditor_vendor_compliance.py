from decimal import Decimal
import pytest
from django.test import RequestFactory

from apps.accounts.models import Role, User
from apps.audit.selectors import (
    get_vendor_compliance_audit,
    get_auditor_dashboard_data,
    get_invoice_exceptions_audit,
    get_po_changes_audit,
)
from apps.audit.views import (
    audit_vendors_view,
    audit_invoice_exceptions_view,
    audit_po_changes_view,
    auditor_dashboard_view,
)
from apps.invoices.models import MatchException, SupplierInvoice
from apps.orders.models import PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.vendors.models import Vendor, VendorCategory, VendorDocument


@pytest.mark.django_db
class TestAuditorPortalBugs:
    def test_bug_002_vendor_compliance_audit_counts_and_template_keys(self):
        org = Organization.objects.create(name="Test Org Compliance", code="TOC-01")
        category = VendorCategory.objects.create(name="IT Services", code="IT-01")

        # Create active vendor
        v_active = Vendor.objects.create(
            legal_name="Vendor Active Ltd",
            trade_name="Active Ltd",
            vendor_number="VND-ACT-001",
            tax_identification_number="TIN-ACT-001",
            organization=org,
            category=category,
            status=Vendor.STATUS_ACTIVE,
            email="active@example.com",
            address="123 Active St",
        )

        # Create suspended vendor
        v_suspended = Vendor.objects.create(
            legal_name="Vendor Suspended Ltd",
            trade_name="Suspended Ltd",
            vendor_number="VND-SUS-001",
            tax_identification_number="TIN-SUS-001",
            organization=org,
            category=category,
            status=Vendor.STATUS_SUSPENDED,
            email="suspended@example.com",
            address="456 Suspended St",
        )

        # Create draft vendor with verified KYC document
        v_draft = Vendor.objects.create(
            legal_name="Vendor Draft KYC Ltd",
            trade_name="Draft KYC Ltd",
            vendor_number="VND-DFT-001",
            tax_identification_number="TIN-DFT-001",
            organization=org,
            category=category,
            status=Vendor.STATUS_DRAFT,
            email="draft@example.com",
            address="789 Draft St",
        )
        VendorDocument.objects.create(
            vendor=v_draft,
            document_type=VendorDocument.DOC_TYPE_TAX,
            title="Tax Clearance",
            file="vendor_documents/test.pdf",
            is_verified=True,
            status=VendorDocument.STATUS_VERIFIED,
        )

        # 1. Test selector directly
        data = get_vendor_compliance_audit(organization=org)
        summary = data["summary"]

        assert summary["total_vendors"] == 3
        assert summary["active_vendors"] == 1
        assert summary["active_count"] == 1
        assert summary["suspended_vendors"] == 1
        assert summary["suspended_count"] == 1
        assert summary["kyc_verified_count"] >= 2
        assert "compliance_rate" in summary
        assert summary["compliance_rate"] > 0

        # Check result row fields
        results = data["results"]
        active_row = next(r for r in results if r["vendor_number"] == "VND-ACT-001")
        assert active_row["category_name"] is not None
        assert active_row["kyc_status"] == "VERIFIED"
        assert active_row["performance_score"] is not None

        # 2. Test view execution with an Auditor user
        auditor_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
        auditor_user = User.objects.create_user(
            email="compliance_auditor@test.com",
            password="TestPassword123!",
            role=auditor_role,
        )

        rf = RequestFactory()
        request = rf.get("/audit/vendors/")
        request.user = auditor_user

        response = audit_vendors_view(request)
        assert response.status_code == 200

        content = response.content.decode("utf-8")
        assert "ACTIVE VENDORS" in content
        assert "SUSPENDED / AT-RISK" in content
        assert "KYC VERIFIED" in content

        # 3. Test get_auditor_dashboard_data for auditor without org
        dash_data = get_auditor_dashboard_data(user=auditor_user)
        assert dash_data["domain_counts"]["total_vendors"] >= 3
        assert dash_data["domain_counts"]["suspended_vendors_count"] >= 1

    def test_bug_001_invoice_exceptions_summary_dynamic_calculation(self):
        org = Organization.objects.create(name="Test Org Exceptions", code="TOE-01")
        dept = Department.objects.create(name="Finance Ops", code="FIN-OPS", organization=org)
        cost_center = CostCenter.objects.create(name="CC Finance", code="CC-FIN", department=dept)
        category = VendorCategory.objects.create(name="Consulting", code="CON-01")
        vendor = Vendor.objects.create(
            legal_name="Exceptions Vendor",
            vendor_number="VND-EXC-001",
            tax_identification_number="TIN-EXC-001",
            organization=org,
            category=category,
            status=Vendor.STATUS_ACTIVE,
            email="exc@example.com",
            address="123 Exc Way",
        )
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-EXC01",
            vendor=vendor,
            cost_center=cost_center,
            total_amount=Decimal("5000.00"),
            status=PurchaseOrder.STATUS_ISSUED,
            version=1,
        )
        invoice = SupplierInvoice.objects.create(
            invoice_number="INV-2026-EXC01",
            vendor=vendor,
            po=po,
            subtotal=Decimal("5000.00"),
            total_amount=Decimal("5000.00"),
            tax_amount=Decimal("0.00"),
            invoice_date="2026-11-01",
            due_date="2026-11-30",
        )

        # Create Resolved Price Variance exception
        MatchException.objects.create(
            invoice=invoice,
            exception_type=MatchException.TYPE_PRICE_VARIANCE,
            status=MatchException.STATUS_RESOLVED,
            variance_amount=Decimal("350.50"),
            description="Price mismatch variance",
        )
        # Create Open Quantity Variance exception
        MatchException.objects.create(
            invoice=invoice,
            exception_type=MatchException.TYPE_QUANTITY_VARIANCE,
            status=MatchException.STATUS_OPEN,
            variance_amount=Decimal("150.00"),
            description="Quantity excess",
        )

        # 1. Unfiltered summary
        data_all = get_invoice_exceptions_audit(organization=org)
        summary_all = data_all["summary"]
        assert summary_all["open_count"] == 1
        assert summary_all["resolved_count"] == 1
        assert summary_all["price_variance_count"] == 1
        assert summary_all["total_variance_exposure"] == "500.50"

        # 2. Filtered by status=RESOLVED (as described in BUG-001)
        data_resolved = get_invoice_exceptions_audit(status="RESOLVED", organization=org)
        summary_res = data_resolved["summary"]
        assert summary_res["resolved_count"] == 1
        assert summary_res["total_variance_exposure"] == "350.50"
        assert summary_res["price_variance_count"] == 1

        # 3. Test view rendering
        auditor_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
        auditor_user = User.objects.create_user(
            email="inv_auditor@test.com",
            password="TestPassword123!",
            role=auditor_role,
        )
        rf = RequestFactory()
        req = rf.get("/audit/invoice-exceptions/?status=RESOLVED")
        req.user = auditor_user
        resp = audit_invoice_exceptions_view(req)
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "TOTAL VARIANCE EXPOSURE" in content
        assert "$350.50" in content

    def test_bug_003_and_004_po_changes_metrics_sorting_and_filtering(self):
        org = Organization.objects.create(name="Test Org PO", code="TOPO-01")
        dept = Department.objects.create(name="Procurement", code="PROC", organization=org)
        cost_center = CostCenter.objects.create(name="CC Tech", code="CC-TECH", department=dept)
        category = VendorCategory.objects.create(name="Hardware", code="HW-01")
        vendor = Vendor.objects.create(
            legal_name="PO Test Vendor",
            vendor_number="VND-PO-001",
            tax_identification_number="TIN-PO-001",
            organization=org,
            category=category,
            status=Vendor.STATUS_ACTIVE,
            email="povendor@example.com",
            address="PO St",
        )

        # PO 1: original v1
        po1 = PurchaseOrder.objects.create(
            po_number="PO-2026-001",
            vendor=vendor,
            cost_center=cost_center,
            total_amount=Decimal("1200.00"),
            status=PurchaseOrder.STATUS_ISSUED,
            version=1,
        )
        # PO 2: amended v2
        po2 = PurchaseOrder.objects.create(
            po_number="PO-2026-002",
            vendor=vendor,
            cost_center=cost_center,
            total_amount=Decimal("4500.00"),
            status=PurchaseOrder.STATUS_ISSUED,
            version=2,
        )

        # 1. Summary validation (BUG-003)
        data = get_po_changes_audit(organization=org)
        summary = data["summary"]
        assert summary["total_pos"] == 2
        assert summary["amended_pos_count"] == 1
        assert summary["total_committed_value"] == "5,700.00"

        # 2. Version filtering (BUG-004)
        data_amended = get_po_changes_audit(version="amended", organization=org)
        assert data_amended["total_count"] == 1
        assert data_amended["results"][0]["po_number"] == "PO-2026-002"

        # 3. Search by compliance check label (BUG-004)
        data_check = get_po_changes_audit(search="AMENDMENT AUDITED", organization=org)
        assert data_check["total_count"] == 2

        # 4. Sorting by total_amount descending (BUG-004)
        data_sorted_desc = get_po_changes_audit(sort_by="-total_amount", organization=org)
        assert data_sorted_desc["results"][0]["po_number"] == "PO-2026-002"

        # Sorting by total_amount ascending
        data_sorted_asc = get_po_changes_audit(sort_by="total_amount", organization=org)
        assert data_sorted_asc["results"][0]["po_number"] == "PO-2026-001"

        # 5. View rendering test
        auditor_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
        auditor_user = User.objects.create_user(
            email="po_auditor@test.com",
            password="TestPassword123!",
            role=auditor_role,
        )
        rf = RequestFactory()
        req = rf.get("/audit/po-changes/?search=AMENDMENT+AUDITED")
        req.user = auditor_user
        resp = audit_po_changes_view(req)
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "$5,700.00" in content
        assert "AMENDMENT AUDITED" in content

    def test_bug_005_auditor_sidebar_scroll_container_present(self):
        auditor_role, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
        auditor_user = User.objects.create_user(
            email="sidebar_auditor@test.com",
            password="TestPassword123!",
            role=auditor_role,
        )
        rf = RequestFactory()
        req = rf.get("/audit/dashboard/")
        req.user = auditor_user
        resp = auditor_dashboard_view(req)
        assert resp.status_code == 200
        content = resp.content.decode("utf-8")
        assert "auditor-sidebar-scroll" in content
        assert "Financial Reports" in content
        assert "overflow-y-auto" in content

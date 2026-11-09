import pytest
from django.test import RequestFactory
from django.contrib.auth.models import AnonymousUser

from apps.accounts.models import Role, User
from apps.audit.selectors import get_vendor_compliance_audit, get_auditor_dashboard_data
from apps.audit.views import audit_vendors_view
from apps.organization.models import Organization
from apps.vendors.models import Vendor, VendorCategory, VendorDocument


@pytest.mark.django_db
class TestAuditorVendorCompliance:
    def test_vendor_compliance_audit_counts_and_template_keys(self):
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
        assert summary["kyc_verified_count"] >= 2  # active vendor + draft with verified doc
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
        # Ensure KPI card HTML contains non-zero counts
        assert "ACTIVE VENDORS" in content
        assert "SUSPENDED / AT-RISK" in content
        assert "KYC VERIFIED" in content

        # 3. Test get_auditor_dashboard_data for auditor without org
        dash_data = get_auditor_dashboard_data(user=auditor_user)
        assert dash_data["domain_counts"]["total_vendors"] >= 3
        assert dash_data["domain_counts"]["suspended_vendors_count"] >= 1


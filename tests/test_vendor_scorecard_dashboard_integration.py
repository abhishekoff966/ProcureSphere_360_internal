import pytest
from decimal import Decimal
from django.test import Client
from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter
from apps.scorecards.models import VendorScorecard, VendorScorecardSLAMetric
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import register_vendor_service


@pytest.fixture
def vendor_scorecard_setup(db_roles):
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB")
    dept = Department.objects.create(name="Procurement", code="PROC", organization=org)
    cost_center = CostCenter.objects.create(name="IT Hardware", code="CC-IT-01", department=dept)
    category = VendorCategory.objects.create(name="Hardware", code="CAT-HW")

    # Procurement Evaluator
    evaluator = User.objects.create_user(
        email="evaluator@hpe.com",
        password="Password123!",
        role=db_roles[Role.PROC_EXEC],
    )

    # Vendor A
    vendor_a = register_vendor_service(
        legal_name="Apex Electronics Corp",
        tax_identification_number="TIN-APEX-001",
        category=category,
        email="contact@apex.com",
        address="100 Silicon Blvd",
    )
    vendor_a.status = Vendor.STATUS_ACTIVE
    vendor_a.save()

    user_a = User.objects.create_user(
        email="vendor_a@apex.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor_a,
    )

    # Vendor B
    vendor_b = register_vendor_service(
        legal_name="Beta Logistics LLC",
        tax_identification_number="TIN-BETA-002",
        category=category,
        email="contact@beta.com",
        address="200 Warehouse Way",
    )
    vendor_b.status = Vendor.STATUS_ACTIVE
    vendor_b.save()

    user_b = User.objects.create_user(
        email="vendor_b@beta.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor_b,
    )

    # Scorecard for Vendor A (calculated composite = 93.85 -> 93.9)
    sc_a = VendorScorecard.objects.create(
        vendor=vendor_a,
        evaluation_period="2026-Q3",
        delivery_score=Decimal("92.5"),
        quality_score=Decimal("95.0"),
        price_score=Decimal("88.0"),
        compliance_score=Decimal("100.0"),
        evaluator_comments="Outstanding fulfillment and SLA compliance throughout Q3.",
        evaluated_by=evaluator,
    )

    # SLA Metric for Vendor A (OVERALL snapshot)
    sla_a = VendorScorecardSLAMetric.objects.create(
        scorecard=sc_a,
        metric_code="OVERALL",
        target_pct=Decimal("95.0"),
        actual_pct=Decimal("97.2"),
        achievement_pct=Decimal("102.3"),
        breaches=0,
        observations=12,
        notes="Met and exceeded delivery SLA requirements.",
    )

    # Scorecard for Vendor B
    sc_b = VendorScorecard.objects.create(
        vendor=vendor_b,
        evaluation_period="2026-Q3",
        delivery_score=Decimal("65.0"),
        quality_score=Decimal("60.0"),
        price_score=Decimal("70.0"),
        compliance_score=Decimal("80.0"),
        evaluator_comments="Delivery bottlenecks observed in August.",
        evaluated_by=evaluator,
    )

    return {
        "vendor_a": vendor_a,
        "user_a": user_a,
        "sc_a": sc_a,
        "sla_a": sla_a,
        "vendor_b": vendor_b,
        "user_b": user_b,
        "sc_b": sc_b,
        "evaluator": evaluator,
    }


@pytest.mark.django_db
class TestVendorScorecardIntegration:

    def test_vendor_dashboard_overview_displays_scorecard(self, vendor_scorecard_setup):
        """Vendor dashboard overview (/vendor/) renders the scorecard card with composite score and period."""
        user_a = vendor_scorecard_setup["user_a"]
        client = Client()
        client.force_login(user_a)

        resp = client.get("/vendor/")
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")

        # Scorecard section elements
        assert "Supplier Performance &amp; Scorecard" in html or "Supplier Performance & Scorecard" in html
        assert "2026-Q3" in html
        assert "93.9" in html
        assert "Tier 1 / Preferred" in html
        assert "View Full Performance History" in html
        assert "/vendor/performance/" in html

    def test_vendor_dashboard_overview_empty_state_without_scorecard(self, vendor_scorecard_setup):
        """When a vendor has no scorecards, a clean informational empty state is displayed."""
        user_b = vendor_scorecard_setup["user_b"]
        vendor_scorecard_setup["sc_b"].delete()

        client = Client()
        client.force_login(user_b)
        resp = client.get("/vendor/")
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")

        assert "Supplier Performance &amp; Scorecard" in html or "Supplier Performance & Scorecard" in html
        assert "No published scorecard evaluation yet" in html

    def test_vendor_performance_history_view(self, vendor_scorecard_setup):
        """Vendor performance page (/vendor/performance/) displays full evaluation breakdown and SLA metrics."""
        user_a = vendor_scorecard_setup["user_a"]
        client = Client()
        client.force_login(user_a)

        resp = client.get("/vendor/performance/")
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")

        assert "Supplier Performance & Scorecards" in html
        assert "2026-Q3" in html
        assert "93.9" in html
        assert "Delivery Timeliness" in html
        assert "Quality & Acceptance" in html
        assert "Pricing Adherence" in html
        assert "Compliance / KYC" in html
        assert "Outstanding fulfillment and SLA compliance throughout Q3." in html

        # SLA Snapshot
        assert "Contract SLA Fulfillment &amp; Compliance Snapshots" in html or "Contract SLA Fulfillment & Compliance Snapshots" in html
        assert "OVERALL" in html
        assert "0 Breaches" in html

    def test_vendor_scorecard_tenant_isolation(self, vendor_scorecard_setup):
        """Vendor A must NEVER see Vendor B's scorecards, evaluator comments, or SLA metrics."""
        user_a = vendor_scorecard_setup["user_a"]
        client = Client()
        client.force_login(user_a)

        resp = client.get("/vendor/performance/")
        assert resp.status_code == 200
        html = resp.content.decode("utf-8")

        # Vendor A sees their own
        assert "93.9" in html
        assert "Outstanding fulfillment and SLA compliance" in html

        # Vendor A does NOT see Vendor B's data
        assert "67.5" not in html
        assert "Delivery bottlenecks observed in August." not in html
        assert "Beta Logistics" not in html

    def test_unauthenticated_and_non_vendor_access_denied(self, vendor_scorecard_setup):
        """Anonymous and non-vendor users are blocked from vendor performance views."""
        client = Client()

        # Unauthenticated redirect to login
        resp_anon = client.get("/vendor/performance/")
        assert resp_anon.status_code == 302
        assert "/login/" in resp_anon.url

        # Staff user without vendor gets 403 Forbidden
        evaluator = vendor_scorecard_setup["evaluator"]
        client.force_login(evaluator)
        resp_eval = client.get("/vendor/performance/")
        assert resp_eval.status_code == 403

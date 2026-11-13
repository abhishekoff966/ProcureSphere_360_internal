"""Regression tests for Procurement Executive BUG-028 through BUG-034."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.requisitions.models import PurchaseRequisition
from apps.sourcing.models import SourcingEvent

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def _fast_password_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class TestBugs028To034:
    def test_bug_028_bid_portal_paginates_and_renders_pagination_bar(
        self, client, procurement_world
    ):
        w = procurement_world
        now = timezone.now()
        for i in range(1, 24):
            SourcingEvent.objects.create(
                title=f"Bid Portal Event {i:02d}",
                event_number=f"RFQ-BP-{i:03d}",
                event_type=SourcingEvent.TYPE_RFQ,
                status=SourcingEvent.STATUS_BID_WINDOW,
                created_by=w["exec"],
                bid_start_date=now - timedelta(hours=2),
                bid_end_date=now + timedelta(days=5),
                technical_weight=Decimal("50.00"),
                commercial_weight=Decimal("50.00"),
            )

        client.force_login(w["exec"])
        r1 = client.get(reverse("bid_portal_dashboard"))
        assert r1.status_code == 200
        assert len(r1.context["events"]) == 20
        assert r1.context["page_obj"].paginator.num_pages == 2
        html1 = r1.content.decode()
        assert 'aria-label="Report Pagination"' in html1
        assert "1 / 2" in html1
        assert "Next" in html1

        r2 = client.get(reverse("bid_portal_dashboard"), {"page": 2})
        assert r2.status_code == 200
        assert len(r2.context["events"]) == 3
        assert "2 / 2" in r2.content.decode()

        # Also supports custom per_page (e.g. 10)
        r_per10 = client.get(reverse("bid_portal_dashboard"), {"per_page": 10})
        assert r_per10.status_code == 200
        assert len(r_per10.context["events"]) == 10
        assert r_per10.context["page_obj"].paginator.num_pages == 3

    def test_bug_029_evaluation_dashboard_renders_action_column_header(
        self, client, procurement_world
    ):
        client.force_login(procurement_world["exec"])
        resp = client.get(reverse("evaluation_dashboard"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert '<th class="text-end pe-3 text-nowrap">Action</th>' in html

    def test_bug_030_vendor_list_filter_dropdowns_have_non_compressed_columns(
        self, client, procurement_world
    ):
        client.force_login(procurement_world["exec"])
        resp = client.get(reverse("vendor_list"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "col-12 col-sm-6 col-md-4 col-xl-3" in html
        assert "col-6 col-md-4 col-lg\"" not in html

    def test_bug_031_and_032_pr_aging_uses_boxed_stat_cards_and_proportioned_table(
        self, client, procurement_world
    ):
        w = procurement_world
        PurchaseRequisition.objects.create(
            pr_number="PR-STYLE-001",
            title="High Capacity Server Rack",
            justification="UI grid check",
            requester=w["requester"],
            department=w["cost_center"].department,
            cost_center=w["cost_center"],
            status=PurchaseRequisition.STATUS_SUBMITTED,
            total_amount=Decimal("4500.00"),
            requested_delivery_date=timezone.now().date() + timedelta(days=5),
        )
        client.force_login(w["exec"])
        resp = client.get(reverse("proc_exec_pr_aging"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "stat-card h-100 border-start border-primary border-4" in html
        assert "stat-card h-100 border-start border-success border-4" in html
        assert "stat-card h-100 border-start border-warning border-4" in html
        assert "stat-card h-100 border-start border-danger border-4" in html
        assert 'style="min-width: 920px;"' in html
        assert "text-nowrap" in html

    def test_bug_033_and_034_sourcing_cycle_uses_boxed_stat_cards_and_proportioned_table(
        self, client, procurement_world
    ):
        w = procurement_world
        now = timezone.now()
        SourcingEvent.objects.create(
            title="Cloud Firewall Upgrade",
            event_number="RFQ-STY-001",
            event_type=SourcingEvent.TYPE_RFQ,
            status=SourcingEvent.STATUS_PUBLISHED,
            created_by=w["exec"],
            bid_start_date=now + timedelta(days=1),
            bid_end_date=now + timedelta(days=5),
            technical_weight=Decimal("50.00"),
            commercial_weight=Decimal("50.00"),
        )
        client.force_login(w["exec"])
        resp = client.get(reverse("proc_exec_sourcing_cycle"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "stat-card h-100 border-start border-primary border-4" in html
        assert "stat-card h-100 border-start border-info border-4" in html
        assert "stat-card h-100 border-start border-dark border-4" in html
        assert "stat-card h-100 border-start border-success border-4" in html
        assert 'style="min-width: 960px;"' in html
        assert '<th class="text-center text-nowrap">Invited</th>' in html
        assert '<th class="text-center text-nowrap">Bids</th>' in html

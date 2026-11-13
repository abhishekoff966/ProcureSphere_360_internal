"""Tests for Procurement Executive Reports pagination, sidebar scroll preservation,
and Notifications read-only mode + Procurement Executive dashboard scoping.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.orders.models import PurchaseOrder
from apps.requisitions.models import PurchaseRequisition
from apps.sourcing.models import SourcingEvent

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


@pytest.fixture(autouse=True)
def _fast_password_hashes(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


class TestReportsPaginationAndSidebarScroll:
    def test_pr_aging_and_po_status_always_render_pagination_bar_and_support_per_page(
        self, client, procurement_world
    ):
        w = procurement_world
        delivery_date = timezone.now().date() + timedelta(days=7)
        for i in range(1, 7):
            PurchaseRequisition.objects.create(
                pr_number=f"PR-SMALL-{i:03d}",
                title=f"Small Dataset PR {i:02d}",
                justification="Visible pagination bar",
                requester=w["requester"],
                department=w["cost_center"].department,
                cost_center=w["cost_center"],
                status=PurchaseRequisition.STATUS_SUBMITTED,
                total_amount=Decimal("250.00"),
                requested_delivery_date=delivery_date,
            )
            PurchaseOrder.objects.create(
                po_number=f"PO-SMALL-{i:03d}",
                vendor=w["vendor_a"],
                cost_center=w["cost_center"],
                status=PurchaseOrder.STATUS_ISSUED,
                subtotal=Decimal("200.00"),
                tax_amount=Decimal("20.00"),
                total_amount=Decimal("220.00"),
                created_by=w["exec"],
            )

        client.force_login(w["exec"])
        for url_name in ("proc_exec_pr_aging", "proc_exec_po_status"):
            # Even with <= 10 rows (single page), pagination controls & page-size selector render visibly
            resp_single = client.get(reverse(url_name))
            assert resp_single.status_code == 200
            html_single = resp_single.content.decode()
            assert 'aria-label="Report Pagination"' in html_single
            assert "1 / 1" in html_single
            assert "Previous" in html_single
            assert "Next" in html_single
            assert 'name="per_page"' in html_single

            # Selecting per_page=5 paginates 6 records into 2 pages
            resp_p1 = client.get(reverse(url_name), {"per_page": 5})
            assert resp_p1.status_code == 200
            assert len(resp_p1.context["rows"]) == 5
            assert resp_p1.context["page_obj"].paginator.num_pages == 2
            html_p1 = resp_p1.content.decode()
            assert "1 / 2" in html_p1
            assert "per_page=5&amp;page=2" in html_p1 or "per_page=5&page=2" in html_p1

            resp_p2 = client.get(reverse(url_name), {"per_page": 5, "page": 2})
            assert resp_p2.status_code == 200
            assert len(resp_p2.context["rows"]) == 1
            assert "2 / 2" in resp_p2.content.decode()

    def test_pr_aging_report_paginates_preserves_filters_and_exports_all(
        self, client, procurement_world
    ):
        w = procurement_world
        delivery_date = timezone.now().date() + timedelta(days=10)
        for i in range(1, 13):
            PurchaseRequisition.objects.create(
                pr_number=f"PR-PAG-{i:03d}",
                title=f"Paginated Widget {i:02d}",
                justification="Pagination check",
                requester=w["requester"],
                department=w["cost_center"].department,
                cost_center=w["cost_center"],
                status=PurchaseRequisition.STATUS_SUBMITTED,
                total_amount=Decimal("100.00") * i,
                requested_delivery_date=delivery_date,
            )

        client.force_login(w["exec"])

        resp_p1 = client.get(reverse("proc_exec_pr_aging"), {"q": "Paginated Widget"})
        assert resp_p1.status_code == 200
        assert resp_p1.context["total"] == 12
        assert len(resp_p1.context["rows"]) == 10
        assert resp_p1.context["page_obj"].paginator.num_pages == 2
        html_p1 = resp_p1.content.decode()
        assert "1 / 2" in html_p1
        assert "q=Paginated+Widget&amp;page=2" in html_p1 or "q=Paginated+Widget&page=2" in html_p1

        resp_p2 = client.get(
            reverse("proc_exec_pr_aging"), {"q": "Paginated Widget", "page": 2}
        )
        assert resp_p2.status_code == 200
        assert len(resp_p2.context["rows"]) == 2
        assert "2 / 2" in resp_p2.content.decode()

        # CSV export must export all 12 filtered rows (header + 12 rows), unpaginated
        csv_resp = client.get(
            reverse("proc_exec_pr_aging"), {"q": "Paginated Widget", "export": "csv"}
        )
        assert csv_resp.status_code == 200
        lines = [ln for ln in csv_resp.content.decode().splitlines() if ln.strip()]
        assert len(lines) == 13

    def test_sourcing_cycle_and_po_status_and_audit_trail_paginate(
        self, client, procurement_world
    ):
        w = procurement_world
        now = timezone.now()
        for i in range(1, 12):
            SourcingEvent.objects.create(
                title=f"Cycle Event {i:02d}",
                event_number=f"RFQ-CYC-{i:03d}",
                event_type=SourcingEvent.TYPE_RFQ,
                status=SourcingEvent.STATUS_PUBLISHED,
                created_by=w["exec"],
                bid_start_date=now + timedelta(days=1),
                bid_end_date=now + timedelta(days=5),
                technical_weight=Decimal("50.00"),
                commercial_weight=Decimal("50.00"),
            )
            PurchaseOrder.objects.create(
                po_number=f"PO-PAG-{i:03d}",
                vendor=w["vendor_a"],
                cost_center=w["cost_center"],
                status=PurchaseOrder.STATUS_ISSUED,
                subtotal=Decimal("500.00"),
                tax_amount=Decimal("50.00"),
                total_amount=Decimal("550.00"),
                created_by=w["exec"],
            )
            AuditLog.objects.create(
                actor=w["auditor"],
                action=AuditLog.ACTION_CREATE,
                target_model="PurchaseOrder",
                target_object_id=f"obj-{i:03d}",
            )

        client.force_login(w["exec"])

        for url_name in ["proc_exec_sourcing_cycle", "proc_exec_po_status"]:
            r1 = client.get(reverse(url_name))
            assert r1.status_code == 200
            assert r1.context["total"] == 11
            assert len(r1.context["rows"]) == 10
            assert "1 / 2" in r1.content.decode()

            r2 = client.get(reverse(url_name), {"page": 2})
            assert r2.status_code == 200
            assert len(r2.context["rows"]) == 1
            assert "2 / 2" in r2.content.decode()

        # Audit trail report pagination (for auditor)
        client.force_login(w["auditor"])
        aud_r1 = client.get(reverse("proc_exec_audit_report"))
        assert aud_r1.status_code == 200
        assert aud_r1.context["page_obj"].paginator.num_pages >= 2
        assert len(aud_r1.context["rows"]) == 10
        assert "/ 2" in aud_r1.content.decode() or f"/ {aud_r1.context['page_obj'].paginator.num_pages}" in aud_r1.content.decode()

    @pytest.mark.parametrize(
        "url_name,expected_path,label",
        [
            ("proc_exec_pr_aging", "/reports/pr-aging/", "PR Aging"),
            ("proc_exec_sourcing_cycle", "/reports/sourcing-cycle/", "Sourcing Cycle"),
            ("proc_exec_po_status", "/reports/po-status/", "PO Status"),
        ],
    )
    def test_report_sidebar_stays_on_report_and_preserves_scroll(
        self, client, procurement_world, url_name, expected_path, label
    ):
        client.force_login(procurement_world["exec"])
        resp = client.get(reverse(url_name), follow=False)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "ps_proc_exec_nav_scroll" in html
        assert "scrollIntoView" in html
        assert f'href="{expected_path}" class="vendor-navlink proc-navlink active-nav-item"' in html
        assert label in html


class TestProcExecNotificationsReadOnlyAndScoped:
    def _seed_mixed_notifications(self, user):
        pe_po = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_PO_ACKNOWLEDGED,
            title="PO Acknowledged: PO-2026-00099",
            message="Vendor acknowledged PO.",
            target_url="/orders/00000000-0000-0000-0000-000000000001/",
        )
        pe_bid = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_BID_SUBMITTED,
            title="Bid Submitted on RFQ-2026-00099",
            message="Vendor submitted commercial quote.",
            target_url="/sourcing-events/00000000-0000-0000-0000-000000000002/",
        )
        pe_kyc = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_KYC_REQUEST,
            title="Vendor Approved: Alpha Co",
            message="Vendor KYC approved.",
            target_url="/vendors/00000000-0000-0000-0000-000000000003/",
        )
        non_pe_invoice = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title="Supplier Invoice Submitted: INV-99",
            message="Invoice submitted.",
            target_url="/invoices/00000000-0000-0000-0000-000000000004/",
        )
        non_pe_grn = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title="GRN Dispatched: GRN-99",
            message="Stores GRN dispatched.",
            target_url="/receipts/00000000-0000-0000-0000-000000000005/",
        )
        non_pe_exception = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_EXCEPTION_RAISED,
            title="3-Way Match Exception INV-99",
            message="Variance detected.",
            target_url="/invoices/00000000-0000-0000-0000-000000000006/",
        )
        non_pe_contract = Notification.objects.create(
            recipient=user,
            notification_type=Notification.TYPE_CONTRACT_EXPIRATION,
            title="Contract Expiring CON-99",
            message="Expires in 30 days.",
            target_url="/contracts/00000000-0000-0000-0000-000000000007/",
        )
        return {
            "pe": [pe_po, pe_bid, pe_kyc],
            "non_pe": [non_pe_invoice, non_pe_grn, non_pe_exception, non_pe_contract],
        }

    def test_proc_exec_notifications_list_is_scoped_and_read_only(
        self, client, procurement_world
    ):
        pe_user = procurement_world["exec"]
        seeded = self._seed_mixed_notifications(pe_user)

        client.force_login(pe_user)
        resp = client.get(reverse("notification_list"))
        assert resp.status_code == 200
        html = resp.content.decode()

        # Only PE-dashboard notifications are visible and counted
        assert resp.context["unread_count"] == 3
        assert resp.context["unread_notifications_count"] == 3
        for item in seeded["pe"]:
            assert item.title in html
        for item in seeded["non_pe"]:
            assert item.title not in html

        # Read-only mode: Open button is removed, Mark read stays
        assert "fa-arrow-up-right-from-square" not in html
        assert "> Open<" not in html
        assert "Open</button>" not in html
        assert "Read-only" in html
        assert "Mark read" in html

        # Even if notification_open URL is posted by PROC_EXEC, stay on /notifications/ (read-only)
        target_pe = seeded["pe"][0]
        open_resp = client.post(
            reverse("notification_open", kwargs={"notification_id": target_pe.id}),
            follow=False,
        )
        assert open_resp.status_code == 302
        assert open_resp.url == reverse("notification_list")
        target_pe.refresh_from_db()
        assert target_pe.is_read is True

        # After marking read, that item displays a read-only 'Read' status badge
        after_html = client.get(reverse("notification_list")).content.decode()
        assert ">\n                        <i class=\"fa-solid fa-check me-1 text-success\"></i> Read" in after_html or "Read\n" in after_html

    def test_proc_exec_htmx_and_api_feeds_are_scoped_and_hide_open_links(
        self, client, procurement_world
    ):
        pe_user = procurement_world["exec"]
        self._seed_mixed_notifications(pe_user)

        # Also add an actionable PE notification and an actionable Contract notification
        Notification.objects.create(
            recipient=pe_user,
            notification_type=Notification.TYPE_BID_DEADLINE,
            title="RFQ-99 Bid Window Closing",
            message="Closes tomorrow.",
            target_url="/sourcing-events/00000000-0000-0000-0000-000000000010/",
        )
        Notification.objects.create(
            recipient=pe_user,
            notification_type=Notification.TYPE_APPROVAL_REQUIRED,
            title="Contract Business Approval CON-99",
            message="Contract awaits approval.",
            target_url="/contracts/00000000-0000-0000-0000-000000000011/",
        )

        client.force_login(pe_user)

        feed_resp = client.get(reverse("proc_exec_notifications_feed"))
        assert feed_resp.status_code == 200
        feed_html = feed_resp.content.decode()
        assert "PO Acknowledged: PO-2026-00099" in feed_html
        assert "Supplier Invoice Submitted" not in feed_html
        assert "GRN Dispatched" not in feed_html
        assert "Contract Expiring" not in feed_html
        assert "Contract Business Approval" not in feed_html
        assert "Open linked record" not in feed_html

        pending_resp = client.get(reverse("proc_exec_pending_actions"))
        assert pending_resp.status_code == 200
        pending_html = pending_resp.content.decode()
        assert "RFQ-99 Bid Window Closing" in pending_html
        assert "3-Way Match Exception" not in pending_html
        assert "Contract Expiring" not in pending_html
        assert "Contract Business Approval" not in pending_html
        assert "Take action" not in pending_html

        # API endpoints also scope cleanly to PE notifications
        summary = client.get("/api/v1/notifications/summary/").json()
        assert summary["total_count"] == 4
        assert summary["unread_count"] == 4

    def test_other_roles_retain_open_button_and_full_notifications(
        self, client, procurement_world
    ):
        finance_user = procurement_world["finance"]
        notif = Notification.objects.create(
            recipient=finance_user,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title="New Supplier Invoice Submitted: INV-FIN-01",
            message="Ready for 3-way match.",
            target_url="/invoices/",
        )
        client.force_login(finance_user)
        resp = client.get(reverse("notification_list"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "INV-FIN-01" in html
        assert "Open" in html
        open_resp = client.post(
            reverse("notification_open", kwargs={"notification_id": notif.id}),
            follow=False,
        )
        assert open_resp.status_code == 302
        assert open_resp.url == "/invoices/"

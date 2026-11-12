"""
Regression tests for Procurement Executive manual testing sheet bugs BUG-020 through BUG-027:
- BUG-020: RFQ / RFP Sourcing filter toolbar Deadline from / Deadline to layout (no col-lg-1 clipping)
- BUG-021: Sourcing event 'Review onboarding' link org-scoping + route (no 404 Not Found)
- BUG-022: XSS / <script> payload rejection on Vendor inputs + sanitized safe display
- BUG-023: PO Amend modal placeholder, disabled-until-dirty Save button, inline validation
- BUG-024: PO Amend modal business-friendly copy (no POAmendment/amend_purchase_order_service jargon)
- BUG-025: Supplier Invoice detail view renders 200 OK for PROC_EXEC (no missing layouts/proc_exec_base.html)
- BUG-026: Vendor Bid Portal table includes 'Action' column header above 'Monitor' buttons
- BUG-027: Vendor Bid Portal table header/body 8-column alignment and centered numeric metrics
"""
import io
import re
from datetime import timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from apps.core.validators import sanitize_plain_display_text, validate_no_html_or_script
from apps.invoices.models import SupplierInvoice
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import Organization
from apps.sourcing.models import SourcingEvent
from apps.sourcing.selectors import get_pending_onboarding_vendors
from apps.vendors.forms import VendorEditForm, VendorRegistrationForm
from apps.vendors.models import Vendor
from apps.vendors.services import bulk_import_vendors_from_csv_service

pytestmark = [pytest.mark.django_db]


class TestBug020SourcingFilterLayout:
    def test_sourcing_list_deadline_inputs_use_spacious_grid_columns(self, client, procurement_world):
        w = procurement_world
        client.force_login(w["exec"])
        resp = client.get(reverse("sourcing_list"))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'for="s-from"' in html
        assert 'for="s-to"' in html
        # Ensure Deadline from / to are no longer squeezed into col-lg-1
        assert "col-6 col-md-4 col-lg-3" in html
        assert 'class="col-6 col-md-3 col-lg-1"' not in html


class TestBug021And022OnboardingRoutingAndXss:
    def _make_event(self, world):
        now = timezone.now()
        return SourcingEvent.objects.create(
            title="Network Switch Refresh",
            event_number="RFQ-2026-00182",
            event_type=SourcingEvent.TYPE_RFQ,
            status=SourcingEvent.STATUS_DRAFT,
            created_by=world["exec"],
            bid_start_date=now + timedelta(days=1),
            bid_end_date=now + timedelta(days=5),
            technical_weight=Decimal("60.00"),
            commercial_weight=Decimal("40.00"),
        )

    def test_pending_onboarding_vendor_review_links_resolve_200(self, client, procurement_world):
        w = procurement_world
        event = self._make_event(w)
        w["exec"].organization = w["cost_center"].department.organization
        w["exec"].save(update_fields=["organization"])
        other_org = Organization.objects.create(name="Other Corp", code="OTHER-ORG")
        other_vendor = Vendor.objects.create(
            legal_name="External Pending Corp Ltd",
            vendor_number="VND-OTHER-001",
            tax_identification_number="TAX-OTHER-1234",
            category=w["hardware"],
            organization=other_org,
            status=Vendor.STATUS_SUBMITTED,
            email="other@example.com",
            address="99 External Rd",
        )

        # Unfiltered call works; org-scoped call excludes cross-org vendor that would 404
        exec_org = getattr(w["exec"], "effective_organization", None)
        scoped = list(get_pending_onboarding_vendors(event, organization=exec_org))
        assert w["vendor_draft"] in scoped
        assert other_vendor not in scoped

        client.force_login(w["exec"])
        resp = client.get(reverse("sourcing_detail", args=[event.id]))
        assert resp.status_code == 200
        html = resp.content.decode()
        expected_href = f"{reverse('vendor_detail_status', args=[w['vendor_draft'].id])}?section=onboarding"
        assert expected_href in html
        assert str(other_vendor.id) not in html

        # Clicking every rendered Review onboarding link returns 200 OK (no 404)
        onboarding_resp = client.get(expected_href)
        assert onboarding_resp.status_code == 200
        overview_resp = client.get(reverse("vendor_detail", args=[w["vendor_draft"].id]))
        assert overview_resp.status_code == 200

        # Also verify HTMX invites fragment links cleanly
        htmx_resp = client.get(reverse("sourcing_invites_htmx", args=[event.id]))
        assert htmx_resp.status_code == 200
        assert expected_href in htmx_resp.content.decode()

    @pytest.mark.parametrize(
        "xss_payload",
        [
            "<script>alert('X1109185825zz241')</script>",
            "<img src=x onerror=alert(1)>",
            "javascript:alert(1)",
        ],
    )
    def test_xss_payload_rejected_across_vendor_entry_points(self, api_client, procurement_world, xss_payload):
        w = procurement_world

        with pytest.raises(ValidationError):
            validate_no_html_or_script(xss_payload, "Legal name")

        reg_form = VendorRegistrationForm(
            data={
                "legal_name": xss_payload,
                "trade_name": xss_payload,
                "tax_identification_number": "TAX-88776655",
                "category": str(w["hardware"].id),
                "email": "xss@vendor.test",
                "phone": "9876543210",
                "address": "12 Security Way",
            }
        )
        assert not reg_form.is_valid()
        assert "legal_name" in reg_form.errors
        assert "trade_name" in reg_form.errors

        edit_form = VendorEditForm(
            data={
                "legal_name": "Valid Acme Corp Ltd",
                "trade_name": xss_payload,
                "email": "xss@vendor.test",
                "phone": "9876543210",
                "address": "12 Security Way",
                "category": str(w["hardware"].id),
            }
        )
        assert not edit_form.is_valid()
        assert "trade_name" in edit_form.errors

        # API serializer rejects XSS payloads cleanly with HTTP 400
        api_client.force_authenticate(w["exec"])
        api_resp = api_client.post(
            "/api/v1/vendors/",
            {
                "legal_name": xss_payload,
                "tax_identification_number": "TAX-99112233",
                "category": str(w["hardware"].id),
                "email": "api-xss@vendor.test",
                "address": "1 Api Way",
            },
            format="json",
        )
        assert api_resp.status_code == 400

        # Bulk CSV import rejects XSS rows
        csv_content = f"Legal Name,Tax ID,Category Code,Contact Email\n{xss_payload},TAX-CSV-99,HW,csv@vendor.test\n"
        created, errs = bulk_import_vendors_from_csv_service(
            csv_file=io.BytesIO(csv_content.encode("utf-8")),
            target_org=w["exec"].effective_organization,
            actor=w["exec"],
        )
        assert created == 0
        assert len(errs) == 1

    def test_legacy_xss_vendor_name_sanitized_in_sourcing_and_vendor_views(self, client, procurement_world):
        w = procurement_world
        event = self._make_event(w)
        legacy_xss = Vendor.objects.create(
            legal_name="<script>alert('X1109185825zz241')</script>",
            vendor_number="VND-XSS-999",
            tax_identification_number="TAX-LEGACY-XSS",
            category=w["hardware"],
            organization=getattr(w["exec"], "effective_organization", None),
            status=Vendor.STATUS_SUBMITTED,
            email="legacy-xss@example.com",
            address="10 Legacy Blvd",
        )

        assert "<script>" not in sanitize_plain_display_text(legacy_xss.legal_name)
        assert "X1109185825zz241" not in sanitize_plain_display_text(legacy_xss.legal_name)

        client.force_login(w["exec"])
        for url in [
            reverse("sourcing_detail", args=[event.id]),
            reverse("sourcing_invites_htmx", args=[event.id]),
            reverse("vendor_list"),
            reverse("vendor_detail", args=[legacy_xss.id]),
        ]:
            body = client.get(url).content.decode()
            assert "<script>alert('X1109185825zz241')</script>" not in body
            assert "&lt;script&gt;alert" not in body


class TestBug023And024POAmendModal:
    def _issued_po(self, world):
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-023",
            vendor=world["vendor_a"],
            cost_center=world["cost_center"],
            status=PurchaseOrder.STATUS_ISSUED,
            subtotal=Decimal("1000.00"),
            tax_amount=Decimal("100.00"),
            total_amount=Decimal("1100.00"),
            created_by=world["exec"],
        )
        POLine.objects.create(
            po=po,
            item_description="Enterprise Router",
            quantity=Decimal("10.00"),
            unit_price=Decimal("100.00"),
            line_total=Decimal("1000.00"),
            unit_of_measure="Each",
        )
        return po

    def test_amend_modal_placeholder_dirty_guard_and_business_copy(self, client, procurement_world):
        w = procurement_world
        po = self._issued_po(w)
        client.force_login(w["exec"])
        resp = client.get(reverse("orders_amend_modal", args=[po.id]))
        assert resp.status_code == 200
        html = resp.content.decode()

        # BUG-023: Placeholder + initially disabled Save button + inline error targets
        assert 'placeholder="Enter reason for amending purchase order..."' in html
        assert 'id="po-amend-save-btn"' in html
        assert "disabled>Save amendment</button>" in html
        assert 'id="po-amend-reason-error"' in html
        assert "data-field-error=" in html

        # BUG-024: Technical DB/service jargon removed from end-user UI copy
        assert "POAmendment" not in html
        assert "amend_purchase_order_service" not in html
        assert "will be saved to the audit log" in html

    def test_amend_modal_post_validation_keeps_button_active_and_renders_inline_errors(self, client, procurement_world):
        w = procurement_world
        po = self._issued_po(w)
        line = po.lines.first()
        client.force_login(w["exec"])
        resp = client.post(
            reverse("orders_amend_modal", args=[po.id]),
            {
                "reason": "",
                f"quantity_{line.id}": "0",
                f"unit_price_{line.id}": "100.00",
            },
            HTTP_HX_REQUEST="true",
        )
        assert resp.status_code == 400
        html = resp.content.decode()
        assert "Amendment justification reason is required." in html or "This field is required." in html
        assert "disabled>Save amendment</button>" not in html


class TestBug025InvoiceDetailLayout:
    def test_proc_exec_can_open_supplier_invoice_detail_without_template_error(self, client, procurement_world):
        w = procurement_world
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-025",
            vendor=w["vendor_a"],
            cost_center=w["cost_center"],
            status=PurchaseOrder.STATUS_ISSUED,
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
            total_amount=Decimal("550.00"),
            created_by=w["exec"],
        )
        invoice = SupplierInvoice.objects.create(
            invoice_number="INV-2026-025",
            vendor=w["vendor_a"],
            po=po,
            invoice_date=timezone.localdate(),
            due_date=timezone.localdate() + timedelta(days=30),
            subtotal=Decimal("500.00"),
            tax_amount=Decimal("50.00"),
            total_amount=Decimal("550.00"),
            created_by=w["exec"],
        )
        client.force_login(w["exec"])
        resp = client.get(reverse("invoice_detail", args=[invoice.id]))
        assert resp.status_code == 200
        body = resp.content.decode()
        assert invoice.invoice_number in body
        assert "proc_exec_base.html" not in body


class TestBug026And027BidPortalTableAlignment:
    def test_bid_portal_headers_and_cells_align_one_to_one(self, client, procurement_world):
        w = procurement_world
        now = timezone.now()
        SourcingEvent.objects.create(
            title="Data Center UPS Procurement",
            event_number="RFQ-2026-027",
            event_type=SourcingEvent.TYPE_RFQ,
            status=SourcingEvent.STATUS_BID_WINDOW,
            created_by=w["exec"],
            bid_start_date=now - timedelta(hours=2),
            bid_end_date=now + timedelta(days=3),
            technical_weight=Decimal("50.00"),
            commercial_weight=Decimal("50.00"),
        )
        client.force_login(w["exec"])
        resp = client.get(reverse("bid_portal_dashboard"))
        assert resp.status_code == 200
        html = resp.content.decode()

        thead_match = re.search(r"<thead[^>]*>(.*?)</thead>", html, re.DOTALL)
        tbody_match = re.search(r"<tbody>(.*?)</tbody>", html, re.DOTALL)
        assert thead_match and tbody_match

        headers = re.findall(r"<th[^>]*>(.*?)</th>", thead_match.group(1), re.DOTALL)
        cleaned_headers = [re.sub(r"<[^>]+>", "", h).strip() for h in headers]
        assert cleaned_headers == [
            "Event",
            "Status",
            "Bid window",
            "Invited",
            "Responded",
            "Submitted",
            "Drafts",
            "Action",
        ]

        first_row = re.search(r"<tr>(.*?)</tr>", tbody_match.group(1), re.DOTALL)
        assert first_row
        cells = re.findall(r"<td[^>]*>(.*?)</td>", first_row.group(1), re.DOTALL)
        assert len(cells) == len(cleaned_headers) == 8
        assert "Monitor" in cells[-1]

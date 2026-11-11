"""
Tests for Procurement Executive remaining bugs:
- BUG-001: SourcingEventForm requisition placeholder (replaces -------)
- BUG-014: Edit Vendor modal Save button disabled until dirty & HTMX target preserved
- BUG-015: Edit Vendor phone rejects malformed/alphanumeric strings (e.g. abcde12345, abc1234567890)
- BUG-016: Edit Vendor modal placeholders on all fields
- BUG-017: Edit Vendor legal name rejects numeric/garbage inputs (12345678, 12345678 Corp, tdgdtgd12)
- BUG-018: Success alert banner auto-dismisses & avoids duplicate banner in vendor header
- BUG-019: Deleted vendor removed from DB, UI search results, API delete endpoint, and sets no-cache
"""
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.core.validators import validate_legal_name, validate_optional_phone_number
from apps.sourcing.forms import SourcingEventForm
from apps.vendors.forms import VendorEditForm
from apps.vendors.models import Vendor
from apps.vendors.services import register_vendor_service, update_vendor_service

pytestmark = [pytest.mark.django_db]


class TestBug001SourcingRequisitionPlaceholder:
    def test_requisition_empty_label_is_descriptive(self):
        form = SourcingEventForm()
        empty_label = form.fields["requisition"].empty_label
        assert empty_label != "---------"
        assert "requisition" in empty_label.lower()
        rendered = form["requisition"].as_widget()
        assert "---------" not in rendered
        assert "Select a requisition (optional)..." in rendered


class TestBug015And017EditVendorValidation:
    @pytest.mark.parametrize(
        "bad_phone",
        [
            "abcde12345",
            "abc1234567890",
            "1234567890-0987654321",
            "12345",
            "9876543",          # 7 digits - rejected
            "987654321",        # 9 digits - rejected
            "+91799358617",     # +91 + 9 digits - rejected
            "98765432101",      # 11 digits - rejected
            "9876543210123",    # 13 digits - rejected
            "+9198765432101",   # +91 + 11 digits - rejected
            "+1 (555) 234-5678",
            "0000000000",
            "1234567890",       # invalid Indian prefix (starts with 1)
            "+91 1234567890",
            "987654--3210",
        ],
    )
    def test_phone_validator_rejects_malformed_and_alphanumeric(self, bad_phone):
        with pytest.raises(ValidationError):
            validate_optional_phone_number(bad_phone)

    @pytest.mark.parametrize(
        "good_phone,expected",
        [
            ("", ""),
            ("9876543210", "+919876543210"),
            ("+91 9876543210", "+919876543210"),
            ("919876543210", "+919876543210"),
        ],
    )
    def test_phone_validator_accepts_valid_numbers(self, good_phone, expected):
        assert validate_optional_phone_number(good_phone) == expected

    def test_edit_form_blocks_alphanumeric_phone_and_numeric_legal_name(self, procurement_world):
        cat = procurement_world["hardware"]
        form = VendorEditForm(
            data={
                "legal_name": "12345678",
                "trade_name": "Trade",
                "email": "ok@vendor.test",
                "phone": "abcde12345",
                "address": "100 Main St",
                "category": str(cat.id),
            }
        )
        assert not form.is_valid()
        assert "legal_name" in form.errors
        assert "phone" in form.errors

        form2 = VendorEditForm(
            data={
                "legal_name": "12345678 Corp",
                "email": "ok@vendor.test",
                "phone": "abc1234567890",
                "address": "100 Main St",
                "category": str(cat.id),
            }
        )
        assert not form2.is_valid()
        assert "legal_name" in form2.errors
        assert "phone" in form2.errors

    def test_update_vendor_service_rejects_invalid_phone_and_legal_name(self, procurement_world):
        v = procurement_world["vendor_draft"]
        with pytest.raises(ValidationError):
            update_vendor_service(
                vendor=v,
                actor=procurement_world["exec"],
                legal_name="12345678",
                trade_name="",
                email=v.email,
                phone="",
                address=v.address,
                category=v.category,
                bank_name="",
                bank_account_number="",
                bank_routing_code="",
            )
        with pytest.raises(ValidationError):
            update_vendor_service(
                vendor=v,
                actor=procurement_world["exec"],
                legal_name="Valid Systems Ltd",
                trade_name="",
                email=v.email,
                phone="abcde12345",
                address=v.address,
                category=v.category,
                bank_name="",
                bank_account_number="",
                bank_routing_code="",
            )
        # Valid Bulk XX names used in existing fixtures still pass
        assert validate_legal_name("Bulk 01") == "Bulk 01"


class TestBug014And016And018ModalAndAlerts:
    def test_edit_modal_get_renders_disabled_save_saved_values_and_placeholders(
        self, client, procurement_world
    ):
        client.force_login(procurement_world["exec"])
        v = procurement_world["vendor_draft"]
        resp = client.get(reverse("vendor_edit", args=[v.id]), HTTP_HX_REQUEST="true")
        assert resp.status_code == 200
        html = resp.content.decode()
        assert 'id="vendor-edit-save-btn"' in html
        assert "disabled" in html
        assert 'hx-target="#vendorEditModal"' in html
        assert 'data-saved-value="' in html
        assert "e.g. Acme Solutions (trading name)" in html
        assert "e.g. +91 9876543210" in html
        assert "e.g. HDFC Bank, Citibank" in html

    def test_header_has_no_duplicate_messages_include_and_app_js_auto_dismisses(self):
        with open(r"templates\partials\vendor_pages\header.html", encoding="utf-8") as f:
            hdr = f.read()
        assert "partials/messages.html" not in hdr

        with open(r"templates\base.html", encoding="utf-8") as f:
            base = f.read()
        assert "app.js?v=ps_nav_v10" in base
        assert "ps-flash-messages" in base
        assert "4000" in base

        with open(r"static\js\app.js", encoding="utf-8") as f:
            js = f.read()
        assert "Auto-dismiss success and info alert banners" in js
        assert "alert-success" in js


class TestBug019DeletedVendorRemovedFromSearchAndApi:
    def test_deleted_vendor_removed_from_master_search_and_no_cache(
        self, client, procurement_world
    ):
        v = register_vendor_service(
            legal_name="QA Vendor Alpha Ltd",
            tax_identification_number="TAX-DEL-999001",
            category=procurement_world["hardware"],
            email="qadel@vendor.test",
            address="12 Test Ave",
            created_by_user=procurement_world["exec"],
        )
        client.force_login(procurement_world["exec"])

        # Present in search before delete
        pre_search = client.get(f"{reverse('vendor_list')}?q=QA+Vendor+Alpha+Ltd")
        assert pre_search.status_code == 200
        assert v.vendor_number in pre_search.content.decode()
        assert "no-store" in pre_search.headers.get("Cache-Control", "")

        # Delete via POST view (follow redirect to consume flash confirmation)
        del_resp = client.post(reverse("vendor_delete", args=[v.id]), follow=True)
        assert del_resp.status_code == 200
        assert not Vendor.objects.filter(pk=v.pk).exists()

        # Gone from Vendor Master & Risk search results by both name and vendor_number
        post_by_name = client.get(f"{reverse('vendor_list')}?q=QA+Vendor+Alpha+Ltd")
        assert list(post_by_name.context["vendors"]) == []
        assert v.vendor_number not in post_by_name.content.decode()
        post_by_num = client.get(f"{reverse('vendor_list')}?q={v.vendor_number}")
        assert list(post_by_num.context["vendors"]) == []
        assert "QA Vendor Alpha Ltd" not in post_by_num.content.decode()

    def test_api_delete_routes_through_delete_vendor_service(self, client, procurement_world):
        v = register_vendor_service(
            legal_name="API Delete Corp",
            tax_identification_number="TAX-APIDEL-01",
            category=procurement_world["hardware"],
            email="apidel@vendor.test",
            address="12 Test Ave",
            created_by_user=procurement_world["exec"],
        )
        client.force_login(procurement_world["exec"])
        resp = client.delete(f"/api/v1/vendors/{v.id}/")
        assert resp.status_code == 204
        assert not Vendor.objects.filter(pk=v.pk).exists()

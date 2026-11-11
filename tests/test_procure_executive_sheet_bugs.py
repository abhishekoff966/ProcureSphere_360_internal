"""
Tests for Procurement Executive manual testing report fixes (BUG-002 through BUG-009).
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.sourcing.forms import SourcingEventForm
from apps.vendors.forms import VendorEditForm, VendorRegistrationForm
from apps.vendors.models import VendorCategory

pytestmark = [pytest.mark.django_db, pytest.mark.regression]


class TestProcurementExecutiveBugs:
    def test_bug_003_sourcing_start_and_deadline_validation(self):
        """BUG-003: Bid start date cannot be in the past; bid deadline cannot be in the past; min attribute set."""
        now = timezone.now()

        # 1. Past start date should fail validation with inline error on bid_start_date
        past_start_form = SourcingEventForm(
            data={
                "title": "Hardware Procurement Event",
                "event_type": "RFQ",
                "bid_start_date": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "bid_end_date": (now + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "description": "Valid sourcing scope of requirement.",
                "technical_weight": "50",
                "commercial_weight": "50",
            }
        )
        assert not past_start_form.is_valid()
        assert "bid_start_date" in past_start_form.errors
        assert "cannot be in the past" in str(past_start_form.errors["bid_start_date"])

        # 2. Past deadline should fail validation with inline error on bid_end_date
        past_end_form = SourcingEventForm(
            data={
                "title": "Hardware Procurement Event",
                "event_type": "RFQ",
                "bid_start_date": (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M"),
                "bid_end_date": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "description": "Valid sourcing scope of requirement.",
                "technical_weight": "50",
                "commercial_weight": "50",
            }
        )
        assert not past_end_form.is_valid()
        assert "bid_end_date" in past_end_form.errors
        assert "cannot be in the past" in str(past_end_form.errors["bid_end_date"])

        # 3. Widget has min attribute set to current time
        empty_form = SourcingEventForm()
        assert "min" in empty_form.fields["bid_start_date"].widget.attrs
        assert "min" in empty_form.fields["bid_end_date"].widget.attrs

        # 4. Future start and future deadline should be valid
        future_form = SourcingEventForm(
            data={
                "title": "Hardware Procurement Event",
                "event_type": "RFQ",
                "bid_start_date": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M"),
                "bid_end_date": (now + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M"),
                "description": "Valid sourcing scope of requirement.",
                "technical_weight": "50",
                "commercial_weight": "50",
            }
        )
        assert future_form.is_valid(), future_form.errors

    def test_bug_004_and_005_vendor_registration_validation(self, client, procurement_world):
        """BUG-004 & BUG-005: Register vendor modal returns validation errors via HTMX."""
        client.force_login(procurement_world["exec"])

        # Empty submission via HTMX
        response = client.post(
            "/vendors/htmx/register/",
            data={},
            HTTP_HX_REQUEST="true",
        )
        assert response.status_code == 200
        content = response.content.decode("utf-8")
        assert "is-invalid" in content
        assert "Legal name is required" in content or "This field is required" in content

        # Malformed email submission
        cat = VendorCategory.objects.first() or VendorCategory.objects.create(name="IT Hardware")
        response_email = client.post(
            "/vendors/htmx/register/",
            data={
                "legal_name": "Test Global Tech Ltd",
                "tax_identification_number": "TAX-99887766",
                "category": cat.id,
                "email": "dgchdghcgdhs",  # Malformed email without @
                "address": "123 Business Avenue, Tech Park",
            },
            HTTP_HX_REQUEST="true",
        )
        assert response_email.status_code == 200
        email_content = response_email.content.decode("utf-8")
        assert "Enter a valid email address." in email_content

    def test_bug_006_tax_id_formatting_validation(self):
        """BUG-006: Tax ID rejects repeating hyphens (--), arbitrary digit strings (142423153, 1234567890)."""
        cat = VendorCategory.objects.first() or VendorCategory.objects.create(name="IT Hardware")

        # 1. Repeating hyphens rejected
        form_dashes = VendorRegistrationForm(
            data={
                "legal_name": "Reliable Vendor Corp",
                "tax_identification_number": "1234567890--062123456789",
                "category": cat.id,
                "email": "vendor@reliable.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_dashes.is_valid()
        assert "tax_identification_number" in form_dashes.errors
        assert "consecutive hyphens" in str(form_dashes.errors["tax_identification_number"])

        # 2. Raw unformatted numbers (like 142423153) rejected
        form_raw_digits = VendorRegistrationForm(
            data={
                "legal_name": "Reliable Vendor Corp",
                "tax_identification_number": "142423153",
                "category": cat.id,
                "email": "vendor@reliable.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_raw_digits.is_valid()
        assert "tax_identification_number" in form_raw_digits.errors
        assert "raw unformatted number" in str(form_raw_digits.errors["tax_identification_number"])

        # 3. Formatted Tax ID accepted
        form_valid = VendorRegistrationForm(
            data={
                "legal_name": "Reliable Vendor Corp",
                "tax_identification_number": "TAX-12345678",
                "category": cat.id,
                "email": "vendor@reliable.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_bug_007_legal_name_validation(self):
        """BUG-007: Legal name rejects random gibberish (tdgdtgd12, sameer, ahdqhh, fsacdare)."""
        cat = VendorCategory.objects.first() or VendorCategory.objects.create(name="IT Hardware")

        # 1. tdgdtgd12 (digit mashup) rejected
        form_tdg = VendorRegistrationForm(
            data={
                "legal_name": "tdgdtgd12",
                "tax_identification_number": "TAX-123456",
                "category": cat.id,
                "email": "vendor@test.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_tdg.is_valid()
        assert "legal_name" in form_tdg.errors

        # 2. sameer (single word without corporate suffix) rejected
        form_sameer = VendorRegistrationForm(
            data={
                "legal_name": "sameer",
                "tax_identification_number": "TAX-123456",
                "category": cat.id,
                "email": "vendor@test.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_sameer.is_valid()
        assert "legal_name" in form_sameer.errors
        assert "corporate business name" in str(form_sameer.errors["legal_name"])

        # 3. ahdqhh (unvocalized gibberish) rejected
        form_ahd = VendorRegistrationForm(
            data={
                "legal_name": "ahdqhh",
                "tax_identification_number": "TAX-123456",
                "category": cat.id,
                "email": "vendor@test.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_ahd.is_valid()
        assert "legal_name" in form_ahd.errors

        # 4. fsacdare (single word random mashup) rejected
        form_fsac = VendorRegistrationForm(
            data={
                "legal_name": "fsacdare",
                "tax_identification_number": "TAX-123456",
                "category": cat.id,
                "email": "vendor@test.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_fsac.is_valid()
        assert "legal_name" in form_fsac.errors

        # 5. VendorEditForm also rejects gibberish
        edit_form = VendorEditForm(
            data={
                "legal_name": "ahdqhh",
                "email": "vendor@test.com",
                "address": "100 Tech Blvd",
                "category": cat.id,
            }
        )
        assert not edit_form.is_valid()
        assert "legal_name" in edit_form.errors

        # 6. Valid corporate names accepted
        form_valid = VendorRegistrationForm(
            data={
                "legal_name": "Sameer Corporation Ltd",
                "tax_identification_number": "TAX-123456",
                "category": cat.id,
                "email": "vendor@test.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_bug_008_pan_validation(self):
        """BUG-008: PAN enforces strict 10-character alphanumeric structure (ABCDE1234F)."""
        cat = VendorCategory.objects.first() or VendorCategory.objects.create(name="IT Hardware")
        form_long = VendorRegistrationForm(
            data={
                "legal_name": "Apex Innovations Ltd",
                "tax_identification_number": "TAX-123456",
                "pan_number": "1234567876543212345",
                "category": cat.id,
                "email": "vendor@apex.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_long.is_valid()
        assert "pan_number" in form_long.errors

        form_valid = VendorRegistrationForm(
            data={
                "legal_name": "Apex Innovations Ltd",
                "tax_identification_number": "TAX-123456",
                "pan_number": "ABCDE1234F",
                "category": cat.id,
                "email": "vendor@apex.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert form_valid.is_valid(), form_valid.errors

    def test_bug_009_gstin_validation(self):
        """BUG-009: GSTIN enforces strict 15-character structure."""
        cat = VendorCategory.objects.first() or VendorCategory.objects.create(name="IT Hardware")
        form_bad_gstin = VendorRegistrationForm(
            data={
                "legal_name": "Apex Innovations Ltd",
                "tax_identification_number": "TAX-123456",
                "gstin": "qwertyuiodklk",
                "category": cat.id,
                "email": "vendor@apex.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert not form_bad_gstin.is_valid()
        assert "gstin" in form_bad_gstin.errors

        form_valid_gstin = VendorRegistrationForm(
            data={
                "legal_name": "Apex Innovations Ltd",
                "tax_identification_number": "TAX-123456",
                "pan_number": "ABCDE1234F",
                "gstin": "29ABCDE1234F1Z5",
                "category": cat.id,
                "email": "vendor@apex.com",
                "address": "100 Tech Blvd, Suite 200",
            }
        )
        assert form_valid_gstin.is_valid(), form_valid_gstin.errors

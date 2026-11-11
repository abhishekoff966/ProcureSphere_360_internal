import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.vendors.forms import VendorContactForm, VendorProfileUpdateForm
from apps.vendors.models import Vendor, VendorCategory, VendorContact
from apps.vendors.services import (
    create_vendor_contact_service,
    register_vendor_service,
    update_vendor_contact_service,
)

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def vendor_test_env(db_roles):
    org = Organization.objects.create(name="HPE Global", code="HPE-GLB-TEST")
    dept = Department.objects.create(name="Procurement", code="PROC-TEST", organization=org)
    cost_center = CostCenter.objects.create(name="IT Hardware", code="CC-IT-TEST", department=dept)
    category = VendorCategory.objects.create(name="Enterprise Hardware", code="CAT-HW-TEST")

    vendor = register_vendor_service(
        legal_name="Test Enterprise Solutions Ltd",
        tax_identification_number="TIN-TEST-999",
        category=category,
        email="contact@testvendor.com",
        address="123 Tech Park, Bangalore, KA",
    )
    vendor.status = Vendor.STATUS_ACTIVE
    vendor.is_event_eligible = True
    vendor.event_eligibility_status = Vendor.ELIGIBILITY_ELIGIBLE
    vendor.phone = "+919876543210"
    vendor.pan_number = "ABCDE1234F"
    vendor.gstin = "29ABCDE1234F1Z5"
    vendor.save()

    vendor_user = User.objects.create_user(
        email="vendor.user@testvendor.com",
        password="Password123!",
        role=db_roles[Role.VENDOR_USER],
        vendor=vendor,
    )

    return {
        "vendor": vendor,
        "vendor_user": vendor_user,
        "category": category,
    }


# ==============================================================================
# BUG-018 & BUG-033: Telephone Input Validation & Invalid Indian Prefixes
# ==============================================================================


@pytest.mark.parametrize("valid_prefix", ["6", "7", "8", "9"])
def test_bug_018_and_033_vendor_profile_accepts_valid_indian_prefixes(valid_prefix):
    """BUG-018 & BUG-033: Phone numbers with valid Indian prefixes (6, 7, 8, 9) are accepted."""
    phone_num = f"+91{valid_prefix}123456789"
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": phone_num,
    }
    form = VendorProfileUpdateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == phone_num


def test_bug_018_and_033_vendor_profile_normalizes_12_digits():
    """BUG-018: 12-digit Indian mobile number starting with 91 auto-normalizes to +91XXXXXXXXXX."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "919876543210",
    }
    form = VendorProfileUpdateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == "+919876543210"


@pytest.mark.parametrize("invalid_prefix", ["0", "1", "2", "3", "4", "5"])
def test_bug_033_vendor_profile_rejects_invalid_indian_prefixes(invalid_prefix):
    """BUG-033: Indian mobile numbers starting with 0, 1, 2, 3, 4, 5 are strictly rejected."""
    phone_num = f"+91{invalid_prefix}123456789"
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": phone_num,
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "phone" in form.errors
    assert "Invalid Indian mobile number prefix" in form.errors["phone"][0]


@pytest.mark.parametrize(
    "invalid_phone, expected_err",
    [
        ("9876543210", "country code (+91)"),
        ("+19876543210", "country code (+91)"),
        ("+91987654321", "exactly 10 digits"),
        ("+9198765432109", "exactly 10 digits"),
        ("123456789012", "country code (+91)"),
    ],
)
def test_bug_018_vendor_profile_rejects_invalid_format_and_lengths(invalid_phone, expected_err):
    """BUG-018: Rejects numbers with wrong country codes or incorrect lengths."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": invalid_phone,
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "phone" in form.errors
    assert expected_err in form.errors["phone"][0]


# ==============================================================================
# BUG-021: PAN/GST Tax Identification Field Validation
# ==============================================================================


def test_bug_021_pan_validation_valid_and_normalized():
    """BUG-021: Valid PAN is normalized to uppercase."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "+919876543210",
        "pan_number": "abcde1234f",
    }
    form = VendorProfileUpdateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["pan_number"] == "ABCDE1234F"


@pytest.mark.parametrize(
    "invalid_pan",
    [
        "ABCD1234F",      # 9 chars
        "ABCDEF1234F",    # 11 chars
        "1BCDE1234F",     # Starts with digit
        "ABCDE12345",     # Ends with digit
        "ABCDE-1234F",    # Contains hyphen
    ],
)
def test_bug_021_pan_validation_invalid_formats(invalid_pan):
    """BUG-021: Invalid PAN formats are rejected."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "+919876543210",
        "pan_number": invalid_pan,
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "pan_number" in form.errors
    assert "Invalid PAN format" in form.errors["pan_number"][0]


def test_bug_021_gstin_validation_valid():
    """BUG-021: Valid GSTIN format is accepted."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "+919876543210",
        "gstin": "29ABCDE1234F1Z5",
    }
    form = VendorProfileUpdateForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["gstin"] == "29ABCDE1234F1Z5"


@pytest.mark.parametrize(
    "invalid_gstin",
    [
        "29ABCDE1234F1Z",    # 14 chars (short)
        "29ABCDE1234F1Z5X",  # 16 chars (long)
        "X9ABCDE1234F1Z5",   # Starts with non-digit
        "29ABCDE1234F155",   # 14th char not Z
    ],
)
def test_bug_021_gstin_validation_invalid_formats(invalid_gstin):
    """BUG-021: Invalid GSTIN formats are rejected."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "+919876543210",
        "gstin": invalid_gstin,
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "gstin" in form.errors
    assert "Invalid GSTIN format" in form.errors["gstin"][0]


def test_bug_021_pan_gstin_consistency_mismatch_rejected():
    """BUG-021: Inconsistent PAN and GSTIN are rejected."""
    data = {
        "address": "123 Valid Street, Bangalore",
        "phone": "+919876543210",
        "pan_number": "ABCDE1234F",
        "gstin": "29XYZPQ5678R1Z5",
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "gstin" in form.errors
    assert "does not match the separate PAN" in form.errors["gstin"][0]


# ==============================================================================
# BUG-024: Mandatory Field and Save Changes Validation
# ==============================================================================


@pytest.mark.parametrize("blank_address", ["", "   ", "\t\n"])
def test_bug_024_mandatory_address_cannot_be_empty(blank_address):
    """BUG-024: Empty or whitespace-only address is rejected as required."""
    data = {
        "address": blank_address,
        "phone": "+919876543210",
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "address" in form.errors
    assert "Registered office address is required." in form.errors["address"][0]


@pytest.mark.parametrize("blank_phone", ["", "   "])
def test_bug_024_mandatory_phone_cannot_be_empty(blank_phone):
    """BUG-024: Empty or whitespace-only phone is rejected as required."""
    data = {
        "address": "123 Tech Park, Bangalore",
        "phone": blank_phone,
    }
    form = VendorProfileUpdateForm(data=data)
    assert not form.is_valid()
    assert "phone" in form.errors
    assert "Contact phone number is required." in form.errors["phone"][0]


def test_bug_024_save_changes_via_view(client, vendor_test_env):
    """BUG-024: Save Changes POST with valid mandatory fields successfully updates vendor profile."""
    client.force_login(vendor_test_env["vendor_user"])
    post_data = {
        "action": "update_profile",
        "trade_name": "New Trade Name",
        "address": "456 Innovation Blvd, Hyderabad",
        "phone": "+919123456789",
        "pan_number": "ABCDE1234F",
        "gstin": "29ABCDE1234F1Z5",
        "bank_name": "HDFC Bank",
        "bank_account_number": "123456789012",
        "bank_routing_code": "HDFC0001234",
    }
    resp = client.post(reverse("vendor_profile"), post_data, follow=True)
    assert resp.status_code == 200
    vendor_test_env["vendor"].refresh_from_db()
    assert vendor_test_env["vendor"].trade_name == "New Trade Name"
    assert vendor_test_env["vendor"].address == "456 Innovation Blvd, Hyderabad"
    assert vendor_test_env["vendor"].phone == "+919123456789"


# ==============================================================================
# BUG-033 & BUG-034: Authorized Contact Validation & Unchanged Contact Save
# ==============================================================================


@pytest.mark.parametrize("valid_prefix", ["6", "7", "8", "9"])
def test_bug_033_authorized_contact_accepts_valid_indian_mobile_prefix(valid_prefix):
    """BUG-033: Contact phone with valid Indian prefix (6, 7, 8, 9) is accepted."""
    data = {
        "first_name": "Rohan",
        "last_name": "Verma",
        "email": "rohan@testvendor.com",
        "phone": f"{valid_prefix}123456789",
        "designation": "Manager",
    }
    form = VendorContactForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == f"{valid_prefix}123456789"


@pytest.mark.parametrize("invalid_prefix", ["0", "1", "2", "3", "4", "5"])
def test_bug_033_authorized_contact_rejects_invalid_indian_mobile_prefix(invalid_prefix):
    """BUG-033: Contact phone with invalid Indian prefix is rejected."""
    data = {
        "first_name": "Rohan",
        "last_name": "Verma",
        "email": "rohan@testvendor.com",
        "phone": f"{invalid_prefix}123456789",
        "designation": "Manager",
    }
    form = VendorContactForm(data=data)
    assert not form.is_valid()
    assert "phone" in form.errors
    assert "Invalid Indian mobile number prefix" in form.errors["phone"][0]


def test_bug_034_authorized_contact_optional_phone_can_be_empty():
    """BUG-034: Contact phone is optional; empty phone is permitted."""
    data = {
        "first_name": "Priya",
        "last_name": "Nair",
        "email": "priya@testvendor.com",
        "phone": "",
        "designation": "Legal Head",
    }
    form = VendorContactForm(data=data)
    assert form.is_valid(), form.errors
    assert form.cleaned_data["phone"] == ""


def test_bug_034_unchanged_authorized_contact_saves_successfully(client, vendor_test_env):
    """BUG-034: Editing an authorized contact without changing details succeeds cleanly."""
    vendor = vendor_test_env["vendor"]
    user = vendor_test_env["vendor_user"]

    # Create initial contact
    contact = create_vendor_contact_service(
        vendor=vendor,
        user=user,
        data={
            "first_name": "Amitabh",
            "last_name": "Sen",
            "email": "amitabh@testvendor.com",
            "phone": "9876543210",
            "designation": "Director",
            "is_primary": True,
        },
    )

    client.force_login(user)

    # Submit exact same details without changing anything (unchanged edit)
    edit_data = {
        "action": "edit_contact",
        "contact_id": str(contact.id),
        "first_name": contact.first_name,
        "last_name": contact.last_name,
        "email": contact.email,
        "phone": "9876543210",
        "designation": contact.designation,
        "is_primary": "true",
    }
    resp = client.post(reverse("vendor_profile"), edit_data, follow=True)
    assert resp.status_code == 200
    assert "updated successfully" in resp.content.decode()

    contact.refresh_from_db()
    assert contact.first_name == "Amitabh"
    assert contact.last_name == "Sen"
    assert contact.email == "amitabh@testvendor.com"


def test_bug_034_unchanged_contact_with_legacy_phone_allowed(client, vendor_test_env):
    """BUG-034: Contact with existing / legacy phone format is preserved and allowed when unchanged."""
    vendor = vendor_test_env["vendor"]
    user = vendor_test_env["vendor_user"]

    contact = VendorContact.objects.create(
        vendor=vendor,
        first_name="Sunita",
        last_name="Rao",
        email="sunita@testvendor.com",
        phone="+91-9876543210",
        designation="Operations",
    )

    # Form with contact instance allows the existing phone unchanged
    form = VendorContactForm(
        data={
            "first_name": "Sunita",
            "last_name": "Rao",
            "email": "sunita@testvendor.com",
            "phone": "+91-9876543210",
            "designation": "Senior Operations",
        },
        contact=contact,
    )
    assert form.is_valid(), form.errors


# ==============================================================================
# Template Inspection: Elements, Patterns, Feedback Containers
# ==============================================================================


def test_vendor_profile_template_rendered_fields_and_feedback_elements(client, vendor_test_env):
    """Verify HTML template renders the required validation attributes and feedback elements."""
    client.force_login(vendor_test_env["vendor_user"])
    resp = client.get(reverse("vendor_profile"))
    assert resp.status_code == 200
    content = resp.content.decode()

    # Telephone field validation in Profile modal
    assert 'id="vendorPhoneDigits"' in content
    assert 'pattern="[6-9][0-9]{9}"' in content
    assert 'id="phoneErrorFeedback"' in content
    assert '+91' in content

    # Address field validation in Profile modal
    assert 'id="vendorAddressInput"' in content
    assert 'id="addressErrorFeedback"' in content

    # PAN and GSTIN validation in Profile modal
    assert 'id="vendorPanInput"' in content
    assert 'id="panErrorFeedback"' in content
    assert 'id="vendorGstinInput"' in content
    assert 'id="gstinErrorFeedback"' in content

    # Contact modals
    assert 'id="addContactForm"' in content
    assert 'id="addContactPhoneError"' in content


# ==============================================================================
# Modal Layout & Save Changes Button Tests
# ==============================================================================


def test_edit_profile_modal_scrollable_structure_and_buttons(client, vendor_test_env):
    """
    Verify:
    1. Modal dialog is scrollable (modal-dialog-scrollable).
    2. Form is the modal-content container with flex layout.
    3. Modal header and footer are sticky / fixed while body is scrollable.
    4. Save Changes button with id="saveVendorProfileBtn" and Cancel button are visible in footer.
    5. Form action points to vendor_profile with action="update_profile" and CSRF token.
    """
    client.force_login(vendor_test_env["vendor_user"])
    resp = client.get(reverse("vendor_profile"))
    assert resp.status_code == 200
    content = resp.content.decode()

    # Modal container and scrollable dialog
    assert 'id="editProfileModal"' in content
    assert "modal-dialog-scrollable" in content

    # Form with modal-content
    assert 'id="vendorProfileForm"' in content
    assert 'value="update_profile"' in content
    assert 'class="modal-content' in content

    # Sticky header and footer, independently scrollable body
    assert "overflow-y: auto" in content
    assert "position: sticky; top: 0" in content
    assert "position: sticky; bottom: 0" in content

    # Visible Save Changes and Cancel buttons in footer
    assert 'id="saveVendorProfileBtn"' in content
    assert 'type="submit"' in content
    assert "Save Changes" in content
    assert "Cancel" in content
    assert 'data-bs-dismiss="modal"' in content


def test_edit_profile_save_changes_updates_full_profile_and_shows_feedback(client, vendor_test_env):
    """
    Verify that submitting the form via the existing endpoint and method:
    1. Updates all editable fields (trade name, phone, address, PAN, GSTIN, bank details).
    2. Returns a success redirect with success flash feedback message.
    3. Successfully updates the database record.
    """
    client.force_login(vendor_test_env["vendor_user"])
    vendor = vendor_test_env["vendor"]

    post_data = {
        "action": "update_profile",
        "trade_name": "Cisco Advanced Solutions",
        "phone": "+919876543210",
        "address": "Tower 4, Mindspace IT Park, Hyderabad, Telangana 500081",
        "pan_number": "ABCDE1234F",
        "gstin": "36ABCDE1234F1Z5",
        "bank_name": "State Bank of India",
        "bank_account_number": "12345678901234",
        "bank_routing_code": "SBIN0001234",
    }

    resp = client.post(reverse("vendor_profile"), data=post_data, follow=True)
    assert resp.status_code == 200

    # Success feedback banner rendered
    messages = list(resp.context["messages"])
    assert any("Company profile and banking information updated successfully." in str(m) for m in messages)

    # Database record verification
    vendor.refresh_from_db()
    assert vendor.trade_name == "Cisco Advanced Solutions"
    assert vendor.phone == "+919876543210"
    assert vendor.address == "Tower 4, Mindspace IT Park, Hyderabad, Telangana 500081"
    assert vendor.pan_number == "ABCDE1234F"
    assert vendor.gstin == "36ABCDE1234F1Z5"
    assert vendor.bank_name == "State Bank of India"
    assert vendor.bank_account_number == "12345678901234"
    assert vendor.bank_routing_code == "SBIN0001234"


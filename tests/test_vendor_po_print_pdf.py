import pytest
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from apps.accounts.models import Role, User
from apps.orders.models import POLine, PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def po_test_env(db):
    """Set up Organization, CostCenter, Vendors, and PO for PDF/Print testing."""
    org = Organization.objects.create(
        name="Hewlett Packard Enterprise",
        code="HPE-GLOBAL",
        address="1070 Arastradero Road, Palo Alto, CA 94304",
        tax_identifier="US-EIN-94-1081436",
    )
    dept = Department.objects.create(
        organization=org,
        name="IT Infrastructure Operations",
        code="IT-OPS",
    )
    cost_center = CostCenter.objects.create(
        department=dept,
        code="CC-IT-101",
        name="Compute & Cloud Operations",
    )
    category = VendorCategory.objects.create(
        name="Enterprise Hardware",
        code="CAT-HW-PRINT",
    )
    vendor_a = Vendor.objects.create(
        legal_name="Cisco Systems Inc",
        trade_name="Cisco Global",
        vendor_number="VND-2026-00001",
        email="contact@cisco.com",
        phone="+919876543210",
        address="170 West Tasman Dr, San Jose, CA 95134",
        tax_identification_number="TAX-CISCO-991",
        pan_number="ABCDE1234F",
        gstin="29ABCDE1234F1Z5",
        status=Vendor.STATUS_ACTIVE,
        category=category,
    )
    vendor_b = Vendor.objects.create(
        legal_name="Other Vendor LLC",
        vendor_number="VND-2026-00002",
        status=Vendor.STATUS_ACTIVE,
        category=category,
    )
    role_vendor = Role.objects.create(code=Role.VENDOR_USER, name="Vendor User")
    user_a = User.objects.create(
        email="vendoruser@cisco.com",
        username="vendoruser@cisco.com",
        role=role_vendor,
        vendor=vendor_a,
        is_active=True,
    )
    user_a.set_password("Password123!")
    user_a.save()

    user_b = User.objects.create(
        email="other@othervendor.com",
        username="other@othervendor.com",
        role=role_vendor,
        vendor=vendor_b,
        is_active=True,
    )
    user_b.set_password("Password123!")
    user_b.save()

    po = PurchaseOrder.objects.create(
        po_number="PO-2026-99001",
        version=1,
        vendor=vendor_a,
        cost_center=cost_center,
        status=PurchaseOrder.STATUS_ISSUED,
        subtotal=Decimal("50000.00"),
        tax_amount=Decimal("5000.00"),
        total_amount=Decimal("55000.00"),
        terms_and_conditions="Standard enterprise terms: Net 30 payment, FOB Destination receiving.",
    )
    POLine.objects.create(
        po=po,
        item_description="Catalyst 9300 48-Port Switch",
        quantity=Decimal("10.00"),
        unit_of_measure="EA",
        unit_price=Decimal("5000.00"),
        line_total=Decimal("50000.00"),
    )

    return {
        "org": org,
        "dept": dept,
        "cost_center": cost_center,
        "vendor_a": vendor_a,
        "vendor_b": vendor_b,
        "user_a": user_a,
        "user_b": user_b,
        "po": po,
    }


def test_bug_036_print_css_rules_hide_dashboard_elements(client, po_test_env):
    """BUG-036: Verify print CSS rules hide dashboard chrome, navbars, sidebars, and interactive controls."""
    user = po_test_env["user_a"]
    po = po_test_env["po"]
    client.force_login(user)

    url = reverse("vendor_purchase_order_detail", kwargs={"po_id": po.id})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()

    # Verify @media print stylesheet exists
    assert "@media print" in content

    # Verify dashboard elements are targeted for hiding in print
    assert "global-enterprise-navbar" in content
    assert "vendorSidebar" in content
    assert "screen-only-dashboard" in content
    assert "vendor-profile-avatar-btn" in content
    assert "breadcrumb" in content
    assert "display: none !important" in content


def test_bug_036_all_15_required_po_fields_present_in_print_document(client, po_test_env):
    """
    BUG-036: Verify the printable PO document contains all 15 required sections:
    1. Organization/company information
    2. Purchase Order number
    3. PO creation date
    4. Vendor information
    5. Billing address
    6. Delivery address
    7. Product/service details
    8. Item descriptions and quantities
    9. Unit prices
    10. Tax details
    11. Subtotal and grand total
    12. Payment terms
    13. Delivery terms
    14. Additional notes and conditions
    15. Authorized signature section
    """
    user = po_test_env["user_a"]
    po = po_test_env["po"]
    client.force_login(user)

    url = reverse("vendor_purchase_order_detail", kwargs={"po_id": po.id})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()

    # 1. Organization Information
    assert "Hewlett Packard Enterprise" in content
    assert "IT Infrastructure Operations" in content
    assert "CC-IT-101" in content

    # 2. Purchase Order number & Status
    assert "PO-2026-99001" in content
    assert "PURCHASE ORDER" in content
    assert "ISSUED" in content

    # 3. PO Date
    assert po.created_at.strftime("%B") in content  # Month name

    # 4. Vendor Information
    assert "Cisco Systems Inc" in content
    assert "VND-2026-00001" in content
    assert "TAX-CISCO-991" in content
    assert "ABCDE1234F" in content
    assert "29ABCDE1234F1Z5" in content

    # 5. Billing Address
    assert "Bill To / Invoice To" in content
    assert "Finance &amp; Accounts Payable Division" in content
    assert "ap.finance@procuresphere.com" in content

    # 6. Delivery Address
    assert "Ship To / Deliver To" in content
    assert "Central Receiving Dock, Warehouse Facility A" in content
    assert "FOB Destination" in content

    # 7 & 8. Product / Service Details, Descriptions & Quantities
    assert "Catalyst 9300 48-Port Switch" in content
    assert "10.00" in content
    assert "EA" in content

    # 9. Unit Prices
    assert "$5000.00" in content

    # 10. Tax Details
    assert "$5000.00" in content

    # 11. Subtotal & Grand Total
    assert "$50000.00" in content
    assert "$55000.00" in content

    # 12. Payment Terms
    assert "Net 30 Days" in content

    # 13. Delivery Terms
    assert "FOB Destination" in content

    # 14. Additional Notes and Conditions
    assert "Standard enterprise terms: Net 30 payment, FOB Destination receiving." in content

    # 15. Authorized Signature Section
    assert "Authorized Buyer Signatory" in content
    assert "Vendor Acceptance &amp; Acknowledgement" in content


def test_bug_036_interactive_screen_controls_preserved(client, po_test_env):
    """Verify interactive dashboard controls (Print button, modals, etc.) remain intact on screen."""
    user = po_test_env["user_a"]
    po = po_test_env["po"]
    client.force_login(user)

    url = reverse("vendor_purchase_order_detail", kwargs={"po_id": po.id})
    resp = client.get(url)
    assert resp.status_code == 200
    content = resp.content.decode()

    # Print / Save PDF button remains intact
    assert 'onclick="window.print()"' in content
    assert "Print / Save PDF" in content

    # Screen dashboard container wraps interactive view
    assert 'class="screen-only-dashboard d-print-none"' in content
    assert 'class="po-print-document"' in content


def test_bug_036_idor_protection_preserved(client, po_test_env):
    """Verify unauthorized vendor cannot access or print another vendor's PO."""
    user_b = po_test_env["user_b"]
    po = po_test_env["po"]
    client.force_login(user_b)

    url = reverse("vendor_purchase_order_detail", kwargs={"po_id": po.id})
    resp = client.get(url)
    assert resp.status_code == 403


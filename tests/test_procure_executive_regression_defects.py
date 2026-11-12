import re
from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.organization.models import Organization
from apps.sourcing.models import SourcingEvent
from apps.sourcing.selectors import get_sourcing_dashboard_metrics, get_sourcing_events_for_user
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def proc_exec_context(db):
    org = Organization.objects.create(name="HPE Org", code="HPE")
    other_org = Organization.objects.create(name="Other Org", code="OTH")
    role, _ = Role.objects.get_or_create(
        code="PROC_EXEC",
        defaults={"name": "Procurement Executive"},
    )
    user = User.objects.create_user(
        email="procexec_reg@hpe.com",
        password="TestPassword123!",
        role=role,
        organization=org,
        department=None,
    )
    other_user = User.objects.create_user(
        email="procexec_other@oth.com",
        password="TestPassword123!",
        role=role,
        organization=other_org,
        department=None,
    )
    return user, other_user, org, other_org


@pytest.mark.django_db
def test_def001_sourcing_metrics_match_visible_events_without_department(client, proc_exec_context):
    """SRC-BUG-010: KPI summary cards on /procurement/ and /sourcing-events/ must match visible events."""
    user, other_user, _, _ = proc_exec_context
    now = timezone.now()
    common = {
        "event_type": SourcingEvent.TYPE_RFQ,
        "bid_start_date": now - timedelta(days=5),
        "bid_end_date": now + timedelta(days=5),
    }
    SourcingEvent.objects.create(
        event_number="RFQ-2026-9001",
        title="Draft Event",
        status=SourcingEvent.STATUS_DRAFT,
        created_by=user,
        **common,
    )
    SourcingEvent.objects.create(
        event_number="RFQ-2026-9002",
        title="Tech Review Event",
        status=SourcingEvent.STATUS_TECHNICAL_REVIEW,
        created_by=user,
        **common,
    )
    SourcingEvent.objects.create(
        event_number="RFQ-2026-9003",
        title="Awarded Event",
        status=SourcingEvent.STATUS_AWARDED,
        created_by=user,
        **common,
    )
    SourcingEvent.objects.create(
        event_number="RFQ-2026-9004",
        title="Cancelled Event",
        status=SourcingEvent.STATUS_CANCELLED,
        created_by=None,
        **common,
    )
    # Other org event must stay isolated
    SourcingEvent.objects.create(
        event_number="RFQ-2026-9099",
        title="Other Org Awarded",
        status=SourcingEvent.STATUS_AWARDED,
        created_by=other_user,
        **common,
    )

    visible_events = get_sourcing_events_for_user(user)
    metrics = get_sourcing_dashboard_metrics(user=user)

    assert visible_events.count() == 4
    assert metrics["total"] == 4
    assert metrics["draft"] == 1
    assert metrics["awaiting_evaluation"] == 1
    assert metrics["awarded"] == 1
    assert metrics["cancelled"] == 1

    client.force_login(user)
    sourcing_resp = client.get(reverse("sourcing_list"))
    assert sourcing_resp.status_code == 200
    assert sourcing_resp.context["metrics"]["draft"] == 1
    assert sourcing_resp.context["metrics"]["awaiting_evaluation"] == 1
    assert sourcing_resp.context["metrics"]["awarded"] == 1
    assert sourcing_resp.context["metrics"]["cancelled"] == 1

    dash_resp = client.get(reverse("procurement_dashboard"))
    assert dash_resp.status_code == 200
    assert dash_resp.context["sourcing"]["draft"] == 1
    assert dash_resp.context["sourcing"]["awarded"] == 1
    assert dash_resp.context["sourcing"]["cancelled"] == 1


@pytest.mark.django_db
def test_def002_vendor_detail_sidebar_highlights_active_section(client, proc_exec_context):
    """VND-BUG-019: Vendor detail sub-routes highlight the active parent module with .active and aria-current."""
    user, _, org, _ = proc_exec_context
    category = VendorCategory.objects.create(name="IT Hardware", code="IT-HW")
    vendor = Vendor.objects.create(
        vendor_number="VND-2026-9001",
        legal_name="Acme Networking Pvt Ltd",
        tax_identification_number="TAX-90019001",
        category=category,
        email="acme@example.com",
        address="100 Tech Park, Bengaluru",
        organization=org,
    )

    client.force_login(user)

    resp_master = client.get(reverse("vendor_detail", args=[vendor.id]) + "?section=master")
    assert resp_master.status_code == 200
    html_master = resp_master.content.decode("utf-8")
    assert re.search(
        r'<a[^>]+class="[^"]*\bactive-nav-item\b[^"]*"[^>]+aria-current="page"[^>]*>\s*<span>Vendor Master &amp; Risk</span>',
        html_master,
    )

    resp_status = client.get(reverse("vendor_detail_status", args=[vendor.id]) + "?section=master")
    assert resp_status.status_code == 200
    html_status = resp_status.content.decode("utf-8")
    assert re.search(
        r'<a[^>]+class="[^"]*\bactive-nav-item\b[^"]*"[^>]+aria-current="page"[^>]*>\s*<span>Vendor Master &amp; Risk</span>',
        html_status,
    )

    resp_gov = client.get(reverse("vendor_detail", args=[vendor.id]) + "?section=governance")
    assert resp_gov.status_code == 200
    html_gov = resp_gov.content.decode("utf-8")
    assert re.search(
        r'<a[^>]+class="[^"]*\bactive-nav-item\b[^"]*"[^>]+aria-current="page"[^>]*>\s*<span>Vendor Governance</span>',
        html_gov,
    )


@pytest.mark.django_db
def test_def003_register_vendor_modal_address_has_placeholder(client, proc_exec_context):
    """PROC-BUG-013: Register Vendor modal address textarea includes a placeholder attribute."""
    user, _, _, _ = proc_exec_context
    client.force_login(user)
    resp = client.get(reverse("vendors_register_modal"))
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")
    assert re.search(r'<textarea[^>]+name="address"[^>]+placeholder="[^"]+"', html)

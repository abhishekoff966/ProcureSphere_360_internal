import pytest
from django.core.exceptions import ValidationError
from apps.accounts.models import Role, User
from apps.organization.models import Organization
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors import services as vendor_services
from apps.sourcing.models import SourcingEvent, VendorBid, AwardDecision
from apps.sourcing import services as sourcing_services
from apps.vendors.api_urls import VendorViewSet
from rest_framework.test import APIRequestFactory, force_authenticate


@pytest.fixture
def manager_user(db):
    org = Organization.objects.create(name='Test Subsidiary Org', code='TEST-SUB-01')
    role, _ = Role.objects.get_or_create(name='Procurement Manager')
    user = User.objects.create_user(
        email='test_mgr_scoping@example.com',
        password='Password123!',
        role=role,
        organization=org
    )
    return user


@pytest.fixture
def draft_vendor(db):
    cat, _ = VendorCategory.objects.get_or_create(name='Test IT Services', code='TEST-IT')
    return Vendor.objects.create(
        legal_name='Test Draft Onboarding Supplier',
        vendor_number='VND-TEST-DRAFT-001',
        tax_identification_number='TAX-TEST-DRAFT-001',
        category=cat,
        status=Vendor.STATUS_DRAFT
    )


@pytest.mark.django_db
def test_vendor_suspension_from_draft_allowed(draft_vendor, manager_user):
    """Verify that a Procurement Manager can suspend a vendor from DRAFT status during sanctions review."""
    updated = vendor_services.set_vendor_status_governance_service(
        vendor=draft_vendor,
        actor=manager_user,
        new_status=Vendor.STATUS_SUSPENDED,
        notes='Sanctions screening match: Suspended during onboarding review'
    )
    assert updated.status == Vendor.STATUS_SUSPENDED
    assert 'Sanctions screening match' in updated.status_notes


@pytest.mark.django_db
def test_vendor_viewset_manager_enterprise_scoping(manager_user, draft_vendor):
    """Verify that a Procurement Manager sees all enterprise vendors regardless of tenant organization assignment."""
    # Create another vendor in a completely different organization
    cat, _ = VendorCategory.objects.get_or_create(name='Test IT Services', code='TEST-IT')
    other_org = Organization.objects.create(name='Other Parent Corp', code='OTHER-PARENT')
    other_vendor = Vendor.objects.create(
        legal_name='Enterprise Global Vendor Corp',
        vendor_number='VND-OTHER-002',
        tax_identification_number='TAX-OTHER-002',
        status=Vendor.STATUS_ACTIVE,
        category=cat,
        organization=other_org
    )

    factory = APIRequestFactory()
    req = factory.get('/api/v1/vendors/?page_size=100')
    force_authenticate(req, user=manager_user)
    view = VendorViewSet.as_view({'get': 'list'})
    res = view(req)
    assert res.status_code == 200

    results = res.data.get('results', [])
    vendor_ids = [v['id'] for v in results]
    assert str(draft_vendor.id) in vendor_ids
    assert str(other_vendor.id) in vendor_ids


@pytest.mark.django_db
def test_sourcing_award_from_technical_review_allowed(db, manager_user):
    """Verify that evaluate_and_award_sourcing_event_service allows direct manager executive award from TECHNICAL_REVIEW."""
    cat, _ = VendorCategory.objects.get_or_create(name='Test Hardware', code='TEST-HW')
    vendor = Vendor.objects.create(
        legal_name='Reliable Tech Hardware Ltd',
        vendor_number='VND-AWARD-TEST-001',
        tax_identification_number='TAX-AWARD-001',
        status=Vendor.STATUS_ACTIVE,
        is_event_eligible=True,
        category=cat
    )
    creator = User.objects.create_user(
        email='event_creator_exec@example.com',
        password='Password123!'
    )
    from django.utils import timezone
    from datetime import timedelta
    now = timezone.now()
    event = SourcingEvent.objects.create(
        title='Data Center Hardware Refresh',
        event_number='RFQ-TEST-TECH-001',
        status=SourcingEvent.STATUS_TECHNICAL_REVIEW,
        event_type=SourcingEvent.TYPE_RFQ,
        bid_start_date=now - timedelta(days=2),
        bid_end_date=now + timedelta(days=5),
        description='Server and storage hardware procurement event',
        created_by=creator
    )
    bid = VendorBid.objects.create(
        event=event,
        vendor=vendor,
        total_bid_amount=50000.00,
        status=VendorBid.STATUS_SUBMITTED,
        created_by=creator
    )

    decision = sourcing_services.evaluate_and_award_sourcing_event_service(
        event=event,
        winning_bid=bid,
        award_reason='Executive Manager Technical Award Approval',
        approved_by_user=manager_user
    )
    event.refresh_from_db()
    assert event.status == SourcingEvent.STATUS_AWARDED
    assert decision.status == AwardDecision.STATUS_APPROVED
    assert decision.winning_bid_id == bid.id


def test_base_manager_clean_error_string_no_object_object():
    """Verify base_manager.html template cleans [object Object] and object:object completely."""
    with open('templates/manager/base_manager.html', 'r', encoding='utf-8') as f:
        content = f.read()

    assert "lower === '[object object]'" in content
    assert "lower === 'object object'" in content
    assert "lower === 'object:object'" in content
    assert "return '';" in content

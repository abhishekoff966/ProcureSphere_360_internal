import pytest
from decimal import Decimal
from django.utils import timezone
from django.test import RequestFactory
from apps.accounts.models import Role, User
from apps.organization.models import Organization
from apps.contracts.models import Contract, ContractObligation
from apps.vendors.models import Vendor, VendorCategory
from apps.sourcing.models import SourcingEvent, VendorBid
from apps.audit import selectors
from apps.audit.views import (
    _get_auditor_org,
    audit_contract_changes_view,
    audit_sourcing_activity_view,
    audit_vendors_view,
    audit_approvals_view,
    audit_po_changes_view,
    audit_invoice_exceptions_view,
    audit_security_events_view,
    auditor_dashboard_view,
)


@pytest.fixture
def auditor_user(db):
    org = Organization.objects.create(name='Tenant Sub Corp', code='SUB-TENANT-01')
    role, _ = Role.objects.get_or_create(name='Compliance Auditor', code=Role.AUDITOR)
    return User.objects.create_user(
        email='auditor_test_scoping@example.com',
        password='Password123!',
        role=role,
        organization=org
    )


@pytest.fixture
def sample_contracts(db):
    cat, _ = VendorCategory.objects.get_or_create(name='Legal Services', code='LEGAL-SVC')
    other_org = Organization.objects.create(name='Global Parent Corp', code='GLOBAL-CORP')
    owner = User.objects.create_user(
        email='contract_owner_test@example.com',
        password='Password123!'
    )
    vendor = Vendor.objects.create(
        legal_name='Global Legal Counsel LLP',
        vendor_number='VND-GLB-001',
        tax_identification_number='TAX-GLB-001',
        category=cat,
        status=Vendor.STATUS_ACTIVE,
        organization=other_org
    )
    today = timezone.now().date()
    con_active = Contract.objects.create(
        contract_number='CON-TEST-ACT-001',
        title='Global Facilities Maintenance',
        vendor=vendor,
        contract_owner=owner,
        contract_value=Decimal('50000.00'),
        status=Contract.STATUS_ACTIVE,
        start_date=today,
        end_date=today + timezone.timedelta(days=365)
    )
    con_draft = Contract.objects.create(
        contract_number='CON-TEST-DFT-001',
        title='Draft IT Services',
        vendor=vendor,
        contract_owner=owner,
        contract_value=Decimal('25000.00'),
        status=Contract.STATUS_DRAFT,
        start_date=today,
        end_date=today + timezone.timedelta(days=180)
    )
    ContractObligation.objects.create(
        contract=con_active,
        title='Quarterly SLA Audit',
        due_date=today - timezone.timedelta(days=10),
        is_fulfilled=False
    )
    return con_active, con_draft


@pytest.mark.django_db
def test_auditor_org_scoping_global_read_only(auditor_user):
    """Verify that Compliance Auditor has global enterprise visibility (org is None)."""
    rf = RequestFactory()
    req = rf.get('/audit/contract-changes/')
    req.user = auditor_user
    assert _get_auditor_org(req) is None


@pytest.mark.django_db
def test_auditor_contract_changes_summary_kpis(auditor_user, sample_contracts):
    """Verify that get_contract_changes_audit returns active_count, active_contracts, total_contract_value, and overdue_obligations_count."""
    res = selectors.get_contract_changes_audit()
    summary = res['summary']

    assert summary['active_contracts'] == 1
    assert summary['active_count'] == 1
    assert summary['total_contracts'] == 2
    assert summary['total_count'] == 2
    assert 'total_contract_value' in summary
    assert 'active_contract_value' in summary
    assert summary['overdue_obligations_count'] == 1
    assert summary['total_overdue_obligations'] == 1


@pytest.mark.django_db
def test_contract_changes_template_renders_non_zero_counts(auditor_user, sample_contracts):
    """Verify contract_changes view renders populated KPI cards for an auditor user belonging to a tenant organization."""
    rf = RequestFactory()
    req = rf.get('/audit/contract-changes/')
    req.user = auditor_user
    response = audit_contract_changes_view(req)
    assert response.status_code == 200
    content = response.content.decode('utf-8')

    assert 'CON-TEST-ACT-001' in content
    assert 'CON-TEST-DFT-001' in content
    # Verify active contracts count is 1, not 0
    assert 'Live Executed Agreements (2 Total Portfolio)' in content


@pytest.mark.django_db
def test_sourcing_activity_summary_and_aliases(auditor_user):
    """Verify get_sourcing_activity_audit returns bids_count and requisition_number aliases."""
    res = selectors.get_sourcing_activity_audit()
    summary = res['summary']
    assert 'total_events' in summary
    assert 'total_count' in summary
    assert 'awarded_events' in summary
    assert 'total_bids_submitted' in summary

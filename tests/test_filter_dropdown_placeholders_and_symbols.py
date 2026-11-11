import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.core.superadmin_forms import BudgetAdminForm
from apps.contracts.forms import ContractCreateForm
from apps.sourcing.forms import SourcingEventForm
from apps.vendors.filters import VendorFilter
from apps.vendors.forms import VendorEditForm, VendorRegistrationForm


@pytest.fixture
def superadmin_client(db):
    role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    admin_user = User.objects.create_superuser(
        email="superadmin_filter_test@procuresphere.local",
        password="SuperPassword123!",
        role=role,
    )
    client = Client()
    client.force_login(admin_user)
    return client, admin_user


@pytest.fixture
def dept_approver_client(db):
    org, _ = Organization.objects.get_or_create(name="HPE Test Org", code="HPE_TST", is_active=True)
    dept, _ = Department.objects.get_or_create(name="Engineering", code="ENG", organization=org)
    role, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Department Approver"})
    user = User.objects.create_user(
        email="approver_filter_test@procuresphere.local",
        password="ApproverPassword123!",
        role=role,
        organization=org,
        department=dept,
    )
    client = Client()
    client.force_login(user)
    return client, user, org, dept


@pytest.mark.django_db
def test_budget_admin_form_has_meaningful_cost_center_placeholder():
    """Verify BudgetAdminForm cost_center empty_label is not Django's unmeaningful '---------'."""
    form = BudgetAdminForm()
    assert form.fields["cost_center"].empty_label == "-- Select Cost Center --"


@pytest.mark.django_db
def test_contracts_sourcing_vendor_forms_have_meaningful_placeholders():
    """Verify contracts, sourcing, and vendors forms have meaningful empty labels."""
    c_form = ContractCreateForm()
    assert c_form.fields["vendor"].empty_label == "-- Select Vendor --"
    assert c_form.fields["sourcing_event"].empty_label == "-- Select Sourcing Event (Optional) --"
    assert c_form.fields["po"].empty_label == "-- Select Purchase Order (Optional) --"

    s_form = SourcingEventForm()
    assert s_form.fields["requisition"].empty_label == "-- Select Requisition (Optional) --"

    v_form1 = VendorEditForm()
    assert v_form1.fields["category"].empty_label == "-- Select Category --"

    v_form2 = VendorRegistrationForm()
    assert v_form2.fields["category"].empty_label == "-- Select Category --"


@pytest.mark.django_db
def test_superadmin_users_filter_dropdown_options(superadmin_client):
    """Verify Superadmin Users page has meaningful filter labels and no unmeaningful '---------'."""
    client, _ = superadmin_client
    resp = client.get(reverse("superadmin:users"))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "-- All Roles --" in content
    assert "-- All Statuses --" in content
    assert "<option value=\"\">All Roles</option>" not in content
    assert "<option value=\"\">All Statuses</option>" not in content
    assert "---------" not in content


@pytest.mark.django_db
def test_superadmin_organizations_department_filter(superadmin_client):
    """Verify Superadmin Organizations/Cost Centers page has '-- All Departments --'."""
    client, admin_user = superadmin_client
    org = Organization.objects.create(name="HPE Test Org", code="HPE_TST", is_active=True)
    admin_user.organization = org
    admin_user.save()
    dept = Department.objects.create(name="Engineering", code="ENG", organization=org)
    CostCenter.objects.create(name="Cloud Infra", code="CC-ENG-101", department=dept)

    resp = client.get(reverse("superadmin:organizations"))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "-- All Departments --" in content
    assert "---------" not in content


@pytest.mark.django_db
def test_department_approver_inbox_filter_and_styling(dept_approver_client):
    """Verify Approvals Inbox has '-- All Statuses --' and prominent chevron styling."""
    client, user, org, dept = dept_approver_client
    resp = client.get("/approvals/inbox/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "-- All Statuses --" in content
    assert "---------" not in content
    # SVG chevron is styled globally or in layout
    assert "background-image" in content
    assert "appearance: none" in content


@pytest.mark.django_db
def test_approver_base_layout_has_dropdown_arrow_css(dept_approver_client):
    """Verify Department Approver workspace layout includes prominent dropdown chevron CSS."""
    client, user, org, dept = dept_approver_client
    resp = client.get("/approvals/inbox/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "data:image/svg+xml" in content
    assert "appearance: none" in content


@pytest.mark.django_db
def test_vendor_filter_dropdown_placeholders_and_widgets():
    """Verify VendorFilter dropdown options have meaningful empty labels and form-select classes."""
    v_filter = VendorFilter()
    form = v_filter.form

    # Check empty labels on filter form fields
    assert form.fields["category"].empty_label == "-- All Categories --"
    assert form.fields["status"].empty_label == "-- All Statuses --"
    assert form.fields["approval_status"].empty_label == "-- All Approval Statuses --"
    assert form.fields["operational_status"].empty_label == "-- All Operational Statuses --"
    assert form.fields["risk_level"].empty_label == "-- All Risk Levels --"
    assert form.fields["restriction"].empty_label == "-- All Hold / Suspension --"
    assert form.fields["performance"].empty_label == "-- All Performance --"

    # Check rendered widgets
    for field_name in ["category", "operational_status", "approval_status", "risk_level", "restriction", "performance"]:
        rendered = form[field_name].as_widget()
        assert "---------" not in rendered, f"Field {field_name} contains unmeaningful '---------' placeholder"
        assert "form-select" in rendered, f"Field {field_name} missing form-select class"


@pytest.mark.django_db
def test_vendor_list_page_renders_meaningful_filter_placeholders(superadmin_client):
    """Verify GET /vendors/ renders clean filter options without unmeaningful dashes."""
    client, _ = superadmin_client
    resp = client.get(reverse("vendor_list"))
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert "-- All Categories --" in content
    assert "-- All Operational Statuses --" in content
    assert "-- All Approval Statuses --" in content
    assert "-- All Risk Levels --" in content
    assert "-- All Hold / Suspension --" in content
    assert "-- All Performance --" in content
    assert "---------" not in content


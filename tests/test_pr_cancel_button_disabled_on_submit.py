from decimal import Decimal

import pytest
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.services import create_purchase_requisition_service


@pytest.fixture
def requester_world(db):
    org = Organization.objects.create(name="HPE Tech Org", code="HPET")
    dept = Department.objects.create(name="IT Procurement", code="ITP", organization=org)
    cc = CostCenter.objects.create(name="IT Operations", code="CC-IT-99", department=dept)
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    user = User.objects.create_user(
        email="req_cancel_test@hpe.com",
        password="Password123!",
        role=role_req,
        department=dept,
    )
    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "user": user,
    }


@pytest.mark.django_db
def test_pr_create_page_contains_cancel_button_with_disable_protections(client, requester_world):
    """
    Verify that creating a new PR has an identifiable cancel button,
    disable/enable handlers, and click prevention logic during loading/submission.
    """
    client.force_login(requester_world["user"])
    res = client.get("/requisitions/create/")
    assert res.status_code == 200
    html = res.content.decode("utf-8")

    # 1. Identifiable Cancel button with ID and data-action
    assert 'id="pr_cancel_btn"' in html
    assert 'data-action="cancel"' in html
    assert "Cancel" in html

    # 2. Dedicated disable and enable handler functions in script
    assert "disableCancelButtons()" in html or "function disableCancelButtons()" in html
    assert "enableCancelButtons()" in html or "function enableCancelButtons()" in html

    # 3. Form submission hook to disable Cancel button
    assert "disableCancelButtons();" in html

    # 4. Click event interception on Cancel button when submitting
    assert "prCancelBtn.addEventListener('click'" in html or "pr_cancel_btn" in html


@pytest.mark.django_db
def test_pr_edit_page_contains_cancel_button_with_disable_protections(client, requester_world):
    """
    Verify that editing a PR also has an identifiable cancel button,
    disable/enable handlers, and click prevention logic during loading/submission.
    """
    client.force_login(requester_world["user"])
    future_date = (timezone.now().date() + timezone.timedelta(days=7)).isoformat()
    pr = create_purchase_requisition_service(
        title="PR for Edit Test",
        justification="Verifying cancel button disable behavior",
        requester=requester_world["user"],
        department=requester_world["dept"],
        cost_center=requester_world["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Network Switch",
                "quantity": 2,
                "estimated_unit_price": Decimal("250.00"),
            }
        ],
    )

    res = client.get(f"/requisitions/{pr.pk}/edit/")
    assert res.status_code == 200
    html = res.content.decode("utf-8")

    # 1. Identifiable Cancel button with ID and data-action
    assert 'id="pr_edit_cancel_btn"' in html
    assert 'data-action="cancel"' in html

    # 2. Handlers and submit hooks present
    assert "disableCancelButtons()" in html or "function disableCancelButtons()" in html
    assert "disableCancelButtons();" in html
    assert "prEditCancelBtn" in html


def test_app_js_global_cancel_button_disable_protections():
    """
    Verify that static/js/app.js implements global disable and interception
    for cancel buttons during form submission.
    """
    with open("static/js/app.js", "r", encoding="utf-8") as f:
        js = f.read()

    assert "window.disableFormCancelButtons" in js
    assert "window.resetFormCancelButtons" in js
    assert "window.disableFormCancelButtons(form)" in js
    assert "#pr_cancel_btn" in js
    assert '[data-action="cancel"]' in js

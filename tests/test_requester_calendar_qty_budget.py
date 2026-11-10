from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.utils import timezone
from rest_framework.exceptions import ValidationError as DRFValidationError

from apps.accounts.models import Role, User
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.api_urls import PRLineSerializer, PurchaseRequisitionSerializer
from apps.requisitions.models import PurchaseRequisition
from apps.requisitions.services import create_purchase_requisition_service


@pytest.fixture
def pr_setup(db):
    org = Organization.objects.create(name="HPE Corp", code="HPE")
    dept = Department.objects.create(name="Engineering", code="ENG", organization=org)
    cc = CostCenter.objects.create(name="Cloud Ops", code="CC-ENG-001", department=dept)
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    requester = User.objects.create_user(
        email="req_user@example.com",
        password="Password123!",
        role=role_req,
        department=dept,
    )
    return {
        "org": org,
        "dept": dept,
        "cc": cc,
        "requester": requester,
    }


@pytest.mark.django_db
def test_past_delivery_date_rejected_in_service(pr_setup):
    yesterday = timezone.now().date() - timezone.timedelta(days=1)
    with pytest.raises(ValidationError, match="Requested delivery date cannot be in the past"):
        create_purchase_requisition_service(
            title="Past Delivery PR",
            justification="Testing past delivery date",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=yesterday,
            line_items=[
                {
                    "item_description": "Servers",
                    "quantity": 5,
                    "estimated_unit_price": Decimal("100.00"),
                }
            ],
        )


@pytest.mark.django_db
def test_quantity_must_be_positive_whole_number_in_service(pr_setup):
    future_date = timezone.now().date() + timezone.timedelta(days=10)

    # 1. Quantity = 0
    with pytest.raises(ValidationError, match="greater than zero"):
        create_purchase_requisition_service(
            title="Zero Qty PR",
            justification="Testing zero qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Laptops",
                    "quantity": 0,
                    "estimated_unit_price": Decimal("500.00"),
                }
            ],
        )

    # 2. Quantity < 0
    with pytest.raises(ValidationError, match="greater than zero"):
        create_purchase_requisition_service(
            title="Negative Qty PR",
            justification="Testing neg qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Laptops",
                    "quantity": -2,
                    "estimated_unit_price": Decimal("500.00"),
                }
            ],
        )

    # 3. Decimal quantity (e.g. 2.5)
    with pytest.raises(ValidationError, match="whole number"):
        create_purchase_requisition_service(
            title="Decimal Qty PR",
            justification="Testing decimal qty",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Cables",
                    "quantity": Decimal("2.5"),
                    "estimated_unit_price": Decimal("10.00"),
                }
            ],
        )


@pytest.mark.django_db
def test_valid_integer_quantity_and_future_date_succeeds(pr_setup):
    future_date = timezone.now().date() + timezone.timedelta(days=15)
    pr = create_purchase_requisition_service(
        title="Valid PR",
        justification="Valid justification",
        requester=pr_setup["requester"],
        department=pr_setup["dept"],
        cost_center=pr_setup["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Monitors",
                "quantity": 3,
                "estimated_unit_price": Decimal("250.00"),
            }
        ],
    )
    assert pr.status == PurchaseRequisition.STATUS_DRAFT
    assert pr.lines.first().quantity == Decimal("3")
    assert pr.total_amount == Decimal("750.00")


@pytest.mark.django_db
def test_serializer_validates_whole_quantity_and_future_date():
    yesterday = timezone.now().date() - timezone.timedelta(days=1)
    ser = PurchaseRequisitionSerializer(data={"requested_delivery_date": yesterday})
    assert not ser.is_valid()
    assert "requested_delivery_date" in ser.errors

    line_ser = PRLineSerializer(
        data={"item_description": "Item", "quantity": 1.75, "estimated_unit_price": 10}
    )
    assert not line_ser.is_valid()
    assert "quantity" in line_ser.errors


@pytest.mark.django_db
def test_empty_line_items_rejected_in_service(pr_setup):
    future_date = timezone.now().date() + timezone.timedelta(days=15)
    with pytest.raises(ValidationError, match="must contain at least one line item"):
        create_purchase_requisition_service(
            title="Empty Items PR",
            justification="Testing empty items",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[],
        )


@pytest.mark.django_db
def test_pr_create_and_edit_rejects_empty_line_items(client, pr_setup):
    client.force_login(pr_setup["requester"])
    future_date = (timezone.now().date() + timezone.timedelta(days=15)).isoformat()

    # 1. Create with empty line items
    create_resp = client.post(
        "/requisitions/create/",
        {
            "title": "PR with No Lines",
            "justification": "Test justification",
            "department": pr_setup["dept"].id,
            "cost_center": pr_setup["cc"].id,
            "requested_delivery_date": future_date,
            "item_description[]": [""],
            "quantity[]": ["1"],
            "unit_of_measure[]": ["EA"],
            "estimated_unit_price[]": ["100.00"],
            "submit_action": "draft",
        },
        follow=True,
    )
    assert any("Please add at least one line item" in str(m) for m in create_resp.context["messages"])
    assert PurchaseRequisition.objects.filter(title="PR with No Lines").count() == 0

    # 2. Create valid PR first
    valid_pr = create_purchase_requisition_service(
        title="Valid Draft PR",
        justification="Valid justification",
        requester=pr_setup["requester"],
        department=pr_setup["dept"],
        cost_center=pr_setup["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Initial Item",
                "quantity": 1,
                "estimated_unit_price": Decimal("50.00"),
            }
        ],
    )
    assert valid_pr.lines.count() == 1

    # 3. Edit attempt with empty items
    edit_resp = client.post(
        f"/requisitions/{valid_pr.pk}/edit/",
        {
            "title": "Valid Draft PR - Updated",
            "justification": "Valid justification updated",
            "department": pr_setup["dept"].id,
            "cost_center": pr_setup["cc"].id,
            "requested_delivery_date": future_date,
            "item_description[]": ["   "],
            "quantity[]": ["1"],
            "unit_of_measure[]": ["EA"],
            "estimated_unit_price[]": ["50.00"],
        },
        follow=True,
    )
    assert any("Please add at least one line item" in str(m) for m in edit_resp.context["messages"])
    valid_pr.refresh_from_db()
    assert valid_pr.lines.count() == 1
    assert valid_pr.lines.first().item_description == "Initial Item"


@pytest.mark.django_db
def test_estimated_unit_price_zero_or_negative_rejected_in_service(pr_setup):
    """BUG-015: Unit price must be strictly greater than zero in service."""
    future_date = timezone.now().date() + timezone.timedelta(days=10)

    # Unit price = 0
    with pytest.raises(ValidationError, match="must be greater than zero"):
        create_purchase_requisition_service(
            title="Zero Price PR",
            justification="Testing zero unit price",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Free Item",
                    "quantity": 1,
                    "estimated_unit_price": Decimal("0.00"),
                }
            ],
        )

    # Unit price < 0
    with pytest.raises(ValidationError, match="must be greater than zero"):
        create_purchase_requisition_service(
            title="Negative Price PR",
            justification="Testing negative unit price",
            requester=pr_setup["requester"],
            department=pr_setup["dept"],
            cost_center=pr_setup["cc"],
            requested_delivery_date=future_date,
            line_items=[
                {
                    "item_description": "Negative Item",
                    "quantity": 1,
                    "estimated_unit_price": Decimal("-50.00"),
                }
            ],
        )


@pytest.mark.django_db
def test_estimated_unit_price_zero_rejected_in_views(client, pr_setup):
    """BUG-015 & BUG-016: Unit price must be > 0 in create and edit views."""
    client.force_login(pr_setup["requester"])
    future_date = (timezone.now().date() + timezone.timedelta(days=10)).isoformat()

    # 1. Create with zero unit price
    resp = client.post(
        "/requisitions/create/",
        {
            "title": "Zero Price PR",
            "justification": "Test justification",
            "department": pr_setup["dept"].id,
            "cost_center": pr_setup["cc"].id,
            "requested_delivery_date": future_date,
            "item_description[]": ["Zero Price Item"],
            "quantity[]": ["2"],
            "unit_of_measure[]": ["EA"],
            "estimated_unit_price[]": ["0.00"],
            "action_type": "save_draft",
        },
        follow=True,
    )
    assert any("Estimated unit price for line item 'Zero Price Item' must be greater than zero." in str(m) for m in resp.context["messages"])
    assert PurchaseRequisition.objects.filter(title="Zero Price PR").count() == 0

    # 2. Valid PR to edit
    pr = create_purchase_requisition_service(
        title="Valid PR",
        justification="Valid justification",
        requester=pr_setup["requester"],
        department=pr_setup["dept"],
        cost_center=pr_setup["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Initial Item",
                "quantity": 1,
                "estimated_unit_price": Decimal("50.00"),
            }
        ],
    )

    # 3. Edit with negative price
    edit_resp = client.post(
        f"/requisitions/{pr.pk}/edit/",
        {
            "title": "Valid PR - Edited",
            "justification": "Valid justification edited",
            "department": pr_setup["dept"].id,
            "cost_center": pr_setup["cc"].id,
            "requested_delivery_date": future_date,
            "item_description[]": ["Initial Item"],
            "quantity[]": ["1"],
            "unit_of_measure[]": ["EA"],
            "estimated_unit_price[]": ["-10.00"],
        },
        follow=True,
    )
    assert any("Estimated unit price for line item 'Initial Item' must be greater than zero." in str(m) for m in edit_resp.context["messages"])
    pr.refresh_from_db()
    assert pr.lines.first().estimated_unit_price == Decimal("50.00")


@pytest.mark.django_db
def test_create_requisition_ui_elements_and_indicators(client, pr_setup):
    """BUG-012, BUG-014, BUG-021: Verify red asterisks, placeholders, and UOM column width in HTML."""
    client.force_login(pr_setup["requester"])
    resp = client.get("/requisitions/create/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")

    # BUG-012/013: High-visibility red asterisks
    assert 'Requisition Title <span class="text-danger">*</span>' in content
    assert 'Business Justification <span class="text-danger">*</span>' in content
    assert 'Department <span class="text-danger">*</span>' in content
    assert 'Cost Center <span class="text-danger">*</span>' in content
    assert 'Required Delivery Date <span class="text-danger">*</span>' in content

    # BUG-021: Placeholder in item description
    assert 'placeholder="e.g. Dell Latitude 5540 Laptop"' in content

    # BUG-015: min="0.01" on unit price input
    assert 'min="0.01"' in content

    # BUG-014: UOM min-width styling
    assert 'min-width: 125px;' in content
    assert 'style="min-width: 115px;"' in content

    # Mandatory Line Item Alert Pop-up Modal
    assert 'id="mandatoryLineItemModal"' in content
    assert 'Default Line Item Cannot Be Deleted' in content
    assert 'one mandatory line item' in content


@pytest.mark.django_db
def test_edit_requisition_mandatory_line_item_modal(client, pr_setup):
    """Verify mandatory line item pop-up modal is present in edit requisition view."""
    client.force_login(pr_setup["requester"])
    future_date = (timezone.now().date() + timezone.timedelta(days=10)).isoformat()
    pr = create_purchase_requisition_service(
        title="Sample PR for Edit Modal",
        justification="Testing edit modal",
        requester=pr_setup["requester"],
        department=pr_setup["dept"],
        cost_center=pr_setup["cc"],
        requested_delivery_date=future_date,
        line_items=[
            {
                "item_description": "Default Item",
                "quantity": 1,
                "estimated_unit_price": Decimal("75.00"),
            }
        ],
    )
    resp = client.get(f"/requisitions/{pr.pk}/edit/")
    assert resp.status_code == 200
    content = resp.content.decode("utf-8")
    assert 'id="mandatoryLineItemModal"' in content
    assert 'Default Line Item Cannot Be Deleted' in content
    assert 'one mandatory line item' in content




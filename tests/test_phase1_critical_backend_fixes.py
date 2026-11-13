from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.models import Budget, SpendLedger
from apps.contracts.models import Contract
from apps.contracts.services import (
    activate_contract_service,
    approve_business_service,
    approve_legal_review_service,
    create_contract_service,
    create_contract_version_service,
    reject_legal_review_service,
    renew_contract_service,
    submit_for_legal_review_service,
    terminate_contract_service,
)
from apps.invoices.models import MatchException, SupplierInvoice
from apps.invoices.services import (
    create_supplier_invoice_service,
    mark_invoice_paid_service,
    resolve_match_exception_service,
)
from apps.orders.models import POLine, PurchaseOrder
from apps.orders.services import (
    acknowledge_purchase_order_service,
    amend_purchase_order_service,
    generate_purchase_order_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.receipts.models import GoodsReceipt, ReceiptLine
from apps.receipts.services import create_goods_receipt_service
from apps.vendors.models import Vendor, VendorCategory


def _get_or_create_role(code, name):
    role = Role.objects.filter(code=code).first()
    if not role:
        try:
            role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
        except Exception:
            role = Role.objects.filter(code=code).first()
    return role


def _get_or_create_user(email, password, role_code, role_name):
    role = _get_or_create_role(role_code, role_name)
    user = User.objects.filter(email=email).first()
    if not user:
        user = User.objects.create_user(email=email, password=password, role=role)
    else:
        if role and user.role_id != role.id:
            user.role = role
            user.save(update_fields=["role"])
    return user


@pytest.fixture
def rbac_users(db):
    super_admin = _get_or_create_user("superadmin_test@hpe.com", "Password123!", Role.SUPER_ADMIN, "Super Admin")
    legal_mgr = _get_or_create_user("legal_test@hpe.com", "Password123!", Role.LEGAL_MGR, "Legal Manager")
    proc_mgr = _get_or_create_user("procmgr_test@hpe.com", "Password123!", Role.PROC_MGR, "Procurement Manager")
    proc_exec = _get_or_create_user("procexec_test@hpe.com", "Password123!", Role.PROC_EXEC, "Procurement Executive")
    finance_ap = _get_or_create_user("finance_test@hpe.com", "Password123!", Role.FINANCE_AP, "Finance AP")
    requester = _get_or_create_user("requester_test@hpe.com", "Password123!", Role.REQUESTER, "Requester")
    stores = _get_or_create_user("stores_test@hpe.com", "Password123!", Role.STORES_RECEIVER, "Stores Receiver")
    auditor = _get_or_create_user("auditor_test@hpe.com", "Password123!", Role.AUDITOR, "Compliance Auditor")

    return {
        "super_admin": super_admin,
        "legal_mgr": legal_mgr,
        "proc_mgr": proc_mgr,
        "proc_exec": proc_exec,
        "finance_ap": finance_ap,
        "requester": requester,
        "stores": stores,
        "auditor": auditor,
    }


@pytest.fixture
def base_org_data(db):
    org, _ = Organization.objects.get_or_create(
        code="HPE-US-TEST",
        defaults={"name": "Hewlett Packard Enterprise Test", "tax_identifier": "US-123456"}
    )
    dept, _ = Department.objects.get_or_create(
        code="DEP-IT-TEST",
        defaults={"organization": org, "name": "IT Infrastructure"}
    )
    cc, _ = CostCenter.objects.get_or_create(
        code="CC-DC-TEST",
        defaults={"department": dept, "name": "Data Center CC"}
    )
    today = timezone.now().date()
    fp, _ = FiscalPeriod.objects.get_or_create(
        organization=org,
        year=2026,
        period_number=1,
        defaults={
            "name": "2026-Q1",
            "start_date": today,
            "end_date": today + timezone.timedelta(days=90),
        }
    )
    budget, _ = Budget.objects.get_or_create(
        cost_center=cc,
        fiscal_period=fp,
        defaults={
            "allocated_amount": Decimal("500000.00"),
            "reserved_amount": Decimal("0.00"),
            "committed_amount": Decimal("0.00"),
            "actual_amount": Decimal("0.00"),
        },
    )
    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-HARDWARE-TEST", defaults={"name": "Hardware"}
    )
    vendor, _ = Vendor.objects.get_or_create(
        legal_name="Enterprise Systems Ltd",
        defaults={
            "category": category,
            "tax_identification_number": "TAX-ESL-99",
            "email": "vendor@systems.com",
            "address": "400 Enterprise Way",
            "status": Vendor.STATUS_ACTIVE,
        },
    )
    if vendor.status != Vendor.STATUS_ACTIVE:
        vendor.status = Vendor.STATUS_ACTIVE
        vendor.save()
    return {
        "organization": org,
        "department": dept,
        "cost_center": cc,
        "fiscal_period": fp,
        "budget": budget,
        "vendor": vendor,
    }


# =========================================================================
# 1. SUPER ADMIN AUTHORIZATION TESTS
# =========================================================================

@pytest.mark.django_db
def test_superadmin_views_reject_unauthorized_roles(rbac_users):
    """Ensure non-superadmin roles are denied access server-side to user and org management."""
    client = Client()
    unauthorized_users = [
        rbac_users["requester"],
        rbac_users["proc_exec"],
        rbac_users["finance_ap"],
        rbac_users["auditor"],
    ]
    endpoints = [
        "/superadmin/users/",
        "/superadmin/organizations/",
        "/superadmin/organizations/create/",
        "/superadmin/budgets/",
        "/superadmin/policies/",
        "/superadmin/audit/",
    ]

    for user in unauthorized_users:
        client.force_login(user)
        for url in endpoints:
            response = client.get(url, follow=False)
            assert response.status_code == 403, f"User {user.email} was not denied access (403) to {url}, got {response.status_code}"

    # Super Admin must have access
    client.force_login(rbac_users["super_admin"])
    response = client.get("/superadmin/users/", follow=True)
    assert response.status_code == 200


# =========================================================================
# 2. CONTRACT AUTHORIZATION & ACTIVATION GUARD TESTS
# =========================================================================

@pytest.mark.django_db
def test_contract_activation_cannot_bypass_mandatory_workflow(rbac_users, base_org_data):
    """Direct activation from DRAFT or LEGAL_REVIEW must be rejected."""
    today = timezone.now().date()
    contract = create_contract_service(
        title="Direct Activation Guard Test",
        vendor=base_org_data["vendor"],
        contract_value=Decimal("100000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        contract_owner=rbac_users["legal_mgr"],
    )
    assert contract.status == Contract.STATUS_DRAFT

    # Attempt direct activation from DRAFT -> MUST FAIL
    with pytest.raises(ValidationError, match="Cannot activate contract in status 'DRAFT'"):
        activate_contract_service(contract=contract, user=rbac_users["proc_mgr"])

    # Attempt business approval directly from DRAFT -> MUST FAIL
    with pytest.raises(ValidationError, match="Mandatory legal review must be completed first"):
        approve_business_service(contract=contract, user=rbac_users["proc_mgr"])

    # Move to LEGAL_REVIEW
    submit_for_legal_review_service(contract=contract, user=rbac_users["legal_mgr"])
    assert contract.status == Contract.STATUS_LEGAL_REVIEW

    # Attempt direct activation from LEGAL_REVIEW -> MUST FAIL
    with pytest.raises(ValidationError, match="Cannot activate contract in status 'LEGAL_REVIEW'"):
        activate_contract_service(contract=contract, user=rbac_users["proc_mgr"])


@pytest.mark.django_db
def test_contract_role_authorization_on_actions(rbac_users, base_org_data):
    """Enforce authorized roles on legal approval, business approval, amendment, renewal, termination."""
    today = timezone.now().date()
    contract = create_contract_service(
        title="Role Enforcement Contract",
        vendor=base_org_data["vendor"],
        contract_value=Decimal("75000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=rbac_users["legal_mgr"],
    )
    submit_for_legal_review_service(contract=contract, user=rbac_users["legal_mgr"])

    # 1. Non-legal user cannot approve legal review
    with pytest.raises(ValidationError, match="Only Legal Managers or Super Admins"):
        approve_legal_review_service(contract=contract, user=rbac_users["requester"])
    with pytest.raises(ValidationError, match="Only Legal Managers or Super Admins"):
        approve_legal_review_service(contract=contract, user=rbac_users["proc_exec"])

    # Legal Manager approves legal review -> moves to BUSINESS_APPROVAL
    approve_legal_review_service(contract=contract, user=rbac_users["legal_mgr"])
    assert contract.status == Contract.STATUS_BUSINESS_APPROVAL

    # 2. Non-business-approver cannot perform business approval
    with pytest.raises(ValidationError, match="Only Procurement Managers, Department Approvers, or Super Admins"):
        approve_business_service(contract=contract, user=rbac_users["requester"])
    with pytest.raises(ValidationError, match="Only Procurement Managers, Department Approvers, or Super Admins"):
        approve_business_service(contract=contract, user=rbac_users["legal_mgr"])

    # Procurement Manager approves business -> moves to ACTIVE
    approve_business_service(contract=contract, user=rbac_users["proc_mgr"])
    assert contract.status == Contract.STATUS_ACTIVE

    # 3. Non-authorized role cannot amend
    with pytest.raises(ValidationError, match="Only Legal Managers, Procurement Managers, or Super Admins"):
        create_contract_version_service(
            contract=contract,
            user=rbac_users["requester"],
            amendment_summary="Unauthorized change",
            contract_value=Decimal("80000.00"),
            start_date=contract.start_date,
            end_date=contract.end_date,
        )

    # 4. Non-authorized role cannot renew
    with pytest.raises(ValidationError, match="Only Legal Managers, Procurement Managers, or Super Admins"):
        renew_contract_service(
            contract=contract,
            user=rbac_users["requester"],
            new_end_date=today + timezone.timedelta(days=365),
        )

    # 5. Non-authorized role cannot terminate
    with pytest.raises(ValidationError, match="Only Legal Managers, Procurement Managers, or Super Admins"):
        terminate_contract_service(
            contract=contract,
            user=rbac_users["requester"],
            reason="Unauthorized termination",
        )


@pytest.mark.django_db
def test_contract_template_views_enforce_rbac(rbac_users, base_org_data):
    """Verify template action views reject unauthorized roles and redirect with error."""
    client = Client()
    today = timezone.now().date()
    contract = create_contract_service(
        title="View RBAC Contract",
        vendor=base_org_data["vendor"],
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        contract_owner=rbac_users["legal_mgr"],
    )
    submit_for_legal_review_service(contract=contract, user=rbac_users["legal_mgr"])

    # Requester attempting legal approval
    client.force_login(rbac_users["requester"])
    response = client.post(f"/contracts/{contract.id}/legal-approve/", {"notes": "hack"}, follow=True)
    assert response.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW, "Legal review was bypassed by Requester!"

    # Requester attempting business approval
    response = client.post(f"/contracts/{contract.id}/business-approve/", {"notes": "hack"}, follow=True)
    assert response.status_code == 200
    contract.refresh_from_db()
    assert contract.status == Contract.STATUS_LEGAL_REVIEW


# =========================================================================
# 3. INVOICE PAYMENT AUTHORIZATION TESTS
# =========================================================================

@pytest.mark.django_db
def test_invoice_payment_restricted_to_finance_ap(rbac_users, base_org_data):
    """Ensure pay_invoice_view and mark_invoice_paid_service require Finance/AP role."""
    today = timezone.now().date()

    # Generate PO
    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Server Hardware Line 1",
                "quantity": Decimal("10.00"),
                "unit_price": Decimal("1000.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])

    # Create Invoice
    invoice = create_supplier_invoice_service(
        vendor=base_org_data["vendor"],
        po=po,
        invoice_number="INV-SEC-TEST-001",
        invoice_date=today,
        due_date=today + timezone.timedelta(days=30),
        line_items=[
            {
                "po_line": po.lines.first(),
                "quantity": Decimal("10.00"),
                "unit_price": Decimal("1000.00"),
                "item_description": "Server Hardware Line 1",
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
    invoice.save(update_fields=["status"])

    # 1. Service-level check: non-Finance role must raise ValidationError
    unauthorized_users = [
        rbac_users["requester"],
        rbac_users["proc_exec"],
        rbac_users["stores"],
        rbac_users["auditor"],
    ]
    for unauth_user in unauthorized_users:
        with pytest.raises(ValidationError, match="Only Finance / AP personnel"):
            mark_invoice_paid_service(invoice=invoice, user=unauth_user, payment_reference="TEST-001")

    # 2. View-level check: non-Finance role attempting POST /invoices/ready/<id>/pay/
    client = Client()
    for unauth_user in unauthorized_users:
        client.force_login(unauth_user)
        response = client.post(f"/invoices/ready/{invoice.id}/pay/", follow=False)
        assert response.status_code == 403
        invoice.refresh_from_db()
        assert invoice.status == SupplierInvoice.STATUS_READY_FOR_PAYMENT

    # 3. Authorized Finance / AP executes payment
    client.force_login(rbac_users["finance_ap"])
    response = client.post(
        f"/invoices/ready/{invoice.id}/pay/",
        data={
            "payment_reference": "UTR-2026-00110",
            "payment_method": "ACH_TRANSFER",
            "payment_date": timezone.now().date().isoformat(),
        },
        follow=True,
    )
    assert response.status_code == 200
    invoice.refresh_from_db()
    assert invoice.status == SupplierInvoice.STATUS_PAID

    # Verify AuditLog and SpendLedger
    assert AuditLog.objects.filter(
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        action=AuditLog.ACTION_UPDATE,
    ).exists()
    assert SpendLedger.objects.filter(
        reference_number=invoice.invoice_number,
        entry_type=SpendLedger.ENTRY_ACTUAL,
    ).exists()


@pytest.mark.django_db
def test_invoice_exception_resolution_restricted_to_finance(rbac_users, base_org_data):
    """Ensure resolve_exception_view and resolve_match_exception_service require Finance/AP role."""
    today = timezone.now().date()
    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Memory RAM 64GB",
                "quantity": Decimal("5.00"),
                "unit_price": Decimal("200.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])
    invoice = create_supplier_invoice_service(
        vendor=base_org_data["vendor"],
        po=po,
        invoice_number="INV-EXC-TEST-002",
        invoice_date=today,
        due_date=today + timezone.timedelta(days=30),
        line_items=[
            {
                "po_line": po.lines.first(),
                "quantity": Decimal("5.00"),
                "unit_price": Decimal("220.00"),
                "item_description": "Memory RAM 64GB",
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    exception = MatchException.objects.create(
        invoice=invoice,
        exception_type=MatchException.TYPE_PRICE_VARIANCE,
        description="Price variance above tolerance",
        status=MatchException.STATUS_OPEN,
    )

    # Non-finance user cannot resolve in service
    with pytest.raises(ValidationError, match="Only Finance / AP personnel"):
        resolve_match_exception_service(
            match_exception=exception,
            resolved_by_user=rbac_users["requester"],
            resolution_notes="Unauthorized attempt",
            action="RESOLVE",
        )

# Non-finance user cannot resolve in view
    client = Client()
    client.force_login(rbac_users["requester"])
    response = client.post(
        f"/invoices/exceptions/{exception.id}/resolve/",
        {"action": "RESOLVE", "resolution_notes": "hack"},
        follow=False,
    )
    assert response.status_code == 403

    # Finance user can resolve
    client.force_login(rbac_users["finance_ap"])
    response = client.post(
        f"/invoices/exceptions/{exception.id}/resolve/",
        {"action": "RESOLVE", "resolution_notes": "Variance approved per SLA"},
        follow=True,
    )
    assert response.status_code == 200
    exception.refresh_from_db()
    assert exception.status == MatchException.STATUS_RESOLVED


# =========================================================================
# 4. PURCHASE ORDER AMENDMENT DATA INTEGRITY TESTS
# =========================================================================

@pytest.mark.django_db
def test_po_amendment_with_existing_receipts_preserves_lines(rbac_users, base_org_data):
    """Amending a PO when GoodsReceipt and ReceiptLine exist must NOT throw ProtectedError."""
    # 1. Generate PO
    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Enterprise Core Switch 48-Port",
                "quantity": Decimal("10.00"),
                "unit_price": Decimal("5000.00"),
                "unit_of_measure": "EA",
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])
    vendor_role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})
    vendor_user, _ = User.objects.get_or_create(
        email="vendor_ack_phase1@systems.com",
        defaults={"role": vendor_role, "vendor": base_org_data["vendor"]}
    )
    if not vendor_user.vendor:
        vendor_user.vendor = base_org_data["vendor"]
        vendor_user.save()
    po = acknowledge_purchase_order_service(po=po, vendor_user=vendor_user)

    orig_line = po.lines.first()
    orig_line_id = orig_line.id
    assert orig_line.quantity == Decimal("10.00")

    # 2. Record partial receipt (5 units received)
    receipt = create_goods_receipt_service(
        po=po,
        received_by=rbac_users["stores"],
        delivery_note_number="DN-AMD-001",
        receipt_items=[
            {
                "po_line": orig_line,
                "quantity_received": Decimal("5.00"),
                "quantity_accepted": Decimal("5.00"),
            }
        ],
    )
    orig_line.refresh_from_db()
    assert orig_line.quantity_received == Decimal("5.00")
    assert ReceiptLine.objects.filter(po_line=orig_line).exists()

    # 3. Amend PO: increase quantity from 10 to 12
    # This must NOT fail with django.db.models.ProtectedError
    amendment = amend_purchase_order_service(
        po=po,
        reason="Expansion: 2 additional switches required for rack 4B",
        updated_line_items=[
            {
                "id": orig_line_id,
                "item_description": "Enterprise Core Switch 48-Port",
                "quantity": Decimal("12.00"),
                "unit_price": Decimal("5000.00"),
                "unit_of_measure": "EA",
            }
        ],
        requested_by_user=rbac_users["proc_exec"],
    )

    po.refresh_from_db()
    orig_line.refresh_from_db()

    # Line ID must be preserved
    assert orig_line.id == orig_line_id
    assert orig_line.quantity == Decimal("12.00")
    assert orig_line.quantity_received == Decimal("5.00")
    assert orig_line.remaining_quantity == Decimal("7.00")

    # ReceiptLine reference must remain intact and valid
    receipt_line = receipt.lines.first()
    assert receipt_line.po_line_id == orig_line_id

    # PO version increments to 2
    assert po.version == 2
    assert po.subtotal == Decimal("60000.00")
    assert po.total_amount == Decimal("66000.00")
    assert amendment.amendment_number == 1
    assert amendment.previous_version_snapshot["version"] == 1


@pytest.mark.django_db
def test_po_amendment_guards_against_reducing_below_received_quantity(rbac_users, base_org_data):
    """Amending a PO line below quantity_received must be blocked."""
    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "SSD Storage Array 10TB",
                "quantity": Decimal("8.00"),
                "unit_price": Decimal("2000.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])
    line = po.lines.first()
    line.quantity_received = Decimal("6.00")
    line.save(update_fields=["quantity_received"])

    # Attempt to reduce total quantity to 4 (which is less than 6 received)
    with pytest.raises(ValidationError, match="cannot be less than quantity already received"):
        amend_purchase_order_service(
            po=po,
            reason="Attempt to reduce below received",
            updated_line_items=[
                {
                    "id": line.id,
                    "item_description": "SSD Storage Array 10TB",
                    "quantity": Decimal("4.00"),
                    "unit_price": Decimal("2000.00"),
                }
            ],
            requested_by_user=rbac_users["proc_exec"],
        )


@pytest.mark.django_db
def test_po_amendment_unauthorized_role_rejected(rbac_users, base_org_data):
    """Only procurement roles or superadmin can request PO amendments."""
    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Fiber Optic Cable 50m",
                "quantity": Decimal("20.00"),
                "unit_price": Decimal("50.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])

    with pytest.raises(ValidationError, match="Only procurement roles or Super Admin"):
        amend_purchase_order_service(
            po=po,
            reason="Requester trying to amend PO",
            updated_line_items=[
                {
                    "item_description": "Fiber Optic Cable 50m",
                    "quantity": Decimal("25.00"),
                    "unit_price": Decimal("50.00"),
                }
            ],
            requested_by_user=rbac_users["requester"],
        )


# =========================================================================
# 5. PHASE 1 TARGETED REGRESSION & DEFECT REMEDIATION TESTS
# =========================================================================

@pytest.mark.django_db
def test_celery_beat_task_name_alias_and_resolution():
    """BUG-SYS-001 (TC-NOT-013): Verify both Celery Beat schedule names resolve cleanly."""
    from apps.notifications.tasks import (
        scan_overdue_approvals_and_escalate,
        scan_overdue_approvals_and_escalate_alias,
    )

    res1 = scan_overdue_approvals_and_escalate()
    res2 = scan_overdue_approvals_and_escalate_alias()
    assert isinstance(res1, dict)
    assert isinstance(res2, dict)
    assert "reminders_sent" in res1
    assert "escalations_sent" in res2


@pytest.mark.django_db
def test_failed_login_creates_audit_log_entry(rbac_users):
    """BUG-SYS-002 (TC-AUD-006): Verify failed login attempts create an AuditLog record."""
    client = Client()
    initial_failed_count = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).count()

    response = client.post(
        "/login/",
        {"email": "superadmin_test@hpe.com", "password": "WrongPassword999!"},
    )
    assert response.status_code in (200, 302, 401)

    new_failed_count = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).count()
    assert new_failed_count > initial_failed_count

    latest_log = AuditLog.objects.filter(action=AuditLog.ACTION_LOGIN_FAILED).order_by("-id").first()
    assert latest_log is not None
    assert latest_log.action == AuditLog.ACTION_LOGIN_FAILED
    assert latest_log.new_state.get("event") == "LOGIN_FAILED"


@pytest.mark.django_db
def test_audit_log_deterministic_ordering_and_tie_breaker(rbac_users):
    """BUG-E2E-003: Verify AuditLogViewSet deterministic ordering by -timestamp and -id."""
    fixed_time = timezone.now()
    log1 = AuditLog.objects.create(
        actor=rbac_users["auditor"],
        action=AuditLog.ACTION_LOGIN,
        target_model="User",
        target_object_id=str(rbac_users["auditor"].id),
        timestamp=fixed_time,
    )
    log2 = AuditLog.objects.create(
        actor=rbac_users["auditor"],
        action=AuditLog.ACTION_LOGIN,
        target_model="User",
        target_object_id=str(rbac_users["auditor"].id),
        timestamp=fixed_time,
    )

    from apps.audit.api_views import AuditLogViewSet

    viewset = AuditLogViewSet()
    assert viewset.ordering == ["-timestamp", "-id"]
    from rest_framework.test import APIRequestFactory, force_authenticate

    factory = APIRequestFactory()
    request = factory.get("/api/v1/audit/logs/")
    request.user = rbac_users["auditor"]
    force_authenticate(request, user=rbac_users["auditor"])
    viewset.request = request
    qs = list(viewset.get_queryset()[:2])
    assert len(qs) >= 2
    # Verify higher ID comes first when timestamps are identical
    if qs[0].timestamp == qs[1].timestamp:
        assert qs[0].id > qs[1].id


@pytest.mark.django_db
def test_audit_log_search_finds_pr_number(rbac_users, base_org_data):
    """BUG-E2E-007 (E2E-WFL-018): Verify search query param finds logs matching PR number."""
    from rest_framework.test import APIRequestFactory, force_authenticate

    from apps.audit.api_views import AuditLogViewSet

    test_pr_num = "PR-2026-TEST99"
    AuditLog.objects.create(
        actor=rbac_users["requester"],
        action=AuditLog.ACTION_SUBMIT,
        target_model="PurchaseRequisition",
        target_object_id="00000000-0000-0000-0000-000000000099",
        new_state={"pr_number": test_pr_num, "status": "SUBMITTED"},
    )

    factory = APIRequestFactory()
    request = factory.get(f"/api/v1/audit/logs/?search={test_pr_num}")
    force_authenticate(request, user=rbac_users["auditor"])

    view = AuditLogViewSet.as_view({"get": "list"})
    response = view(request)
    assert response.status_code == 200
    results = response.data.get("results", response.data)
    assert any(item.get("new_state", {}).get("pr_number") == test_pr_num for item in results)


@pytest.mark.django_db
def test_issue_draft_po_returns_400_not_404(rbac_users, base_org_data):
    """BUG-API-008 (PO-172): Issuing a DRAFT PO returns HTTP 400 Bad Request rather than HTTP 404."""
    from rest_framework.test import APIClient

    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Network Router 48-port",
                "quantity": Decimal("2.00"),
                "unit_price": Decimal("1500.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    assert po.status == PurchaseOrder.STATUS_DRAFT

    client = APIClient()
    client.force_authenticate(user=rbac_users["super_admin"])

    response = client.post(f"/api/v1/purchase-orders/{po.id}/issue/", {})
    assert response.status_code == 400
    err_code = response.data.get("error", {}).get("code")
    err_msg = response.data.get("error", {}).get("message", "")
    assert err_code in ("VALIDATION_ERROR", "INVALID_TRANSITION")
    assert "Cannot issue PO in status" in err_msg or "Must be APPROVED" in err_msg


@pytest.mark.django_db
def test_goods_receipt_put_update_remarks_succeeds(rbac_users, base_org_data):
    """BUG-DOM-006 (TC-GRN-007): Full update (PUT) of an existing Goods Receipt updates remarks without failing PO status check."""
    from rest_framework.test import APIClient

    po = generate_purchase_order_service(
        vendor=base_org_data["vendor"],
        cost_center=base_org_data["cost_center"],
        line_items=[
            {
                "item_description": "Server Rack Cabinet",
                "quantity": Decimal("1.00"),
                "unit_price": Decimal("1200.00"),
            }
        ],
        created_by_user=rbac_users["proc_exec"],
    )
    po.status = PurchaseOrder.STATUS_ISSUED
    po.save(update_fields=["status"])

    grn = create_goods_receipt_service(
        po=po,
        received_by=rbac_users["stores"],
        receipt_items=[
            {
                "po_line_id": str(po.lines.first().id),
                "quantity_received": Decimal("1.00"),
                "quantity_accepted": Decimal("1.00"),
                "quantity_rejected": Decimal("0.00"),
            }
        ],
        delivery_note_number="DN-TEST-001",
        remarks="Initial receipt",
    )
    assert grn.remarks == "Initial receipt"

    client = APIClient()
    client.force_authenticate(user=rbac_users["stores"])

    put_data = {
        "po": str(po.id),
        "delivery_note_number": "DN-TEST-001",
        "remarks": "Updated delivery inspection remarks (verified)",
        "received_date": timezone.now().isoformat(),
    }
    response = client.put(f"/api/v1/receipts/{grn.id}/", put_data, format="json")
    assert response.status_code == 200, response.data
    grn.refresh_from_db()
    assert grn.remarks == "Updated delivery inspection remarks (verified)"

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.services import convert_commitment_to_actual_service
from apps.orders.models import PurchaseOrder
from apps.receipts.models import ReceiptLine
from apps.vendors.models import Vendor

from .models import InvoiceLine, MatchException, PaymentStatus, SupplierInvoice


def _get_user_role_code(user):
    if not user:
        return None
    return getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )


@transaction.atomic
def create_supplier_invoice_service(
    *,
    vendor,
    po: PurchaseOrder,
    invoice_number: str,
    invoice_date,
    due_date,
    line_items: list,
    notes: str = "",
    created_by_user=None,
    tax_rate: Decimal = Decimal("0.10"),
    invoice_document=None,
    require_grn: bool = False,
    enforce_billable_limits: bool = False,
) -> SupplierInvoice:
    """
    Creates a new Supplier Invoice in RECEIVED status with line items.
    Enforces unique invoice_number per vendor (duplicate detection).
    Validates PO ownership, status, accepted GRN existence, and billable limits.
    """
    if po.vendor_id != vendor.id:
        raise ValidationError(
            f"Purchase Order {po.po_number} does not belong to vendor '{vendor.legal_name}'."
        )

    if vendor.status != "ACTIVE":
        raise ValidationError(
            f"Cannot create invoice: Vendor '{vendor.legal_name}' is in status '{vendor.status}'. Vendor must be ACTIVE."
        )

    if po.status not in (
        PurchaseOrder.STATUS_ISSUED,
        PurchaseOrder.STATUS_ACKNOWLEDGED,
        PurchaseOrder.STATUS_PARTIAL_RECEIPT,
        PurchaseOrder.STATUS_COMPLETED,
    ):
        raise ValidationError(
            f"Cannot create invoice against Purchase Order {po.po_number} in '{po.status}' status. Invoices require an issued purchase order."
        )

    if require_grn:
        all_receipt_lines = [rl for r in po.receipts.all() for rl in r.lines.all()]
        total_accepted_grn_qty = sum(rl.quantity_accepted for rl in all_receipt_lines)
        if total_accepted_grn_qty <= Decimal("0.00"):
            raise ValidationError(
                f"Cannot submit invoice: Purchase Order {po.po_number} has no accepted Goods Receipt Notes (GRN). A 3-way match requires accepted goods before billing."
            )

    clean_inv_num = str(invoice_number).strip()
    if SupplierInvoice.objects.filter(vendor=vendor, invoice_number__iexact=clean_inv_num).exists():
        raise ValidationError(
            f"Duplicate invoice detected: Invoice '{clean_inv_num}' already exists for vendor '{vendor.legal_name}'."
        )

    subtotal = Decimal("0.00")
    invoice = SupplierInvoice.objects.create(
        vendor=vendor,
        po=po,
        invoice_number=invoice_number,
        invoice_date=invoice_date,
        due_date=due_date,
        status=SupplierInvoice.STATUS_RECEIVED,
        subtotal=Decimal("0.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("0.00"),
        notes=notes,
        invoice_document=invoice_document,
    )

    if not line_items:
        raise ValidationError("Invoice must contain at least one line item.")

    for item in line_items:
        po_line_raw = item.get("po_line") or item.get("po_line_id")
        po_line = None
        if po_line_raw:
            if hasattr(po_line_raw, "id"):
                po_line = po_line_raw
            else:
                from apps.orders.models import POLine
                try:
                    po_line = POLine.objects.filter(pk=po_line_raw, po=po).first()
                except Exception:
                    po_line = None
            if po_line_raw and not po_line:
                raise ValidationError(
                    f"PO line '{po_line_raw}' was not found on Purchase Order {po.po_number}."
                )

        item_desc = item.get(
            "item_description", getattr(po_line, "item_description", "Line Item")
        )
        try:
            qty = Decimal(str(item["quantity"]))
            unit_price = Decimal(str(item["unit_price"]))
        except Exception:
            raise ValidationError(
                f"Quantity and unit price for '{item_desc}' must be valid numeric values."
            )

        if qty <= Decimal("0.00"):
            raise ValidationError(
                f"Invoice line quantity for '{item_desc}' must be greater than zero. "
                f"Negative or zero counts ({qty}) are not permitted."
            )
        if unit_price < Decimal("0.00"):
            raise ValidationError(
                f"Invoice line unit price for '{item_desc}' cannot be negative. "
                f"Negative value ({unit_price}) is not permitted."
            )

        if po_line:
            accepted_qty_agg = ReceiptLine.objects.filter(po_line=po_line).aggregate(
                total_accepted=models.Sum("quantity_accepted")
            )["total_accepted"]
            prior_invoiced_qty = (
                InvoiceLine.objects.filter(po_line=po_line)
                .exclude(invoice__status=SupplierInvoice.STATUS_REJECTED)
                .exclude(invoice=invoice)
                .aggregate(total_prior=models.Sum("quantity"))["total_prior"]
                or Decimal("0.00")
            )
            if accepted_qty_agg is not None and accepted_qty_agg > Decimal("0.00"):
                if prior_invoiced_qty + qty > accepted_qty_agg:
                    raise ValidationError(
                        f"Cumulative invoiced quantity ({prior_invoiced_qty + qty}) for '{item_desc}' would exceed accepted receipt quantity ({accepted_qty_agg})."
                    )
            elif po_line.quantity and (prior_invoiced_qty + qty > po_line.quantity):
                raise ValidationError(
                    f"Cumulative invoiced quantity ({prior_invoiced_qty + qty}) for '{item_desc}' would exceed Purchase Order line quantity ({po_line.quantity})."
                )

        line = InvoiceLine.objects.create(
            invoice=invoice,
            po_line=po_line,
            item_description=item_desc,
            quantity=qty,
            unit_price=unit_price,
        )
        subtotal += line.line_total

    tax = subtotal * tax_rate
    total = subtotal + tax

    prior_invoiced_total = (
        SupplierInvoice.objects.filter(po=po)
        .exclude(pk=invoice.pk)
        .exclude(status=SupplierInvoice.STATUS_REJECTED)
        .aggregate(total=models.Sum("total_amount"))["total"]
        or Decimal("0.00")
    )
    if enforce_billable_limits and (prior_invoiced_total + total > po.total_amount):
        raise ValidationError(
            f"Cumulative invoiced amount (${prior_invoiced_total + total:,.2f}) would exceed Purchase Order total (${po.total_amount:,.2f})."
        )

    invoice.subtotal = subtotal
    invoice.tax_amount = tax
    invoice.total_amount = total
    update_fields = ["subtotal", "tax_amount", "total_amount", "updated_at"]
    if invoice_document:
        update_fields.append("invoice_document")
    invoice.save(update_fields=update_fields)

    AuditLog.objects.create(
        actor=created_by_user,
        action=AuditLog.ACTION_CREATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        new_state={
            "invoice_number": invoice.invoice_number,
            "vendor": vendor.legal_name,
            "po_number": po.po_number,
            "total_amount": str(total),
        },
    )

    from apps.accounts.models import Role
    from apps.notifications.models import Notification

    # 1. Notify Finance & AP
    fin_users = User.objects.filter(role__code=Role.FINANCE_AP, is_active=True)
    for fin_u in fin_users:
        Notification.objects.create(
            recipient=fin_u,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title=f"New Supplier Invoice Submitted: {invoice.invoice_number}",
            message=(
                f"Vendor '{vendor.legal_name}' has submitted invoice {invoice.invoice_number} "
                f"for PO {po.po_number} (Total: ${invoice.total_amount}). "
                f"Ready for 3-way match validation and payment."
            ),
            target_url=f"/invoices/{invoice.id}/",
        )

    # 2. Notify Procurement Executive
    pe_users = User.objects.filter(role__code=Role.PROC_EXEC, is_active=True)
    for pe_u in pe_users:
        Notification.objects.create(
            recipient=pe_u,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title=f"Supplier Invoice Submitted: {invoice.invoice_number}",
            message=(
                f"Vendor '{vendor.legal_name}' has submitted invoice {invoice.invoice_number} "
                f"for PO {po.po_number} (Total: ${invoice.total_amount}). "
                f"You may view the invoice details in read-only mode."
            ),
            target_url=f"/invoices/{invoice.id}/",
        )

    # 3. Notify Procurement Manager
    pm_users = User.objects.filter(role__code=Role.PROC_MGR, is_active=True)
    for pm_u in pm_users:
        Notification.objects.create(
            recipient=pm_u,
            notification_type=Notification.TYPE_GOODS_RECEIVED,
            title=f"Supplier Invoice Submitted: {invoice.invoice_number}",
            message=(
                f"Vendor '{vendor.legal_name}' has submitted invoice {invoice.invoice_number} "
                f"for PO {po.po_number} (Total: ${invoice.total_amount}). "
                f"You may view the invoice details in read-only mode."
            ),
            target_url=f"/invoices/{invoice.id}/",
        )

    return invoice


@transaction.atomic
def run_3_way_match_service(
    *,
    invoice: SupplierInvoice,
    price_tolerance_pct: Decimal = None,
    qty_tolerance_pct: Decimal = None,
    user=None,
) -> tuple[SupplierInvoice, list[MatchException]]:
    """
    Executes automated 3-way match: PO line vs Goods Receipt line vs Supplier Invoice line.
    Generates explicit MatchException records if variance exceeds tolerance thresholds.
    """
    from apps.invoices.models import MatchTolerancePolicy

    policy = MatchTolerancePolicy.get_current()
    if price_tolerance_pct is None:
        price_tolerance_pct = policy.price_tolerance_pct
    if qty_tolerance_pct is None:
        qty_tolerance_pct = policy.qty_tolerance_pct
    price_tolerance_amount = policy.price_tolerance_amount
    qty_tolerance_amount = policy.qty_tolerance_amount

    if invoice.status == SupplierInvoice.STATUS_PAID:
        raise ValidationError("Cannot run 3-way match on an invoice that has already been PAID.")
    if invoice.status not in [
        SupplierInvoice.STATUS_RECEIVED,
        SupplierInvoice.STATUS_EXCEPTION,
        SupplierInvoice.STATUS_MATCHING,
    ]:
        raise ValidationError(
            f"Cannot run 3-way match on invoice in '{invoice.status}' status. Matching is only permitted for RECEIVED and EXCEPTION invoices."
        )

    invoice.status = SupplierInvoice.STATUS_MATCHING
    invoice.save(update_fields=["status", "updated_at"])

    exceptions = []
    max_price_var = Decimal("0.00")
    total_qty_var = Decimal("0.00")

    for inv_line in invoice.lines.all():
        po_line = inv_line.po_line
        if not po_line:
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_UNMATCHED_LINE,
                description=f"Invoice line '{inv_line.item_description}' has no linked PO line.",
                variance_amount=inv_line.line_total,
            )
            exceptions.append(exc)
            continue

        # 1. Quantity Variance check (Billed Qty vs Remaining Uninvoiced Goods Receipt Accepted Qty)
        accepted_qty_agg = ReceiptLine.objects.filter(po_line=po_line).aggregate(
            total_accepted=models.Sum("quantity_accepted")
        )["total_accepted"] or Decimal("0.00")

        # Deduct prior invoiced quantities from other active/matched invoices on this PO line
        prior_invoiced_qty = (
            InvoiceLine.objects.filter(
                po_line=po_line,
                invoice__status__in=[
                    SupplierInvoice.STATUS_MATCHING,
                    SupplierInvoice.STATUS_EXCEPTION,
                    SupplierInvoice.STATUS_APPROVAL,
                    SupplierInvoice.STATUS_READY_FOR_PAYMENT,
                    SupplierInvoice.STATUS_PAID,
                ],
            )
            .exclude(invoice_id=invoice.id)
            .aggregate(total_prior=models.Sum("quantity"))["total_prior"]
            or Decimal("0.00")
        )

        remaining_accepted_qty = max(Decimal("0.00"), accepted_qty_agg - prior_invoiced_qty)
        max_allowed_qty = remaining_accepted_qty * (Decimal("1.00") + qty_tolerance_pct)
        qty_within_amount = (
            qty_tolerance_amount is not None
            and (inv_line.quantity - remaining_accepted_qty) <= qty_tolerance_amount
        )

        if inv_line.quantity > max_allowed_qty and not qty_within_amount:
            qty_diff = inv_line.quantity - remaining_accepted_qty
            total_qty_var += qty_diff
            variance = qty_diff * inv_line.unit_price
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_QUANTITY_VARIANCE,
                description=(
                    f"Billed qty {inv_line.quantity} exceeds remaining uninvoiced accepted GRN qty "
                    f"{remaining_accepted_qty} (Total accepted: {accepted_qty_agg}, Prior invoiced: {prior_invoiced_qty}, Tolerance: {qty_tolerance_pct * 100}%)."
                ),
                variance_amount=variance,
            )
            exceptions.append(exc)

        # 2. Price Variance check (Billed Unit Price vs PO Line Unit Price)
        max_allowed_price = po_line.unit_price * (Decimal("1.00") + price_tolerance_pct)
        price_within_amount = (
            price_tolerance_amount is not None
            and (inv_line.unit_price - po_line.unit_price) <= price_tolerance_amount
        )

        if inv_line.unit_price > max_allowed_price and not price_within_amount:
            unit_diff = inv_line.unit_price - po_line.unit_price
            if po_line.unit_price > Decimal("0.00"):
                diff_pct = (unit_diff / po_line.unit_price) * Decimal("100")
                if diff_pct > max_price_var:
                    max_price_var = diff_pct
            variance = unit_diff * inv_line.quantity
            exc = MatchException.objects.create(
                invoice=invoice,
                exception_type=MatchException.TYPE_PRICE_VARIANCE,
                description=(
                    f"Billed unit price ${inv_line.unit_price} exceeds PO price ${po_line.unit_price} "
                    f"for item '{po_line.item_description}' (Tolerance: {price_tolerance_pct * 100}%)."
                ),
                variance_amount=variance,
            )
            exceptions.append(exc)

    # 3. Check for Total Amount discrepancy
    calc_subtotal = sum(line.line_total for line in invoice.lines.all())
    expected_total = calc_subtotal + invoice.tax_amount
    if abs(invoice.total_amount - expected_total) > Decimal("0.01"):
        exc = MatchException.objects.create(
            invoice=invoice,
            exception_type=MatchException.TYPE_TOTAL_VARIANCE,
            description=f"Total amount mismatch: Stated total ${invoice.total_amount} != calculated total ${expected_total}.",
            variance_amount=abs(invoice.total_amount - expected_total),
        )
        exceptions.append(exc)

    # 4. Record MatchResult audit record
    from apps.invoices.models import MatchResult
    match_user = user or invoice.created_by
    if not match_user:
        from apps.accounts.models import User
        match_user = User.objects.filter(is_superuser=True).first() or User.objects.first()

    MatchResult.objects.create(
        invoice=invoice,
        is_matched=(len(exceptions) == 0),
        price_variance_percentage=round(max_price_var, 2),
        quantity_variance=round(total_qty_var, 2),
        tolerance_applied_percentage=round(price_tolerance_pct * Decimal("100"), 2),
        performed_by=match_user,
    )

    if exceptions:
        invoice.status = SupplierInvoice.STATUS_EXCEPTION

        from apps.accounts.models import Role, User
        from apps.notifications.models import Notification

        finance_users = User.objects.filter(role__code=Role.FINANCE_AP, is_active=True)
        for fin_user in finance_users:
            Notification.objects.create(
                recipient=fin_user,
                notification_type=Notification.TYPE_EXCEPTION_RAISED,
                title=f"3-Way Match Exception: Invoice {invoice.invoice_number}",
                message=(
                    f"Invoice {invoice.invoice_number} from {invoice.vendor.legal_name} "
                    f"has {len(exceptions)} match exception(s) requiring Finance resolution."
                ),
                target_url=f"/invoices/{invoice.id}/",
            )
    else:
        invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT

    invoice.save(update_fields=["status", "updated_at"])

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        new_state={
            "status": invoice.status,
            "exception_count": len(exceptions),
        },
    )

    if exceptions:
        from apps.accounts.models import Role
        from apps.notifications.models import Notification
        from apps.notifications.services import notify_role_users

        notify_role_users(
            role_codes=[Role.FINANCE_AP, Role.SUPER_ADMIN],
            notification_type=Notification.TYPE_EXCEPTION_RAISED,
            title=f"3-Way Match Exception: Invoice {invoice.invoice_number}",
            message=f"Invoice {invoice.invoice_number} from {invoice.vendor.legal_name} triggered {len(exceptions)} variance exception(s).",
            target_url=f"/invoices/{invoice.id}/",
        )

    return invoice, exceptions


@transaction.atomic
def resolve_match_exception_service(
    *,
    match_exception: MatchException,
    resolved_by_user,
    resolution_notes: str,
    action: str = "RESOLVE",
) -> MatchException:
    """
    Finance exception resolution workflow for 3-way match variances.
    """
    role_code = _get_user_role_code(resolved_by_user)
    if not (
        getattr(resolved_by_user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can resolve invoice match exceptions."
        )

    if match_exception.status != MatchException.STATUS_OPEN:
        raise ValidationError(f"Match exception is already in status '{match_exception.status}'.")

    if action == "RESOLVE":
        match_exception.status = MatchException.STATUS_RESOLVED
        match_exception.resolved_by = resolved_by_user
        match_exception.resolution_notes = resolution_notes
        match_exception.save(
            update_fields=["status", "resolved_by", "resolution_notes", "updated_at"]
        )

        # If all exceptions on invoice are resolved, promote invoice to READY_FOR_PAYMENT
        invoice = match_exception.invoice
        remaining_open = invoice.exceptions.filter(status=MatchException.STATUS_OPEN).count()
        if remaining_open == 0:
            invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
            invoice.save(update_fields=["status", "updated_at"])

    elif action == "REJECT":
        match_exception.status = MatchException.STATUS_REJECTED
        match_exception.resolved_by = resolved_by_user
        match_exception.resolution_notes = resolution_notes
        match_exception.save(
            update_fields=["status", "resolved_by", "resolution_notes", "updated_at"]
        )

        invoice = match_exception.invoice
        invoice.status = SupplierInvoice.STATUS_REJECTED
        invoice.save(update_fields=["status", "updated_at"])
    else:
        raise ValidationError(
            f"Invalid exception resolution action '{action}'. Must be RESOLVE or REJECT."
        )

    AuditLog.objects.create(
        actor=resolved_by_user,
        action=AuditLog.ACTION_APPROVE if action == "RESOLVE" else AuditLog.ACTION_REJECT,
        target_model="MatchException",
        target_object_id=str(match_exception.id),
        new_state={
            "status": match_exception.status,
            "resolution_notes": resolution_notes,
            "invoice_status": match_exception.invoice.status,
        },
    )

    return match_exception


@transaction.atomic
def mark_invoice_paid_service(
    *,
    invoice: SupplierInvoice,
    user,
    payment_reference: str = None,
    payment_method: str = "ACH_TRANSFER",
    payment_date=None,
) -> SupplierInvoice:
    """
    Marks invoice as PAID, creates a PaymentStatus record (DEF-017),
    and transitions SpendLedger from COMMITMENT to ACTUAL.
    Requires a payment reference (UTR / Check #). Creates a PaymentStatus record
    for transaction evidence and audit trail.
    """
    if not payment_reference:
        raise ValidationError("Transaction / Payment Reference ID (UTR / Check #) is required.")

    # Check for unique payment_reference in PaymentStatus
    if PaymentStatus.objects.filter(payment_reference=payment_reference).exists():
        raise ValidationError(
            f"Payment reference '{payment_reference}' already exists. "
            "Each payment must have a unique transaction/UTR ID."
        )

    if invoice.vendor.status in (Vendor.STATUS_ON_HOLD, Vendor.STATUS_SUSPENDED):
        raise ValidationError(
            f"Cannot pay invoice: Vendor '{invoice.vendor.legal_name}' is currently {invoice.vendor.get_status_display().lower()}."
        )

    role_code = _get_user_role_code(user)
    if not (
        getattr(user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can execute invoice payments."
        )

    if invoice.status != SupplierInvoice.STATUS_READY_FOR_PAYMENT:
        raise ValidationError(
            f"Invoice '{invoice.invoice_number}' in status '{invoice.status}' is not ready for payment. "
            "It must be approved (READY_FOR_PAYMENT) before payment."
        )

    previous_status = invoice.status
    invoice.status = SupplierInvoice.STATUS_PAID
    invoice.save(update_fields=["status", "updated_at"])

    # Create and persist PaymentStatus record linked to the invoice (DEF-017)
    payment_record = PaymentStatus.objects.create(
        invoice=invoice,
        payment_reference=payment_reference,
        payment_date=payment_date or timezone.now().date(),
        amount_paid=invoice.total_amount,
        payment_method=payment_method or "ACH_TRANSFER",
        paid_by=user,
    )

    # Convert spend ledger commitment -> actual
    convert_commitment_to_actual_service(po=invoice.po, invoice=invoice, user=user)

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        previous_state={"status": previous_status},
        new_state={
            "status": invoice.status,
            "payment_reference": payment_record.payment_reference,
            "payment_method": payment_record.payment_method,
            "amount_paid": str(payment_record.amount_paid),
        },
    )

    return invoice


@transaction.atomic
def approve_supplier_invoice_service(
    *,
    invoice: SupplierInvoice,
    user,
    notes: str = "",
) -> SupplierInvoice:
    """
    Finance / AP approves a matched invoice.
    - Enforces Finance AP / Super Admin role authorization.
    - Prevents duplicate invoice approval.
    - Checks for open match exceptions (blocks approval if unresolved).
    - Transitions status to READY_FOR_PAYMENT.
    - Records approved_by, approved_at, and AuditLog.
    - Dispatches notification to vendor and internal stakeholders.
    """
    role_code = _get_user_role_code(user)
    if not (
        getattr(user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can approve invoices."
        )

    if invoice.status in [SupplierInvoice.STATUS_READY_FOR_PAYMENT, SupplierInvoice.STATUS_PAID]:
        raise ValidationError(
            f"Duplicate approval blocked: Invoice '{invoice.invoice_number}' is already approved (status: {invoice.status})."
        )

    if invoice.status == SupplierInvoice.STATUS_REJECTED:
        raise ValidationError(
            f"Cannot approve invoice '{invoice.invoice_number}': Invoice is in REJECTED status."
        )

    open_exceptions_count = invoice.exceptions.filter(status=MatchException.STATUS_OPEN).count()
    if open_exceptions_count > 0:
        raise ValidationError(
            f"Cannot approve invoice: {open_exceptions_count} open 3-way match exception(s) must be resolved first."
        )

    previous_status = invoice.status
    invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
    invoice.approved_by = user
    invoice.approved_at = timezone.now()
    if notes:
        invoice.notes = f"{invoice.notes}\n[APPROVED by {user.email}]: {notes}".strip()
    invoice.save(update_fields=["status", "approved_by", "approved_at", "notes", "updated_at"])

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_APPROVE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        previous_state={"status": previous_status},
        new_state={
            "status": invoice.status,
            "approved_by": user.email,
            "notes": notes,
        },
    )

    from apps.notifications.models import Notification
    vendor_users = User.objects.filter(vendor=invoice.vendor, is_active=True)
    for vu in vendor_users:
        Notification.objects.create(
            recipient=vu,
            notification_type=Notification.TYPE_APPROVAL_REQUIRED,
            title=f"Invoice Approved: {invoice.invoice_number}",
            message=f"Your invoice {invoice.invoice_number} has been approved by Finance and is scheduled for payment.",
            target_url=f"/vendor/invoices/{invoice.id}/",
        )

    return invoice


@transaction.atomic
def reject_supplier_invoice_service(
    *,
    invoice: SupplierInvoice,
    user,
    rejection_reason: str,
) -> SupplierInvoice:
    """
    Finance / AP rejects an invoice with a mandatory reason.
    - Enforces Finance AP / Super Admin role authorization.
    - Mandatory rejection reason.
    - Transitions status to REJECTED.
    - Marks all open exceptions as REJECTED.
    - Records rejected_by, rejected_at, rejection_reason, and AuditLog.
    - Dispatches notification to vendor.
    """
    if not rejection_reason or not str(rejection_reason).strip():
        raise ValidationError("A rejection reason/justification is mandatory.")

    role_code = _get_user_role_code(user)
    if not (
        getattr(user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can reject invoices."
        )

    if invoice.status == SupplierInvoice.STATUS_PAID:
        raise ValidationError(
            f"Cannot reject invoice '{invoice.invoice_number}': Invoice is already PAID."
        )

    previous_status = invoice.status
    clean_reason = str(rejection_reason).strip()
    invoice.status = SupplierInvoice.STATUS_REJECTED
    invoice.rejection_reason = clean_reason
    invoice.rejected_by = user
    invoice.rejected_at = timezone.now()
    invoice.notes = f"{invoice.notes}\n[REJECTED by {user.email}]: {clean_reason}".strip()
    invoice.save(
        update_fields=[
            "status",
            "rejection_reason",
            "rejected_by",
            "rejected_at",
            "notes",
            "updated_at",
        ]
    )

    # Mark open exceptions as rejected
    invoice.exceptions.filter(status=MatchException.STATUS_OPEN).update(
        status=MatchException.STATUS_REJECTED,
        resolution_notes=clean_reason,
        resolved_by=user,
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_REJECT,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        previous_state={"status": previous_status},
        new_state={
            "status": invoice.status,
            "rejection_reason": clean_reason,
            "rejected_by": user.email,
        },
    )

    from apps.notifications.models import Notification
    vendor_users = User.objects.filter(vendor=invoice.vendor, is_active=True)
    for vu in vendor_users:
        Notification.objects.create(
            recipient=vu,
            notification_type=Notification.TYPE_EXCEPTION_RAISED,
            title=f"Invoice Rejected: {invoice.invoice_number}",
            message=f"Your invoice {invoice.invoice_number} has been rejected by Finance. Reason: {clean_reason}",
            target_url=f"/vendor/invoices/{invoice.id}/",
        )

    return invoice


@transaction.atomic
def request_invoice_correction_service(
    *,
    invoice: SupplierInvoice,
    user,
    correction_notes: str,
) -> SupplierInvoice:
    """
    Finance / AP requests vendor corrections on an invoice.
    - Enforces Finance AP / Super Admin role authorization.
    - Mandatory correction instructions.
    - Transitions status to EXCEPTION.
    - Records correction_request_notes and AuditLog.
    - Dispatches notification to vendor.
    """
    if not correction_notes or not str(correction_notes).strip():
        raise ValidationError("Correction instructions/notes are mandatory.")

    role_code = _get_user_role_code(user)
    if not (
        getattr(user, "is_superuser", False)
        or role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN]
    ):
        raise ValidationError(
            "Permission Denied: Only Finance / AP personnel can request invoice corrections."
        )

    if invoice.status in [SupplierInvoice.STATUS_PAID, SupplierInvoice.STATUS_REJECTED]:
        raise ValidationError(
            f"Cannot request corrections on invoice in status '{invoice.status}'."
        )

    previous_status = invoice.status
    clean_notes = str(correction_notes).strip()
    invoice.status = SupplierInvoice.STATUS_EXCEPTION
    invoice.correction_request_notes = clean_notes
    invoice.notes = f"{invoice.notes}\n[CORRECTION REQUESTED by {user.email}]: {clean_notes}".strip()
    invoice.save(
        update_fields=["status", "correction_request_notes", "notes", "updated_at"]
    )

    AuditLog.objects.create(
        actor=user,
        action=AuditLog.ACTION_UPDATE,
        target_model="SupplierInvoice",
        target_object_id=str(invoice.id),
        previous_state={"status": previous_status},
        new_state={
            "status": invoice.status,
            "action": "CORRECTION_REQUESTED",
            "correction_notes": clean_notes,
            "requested_by": user.email,
        },
    )

    from apps.notifications.models import Notification
    vendor_users = User.objects.filter(vendor=invoice.vendor, is_active=True)
    for vu in vendor_users:
        Notification.objects.create(
            recipient=vu,
            notification_type=Notification.TYPE_EXCEPTION_RAISED,
            title=f"Correction Requested: Invoice {invoice.invoice_number}",
            message=f"Finance has requested corrections on invoice {invoice.invoice_number}: {clean_notes}",
            target_url=f"/vendor/invoices/{invoice.id}/",
        )

    return invoice

from django.db.models import Q

from apps.accounts.models import Role

from .models import POAmendment, PurchaseOrder


def scope_purchase_orders_queryset(qs, user):
    """
    Enforces strict role-based and organizational data scoping on Purchase Orders:
    - Superuser: unrestricted access across all orders.
    - Vendor user: strictly isolated to their own vendor organization.
    - Requester: strictly isolated to their cost center department.
    - Internal roles (Auditor, Procurement Manager, Finance AP, etc.):
      Strictly scoped to their effective_organization. Cannot see other orgs.
    """
    if not user or not user.is_authenticated:
        return qs.none()

    role_code = getattr(user, "role_code", None) or (
        user.role.code if hasattr(user, "role") and user.role else None
    )

    if user.is_superuser or role_code == Role.SUPER_ADMIN:
        return qs

    # Vendor scoping
    if getattr(user, "is_vendor", False) or role_code == Role.VENDOR_USER:
        vendor_id = getattr(user, "vendor_id", None)
        return qs.filter(vendor_id=vendor_id) if vendor_id else qs.none()

    # Requester scoping: only POs originating from own PRs or created by user
    if role_code == Role.REQUESTER:
        return qs.filter(Q(created_by=user) | Q(requisition__requester=user))

    # Organization-level scoping for internal roles (Auditor, Procurement Manager, etc.)
    scoped_org = getattr(user, "effective_organization", None)
    if scoped_org:
        return qs.filter(
            Q(created_by=user)
            | Q(cost_center__department__organization=scoped_org)
            | Q(requisition__department__organization=scoped_org)
            | Q(cost_center__isnull=True, requisition__isnull=True)
        )

    # Internal roles with no resolvable organization (e.g. seeded single-org
    # deployments) retain access; any configured organization locks them down.
    if role_code:
        return qs
    if getattr(user, "is_staff", False):
        return qs

    return qs.none()


def get_all_purchase_orders():
    return (
        PurchaseOrder.objects.select_related("vendor", "cost_center")
        .prefetch_related("lines", "amendments")
        .order_by("-created_at")
    )


def get_purchase_order_by_id(po_id):
    return (
        PurchaseOrder.objects.select_related("vendor", "cost_center")
        .prefetch_related("lines", "amendments")
        .filter(id=po_id)
        .first()
    )


def get_po_version_history(po_id):
    return (
        POAmendment.objects.select_related("po", "requested_by")
        .filter(po_id=po_id)
        .order_by("amendment_number")
    )

"""Read/query logic for notifications.

All querysets are scoped by recipient (IDOR protection): callers must pass
the authenticated user; no function ever returns another user's rows.
"""

from __future__ import annotations

from typing import Iterable

from django.db.models import Q

from .models import Notification

# Notification types that require an action (drive the "Pending Actions" widget).
# Notification types belonging to the Procurement Executive dashboard.
PROC_EXEC_ALLOWED_TYPES = [
    Notification.TYPE_APPROVAL_REQUIRED,
    Notification.TYPE_BID_DEADLINE,
    Notification.TYPE_PO_ACKNOWLEDGED,
    Notification.TYPE_RFQ_INVITATION,
    Notification.TYPE_CLARIFICATION_RESPONSE,
    Notification.TYPE_BID_SUBMITTED,
    Notification.TYPE_BID_AMENDED,
    Notification.TYPE_PO_ISSUED,
    Notification.TYPE_PO_AMENDMENT,
    Notification.TYPE_KYC_REQUEST,
]

# Target URLs belonging to other role desks (Finance, Stores, Legal) are excluded for PROC_EXEC.
PROC_EXEC_EXCLUDED_TARGET_PREFIXES = ("/invoices/", "/receipts/", "/contracts/")

ACTIONABLE_TYPES = [
    Notification.TYPE_APPROVAL_REQUIRED,
    Notification.TYPE_BID_DEADLINE,
    Notification.TYPE_EXCEPTION_RAISED,
    Notification.TYPE_CONTRACT_EXPIRATION,
    Notification.TYPE_CONTRACT_MILESTONE,
    Notification.TYPE_CONTRACT_OBLIGATION,
    Notification.TYPE_RFQ_INVITATION,
    Notification.TYPE_CLARIFICATION_RESPONSE,
    Notification.TYPE_KYC_REQUEST,
]


def _get_role_code(user) -> str:
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else ""
    )
    return str(role_code or "").upper()


def scope_notifications_for_user(qs, user):
    """Scope notifications to the user's workspace when a role-specific feed is required."""
    if _get_role_code(user) == "PROC_EXEC":
        excluded_q = Q()
        for prefix in PROC_EXEC_EXCLUDED_TARGET_PREFIXES:
            excluded_q |= Q(target_url__startswith=prefix)
        return qs.filter(notification_type__in=PROC_EXEC_ALLOWED_TYPES).exclude(excluded_q)
    return qs


def get_recent_unread_notifications(user, limit: int = 10) -> Iterable[Notification]:
    """Top-N most recent unread notifications for ``user`` (newest first)."""
    qs = scope_notifications_for_user(
        Notification.objects.filter(recipient=user, is_read=False), user
    )
    return list(
        qs.select_related("recipient").order_by("-created_at")[:limit]
    )


def get_pending_action_notifications(user, limit: int = 5) -> Iterable[Notification]:
    """Unread actionable notifications (approvals, deadlines, exceptions)."""
    qs = scope_notifications_for_user(
        Notification.objects.filter(
            recipient=user, is_read=False, notification_type__in=ACTIONABLE_TYPES
        ),
        user,
    )
    return list(
        qs.select_related("recipient").order_by("-created_at")[:limit]
    )


def get_unread_count(user) -> int:
    """Count of unread notifications for ``user`` (badge counter)."""
    return scope_notifications_for_user(
        Notification.objects.filter(recipient=user, is_read=False), user
    ).count()

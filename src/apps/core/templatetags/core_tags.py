import json
from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from apps.core.validators import sanitize_plain_display_text

register = template.Library()

_STATUS_LABELS = {
    "DRAFT": "Draft",
    "SUBMITTED": "Submitted",
    "UNDER_REVIEW": "KYC Review",
    "KYC_REVIEW": "KYC Review",
    "APPROVED": "Approved",
    "ACTIVE": "Active",
    "ON_HOLD": "On Hold",
    "SUSPENDED": "Suspended",
    "BLACKLISTED": "Blacklisted",
    "REJECTED": "Rejected",
    "PUBLISHED": "Published",
    "BID_WINDOW": "Bid Window Open",
    "TECHNICAL_REVIEW": "Technical Review",
    "COMMERCIAL_REVIEW": "Commercial Review",
    "AWARD_APPROVAL": "Award Approval",
    "AWARDED": "Awarded",
    "CANCELLED": "Cancelled",
    "PENDING": "Pending Approval",
    "AMENDED": "Amended",
    "WITHDRAWN": "Withdrawn",
    "CLOSED": "Closed",
    "ISSUED": "Issued",
    "ACKNOWLEDGED": "Acknowledged",
    "PARTIAL_RECEIPT": "Partial Receipt",
    "COMPLETED": "Completed",
    "RECEIVED": "Received",
    "VALIDATION": "Validation",
    "MATCH_EXCEPTION": "Match Exception",
    "READY_FOR_PAYMENT": "Ready for Payment",
    "PAID": "Paid",
    "VERIFIED": "Verified",
    "CHANGES_REQUIRED": "Changes Required",
}

_STATUS_BADGE_CLASSES = {
    "ACTIVE": "bg-success-subtle text-success border border-success-subtle",
    "APPROVED": "bg-success-subtle text-success border border-success-subtle",
    "AWARDED": "bg-success-subtle text-success border border-success-subtle",
    "PAID": "bg-success-subtle text-success border border-success-subtle",
    "COMPLETED": "bg-success-subtle text-success border border-success-subtle",
    "VERIFIED": "bg-success-subtle text-success border border-success-subtle",
    "SUSPENDED": "bg-danger-subtle text-danger border border-danger-subtle",
    "BLACKLISTED": "bg-danger-subtle text-danger border border-danger-subtle",
    "REJECTED": "bg-danger-subtle text-danger border border-danger-subtle",
    "CANCELLED": "bg-danger-subtle text-danger border border-danger-subtle",
    "ON_HOLD": "bg-warning-subtle text-warning-emphasis border border-warning-subtle",
    "BID_WINDOW": "bg-primary-subtle text-primary border border-primary-subtle",
    "PUBLISHED": "bg-info-subtle text-info border border-info-subtle",
    "TECHNICAL_REVIEW": "bg-info-subtle text-info border border-info-subtle",
    "COMMERCIAL_REVIEW": "bg-info-subtle text-info border border-info-subtle",
    "AWARD_APPROVAL": "bg-warning-subtle text-warning-emphasis border border-warning-subtle",
    "UNDER_REVIEW": "bg-warning-subtle text-warning-emphasis border border-warning-subtle",
    "KYC_REVIEW": "bg-warning-subtle text-warning-emphasis border border-warning-subtle",
    "SUBMITTED": "bg-light text-dark border",
    "DRAFT": "bg-light text-muted border",
}


@register.filter(name="safe_display")
def safe_display(value):
    """Strip unsafe script/HTML tags from legacy untrusted text while keeping autoescaping enabled."""
    return sanitize_plain_display_text(value)


@register.filter(name="format_audit_action")
def format_audit_action(log_or_action, new_state=None):
    """
    Renders human-friendly action text instead of exposing generic 'UPDATE'.
    Handles AuditLog objects, action strings, and credential events.
    """
    if log_or_action is None:
        return ""

    if hasattr(log_or_action, "new_state"):
        new_state = log_or_action.new_state
        action = getattr(log_or_action, "action", "")
        action_display = getattr(log_or_action, "get_action_display", lambda: action)()
    else:
        action = str(log_or_action)
        action_display = action.replace("_", " ").title()

    if isinstance(new_state, dict):
        event = new_state.get("event")
        if event == "credentials_created":
            return "Credentials Created"
        elif event == "credentials_reset":
            return "Credentials Reset"
        elif event == "credentials_resent":
            return "Credentials Resent"
        elif event == "credential_delivery_failed":
            return "Delivery Failed"
        elif event == "status_change":
            return "Status Change"
        elif "status" in new_state and action in ("UPDATE", "SUBMIT", "APPROVE", "REJECT"):
            status_val = new_state.get("status")
            status_lbl = _STATUS_LABELS.get(status_val, str(status_val).replace("_", " ").title())
            return f"Status: {status_lbl}"

    return action_display


@register.filter(name="format_audit_state")
def format_audit_state(state, log=None):
    """
    Parses backend state objects and renders them in clean, human-readable format.
    Never exposes raw Python dictionary strings or raw JSON code blocks.
    """
    if state is None or state == "" or state == {}:
        return mark_safe('<span class="text-muted">—</span>')

    if isinstance(state, str):
        state_str = state.strip()
        if (state_str.startswith("{") and state_str.endswith("}")) or (state_str.startswith("[") and state_str.endswith("]")):
            try:
                state = json.loads(state_str)
            except Exception:
                pass

    if not isinstance(state, dict):
        return mark_safe(escape(str(state)))

    # Credentials event formatting
    event = state.get("event")
    login_id = state.get("login_id") or state.get("sent_to") or state.get("email")
    if event == "credentials_created":
        if login_id:
            return mark_safe(f'<span>Credentials created for <strong class="text-dark">{escape(login_id)}</strong></span>')
        return mark_safe('<span>Credentials created</span>')
    elif event == "credentials_reset":
        if login_id:
            return mark_safe(f'<span>Credentials reset for <strong class="text-dark">{escape(login_id)}</strong></span>')
        return mark_safe('<span>Credentials reset</span>')
    elif event == "credentials_resent":
        if login_id:
            return mark_safe(f'<span>Credentials resent to <strong class="text-dark">{escape(login_id)}</strong></span>')
        return mark_safe('<span>Credentials resent</span>')
    elif event == "credential_delivery_failed":
        err = state.get("error") or "Permanent error"
        return mark_safe(f'<span class="text-danger fw-semibold">Delivery failed for <strong class="text-dark">{escape(login_id or "")}</strong>:</span> <span class="text-muted">{escape(err)}</span>')

    # Status formatting
    if "status" in state and len(state) <= 4:
        status_val = state.get("status")
        status_lbl = _STATUS_LABELS.get(status_val, str(status_val).replace("_", " ").title())
        badge_cls = _STATUS_BADGE_CLASSES.get(status_val, "bg-light text-dark border")

        parts = [f'<span class="badge {badge_cls}">{escape(status_lbl)}</span>']

        note = state.get("reason") or state.get("notes") or state.get("comments") or state.get("justification")
        if note:
            parts.append(f'<div class="small text-muted mt-1"><i class="fa-solid fa-quote-left text-muted me-1 small"></i>{escape(note)}</div>')

        extras = []
        for k, v in state.items():
            if k in ("status", "reason", "notes", "comments", "justification", "event", "vendor_number", "event_number", "attempts", "user_active"):
                continue
            clean_k = k.replace("_", " ").title()
            extras.append(f'<span class="text-muted">{escape(clean_k)}:</span> {escape(str(v))}')
        if extras:
            parts.append(f'<div class="small text-muted mt-1">{" · ".join(extras)}</div>')

        return mark_safe("".join(parts))

    # General dictionary formatting (clean key-value pairs)
    rows = []
    for k, v in state.items():
        if k in ("attempts",):
            continue
        clean_k = k.replace("_", " ").title()
        if str(v) in _STATUS_LABELS:
            badge_cls = _STATUS_BADGE_CLASSES.get(str(v), "bg-light text-dark border")
            v_html = f'<span class="badge {badge_cls} py-0 px-2" style="font-size: 0.75rem;">{escape(_STATUS_LABELS[str(v)])}</span>'
        else:
            v_html = f'<span class="fw-semibold text-dark">{escape(str(v))}</span>'
        rows.append(f'<div><span class="text-muted">{escape(clean_k)}:</span> {v_html}</div>')

    if not rows:
        return mark_safe('<span class="text-muted">—</span>')

    return mark_safe(f'<div class="d-flex flex-column gap-1 small text-wrap">{"".join(rows)}</div>')


@register.filter(name="format_audit_updated_state")
def format_audit_updated_state(log):
    """
    Formats the Updated state column of an AuditLog entry.
    If a state transition occurred, renders a clear change summary:
    e.g., 'Status changed from Published to Bid Window'
    Handles credentials events, status changes, and general object states cleanly.
    """
    if log is None:
        return mark_safe('<span class="text-muted">—</span>')

    prev_state = getattr(log, "previous_state", None)
    new_state = getattr(log, "new_state", None)

    if isinstance(prev_state, str) and prev_state.strip().startswith("{"):
        try:
            prev_state = json.loads(prev_state)
        except Exception:
            pass
    if isinstance(new_state, str) and new_state.strip().startswith("{"):
        try:
            new_state = json.loads(new_state)
        except Exception:
            pass

    if new_state is None or new_state == "" or new_state == {}:
        return mark_safe('<span class="text-muted">—</span>')

    if isinstance(new_state, dict):
        event = new_state.get("event")
        login_id = new_state.get("login_id") or new_state.get("sent_to") or new_state.get("email")

        # Specific credentials events requested by user
        if event == "credentials_created":
            if login_id:
                return mark_safe(f'<span>Credentials created for <strong class="text-dark">{escape(login_id)}</strong></span>')
            return mark_safe('<span>Credentials created</span>')
        elif event == "credentials_reset":
            if login_id:
                return mark_safe(f'<span>Credentials reset for <strong class="text-dark">{escape(login_id)}</strong></span>')
            return mark_safe('<span>Credentials reset</span>')
        elif event == "credentials_resent":
            if login_id:
                return mark_safe(f'<span>Credentials resent to <strong class="text-dark">{escape(login_id)}</strong></span>')
            return mark_safe('<span>Credentials resent</span>')
        elif event == "credential_delivery_failed":
            err = new_state.get("error") or "Permanent error"
            return mark_safe(f'<span class="text-danger fw-semibold">Delivery failed for <strong class="text-dark">{escape(login_id or "")}</strong>:</span> <span class="text-muted">{escape(err)}</span>')

        # Status transition check
        if isinstance(prev_state, dict) and "status" in prev_state and "status" in new_state:
            prev_status = prev_state.get("status")
            curr_status = new_state.get("status")
            if prev_status and curr_status and prev_status != curr_status:
                prev_lbl = _STATUS_LABELS.get(prev_status, str(prev_status).replace("_", " ").title())
                curr_lbl = _STATUS_LABELS.get(curr_status, str(curr_status).replace("_", " ").title())
                curr_badge_cls = _STATUS_BADGE_CLASSES.get(curr_status, "bg-success-subtle text-success border border-success-subtle")

                html = (
                    f'<span>Status changed from <span class="text-muted fw-semibold">{escape(prev_lbl)}</span> '
                    f'to <span class="badge {curr_badge_cls}">{escape(curr_lbl)}</span></span>'
                )
                reason = new_state.get("reason") or new_state.get("notes") or new_state.get("comments")
                if reason:
                    html += f'<div class="small text-muted mt-1"><i class="fa-solid fa-quote-left text-muted me-1 small"></i>{escape(reason)}</div>'
                return mark_safe(html)

    return format_audit_state(new_state, log)


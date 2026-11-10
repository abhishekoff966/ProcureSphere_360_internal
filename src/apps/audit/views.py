from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import render

from apps.accounts.models import Role
from apps.audit.selectors import (
    get_approval_history_audit,
    get_audit_logs,
    get_audit_metrics,
    get_auditor_dashboard_data,
    get_contract_changes_audit,
    get_invoice_exceptions_audit,
    get_po_changes_audit,
    get_security_events_audit,
    get_sourcing_activity_audit,
    get_vendor_compliance_audit,
)
from apps.audit.services import get_transaction_lifecycle_service
from apps.organization.selectors import resolve_user_scope_organization


def _enforce_auditor_access(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )
    if not (
        user.is_superuser
        or role_code
        in [Role.AUDITOR, Role.SUPER_ADMIN, "AUDITOR", "SUPER_ADMIN"]
    ):
        raise PermissionDenied("Access restricted to Compliance Auditor role.")


def _get_auditor_org(request):
    user = request.user
    if user.is_superuser:
        return None
    return getattr(user, "effective_organization", None) or getattr(user, "organization", None)


@login_required
def auditor_dashboard_view(request):
    """
    Main Executive Compliance Auditor & Traceability Hub Dashboard.
    Supplies core executive KPIs, activity trend charts, action distribution,
    open anomalies, and recent audit activity feed.
    """
    _enforce_auditor_access(request)
    try:
        period_days = int(request.GET.get("period_days", 30))
        if period_days <= 0:
            period_days = 30
    except (ValueError, TypeError):
        period_days = 30
    org = _get_auditor_org(request)
    dashboard_data = get_auditor_dashboard_data(period_days=period_days, user=request.user, organization=org)
    vendor_compliance = get_vendor_compliance_audit(limit=10, organization=org)
    approval_history = get_approval_history_audit(limit=10, organization=org)
    po_changes = get_po_changes_audit(limit=10, organization=org)
    invoice_exceptions = get_invoice_exceptions_audit(limit=10, organization=org)
    contract_changes = get_contract_changes_audit(limit=10, organization=org)
    security_events = get_security_events_audit(limit=10, organization=org)
    sourcing_activity = get_sourcing_activity_audit(limit=10, organization=org)

    context = {
        "metrics": {
            "total_audit_logs": dashboard_data["metrics"]["total_logs_all_time"],
            "total_approvals": dashboard_data["metrics"]["critical_events"]["approvals"],
            "total_rejections": dashboard_data["metrics"]["critical_events"]["rejections"],
            "total_logins": dashboard_data["metrics"]["critical_events"]["logins"],
            "total_exports": dashboard_data["metrics"]["critical_events"]["exports"],
            "open_exceptions": dashboard_data["domain_counts"]["open_exceptions_count"],
            "suspended_vendors": dashboard_data["domain_counts"]["suspended_vendors_count"],
            "overdue_obligations": dashboard_data["domain_counts"]["overdue_obligations_count"],
            "compliance_rate": vendor_compliance["summary"]["compliance_rate"],
        },
        "audit_data": dashboard_data,
        "recent_logs": dashboard_data["recent_logs"],
        "recent_approvals": dashboard_data["recent_approvals"],
        "open_exceptions": dashboard_data["open_exceptions"],
        "suspended_vendors": dashboard_data["suspended_vendors"],
        "overdue_obligations": dashboard_data["overdue_obligations"],
        "active_contract_alerts": dashboard_data["active_contract_alerts"],
        "vendor_compliance": vendor_compliance,
        "approval_history": approval_history,
        "po_changes": po_changes,
        "invoice_exceptions": invoice_exceptions,
        "contract_changes": contract_changes,
        "security_events": security_events,
        "sourcing_activity": sourcing_activity,
        "period_days": period_days,
    }
    return render(request, "pages/dashboards/auditor_dashboard.html", context)


@login_required
def audit_log_view(request):
    """
    Dedicated Append-Only Immutable Audit Log Register Page.
    """
    _enforce_auditor_access(request)
    action_filter = request.GET.get("action", "").strip()
    search_query = request.GET.get("q", "").strip() or request.GET.get("search", "").strip()
    model_filter = (
        request.GET.get("model", "").strip() or request.GET.get("target_model", "").strip()
    )
    try:
        page = max(1, int(request.GET.get("page", 1)))
    except (ValueError, TypeError):
        page = 1

    try:
        limit = int(request.GET.get("limit", 50))
        if limit <= 0:
            limit = 50
        elif limit > 500:
            limit = 500
    except (ValueError, TypeError):
        limit = 50

    offset = (page - 1) * limit
    org = _get_auditor_org(request)

    data = get_audit_logs(
        limit=limit,
        offset=offset,
        action=action_filter if action_filter else None,
        search_term=search_query if search_query else None,
        target_model=model_filter if model_filter else None,
        organization=org,
    )
    total_count = data.get("total_count", 0)
    total_pages = max(1, (total_count + limit - 1) // limit)

    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if getattr(request.user, "role", None) else None
    )
    base_layout = "layouts/requester_base.html"
    if getattr(request.user, "is_superuser", False) or role_code in ["SUPER_ADMIN", "ADMIN"]:
        base_layout = "layouts/superadmin_base.html"
    elif role_code == "DEPT_APPROVER":
        base_layout = "layouts/approver_base.html"
    elif role_code == "FINANCE_AP":
        base_layout = "layouts/finance_base.html"
    elif role_code in ["PROC_MGR", "PROC_EXEC"]:
        base_layout = "layouts/procurement_base.html"
    elif role_code in ["AUDITOR"]:
        base_layout = "layouts/auditor_base.html"
    elif role_code in ["STORES_RECEIVER", "STORES"]:
        base_layout = "layouts/stores_base.html"

    context = {
        "logs": data.get("logs", []),
        "audit_logs": data.get("logs", []),
        "action": action_filter,
        "action_filter": action_filter,
        "search": search_query,
        "search_query": search_query,
        "model": model_filter,
        "model_filter": model_filter,
        "page": page,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
        "base_layout": base_layout,
    }
    return render(request, "audit/logs.html", context)


@login_required
def audit_approvals_view(request):
    """
    Approval Governance History View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "").strip()
    target_model = (
        request.GET.get("model", "").strip() or request.GET.get("target_model", "").strip()
    )
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_approval_history_audit(
        target_model=target_model if target_model else None,
        action=action_filter if action_filter else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "approvals": data["results"],
        "search_query": search_query,
        "action_filter": action_filter,
        "target_model": target_model,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/approvals.html", context)


@login_required
def audit_po_changes_view(request):
    """
    Purchase Order Changes & Amendments View.
    Supports filtering and sorting by Version, Total Amount, Status, and Compliance Check.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    version_filter = request.GET.get("version", "").strip()
    sort_by = request.GET.get("sort", "").strip()
    compliance_filter = request.GET.get("compliance", "").strip() or request.GET.get("compliance_check", "").strip()
    min_amount = request.GET.get("min_amount", "").strip()
    max_amount = request.GET.get("max_amount", "").strip()

    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_po_changes_audit(
        search=search_query if search_query else None,
        status=status_filter if status_filter else None,
        version=version_filter if version_filter else None,
        compliance_check=compliance_filter if compliance_filter else None,
        min_amount=min_amount if min_amount else None,
        max_amount=max_amount if max_amount else None,
        sort_by=sort_by if sort_by else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "pos": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "version_filter": version_filter,
        "compliance_filter": compliance_filter,
        "min_amount": min_amount,
        "max_amount": max_amount,
        "sort_by": sort_by,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/po_changes.html", context)


@login_required
def audit_vendors_view(request):
    """
    Vendor Compliance & KYC Audit View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    kyc_status = request.GET.get("kyc_status", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_vendor_compliance_audit(
        status=status_filter if status_filter else None,
        kyc_status=kyc_status if kyc_status else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "vendors": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "kyc_status": kyc_status,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/vendors.html", context)


@login_required
def audit_invoice_exceptions_view(request):
    """
    3-Way Match Invoice Exceptions View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    exception_type = request.GET.get("exception_type", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_invoice_exceptions_audit(
        status=status_filter if status_filter else None,
        exception_type=exception_type if exception_type else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "exceptions": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "exception_type": exception_type,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/invoice_exceptions.html", context)


@login_required
def audit_contract_changes_view(request):
    """
    Contract Compliance & Milestone Governance View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_contract_changes_audit(
        search=search_query if search_query else None,
        status=status_filter if status_filter else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "contracts": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/contract_changes.html", context)


@login_required
def audit_security_events_view(request):
    """
    Security & Authentication Events View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    action_filter = request.GET.get("action", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_security_events_audit(
        search=search_query if search_query else None,
        action=action_filter if action_filter else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "security_events": data["results"],
        "search_query": search_query,
        "action_filter": action_filter,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/security_events.html", context)


@login_required
def audit_sourcing_activity_view(request):
    """
    Sourcing & Competitive Bids Activity View.
    """
    _enforce_auditor_access(request)
    search_query = request.GET.get("search", "").strip() or request.GET.get("q", "").strip()
    status_filter = request.GET.get("status", "").strip()
    event_type = request.GET.get("event_type", "").strip()
    page = int(request.GET.get("page", 1))
    limit = int(request.GET.get("limit", 50))
    offset = (page - 1) * limit

    org = _get_auditor_org(request)
    data = get_sourcing_activity_audit(
        status=status_filter if status_filter else None,
        event_type=event_type if event_type else None,
        search=search_query if search_query else None,
        limit=limit,
        offset=offset,
        organization=org,
    )
    total_count = data["total_count"]
    total_pages = max(1, (total_count + limit - 1) // limit)

    context = {
        "summary": data["summary"],
        "events": data["results"],
        "search_query": search_query,
        "status_filter": status_filter,
        "event_type": event_type,
        "current_page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }
    return render(request, "audit/sourcing_activity.html", context)


@login_required
def audit_lifecycle_view(request):
    """
    Source-to-Pay Transaction Lifecycle Traceability Explorer.
    """
    _enforce_auditor_access(request)
    identifier = request.GET.get("identifier", "").strip() or request.GET.get("q", "").strip()
    entity_type = request.GET.get("entity_type", "AUTO").strip()

    org = _get_auditor_org(request)
    lifecycle = None
    if identifier:
        lifecycle = get_transaction_lifecycle_service(
            entity_type=entity_type,
            entity_identifier=identifier,
            organization=org,
        )

    context = {
        "identifier": identifier,
        "entity_type": entity_type,
        "lifecycle": lifecycle,
    }
    return render(request, "audit/lifecycle_viewer.html", context)


@login_required
def audit_metrics_api_view(request):
    """
    JSON API for auditor statistical metrics.
    """
    _enforce_auditor_access(request)
    try:
        period_days = int(request.GET.get("period_days", 30))
    except (ValueError, TypeError):
        period_days = 30
    org = _get_auditor_org(request)
    data = get_audit_metrics(period_days=period_days, organization=org)
    return JsonResponse(data)

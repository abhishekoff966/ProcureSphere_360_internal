"""django-axes helpers."""


def get_axes_username(request, credentials):
    """
    Username used for login throttling. This project signs in by e-mail, but the portal, API and
    admin pass it as ``username`` or ``email``; axes' default only looks at the user model's
    USERNAME_FIELD, so failures were recorded against ``None`` (an IP-wide lock that a correct
    password for a named account could bypass). Normalised so case variants share one counter.
    """
    creds = credentials or {}
    value = creds.get("username") or creds.get("email")
    if not value and request is not None:
        data = getattr(request, "data", None)
        if isinstance(data, dict):
            value = data.get("email") or data.get("username")
        if not value:
            post = getattr(request, "POST", None)
            if post:
                value = post.get("email") or post.get("username")
        if not value:
            import json
            try:
                body = getattr(request, "body", None)
                if body:
                    payload = json.loads(body.decode("utf-8", errors="ignore"))
                    if isinstance(payload, dict):
                        value = payload.get("email") or payload.get("username")
            except Exception:
                pass
    if value is not None:
        value = str(value).strip().lower()
    else:
        value = ""
    return value or None


def get_axes_client_ip(request):
    """
    Resolve client IP consistently behind proxies (Render, Nginx, ALB).
    """
    if not request:
        return None
    try:
        from apps.core.middleware import get_client_ip
        ip = get_client_ip()
        if ip:
            return ip
    except Exception:
        pass
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def get_axes_lockout_response(request, original_response=None, credentials=None):
    """
    Unified lockout response for django-axes:
    - Returns JSON 429 with ACCOUNT_LOCKED for API and AJAX requests.
    - Returns rendered 429 HTML lockout page for standard browser navigation.
    """
    from django.conf import settings
    from django.http import JsonResponse
    from django.shortcuts import render

    try:
        from apps.accounts.models import User
        from apps.audit.models import AuditLog
        from apps.audit.services import create_audit_log_service

        username = (credentials.get("username") if isinstance(credentials, dict) else "") or ""
        if not username and request is not None:
            username = getattr(request, "_axes_failed_username", "") or ""
        matched_user = User.objects.filter(email__iexact=str(username).strip()).first() if username else None
        create_audit_log_service(
            actor=matched_user,
            action=AuditLog.ACTION_LOGIN_FAILED,
            target_model="User",
            target_object_id=str(matched_user.id if matched_user else username),
            new_state={
                "event": "LOGIN_FAILED",
                "email": str(username).strip().lower(),
                "reason": "Account locked",
            },
            request=request,
        )
    except Exception:
        pass

    is_api = (
        (getattr(request, "path_info", "") and request.path_info.startswith("/api/"))
        or (getattr(request, "path", "") and request.path.startswith("/api/"))
        or getattr(request, "content_type", "") == "application/json"
        or request.META.get("HTTP_ACCEPT", "").find("application/json") != -1
        or request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest"
    )

    if is_api:
        return JsonResponse(
            {
                "error": {
                    "code": "ACCOUNT_LOCKED",
                    "message": "Too many failed login attempts. Account is locked temporarily.",
                    "details": {"retry_after_seconds": 180},
                }
            },
            status=429,
        )

    template = getattr(settings, "AXES_LOCKOUT_TEMPLATE", "pages/lockout.html")
    context = {
        "status": 429,
        "username": (credentials.get("username") if isinstance(credentials, dict) else "") or "",
    }
    return render(request, template, context, status=429)



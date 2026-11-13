import time

from django.conf import settings
from django.contrib.auth import logout
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect

EXEMPT_PREFIXES = (
    "/static/",
    "/logout/",
    "/password-change/",
    "/accounts/change-password/",
    "/health",
    "/readyz",
    "/api/",
)

SESSION_LAST_ACTIVITY_KEY = "_last_activity_ts"
DEFAULT_IDLE_TIMEOUT_SECONDS = 300
TIMEOUT_EXEMPT_PATHS = {
    "/login",
    "/logout",
    "/admin/login",
    "/admin/logout",
    "/accounts/login",
    "/accounts/logout",
}
TIMEOUT_EXEMPT_PREFIXES = (
    "/static/",
    "/media/",
    "/favicon.ico",
    "/health",
    "/readyz",
    "/login/",
    "/logout/",
    "/admin/login/",
    "/admin/logout/",
    "/accounts/login/",
    "/accounts/logout/",
    "/api/v1/auth/login/",
    "/api/v1/auth/token/",
    "/api/v1/auth/logout/",
)
PASSIVE_POLL_PATHS = (
    "/notifications/htmx/feed/",
    "/notifications/htmx/pending-actions/",
)


class ForcePasswordChangeMiddleware:
    """Sends users holding a temporary password to the change-password page until they set their own."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and getattr(user, "must_change_password", False)
            and not request.path.startswith(EXEMPT_PREFIXES)
        ):
            return redirect("/accounts/change-password/")
        return self.get_response(request)


# Provide both alias names for compatibility across branches and settings
PasswordChangeRequiredMiddleware = ForcePasswordChangeMiddleware


class SessionIdleTimeoutMiddleware:
    """
    Enforces a strict inactivity auto-signout across all authenticated dashboards
    and Django Admin sessions after SESSION_IDLE_TIMEOUT_SECONDS (default: 300s / 5m).
    Passive background poll requests do not renew the idle activity timestamp.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return self.get_response(request)

        path = request.path or "/"
        if path in TIMEOUT_EXEMPT_PATHS or path.startswith(TIMEOUT_EXEMPT_PREFIXES):
            return self.get_response(request)

        timeout_seconds = int(getattr(settings, "SESSION_IDLE_TIMEOUT_SECONDS", DEFAULT_IDLE_TIMEOUT_SECONDS))
        if timeout_seconds <= 0:
            return self.get_response(request)

        now = time.time()
        is_passive_poll = (
            request.headers.get("X-PS-Background-Poll") == "true"
            or path in PASSIVE_POLL_PATHS
        )
        raw_last_activity = request.session.get(SESSION_LAST_ACTIVITY_KEY)

        if raw_last_activity is None:
            request.session[SESSION_LAST_ACTIVITY_KEY] = now
            return self.get_response(request)

        try:
            last_activity = float(raw_last_activity)
        except (TypeError, ValueError):
            request.session[SESSION_LAST_ACTIVITY_KEY] = now
            return self.get_response(request)

        idle_seconds = now - last_activity
        if idle_seconds >= timeout_seconds:
            self._record_timeout_audit_log(request, user, int(idle_seconds))
            logout(request)

            if request.headers.get("HX-Request") == "true":
                response = HttpResponse(status=200)
                response["HX-Redirect"] = "/login/?session_expired=1"
                response.set_cookie("ps360_just_logged_out", "1", max_age=60, path="/", samesite="Lax")
                return response

            if (
                path.startswith("/api/")
                or request.headers.get("X-Requested-With") == "XMLHttpRequest"
                or "application/json" in request.headers.get("Accept", "")
            ):
                return JsonResponse(
                    {
                        "error": {
                            "code": "SESSION_EXPIRED",
                            "message": "Your session expired after 5 minutes of inactivity. Please sign in again.",
                        },
                        "redirect_url": "/login/?session_expired=1",
                    },
                    status=401,
                )

            if path == "/":
                return self.get_response(request)

            response = redirect("/login/?session_expired=1")
            response.set_cookie("ps360_just_logged_out", "1", max_age=60, path="/", samesite="Lax")
            return response

        if not is_passive_poll:
            request.session[SESSION_LAST_ACTIVITY_KEY] = now

        return self.get_response(request)

    @staticmethod
    def _record_timeout_audit_log(request, user, idle_seconds: int) -> None:
        try:
            from apps.audit.models import AuditLog
            from apps.audit.services import create_audit_log_service

            create_audit_log_service(
                actor=user,
                action=AuditLog.ACTION_LOGOUT,
                target_model="User",
                target_object_id=str(user.id),
                previous_state={"email": user.email},
                new_state={
                    "reason": "inactivity_timeout",
                    "idle_seconds": idle_seconds,
                },
                ip_address=request.META.get("REMOTE_ADDR"),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
        except Exception:
            pass

import threading
import uuid

# Thread-local storage for request correlation context
_request_context = threading.local()


def get_current_request_id():
    return getattr(_request_context, "request_id", None)


def get_current_user():
    return getattr(_request_context, "user", None)


def get_client_ip():
    return getattr(_request_context, "client_ip", None)


class RequestIDMiddleware:
    """
    Middleware that assigns a unique UUID correlation ID (X-Request-ID) to every HTTP request.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.request_id = request_id
        _request_context.request_id = request_id

        # Determine client IP address
        x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
        if x_forwarded_for:
            ip = x_forwarded_for.split(",")[0].strip()
        else:
            ip = request.META.get("REMOTE_ADDR")
        _request_context.client_ip = ip

        try:
            response = self.get_response(request)
        finally:
            # Never leak request context into later work on the same thread.
            _request_context.request_id = None
            _request_context.client_ip = None
        response["X-Request-ID"] = request_id
        response["Server"] = "ProcureSphere360"
        return response


class AuditContextMiddleware:
    """
    Middleware that sets the current user in thread-local storage for audit tracking.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hasattr(request, "user") and request.user.is_authenticated:
            _request_context.user = request.user
        else:
            _request_context.user = None

        try:
            return self.get_response(request)
        finally:
            _request_context.user = None


class SecurityHeadersMiddleware:
    """
    Middleware that enforces baseline security headers per PRD and AGENTS.md §12:
    Content-Security-Policy, Strict-Transport-Security, Permissions-Policy.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if "Content-Security-Policy" not in response:
            response["Content-Security-Policy"] = (
                "default-src 'self'; "
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdn.jsdelivr.net https://unpkg.com https://cdnjs.cloudflare.com; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com https://cdn.jsdelivr.net data:; "
                "img-src 'self' data: https:; "
                "media-src 'self'; "
                "connect-src 'self' https://cdn.jsdelivr.net https://unpkg.com;"
            )
        if request.is_secure() and "Strict-Transport-Security" not in response:
            response["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if "Permissions-Policy" not in response:
            response["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        return response


class VendorPortalIsolationMiddleware:
    """
    Strict boundary enforcement: blocks external Vendor Portal users from accessing
    internal staff portals, requisitions, contracts, orders, invoices, and reports (DEF-003).
    """

    BLOCKED_INTERNAL_PREFIXES = (
        "/requisitions",
        "/contracts",
        "/orders",
        "/purchase-orders",
        "/invoices",
        "/receipts",
        "/budgets",
        "/reports",
        "/approvals",
        "/superadmin",
        "/manager",
        "/audit",
        "/admin",
        "/api/v1/requisitions",
                "/api/v1/budgets",
                        "/api/v1/reports",
        "/api/v1/audit",
        "/api/v1/manager",
    )

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated and not user.is_superuser:
            from apps.accounts.models import Role

            role_code = getattr(user, "role_code", None) or (
                user.role.code if hasattr(user, "role") and user.role else None
            )
            is_vendor = getattr(user, "is_vendor", False) or (role_code == Role.VENDOR_USER)

            if is_vendor:
                path = request.path.rstrip("/")
                for prefix in self.BLOCKED_INTERNAL_PREFIXES:
                    if path == prefix or path.startswith(prefix + "/"):
                        if request.path.startswith("/api/"):
                            from django.http import JsonResponse

                            return JsonResponse(
                                {
                                    "error": {
                                        "code": "FORBIDDEN",
                                        "message": "External vendors are strictly prohibited from accessing internal buyer endpoints.",
                                    }
                                },
                                status=403,
                            )
                        from django.core.exceptions import PermissionDenied

                        raise PermissionDenied("Vendors are restricted to the Vendor Portal (/vendor/).")

        return self.get_response(request)


class HtmxAuthInterceptorMiddleware:
    """
    Prevents HTMX requests from swapping full HTML login pages into partial or modal containers.
    If an HTMX request receives a redirect to login or an unauthenticated response,
    this converts it into a 200 response with the 'HX-Redirect' header so HTMX
    navigates the entire browser window cleanly.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_htmx = (
            request.headers.get("HX-Request") == "true"
            or request.META.get("HTTP_HX_REQUEST") == "true"
        )
        if is_htmx:
            location = response.get("Location", "")
            if response.status_code in (301, 302, 303, 307) and (
                "/login" in location or "accounts/login" in location
            ):
                from django.http import HttpResponse

                clean_loc = location if location else "/login/?session_expired=1"
                htmx_resp = HttpResponse(status=200)
                htmx_resp["HX-Redirect"] = clean_loc
                return htmx_resp

            if response.status_code in (401, 403):
                response["HX-Redirect"] = "/login/?session_expired=1"

        return response


import decimal

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import DataError
from django.db.models.deletion import ProtectedError
from django.http import Http404
from rest_framework import status
from rest_framework.exceptions import (
    AuthenticationFailed,
    NotAuthenticated,
    NotFound,
    PermissionDenied,
    Throttled,
)
from rest_framework.exceptions import (
    ValidationError as DRFValidationError,
)
from rest_framework.response import Response
from rest_framework.views import exception_handler


def _get_active_request_id(context=None):
    if context and "request" in context:
        req = context["request"]
        rid = getattr(req, "request_id", None) or getattr(req, "correlation_id", None)
        if rid:
            return str(rid)
        header_rid = req.headers.get("X-Request-ID") if hasattr(req, "headers") else req.META.get("HTTP_X_REQUEST_ID")
        if header_rid:
            return str(header_rid)
    try:
        from apps.core.middleware import get_current_request_id
        rid = get_current_request_id()
        if rid:
            return str(rid)
    except Exception:
        pass
    import uuid
    return str(uuid.uuid4())


def custom_exception_handler(exc, context):
    """
    Custom DRF exception handler returning standardized JSON error structure:
    {
        "error": {
            "code": "VALIDATION_ERROR",
            "message": "Human readable error summary",
            "details": { ... field specific errors ... },
            "request_id": "...",
            "fields": { ... }
        }
    }
    """
    req_id = _get_active_request_id(context)

    if isinstance(exc, (decimal.InvalidOperation, OverflowError, DataError, ValueError)):
        return Response(
            {
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Invalid input format or numeric value out of bounds.",
                    "details": str(exc),
                    "request_id": req_id,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, ProtectedError):
        protected_list = [str(obj) for obj in exc.protected_objects]
        return Response(
            {
                "error": {
                    "code": "PROTECTED_ERROR",
                    "message": "Cannot delete record because it is referenced by other active entities.",
                    "details": {"protected_objects": protected_list},
                    "request_id": req_id,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    from django.db import IntegrityError
    if isinstance(exc, IntegrityError):
        err_str = str(exc).lower()
        if "po_number" in err_str:
            return Response(
                {
                    "error": {
                        "code": "DUPLICATE_RESOURCE",
                        "message": "A Purchase Order with this PO number already exists.",
                        "details": {"po_number": ["Purchase Order with this PO number already exists."]},
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        if "grn_number" in err_str or "delivery_note_number" in err_str:
            return Response(
                {
                    "error": {
                        "code": "DUPLICATE_RESOURCE",
                        "message": "A Goods Receipt with this GRN or Delivery Note already exists.",
                        "details": {"grn_number": ["Goods receipt with this identifier already exists."]},
                    }
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(
            {
                "error": {
                    "code": "INTEGRITY_ERROR",
                    "message": "A record with this unique identifier or constrained attribute already exists.",
                    "details": str(exc),
                    "request_id": req_id,
                }
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    if isinstance(exc, DjangoValidationError):
        # Domain/service-layer validation errors -> structured 400 instead of a 500.
        detail = exc.message_dict if hasattr(exc, "error_dict") else exc.messages
        exc = DRFValidationError(detail=detail)

    response = exception_handler(exc, context)

    if response is not None:
        status_code = response.status_code

        request = context.get("request") if context else None
        is_unauthenticated = isinstance(exc, NotAuthenticated) or (
            request and (not getattr(request, "user", None) or not request.user.is_authenticated)
        )

        # Derive semantic error code based on exception and status code
        if isinstance(exc, (Http404, NotFound)) or status_code == status.HTTP_404_NOT_FOUND:
            error_code = "NOT_FOUND"
        elif is_unauthenticated and status_code in (
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
        ):
            error_code = "NOT_AUTHENTICATED"
        elif isinstance(exc, PermissionDenied) or status_code == status.HTTP_403_FORBIDDEN:
            error_code = "PERMISSION_DENIED"
        elif (
            isinstance(exc, (NotAuthenticated, AuthenticationFailed))
            or status_code == status.HTTP_401_UNAUTHORIZED
        ):
            error_code = "NOT_AUTHENTICATED"
        elif isinstance(exc, Throttled) or status_code == status.HTTP_429_TOO_MANY_REQUESTS:
            error_code = "THROTTLED"
        elif isinstance(exc, DRFValidationError) or status_code == status.HTTP_400_BAD_REQUEST:
            error_code = "VALIDATION_ERROR"
        else:
            error_code = getattr(exc, "default_code", "API_ERROR")
            if isinstance(error_code, str):
                error_code = error_code.upper()

        def _clean_item(item):
            if isinstance(item, dict):
                return {k: _clean_item(v) for k, v in item.items()}
            if isinstance(item, (list, tuple)):
                return [_clean_item(v) for v in item]
            return str(item)

        if hasattr(exc, "detail") and isinstance(exc.detail, dict):
            details = _clean_item(exc.detail)
            message = "Validation failed for one or more fields."
        elif hasattr(exc, "detail") and isinstance(exc.detail, list):
            details = _clean_item(exc.detail)
            message = str(details[0]) if details else "Validation failed."
        elif hasattr(exc, "detail"):
            details = _clean_item(exc.detail)
            message = str(exc.detail)
        else:
            raw_data = response.data if isinstance(response.data, (dict, list)) else {}
            details = _clean_item(raw_data)
            message = str(exc)

        if "ErrorDetail(" in message or "[" in message:
            import re
            cleaned = re.sub(r"\[?ErrorDetail\(string=[\'\"](.*?)[\'\"](?:\s*,\s*code=[\'\"]\w+[\'\"])?\)[,\s\]]*", r"\1", message).strip("[]'\" ")
            if cleaned:
                message = cleaned
            import re
            m = re.search(r"""ErrorDetail\(string=['"](.*?)['"]""", message)
            if m:
                message = m.group(1)

        error_payload = {
            "code": error_code,
            "message": message,
            "details": details,
            "request_id": req_id,
        }
        if isinstance(details, dict):
            error_payload["fields"] = details
        custom_response_data = {"error": error_payload}
        return Response(custom_response_data, status=status_code)

    import logging

    from django.conf import settings

    logger = logging.getLogger(__name__)
    logger.exception("Unhandled server error: %s", exc)

    # Sanitize exception details in production to prevent CWE-209 information disclosure
    is_test_or_debug = getattr(settings, "DEBUG", False) or getattr(settings, "TESTING", False) or "test" in getattr(settings, "SETTINGS_MODULE", "")
    details = (
        str(exc)
        if is_test_or_debug
        else "An unexpected error occurred. Please contact the system administrator."
    )

    return Response(
        {
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected internal server error occurred.",
                "details": details,
                "request_id": req_id,
            }
        },
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )

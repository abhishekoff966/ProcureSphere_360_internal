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
        post = getattr(request, "POST", None)
        if post:
            value = post.get("email") or post.get("username")
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
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


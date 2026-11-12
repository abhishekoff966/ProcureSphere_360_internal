import time

import pytest
from django.conf import settings

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.vendors.models import Vendor, VendorCategory


def _create_user_for_role(role_code, idx=1):
    role, _ = Role.objects.get_or_create(code=role_code, defaults={"name": role_code})
    kwargs = {
        "email": f"timeout_{role_code.lower()}_{idx}@hpe.com",
        "password": "Password123!",
        "role": role,
    }
    if role_code == Role.SUPER_ADMIN:
        kwargs["is_superuser"] = True
        kwargs["is_staff"] = True
    user = User.objects.create_user(**kwargs)
    if role_code == Role.VENDOR_USER:
        cat, _ = VendorCategory.objects.get_or_create(name="Timeout Cat", code="CAT-TMO")
        vendor = Vendor.objects.create(
            legal_name=f"Timeout Vendor {idx}",
            tax_identification_number=f"TIN-TMO-{idx}",
            category=cat,
            email=user.email,
            status=Vendor.STATUS_ACTIVE,
        )
        user.vendor = vendor
        user.save()
    return user


def test_inactivity_timeout_settings_configured_to_five_minutes():
    assert settings.SESSION_IDLE_TIMEOUT_SECONDS == 300
    assert settings.SESSION_COOKIE_AGE == 300
    assert settings.SESSION_SAVE_EVERY_REQUEST is True
    mw = list(settings.MIDDLEWARE)
    auth_idx = mw.index("django.contrib.auth.middleware.AuthenticationMiddleware")
    idle_idx = mw.index("apps.accounts.middleware.SessionIdleTimeoutMiddleware")
    assert idle_idx == auth_idx + 1


@pytest.mark.django_db
def test_all_dashboards_and_django_admin_include_idle_timeout_script(client):
    roles = [
        Role.SUPER_ADMIN,
        Role.REQUESTER,
        Role.DEPT_APPROVER,
        Role.PROC_MGR,
        Role.PROC_EXEC,
        Role.STORES_RECEIVER,
        Role.FINANCE_AP,
        Role.LEGAL_MGR,
        Role.AUDITOR,
        Role.VENDOR_USER,
    ]
    for idx, role_code in enumerate(roles, start=1):
        user = _create_user_for_role(role_code, idx=idx)
        client.force_login(user)
        resp = client.get("/dashboard/", follow=True)
        assert resp.status_code == 200
        html = resp.content.decode()
        assert "/static/js/session_timeout.js" in html, f"Missing timeout script for {role_code}"
        assert "window.PS360_SESSION_CONFIG" in html, f"Missing timeout config for {role_code}"
        assert "idleTimeoutSeconds: 300" in html

    # Django Platform Admin (/admin/) also loads inactivity timeout script
    sa_user = User.objects.filter(is_superuser=True).first()
    client.force_login(sa_user)
    admin_resp = client.get("/admin/")
    assert admin_resp.status_code == 200
    admin_html = admin_resp.content.decode()
    assert "/static/js/session_timeout.js" in admin_html
    assert "window.PS360_SESSION_CONFIG" in admin_html
    assert "idleTimeoutSeconds: 300" in admin_html

    # Login page for anonymous visitor must NOT initialize dashboard timeout script
    client.logout()
    login_resp = client.get("/login/")
    assert login_resp.status_code == 200
    assert "PS360_SESSION_CONFIG" not in login_resp.content.decode()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("role_code", "dashboard_url"),
    [
        (Role.STORES_RECEIVER, "/stores/"),
        (Role.SUPER_ADMIN, "/superadmin/"),
        (Role.PROC_MGR, "/manager/"),
        (Role.PROC_EXEC, "/procurement/"),
        (Role.LEGAL_MGR, "/legal/"),
        (Role.FINANCE_AP, "/finance/"),
        (Role.DEPT_APPROVER, "/approver/"),
        (Role.AUDITOR, "/audit/"),
        (Role.REQUESTER, "/requester/"),
        (Role.VENDOR_USER, "/vendor/"),
        (Role.SUPER_ADMIN, "/admin/"),
    ],
)
def test_expired_idle_session_auto_signs_out_every_role_dashboard(client, role_code, dashboard_url):
    user = _create_user_for_role(role_code, idx=99)
    client.force_login(user)

    # Active session (< 300s idle) works normally and bumps _last_activity_ts
    session = client.session
    session["_last_activity_ts"] = time.time() - 120
    session.save()

    ok_resp = client.get(dashboard_url, follow=False)
    assert ok_resp.status_code in (200, 302)
    if ok_resp.status_code == 302:
        assert "/login/" not in ok_resp.url
    assert float(client.session["_last_activity_ts"]) > time.time() - 10

    # Idle >= 300s triggers automatic logout, audit trail, and redirect to login?session_expired=1
    session = client.session
    session["_last_activity_ts"] = time.time() - 305
    session.save()

    expired_resp = client.get(dashboard_url, follow=False)
    assert expired_resp.status_code == 302
    assert expired_resp.url == "/login/?session_expired=1"
    assert expired_resp.cookies.get("ps360_just_logged_out").value == "1"
    assert "_auth_user_id" not in client.session
    assert AuditLog.objects.filter(
        actor=user,
        action=AuditLog.ACTION_LOGOUT,
        new_state__reason="inactivity_timeout",
    ).exists()


@pytest.mark.django_db
def test_passive_60s_htmx_poll_does_not_renew_idle_timer_and_expires_after_5m(client):
    proc_exec = _create_user_for_role(Role.PROC_EXEC, idx=70)
    client.force_login(proc_exec)

    # Dashboard HTML marks background notification polls with X-PS-Background-Poll header
    dash_resp = client.get("/procurement/")
    assert dash_resp.status_code == 200
    assert 'X-PS-Background-Poll' in dash_resp.content.decode()

    stale_ts = time.time() - 240
    session = client.session
    session["_last_activity_ts"] = stale_ts
    session.save()

    for poll_url in ("/notifications/htmx/feed/", "/notifications/htmx/pending-actions/"):
        poll_resp = client.get(
            poll_url,
            HTTP_HX_REQUEST="true",
            HTTP_X_PS_BACKGROUND_POLL="true",
        )
        assert poll_resp.status_code == 200
        # Passive poll must NOT refresh _last_activity_ts
        assert float(client.session["_last_activity_ts"]) == pytest.approx(stale_ts, abs=0.01)

    # Once >= 300s have elapsed since real activity, even a passive poll signs the user out via HX-Redirect
    session = client.session
    session["_last_activity_ts"] = time.time() - 301
    session.save()

    expired_poll_resp = client.get(
        "/notifications/htmx/feed/",
        HTTP_HX_REQUEST="true",
        HTTP_X_PS_BACKGROUND_POLL="true",
    )
    assert expired_poll_resp.status_code == 200
    assert expired_poll_resp["HX-Redirect"] == "/login/?session_expired=1"
    assert "_auth_user_id" not in client.session


@pytest.mark.django_db
def test_keepalive_api_and_timeout_logout_and_login_banner(client):
    user = _create_user_for_role(Role.STORES_RECEIVER, idx=88)

    # Unauthenticated keepalive -> 401 SESSION_EXPIRED
    unauth_ka = client.post("/api/v1/auth/session-keepalive/")
    assert unauth_ka.status_code == 401
    assert unauth_ka.json()["error"]["code"] == "SESSION_EXPIRED"

    # Real portal login initializes _last_activity_ts
    login_resp = client.post(
        "/login/",
        {"email": user.email, "password": "Password123!"},
    )
    assert login_resp.status_code == 302
    assert "_last_activity_ts" in client.session

    # Active user (< 300s idle) calling keepalive gets 200 and refreshes timestamp
    old_ts = time.time() - 180
    session = client.session
    session["_last_activity_ts"] = old_ts
    session.save()

    ok_ka = client.post("/api/v1/auth/session-keepalive/")
    assert ok_ka.status_code == 200
    assert ok_ka.json()["timeout_seconds"] == 300
    assert float(client.session["_last_activity_ts"]) > old_ts + 100

    # Expired user (>= 300s idle) calling keepalive gets 401 SESSION_EXPIRED
    session = client.session
    session["_last_activity_ts"] = time.time() - 305
    session.save()

    exp_ka = client.post("/api/v1/auth/session-keepalive/")
    assert exp_ka.status_code == 401
    assert exp_ka.json()["error"]["code"] == "SESSION_EXPIRED"
    assert "_auth_user_id" not in client.session

    # Client-initiated POST /logout/?reason=inactivity logs out cleanly and redirects to login banner
    client.force_login(user)
    logout_resp = client.post("/logout/?reason=inactivity")
    assert logout_resp.status_code == 302
    assert logout_resp.url == "/login/?session_expired=1"
    assert logout_resp.cookies.get("ps360_just_logged_out").value == "1"
    assert "_auth_user_id" not in client.session

    banner_resp = client.get("/login/?session_expired=1")
    assert banner_resp.status_code == 200
    banner_html = banner_resp.content.decode()
    assert 'data-session-expired-banner="true"' in banner_html
    assert "5 minutes of inactivity" in banner_html


"""
Tests for Step 6: Vendor Dashboard - Email Retry and Recovery Audit & Implementation (ISS-VEND-005).

Verifies the 11 required scenarios:
1. Successful credential email delivery.
2. Temporary network or provider failure triggering retry.
3. Email provider rejection (rate limit 429/5xx retry vs permanent 400 failure).
4. Invalid recipient email address handling (syntax validation fails fast).
5. Maximum retry limit reached (marked FAILED).
6. Manual retry by authorized user (audited, role-checked).
7. Prevention of duplicate credential emails (idempotency & delivered protection).
8. Delivery status update from provider (delivered/bounced) via webhook.
9. Tenant isolation (users from Org A cannot view/retry emails for Org B).
10. Audit trail creation for delivery failures and retries (zero password leakage).
11. Existing vendor registration workflow remains intact.
"""

import json
import re
import socket
from unittest import mock

import pytest
from django.core import mail
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.organization.models import Organization
from apps.vendors.models import Vendor, VendorCredentialDispatch
from apps.vendors.services import (
    issue_vendor_credentials_service,
    process_brevo_webhook_event_service,
)

pytestmark = pytest.mark.django_db

LOGIN_URLS = ["http://localhost:8000/login/", "https://procure.onrender.com/login/"]


@pytest.fixture(autouse=True)
def email_setup(settings):
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.VENDOR_PORTAL_LOGIN_URLS = LOGIN_URLS
    settings.BREVO_API_KEY = ""
    settings.DEFAULT_FROM_EMAIL = "ProcureSphere <no-reply@test.local>"
    settings.VENDOR_EMAIL_MAX_RETRIES = 3
    settings.VENDOR_EMAIL_BACKOFF_BASE = 0.001
    cache.clear()


def _extract_temp_password(body: str) -> str:
    match = re.search(r"Temporary password: (\S+)", body)
    assert match, f"Temporary password not found in email body: {body}"
    return match.group(1)


# ==============================================================================
# Scenario 1: Successful credential email delivery
# ==============================================================================
def test_scenario_01_successful_credential_email_delivery(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])

    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert dispatch.sent_at is not None
    assert dispatch.error == ""
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == [vendor.email.lower()]
    assert vendor.vendor_number in mail.outbox[0].body

    # User created and requires password change
    user = User.objects.get(vendor=vendor)
    assert user.must_change_password is True
    temp_pwd = _extract_temp_password(mail.outbox[0].body)
    assert user.check_password(temp_pwd)

    # Dispatched audit log created
    assert AuditLog.objects.filter(
        target_model="VendorCredentialDispatch",
        target_object_id=str(dispatch.id),
        new_state__event="credential_dispatched",
    ).exists()


# ==============================================================================
# Scenario 2: Temporary network failure triggers retry and succeeds
# ==============================================================================
def test_scenario_02_temporary_network_failure_triggers_retry(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    attempt_count = 0

    def flaky_send_mail(*args, **kwargs):
        nonlocal attempt_count
        attempt_count += 1
        if attempt_count == 1:
            raise OSError("Connection reset by peer")
        # Second attempt succeeds using standard locmem backend
        return 1

    with mock.patch("apps.vendors.services.send_mail", side_effect=flaky_send_mail):
        dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])

    assert attempt_count == 2
    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert dispatch.sent_at is not None
    assert dispatch.error == ""


# ==============================================================================
# Scenario 3: Email provider rejection handling (temporary 429 vs permanent 400)
# ==============================================================================
def test_scenario_03_provider_rejection_transient_vs_permanent(settings, procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]
    settings.BREVO_API_KEY = "xkeysib-test"

    # 3A: Rate limit HTTP 429 (transient) triggers retries
    rate_limit_resp = mock.MagicMock(ok=False, status_code=429, text="Too Many Requests")
    success_resp = mock.MagicMock(ok=True, status_code=200, text='{"messageId": "123"}')

    with mock.patch("apps.vendors.services.requests.post", side_effect=[rate_limit_resp, success_resp]) as mock_post:
        dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
        assert mock_post.call_count == 2
        assert dispatch.status == VendorCredentialDispatch.STATUS_SENT

    # 3B: Bad Request HTTP 400 (permanent) fails immediately without infinite retry loop
    bad_req_resp = mock.MagicMock(ok=False, status_code=400, text="Bad Request: Invalid sender")
    with mock.patch("apps.vendors.services.requests.post", return_value=bad_req_resp) as mock_post:
        with pytest.raises(ValidationError, match="could not be sent"):
            issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"], force=True)
        # Should stop after attempt 1 because 400 is classified as non-transient
        assert mock_post.call_count == 1

    failed_dispatch = VendorCredentialDispatch.objects.filter(vendor=vendor).first()
    assert failed_dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    assert "Brevo API error 400" in failed_dispatch.error


# ==============================================================================
# Scenario 4: Invalid recipient email address handling (syntax check fails fast)
# ==============================================================================
def test_scenario_04_invalid_recipient_email_address_syntax(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]
    vendor.email = "invalid-email-address-without-at"
    vendor.save(update_fields=["email"])

    with pytest.raises(ValidationError, match="Invalid recipient email address format"):
        issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])

    dispatch = VendorCredentialDispatch.objects.filter(vendor=vendor).first()
    assert dispatch is not None
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    assert "Invalid recipient email address syntax" in dispatch.error
    assert len(mail.outbox) == 0


# ==============================================================================
# Scenario 5: Maximum retry limit reached (permanently marked FAILED)
# ==============================================================================
def test_scenario_05_maximum_retry_limit_reached(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    with mock.patch("apps.vendors.services.send_mail", side_effect=socket.timeout("Connection timed out")) as mock_send:
        with pytest.raises(ValidationError, match="could not be sent"):
            issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"], max_retries=3)
        assert mock_send.call_count == 3

    dispatch = VendorCredentialDispatch.objects.filter(vendor=vendor).first()
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    assert "Attempt 1" in dispatch.error and "Attempt 3" in dispatch.error

    # Audit log recorded for failure
    assert AuditLog.objects.filter(
        target_model="VendorCredentialDispatch",
        target_object_id=str(dispatch.id),
        new_state__event="credential_delivery_failed",
    ).exists()


# ==============================================================================
# Scenario 6: Manual retry by authorized user
# ==============================================================================
def test_scenario_06_manual_retry_by_authorized_user(client, procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    # Initial attempt fails
    with mock.patch("apps.vendors.services.send_mail", side_effect=OSError("SMTP down")):
        with pytest.raises(ValidationError):
            issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])

    dispatch = VendorCredentialDispatch.objects.get(vendor=vendor)
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED

    # Unauthorized role (e.g. auditor/stores) cannot retry
    client.force_login(w["auditor"])
    resp_unauth = client.post(reverse("vendor_issue_credentials", args=[vendor.id]), {"force": "true"})
    assert resp_unauth.status_code == 403

    # Authorized user (Procurement Exec) performs manual retry
    client.force_login(w["exec"])
    resp_auth = client.post(reverse("vendor_issue_credentials", args=[vendor.id]), {"force": "true"})
    assert resp_auth.status_code == 302

    # Dispatch is recovered and sent
    latest_dispatch = VendorCredentialDispatch.objects.filter(vendor=vendor).first()
    assert latest_dispatch.status == VendorCredentialDispatch.STATUS_SENT
    assert len(mail.outbox) == 1

    # Audit log records manual retry
    assert AuditLog.objects.filter(
        target_model="VendorCredentialDispatch",
        new_state__event="credential_dispatched",
    ).exists()


# ==============================================================================
# Scenario 7: Prevention of duplicate credential emails (idempotency)
# ==============================================================================
def test_scenario_07_prevention_of_duplicate_credential_emails(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    # 7A: Once DELIVERED, calling without force=True prevents duplicate send
    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
    dispatch.status = VendorCredentialDispatch.STATUS_DELIVERED
    dispatch.save(update_fields=["status"])

    with pytest.raises(ValidationError, match="already been delivered"):
        issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"], force=False)

    # 7B: Idempotency key caches and deduplicates requests
    key = "idem-test-12345"
    d1 = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"], force=True, idempotency_key=key)
    outbox_count_before = len(mail.outbox)

    # Second call with same idempotency key returns cached dispatch without resending email
    d2 = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"], force=True, idempotency_key=key)
    assert d1.id == d2.id
    assert len(mail.outbox) == outbox_count_before

    # 7C: In-flight RETRYING state blocks concurrent dispatch
    d1.status = VendorCredentialDispatch.STATUS_RETRYING
    d1.save(update_fields=["status"])
    with pytest.raises(ValidationError, match="currently in progress"):
        issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])


# ==============================================================================
# Scenario 8: Delivery status update from provider via webhook
# ==============================================================================
def test_scenario_08_provider_delivery_webhook(client, procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
    assert dispatch.status == VendorCredentialDispatch.STATUS_SENT

    webhook_url = reverse("vendor_brevo_webhook")

    # 8A: 'delivered' webhook transitions status to DELIVERED
    payload_delivered = {
        "event": "delivered",
        "email": vendor.email,
        "date": "2026-11-10 12:00:00",
        "ts": 1794300000,
    }
    resp = client.post(webhook_url, json.dumps(payload_delivered), content_type="application/json")
    assert resp.status_code == 200
    assert resp.json()["processed"] == 1

    dispatch.refresh_from_db()
    assert dispatch.status == VendorCredentialDispatch.STATUS_DELIVERED
    assert dispatch.is_delivered is True

    # 8B: 'hard_bounce' webhook transitions status to FAILED with bounce reason
    payload_bounced = {
        "event": "hard_bounce",
        "email": vendor.email,
        "reason": "Mailbox does not exist",
    }
    resp_bounce = client.post(webhook_url, json.dumps(payload_bounced), content_type="application/json")
    assert resp_bounce.status_code == 200

    dispatch.refresh_from_db()
    assert dispatch.status == VendorCredentialDispatch.STATUS_FAILED
    assert "Mailbox does not exist" in dispatch.error


# ==============================================================================
# Scenario 9: Tenant isolation (Multi-tenant security)
# ==============================================================================
def test_scenario_09_tenant_isolation(client, procurement_world):
    w = procurement_world
    vendor_org_a = w["vendor_draft"]  # Belongs to w["org"]

    # Create Organization B and a Procurement Executive in Organization B
    org_b = Organization.objects.create(name="Competitor Corp", code="COMP-01", tax_identifier="TIN-COMP-01")
    exec_org_b = User.objects.create_user(
        "exec@competitor.test",
        "Password123!",
        role=Role.objects.get(code=Role.PROC_EXEC),
        organization=org_b,
    )

    # Exec from Org B attempts to issue credentials for Org A's vendor
    client.force_login(exec_org_b)
    resp = client.post(reverse("vendor_issue_credentials", args=[vendor_org_a.id]))
    # Must be 404 Not Found due to organization scoping
    assert resp.status_code == 404
    assert len(mail.outbox) == 0


# ==============================================================================
# Scenario 10: Audit trail creation for delivery failures and retries
# ==============================================================================
def test_scenario_10_audit_trail_and_zero_password_leakage(procurement_world):
    w = procurement_world
    vendor = w["vendor_draft"]

    dispatch = issue_vendor_credentials_service(vendor=vendor, issued_by=w["exec"])
    temp_password = _extract_temp_password(mail.outbox[0].body)

    # Verify audit logs created
    audit_logs = AuditLog.objects.filter(target_model__in=["Vendor", "VendorCredentialDispatch"])
    assert audit_logs.exists()

    # Verify the temporary password is NEVER saved in audit records
    for log in audit_logs:
        log_text = f"{log.new_state} {log.previous_state}"
        assert temp_password not in log_text, "Temporary password leaked into AuditLog!"


# ==============================================================================
# Scenario 11: Vendor registration workflow intact
# ==============================================================================
def test_scenario_11_vendor_registration_workflow_intact(client, procurement_world):
    w = procurement_world
    client.force_login(w["exec"])

    # 1. Register vendor via modal
    resp_reg = client.post(
        reverse("vendors_register_modal"),
        {
            "legal_name": "Titanium Solutions Ltd",
            "tax_identification_number": "TIN-TITANIUM-001",
            "category": str(w["hardware"].id),
            "email": "contact@titanium.test",
            "address": "10 Tech Parkway, Suite 400",
        },
        HTTP_HX_REQUEST="true",
    )
    assert resp_reg.status_code == 200
    vendor = Vendor.objects.get(tax_identification_number="TIN-TITANIUM-001")
    assert vendor.status == Vendor.STATUS_DRAFT

    # 2. Issue credentials via modal button
    resp_issue = client.post(
        reverse("vendor_issue_credentials", args=[vendor.id]),
        HTTP_HX_REQUEST="true",
    )
    assert resp_issue.status_code == 200
    assert len(mail.outbox) == 1

    temp_pwd = _extract_temp_password(mail.outbox[0].body)
    vendor_user = User.objects.get(vendor=vendor)
    assert vendor_user.must_change_password is True

    # 3. Vendor logs in with temporary password
    client.logout()
    login_resp = client.post("/login/", {"email": vendor_user.email, "password": temp_pwd})
    assert login_resp.status_code == 302

    # 4. First access forces password change
    first_access = client.get("/vendor/")
    assert first_access.status_code == 302
    assert first_access.url in (reverse("password_change"), "/accounts/change-password/")

    # 5. Vendor updates password
    new_pwd = "Titanium#Secure2026!"
    change_resp = client.post(
        reverse("password_change"),
        {
            "current_password": temp_pwd,
            "new_password1": new_pwd,
            "new_password2": new_pwd,
        },
    )
    assert change_resp.status_code == 302
    vendor_user.refresh_from_db()
    assert vendor_user.must_change_password is False

    # 6. Vendor accesses portal dashboard successfully
    dash_resp = client.get("/vendor/")
    assert dash_resp.status_code == 200
    assert vendor.legal_name in dash_resp.content.decode()

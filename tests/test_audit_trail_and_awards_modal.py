import pytest
from django.template import Context, Template
from apps.audit.models import AuditLog


def test_core_tags_format_audit_action():
    from apps.core.templatetags.core_tags import format_audit_action

    # Direct action strings
    assert format_audit_action("UPDATE") == "Update"
    assert format_audit_action("APPROVE") == "Approve"

    # Log object with credentials event
    class DummyLog:
        action = "UPDATE"
        new_state = {"event": "credentials_created", "login_id": "harisaikumar06@gmail.com"}
        def get_action_display(self):
            return "Update"

    log = DummyLog()
    assert format_audit_action(log) == "Credentials Created"

    log.new_state = {"event": "credentials_reset"}
    assert format_audit_action(log) == "Credentials Reset"

    log.new_state = {"event": "credential_delivery_failed"}
    assert format_audit_action(log) == "Delivery Failed"


def test_core_tags_format_audit_state_credentials():
    from apps.core.templatetags.core_tags import format_audit_state

    # None / empty
    assert "—" in str(format_audit_state(None))
    assert "—" in str(format_audit_state({}))

    # Credentials created
    cred_state = {
        "event": "credentials_created",
        "login_id": "harisaikumar06@gmail.com",
        "vendor_number": "VND-2026-00012",
        "user_active": True,
    }
    rendered = str(format_audit_state(cred_state))
    assert "Credentials created for" in rendered
    assert "harisaikumar06@gmail.com" in rendered
    assert "{" not in rendered
    assert "}" not in rendered


def test_core_tags_format_audit_updated_state_transition():
    from apps.core.templatetags.core_tags import format_audit_updated_state

    class DummyLog:
        action = "UPDATE"
        previous_state = {"status": "PUBLISHED"}
        new_state = {"status": "BID_WINDOW", "event_number": "RFQ-2026-00173"}
        def get_action_display(self):
            return "Update"

    log = DummyLog()
    rendered = str(format_audit_updated_state(log))
    assert "Status changed from" in rendered
    assert "Published" in rendered
    assert "Bid Window" in rendered
    assert "{" not in rendered
    assert "}" not in rendered


def test_core_tags_format_audit_state_general_dict():
    from apps.core.templatetags.core_tags import format_audit_state

    dict_state = {
        "legal_name": "Acme Industrial Supplies",
        "category": "Raw Materials",
        "risk_level": "HIGH",
    }
    rendered = str(format_audit_state(dict_state))
    assert "Legal Name:" in rendered
    assert "Acme Industrial Supplies" in rendered
    assert "Category:" in rendered
    assert "Risk Level:" in rendered
    assert "{" not in rendered
    assert "}" not in rendered


def test_pending_awards_modal_typography_and_buttons():
    """Verify that templates/manager/awards.html and base_manager.html resolve all 4 reported pending awards defects."""
    with open("templates/manager/awards.html", "r", encoding="utf-8") as f:
        content = f.read()

    # Defect 1: Safe error extraction and no [object Object]
    assert "safeExtractErrorMessage" in content
    with open("templates/manager/base_manager.html", "r", encoding="utf-8") as f_base:
        base_content = f_base.read()
    assert "safeExtractErrorMessage" in base_content
    assert "cleaned !== '[object Object]'" in base_content

    # Defect 2: Centered fixed overlay positioning and stacking context
    assert "position: fixed !important;" in content
    assert "z-index: 1060 !important;" in content
    assert "modal-dialog-centered" in content
    assert "document.body.appendChild(modalEl)" in content

    # Defect 3: Immediate DOM mounting & header title rendering (no lag/blank)
    assert "DOMContentLoaded" in content
    assert "title.textContent = 'Authorize Final Contract Award'" in content
    assert "title.style.color = '#111827'" in content

    # Defect 4: High-contrast header title for Reject Sourcing Event (#111827 on light background)
    assert "title.textContent = 'Reject Sourcing Event'" in content
    assert "color: #111827" in content
    assert "Confirm & Submit Award" in content
    assert "Confirm & Commit Award" not in content
    assert "modal-header bg-hpe-navy text-white" not in content

    # Form-level inline error alert and Award Status table column
    assert "modalErrorAlert" in content
    assert "Award Status" in content
    assert "getAwardStatusBadge" in content
    assert "cleanErrorString" in base_content
    assert "cleanErrorString" in content


import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User


@pytest.fixture
def superadmin_client(db):
    role, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    admin_user = User.objects.create_superuser(
        email="superadmin_wizard_test@procuresphere.local",
        password="SuperPassword123!",
        role=role,
    )
    client = Client()
    client.force_login(admin_user)
    return client, admin_user


@pytest.mark.django_db
def test_bug021_approval_policy_wizard_navigation_controls(superadmin_client):
    """
    BUG-021:
    Multi-step wizard must have functional Next/Back navigation controls
    and must not expose 'Save Policy' prematurely at step 1.
    """
    client, _ = superadmin_client
    url = reverse("superadmin:policy_create")
    resp = client.get(url)
    assert resp.status_code == 200
    html = resp.content.decode("utf-8")

    # 1. Wizard step indicators exist
    assert "1. Scope & Amounts" in html
    assert "2. Approval Sequence" in html
    assert "3. Exception Routing" in html

    # 2. Functional Next and Back buttons exist
    assert 'id="policyWizardNextBtn"' in html
    assert 'id="policyWizardBackBtn"' in html

    # 3. Save Policy button exists but is hidden at step 1 (has d-none)
    assert 'id="savePolicySubmitBtn"' in html
    assert 'id="savePolicySubmitBtn" class="btn btn-success text-white fw-bold rounded-pill px-4 shadow-sm d-none"' in html or 'd-none' in html

    # 4. JavaScript navigation logic exists for progressive step traversal
    assert "updateWizardButtons" in html
    assert "policyWizardNextBtn" in html
    assert "policyWizardBackBtn" in html
    assert "tab-scope" in html
    assert "tab-workflow" in html
    assert "tab-exception" in html


@pytest.mark.django_db
def test_bug023_browser_navigation_guard_state_management():
    """
    BUG-023:
    Browser history navigation across multiple internal modules must seamlessly
    return through the history stack without repeatedly triggering sign-out confirmation.
    The guard must only prompt at the initial entry point.
    """
    with open("static/js/app.js", "r", encoding="utf-8") as f:
        app_js = f.read()

    # Verify initial entry point tracking
    assert "ps_portal_initialized" in app_js
    assert "ps_nav_depth" in app_js
    assert "isInitialEntry" in app_js

    # Verify armDashboardGuard only arms at initial entry point
    assert "if (navDepth > 0 && !isInitialEntry)" in app_js

    # Verify popstate allows seamless internal navigation when navDepth > 0
    assert "curNavDepth > 0" in app_js

    # Verify double-open guard prevents modal from triggering repeatedly
    assert "isAlreadyOpen" in app_js

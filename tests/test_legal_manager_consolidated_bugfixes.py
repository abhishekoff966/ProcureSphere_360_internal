from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.contracts.forms import ContractObligationForm
from apps.contracts.models import Contract
from apps.contracts.services import (
    add_contract_milestone_service,
    add_contract_obligation_service,
    approve_business_service,
    approve_legal_review_service,
    complete_contract_milestone_service,
    create_contract_service,
    fulfill_contract_obligation_service,
    submit_for_legal_review_service,
    terminate_contract_service,
)
from apps.organization.models import CostCenter, Department, Organization
from apps.reports.models import ExportJob
from apps.reports.services import generate_export_job_service
from apps.requisitions.models import PurchaseRequisition
from apps.vendors.models import Vendor, VendorCategory
from apps.vendors.services import approve_vendor_service, register_vendor_service

pytestmark = [pytest.mark.django_db]


def _get_or_create_role(code, name):
    role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
    return role


@pytest.fixture
def legal_bugfix_world():
    org_a = Organization.objects.create(name="HPE Alpha", code="HPEA", is_active=True)
    org_b = Organization.objects.create(name="Beta Corp", code="ORGB", is_active=True)
    dept_a = Department.objects.create(organization=org_a, name="Legal Ops A", code="LEGA")
    dept_b = Department.objects.create(organization=org_b, name="Legal Ops B", code="LEGB")
    cc_a = CostCenter.objects.create(department=dept_a, name="CC Alpha", code="CCA")
    cc_b = CostCenter.objects.create(department=dept_b, name="CC Beta", code="CCB")

    roles = {
        Role.SUPER_ADMIN: _get_or_create_role(Role.SUPER_ADMIN, "Super Admin"),
        Role.LEGAL_MGR: _get_or_create_role(Role.LEGAL_MGR, "Legal Manager"),
        Role.PROC_MGR: _get_or_create_role(Role.PROC_MGR, "Procurement Manager"),
        Role.PROC_EXEC: _get_or_create_role(Role.PROC_EXEC, "Procurement Executive"),
        Role.FINANCE_AP: _get_or_create_role(Role.FINANCE_AP, "Finance AP"),
        Role.AUDITOR: _get_or_create_role(Role.AUDITOR, "Compliance Auditor"),
        Role.DEPT_APPROVER: _get_or_create_role(Role.DEPT_APPROVER, "Department Approver"),
        Role.REQUESTER: _get_or_create_role(Role.REQUESTER, "Requester"),
        Role.VENDOR_USER: _get_or_create_role(Role.VENDOR_USER, "Vendor User"),
    }

    super_admin_a = User.objects.create_user(
        email="superadmin.alpha@test.com",
        password="Password123!",
        role=roles[Role.SUPER_ADMIN],
        organization=org_a,
        department=dept_a,
    )
    legal_a = User.objects.create_user(
        email="legal.alpha@test.com",
        password="Password123!",
        role=roles[Role.LEGAL_MGR],
        organization=org_a,
        department=dept_a,
    )
    legal_b = User.objects.create_user(
        email="legal.beta@test.com",
        password="Password123!",
        role=roles[Role.LEGAL_MGR],
        organization=org_b,
        department=dept_b,
    )
    proc_mgr_a = User.objects.create_user(
        email="procmgr.alpha@test.com",
        password="Password123!",
        role=roles[Role.PROC_MGR],
        organization=org_a,
        department=dept_a,
    )
    proc_exec_a = User.objects.create_user(
        email="procexec.alpha@test.com",
        password="Password123!",
        role=roles[Role.PROC_EXEC],
        organization=org_a,
        department=dept_a,
    )
    finance_a = User.objects.create_user(
        email="finance.alpha@test.com",
        password="Password123!",
        role=roles[Role.FINANCE_AP],
        organization=org_a,
        department=dept_a,
    )
    auditor_a = User.objects.create_user(
        email="auditor.alpha@test.com",
        password="Password123!",
        role=roles[Role.AUDITOR],
        organization=org_a,
        department=dept_a,
    )
    dept_approver_a = User.objects.create_user(
        email="approver.alpha@test.com",
        password="Password123!",
        role=roles[Role.DEPT_APPROVER],
        organization=org_a,
        department=dept_a,
    )
    requester_a = User.objects.create_user(
        email="requester.alpha@test.com",
        password="Password123!",
        role=roles[Role.REQUESTER],
        organization=org_a,
        department=dept_a,
    )
    requester_b = User.objects.create_user(
        email="requester.beta@test.com",
        password="Password123!",
        role=roles[Role.REQUESTER],
        organization=org_b,
        department=dept_b,
    )
    vendor_user = User.objects.create_user(
        email="vendor.portal@test.com",
        password="Password123!",
        role=roles[Role.VENDOR_USER],
    )

    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-CONSOL", defaults={"name": "Consolidated Test Category"}
    )
    vendor_a = register_vendor_service(
        legal_name="Alpha Enterprise Vendors Ltd",
        tax_identification_number="TAX-ALPHA-001",
        category=category,
        email="alpha.vendor@test.com",
        address="1 Alpha Way",
        organization=org_a,
    )
    vendor_b = register_vendor_service(
        legal_name="Beta Cloud Partners Inc",
        tax_identification_number="TAX-BETA-002",
        category=category,
        email="beta.vendor@test.com",
        address="2 Beta Way",
        organization=org_b,
    )

    today = timezone.now().date()
    draft_a = create_contract_service(
        title="Alpha Draft Agreement",
        vendor=vendor_a,
        contract_value=Decimal("100000.00"),
        start_date=today,
        end_date=today + timedelta(days=180),
        contract_owner=legal_a,
    )

    review_a = create_contract_service(
        title="Alpha Legal Review Agreement",
        vendor=vendor_a,
        contract_value=Decimal("250000.00"),
        start_date=today,
        end_date=today + timedelta(days=365),
        contract_owner=proc_mgr_a,
    )
    submit_for_legal_review_service(contract=review_a, user=proc_mgr_a)

    review_b = create_contract_service(
        title="Beta Confidential Legal Contract",
        vendor=vendor_b,
        contract_value=Decimal("990000.00"),
        start_date=today,
        end_date=today + timedelta(days=365),
        contract_owner=legal_b,
    )
    submit_for_legal_review_service(contract=review_b, user=legal_b)

    active_a = create_contract_service(
        title="Alpha Active Master SLA",
        vendor=vendor_a,
        contract_value=Decimal("500000.00"),
        start_date=today - timedelta(days=10),
        end_date=today + timedelta(days=355),
        contract_owner=legal_a,
    )
    submit_for_legal_review_service(contract=active_a, user=legal_a)
    approve_legal_review_service(contract=active_a, user=legal_a)
    approve_business_service(contract=active_a, user=proc_mgr_a)

    terminated_a = create_contract_service(
        title="Alpha Terminated Legacy MSA",
        vendor=vendor_a,
        contract_value=Decimal("150000.00"),
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=330),
        contract_owner=legal_a,
    )
    submit_for_legal_review_service(contract=terminated_a, user=legal_a)
    approve_legal_review_service(contract=terminated_a, user=legal_a)
    approve_business_service(contract=terminated_a, user=proc_mgr_a)
    terminate_contract_service(contract=terminated_a, user=legal_a, reason="Replaced by new MSA")

    pr_a = PurchaseRequisition.objects.create(
        department=dept_a,
        cost_center=cc_a,
        requester=requester_a,
        pr_number="PR-ALPHA-001",
        title="Alpha Internal Hardware PR",
        justification="Operational need",
        status=PurchaseRequisition.STATUS_SUBMITTED,
        total_amount=Decimal("25000.00"),
        requested_delivery_date=today + timedelta(days=14),
    )
    pr_b = PurchaseRequisition.objects.create(
        department=dept_b,
        cost_center=cc_b,
        requester=requester_b,
        pr_number="PR-BETA-999",
        title="Beta Cross-Org PR Should Leak Neither Inbox Nor Action",
        justification="Cross-org",
        status=PurchaseRequisition.STATUS_SUBMITTED,
        total_amount=Decimal("75000.00"),
        requested_delivery_date=today + timedelta(days=14),
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "super_admin_a": super_admin_a,
        "legal_a": legal_a,
        "legal_b": legal_b,
        "proc_mgr_a": proc_mgr_a,
        "proc_exec_a": proc_exec_a,
        "finance_a": finance_a,
        "auditor_a": auditor_a,
        "dept_approver_a": dept_approver_a,
        "vendor_user": vendor_user,
        "vendor_a": vendor_a,
        "vendor_b": vendor_b,
        "draft_a": draft_a,
        "review_a": review_a,
        "review_b": review_b,
        "active_a": active_a,
        "terminated_a": terminated_a,
        "pr_a": pr_a,
        "pr_b": pr_b,
    }


def _collect_messages(resp):
    return [str(m) for m in get_messages(resp.wsgi_request)]


def test_contract_web_mutations_catch_validation_errors_no_500(client, legal_bugfix_world):
    """LM-BUG-01: Invalid milestone, obligation, toggle, and document POSTs redirect cleanly with errors instead of 500."""
    legal_a = legal_bugfix_world["legal_a"]
    active_a = legal_bugfix_world["active_a"]
    client.force_login(legal_a)

    # 1. Milestone due date earlier than contract start_date -> 302 with error message, not 500
    bad_due = (active_a.start_date - timedelta(days=5)).isoformat()
    resp = client.post(
        reverse("contract_milestone_create", kwargs={"contract_id": active_a.id}),
        data={"title": "Early Milestone", "due_date": bad_due, "amount": "1000.00"},
    )
    assert resp.status_code == 302
    assert any("cannot be earlier than contract start date" in m for m in _collect_messages(resp))

    # 2. Completing an already-completed milestone -> 302 with error message, not 500
    ms = add_contract_milestone_service(
        contract=active_a,
        title="Valid Milestone",
        due_date=active_a.start_date + timedelta(days=10),
        amount=Decimal("5000.00"),
        user=legal_a,
    )
    complete_contract_milestone_service(milestone=ms, user=legal_a)
    resp = client.post(
        reverse(
            "contract_milestone_complete",
            kwargs={"contract_id": active_a.id, "milestone_id": ms.id},
        )
    )
    assert resp.status_code == 302
    assert any("already completed" in m.lower() for m in _collect_messages(resp))

    # 3. Obligation with invalid responsible_party -> 302 with error message, not 500
    resp = client.post(
        reverse("contract_obligation_create", kwargs={"contract_id": active_a.id}),
        data={
            "title": "Invalid Party Obligation",
            "responsible_party": "HPE LEGAL",
            "due_date": (active_a.start_date + timedelta(days=15)).isoformat(),
        },
    )
    assert resp.status_code == 302
    assert any("Invalid responsible_party" in m for m in _collect_messages(resp))

    # 4. Fulfilling an already-fulfilled obligation -> 302 with error message, not 500
    ob = add_contract_obligation_service(
        contract=active_a,
        title="Valid Obligation",
        responsible_party="LEGAL",
        due_date=active_a.start_date + timedelta(days=20),
        user=legal_a,
    )
    fulfill_contract_obligation_service(obligation=ob, user=legal_a)
    resp = client.post(
        reverse(
            "contract_obligation_fulfill",
            kwargs={"contract_id": active_a.id, "obligation_id": ob.id},
        )
    )
    assert resp.status_code == 302
    assert any("already fulfilled" in m.lower() for m in _collect_messages(resp))

    # 5. Spoofed PDF / invalid extension document upload -> 302 with error message, not 500
    bad_file = SimpleUploadedFile(
        "malicious.exe", b"MZ-NOT-ALLOWED", content_type="application/octet-stream"
    )
    resp = client.post(
        reverse("contract_document_upload", kwargs={"contract_id": active_a.id}),
        data={"title": "Bad Binary", "file": bad_file},
    )
    assert resp.status_code == 302
    assert len(_collect_messages(resp)) >= 1


def test_contract_obligation_form_uses_valid_select_and_blocks_terminated(client, legal_bugfix_world):
    """LM-BUG-02: Obligation form renders allowed select choices and service/web block obligations on TERMINATED contracts."""
    form = ContractObligationForm()
    rendered_widget = str(form["responsible_party"])
    assert "<select" in rendered_widget
    assert "VENDOR / HPE LEGAL" not in rendered_widget
    for allowed_party in ("LEGAL", "VENDOR", "BUYER", "INTERNAL", "PROCUREMENT", "MUTUAL", "BOTH"):
        assert f'value="{allowed_party}"' in rendered_widget

    legal_a = legal_bugfix_world["legal_a"]
    terminated_a = legal_bugfix_world["terminated_a"]
    with pytest.raises(ValidationError, match="terminated"):
        add_contract_obligation_service(
            contract=terminated_a,
            title="Blocked Obligation",
            responsible_party="LEGAL",
            due_date=terminated_a.start_date + timedelta(days=30),
            user=legal_a,
        )

    client.force_login(legal_a)
    resp = client.post(
        reverse("contract_obligation_create", kwargs={"contract_id": terminated_a.id}),
        data={
            "title": "Blocked Web Obligation",
            "responsible_party": "LEGAL",
            "due_date": (terminated_a.start_date + timedelta(days=30)).isoformat(),
        },
    )
    assert resp.status_code == 302
    assert terminated_a.obligations.count() == 0
    assert any("terminated" in m.lower() for m in _collect_messages(resp))


def test_contract_ui_hides_buttons_by_role_and_status(client, legal_bugfix_world):
    """LM-BUG-03: Contract register and detail buttons reflect exact role & lifecycle permissions."""
    draft_a = legal_bugfix_world["draft_a"]
    active_a = legal_bugfix_world["active_a"]
    terminated_a = legal_bugfix_world["terminated_a"]
    legal_a = legal_bugfix_world["legal_a"]

    # Add one pending milestone and obligation on active & terminated so row buttons can also be verified
    add_contract_milestone_service(
        contract=active_a,
        title="Active Pending MS",
        due_date=active_a.start_date + timedelta(days=20),
        amount=Decimal("2000.00"),
        user=legal_a,
    )
    add_contract_obligation_service(
        contract=active_a,
        title="Active Pending OB",
        responsible_party="LEGAL",
        due_date=active_a.start_date + timedelta(days=25),
        user=legal_a,
    )

    # Contract drafting CTA (DEF-017): only LEGAL_MGR and SUPER_ADMIN see "Draft New Contract"
    for creator_user in (legal_a, legal_bugfix_world["super_admin_a"]):
        client.force_login(creator_user)
        assert "Draft New Contract" in client.get(reverse("contracts_list")).content.decode()

    # Non-drafting internal roles: no "Draft New Contract" on Contract Register
    for non_creator in (
        legal_bugfix_world["auditor_a"],
        legal_bugfix_world["finance_a"],
        legal_bugfix_world["proc_mgr_a"],
        legal_bugfix_world["proc_exec_a"],
    ):
        client.force_login(non_creator)
        assert "Draft New Contract" not in client.get(reverse("contracts_list")).content.decode()

    # Read-only roles (AUDITOR, FINANCE_AP): no mutation controls on contract detail
    for ro_user in (legal_bugfix_world["auditor_a"], legal_bugfix_world["finance_a"]):
        client.force_login(ro_user)
        detail_html = client.get(
            reverse("contract_detail", kwargs={"contract_id": active_a.id})
        ).content.decode()
        for target in (
            "#submitLegalModal",
            "#amendmentModal",
            "#renewModal",
            "#terminateModal",
            "#addMilestoneModal",
            "#addObligationModal",
            "#uploadDocModal",
        ):
            assert f'data-bs-target="{target}"' not in detail_html
        assert "Mark Complete" not in detail_html
        assert "Mark Fulfilled" not in detail_html

    # PROC_EXEC: cannot draft contracts (DEF-017), can manage SLA, cannot amend/renew/terminate
    client.force_login(legal_bugfix_world["proc_exec_a"])
    exec_html = client.get(
        reverse("contract_detail", kwargs={"contract_id": active_a.id})
    ).content.decode()
    assert 'data-bs-target="#addMilestoneModal"' in exec_html
    assert 'data-bs-target="#addObligationModal"' in exec_html
    assert 'data-bs-target="#uploadDocModal"' in exec_html
    assert "Mark Complete" in exec_html
    assert "Mark Fulfilled" in exec_html
    assert 'data-bs-target="#amendmentModal"' not in exec_html
    assert 'data-bs-target="#renewModal"' not in exec_html
    assert 'data-bs-target="#terminateModal"' not in exec_html

    # LEGAL_MGR across DRAFT / ACTIVE / TERMINATED statuses
    client.force_login(legal_a)
    draft_html = client.get(
        reverse("contract_detail", kwargs={"contract_id": draft_a.id})
    ).content.decode()
    assert 'data-bs-target="#submitLegalModal"' in draft_html
    assert 'data-bs-target="#amendmentModal"' not in draft_html
    assert 'data-bs-target="#renewModal"' not in draft_html
    assert 'data-bs-target="#terminateModal"' not in draft_html

    active_html = client.get(
        reverse("contract_detail", kwargs={"contract_id": active_a.id})
    ).content.decode()
    assert 'data-bs-target="#amendmentModal"' in active_html
    assert 'data-bs-target="#renewModal"' in active_html
    assert 'data-bs-target="#terminateModal"' in active_html

    term_html = client.get(
        reverse("contract_detail", kwargs={"contract_id": terminated_a.id})
    ).content.decode()
    for target in (
        "#submitLegalModal",
        "#amendmentModal",
        "#renewModal",
        "#terminateModal",
        "#addMilestoneModal",
        "#addObligationModal",
        "#uploadDocModal",
    ):
        assert f'data-bs-target="{target}"' not in term_html
    assert "Mark Complete" not in term_html
    assert "Mark Fulfilled" not in term_html


def test_contract_forms_and_modals_use_inr_currency_labels(client, legal_bugfix_world):
    """LM-BUG-04: Contract create page and detail modals consistently display INR (₹) labels with zero ($)."""
    client.force_login(legal_bugfix_world["legal_a"])

    create_html = client.get(reverse("contract_create")).content.decode()
    assert "Contract Value (₹)" in create_html
    assert "($)" not in create_html

    detail_html = client.get(
        reverse("contract_detail", kwargs={"contract_id": legal_bugfix_world["active_a"].id})
    ).content.decode()
    assert "New Contract Value (₹)" in detail_html
    assert "Amount (₹)" in detail_html
    assert "Updated Value (₹)" in detail_html
    assert "($)" not in detail_html


def test_legal_manager_cannot_approve_vendor_kyc_service(client, legal_bugfix_world):
    """LM-BUG-05: Legal Manager must not be able to activate vendors via approve_vendor_service or vendor KYC endpoints."""
    legal_a = legal_bugfix_world["legal_a"]
    vendor_a = legal_bugfix_world["vendor_a"]
    vendor_a.status = Vendor.STATUS_KYC_REVIEW
    vendor_a.save(update_fields=["status"])

    with pytest.raises(PermissionDenied):
        approve_vendor_service(vendor=vendor_a, manager=legal_a)

    vendor_a.refresh_from_db()
    assert vendor_a.status == Vendor.STATUS_KYC_REVIEW

    client.force_login(legal_a)
    resp = client.post(
        reverse("vendor_kyc_action", kwargs={"vendor_id": vendor_a.id, "kyc_action": "approve"}),
        data={"notes": "Attempting HTTP bypass"},
    )
    assert resp.status_code == 403


def test_legal_manager_approvals_inbox_org_scoped_contracts_no_pr_leak(client, legal_bugfix_world):
    """LM-BUG-06: Legal Manager inbox renders org-scoped contracts for review instead of dead/cross-org PR links."""
    legal_a = legal_bugfix_world["legal_a"]
    review_a = legal_bugfix_world["review_a"]
    review_b = legal_bugfix_world["review_b"]
    active_a = legal_bugfix_world["active_a"]
    pr_a = legal_bugfix_world["pr_a"]
    pr_b = legal_bugfix_world["pr_b"]

    client.force_login(legal_a)

    pending_resp = client.get(reverse("approvals_inbox"))
    assert pending_resp.status_code == 200
    pending_html = pending_resp.content.decode()
    assert review_a.contract_number in pending_html
    assert review_a.title in pending_html
    assert reverse("contract_detail", kwargs={"contract_id": review_a.id}) in pending_html
    assert review_b.contract_number not in pending_html
    assert pr_a.pr_number not in pending_html
    assert pr_b.pr_number not in pending_html
    assert "Review &amp; Decision" not in pending_html
    assert "Review & Decision" not in pending_html

    history_resp = client.get(reverse("approvals_inbox") + "?tab=history")
    assert history_resp.status_code == 200
    history_html = history_resp.content.decode()
    assert active_a.contract_number in history_html
    assert review_b.contract_number not in history_html
    assert pr_a.pr_number not in history_html

    # PR POST must be rejected without modifying PR status
    post_resp = client.post(
        reverse("approvals_inbox"),
        data={"pr_id": str(pr_a.id), "action": "APPROVED", "comments": "Illegal PR approval"},
    )
    assert post_resp.status_code == 302
    pr_a.refresh_from_db()
    assert pr_a.status == PurchaseRequisition.STATUS_SUBMITTED
    assert any("not authorized" in m.lower() for m in _collect_messages(post_resp))

    # Regression check: DEPT_APPROVER inbox still lists org PRs normally
    client.force_login(legal_bugfix_world["dept_approver_a"])
    approver_html = client.get(reverse("approvals_inbox")).content.decode()
    assert pr_a.pr_number in approver_html
    assert pr_b.pr_number not in approver_html


def test_reports_hub_and_generate_harden_audit_log_and_validation(client, legal_bugfix_world):
    """LM-BUG-07: Reports hub hides audit_log from non-auditors, rejects invalid types without silent fallback, and preserves contract exports."""
    legal_a = legal_bugfix_world["legal_a"]
    auditor_a = legal_bugfix_world["auditor_a"]
    vendor_user = legal_bugfix_world["vendor_user"]

    # 1. LEGAL_MGR does not see audit_log in dropdown; AUDITOR does
    client.force_login(legal_a)
    legal_hub_html = client.get(reverse("reports_hub")).content.decode()
    assert 'value="contract_expiry"' in legal_hub_html
    assert 'value="audit_log"' not in legal_hub_html

    client.force_login(auditor_a)
    auditor_hub_html = client.get(reverse("reports_hub")).content.decode()
    assert 'value="audit_log"' in auditor_hub_html

    # 2. VENDOR_USER is forbidden on /reports/generate/
    client.force_login(vendor_user)
    assert (
        client.post(
            reverse("generate_report"),
            data={"report_type": "contract_expiry", "format_type": "CSV"},
        ).status_code
        == 403
    )

    # 3. LEGAL_MGR attempting audit_log export gets 403 and creates zero ExportJob
    client.force_login(legal_a)
    before_count = ExportJob.objects.count()
    assert (
        client.post(
            reverse("generate_report"),
            data={"report_type": "audit_log", "format_type": "CSV"},
        ).status_code
        == 403
    )
    assert ExportJob.objects.count() == before_count

    # 4. Empty or invalid report_type / invalid format_type redirects with error & creates zero jobs
    for bad_data in (
        {"report_type": "", "format_type": "CSV"},
        {"report_type": "nonexistent_report", "format_type": "CSV"},
        {"report_type": "contract_expiry", "format_type": "EXE"},
    ):
        resp = client.post(reverse("generate_report"), data=bad_data)
        assert resp.status_code == 302
        assert ExportJob.objects.count() == before_count
        assert any("valid" in m.lower() for m in _collect_messages(resp))

    # 5. Service-level guard never falls back from unsupported report_type to get_audit_log_report
    bad_job = ExportJob.objects.create(
        report_type="unknown_legacy_type",
        export_format="CSV",
        requested_by=legal_a,
    )
    with pytest.raises(ValueError, match="Unsupported report type"):
        generate_export_job_service(bad_job.id)
    bad_job.refresh_from_db()
    assert bad_job.status == ExportJob.STATUS_FAILED

    # 6. Legitimate contract_expiry export for LEGAL_MGR works end-to-end
    ok_resp = client.post(
        reverse("generate_report"),
        data={"report_type": "contract_expiry", "format_type": "CSV"},
    )
    assert ok_resp.status_code == 302
    ok_job = ExportJob.objects.filter(requested_by=legal_a, report_type="contract_expiry").latest("id")
    assert ok_job.status == ExportJob.STATUS_COMPLETED


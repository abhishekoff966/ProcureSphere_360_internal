import datetime
from decimal import Decimal
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from celery.exceptions import Retry
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.audit.services import detect_entity_type
from apps.budgets.models import Budget
from apps.contracts.models import Contract
from apps.contracts.services import (
    add_contract_milestone_service,
    create_contract_service,
    create_contract_version_service,
    upload_contract_document_service,
)
from apps.core.middleware import RequestIDMiddleware
from apps.core.validators import validate_file_upload
from apps.notifications.tasks import send_user_welcome_email_task
from apps.orders.models import POLine, PurchaseOrder
from apps.orders.services import (
    acknowledge_purchase_order_service,
    approve_purchase_order_service,
    cancel_purchase_order_governance_service,
    reject_purchase_order_service,
)
from apps.organization.models import CostCenter, Department, FiscalPeriod, Organization
from apps.scorecards.models import VendorScorecard
from apps.scorecards.services import latest_scorecards_queryset
from apps.sourcing.services import _parse_weight
from apps.vendors.models import Vendor, VendorCategory


@pytest.fixture
def base_org_data(db):
    org = Organization.objects.create(name="QA Org", code="QAORG01")
    dept = Department.objects.create(organization=org, name="QA Dept", code="QADEPT01")
    cc = CostCenter.objects.create(department=dept, name="QA CC", code="QACC01")
    fp = FiscalPeriod.objects.create(
        organization=org,
        name="FY2026-Q1",
        year=2026,
        period_number=1,
        start_date=datetime.date(2026, 1, 1),
        end_date=datetime.date(2026, 12, 31),
        is_closed=False,
    )
    cat = VendorCategory.objects.create(name="IT Services", code="IT_SERV")
    return {"org": org, "dept": dept, "cc": cc, "fp": fp, "cat": cat}


def _make_vendor(org, cat, name, number, tin):
    return Vendor.objects.create(
        organization=org,
        legal_name=name,
        vendor_number=number,
        tax_identification_number=tin,
        email=f"{tin.lower()}@vendor.local",
        address="100 Tech Park, Bangalore",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )


@pytest.mark.django_db
class TestRetest16FailedDefects:
    """Retests for all 16 defects reported in the unit test run."""

    # 1. DEF-001 / UT_AUD_049
    def test_def_001_detect_entity_type_recognizes_vnd_prefix(self):
        """detect_entity_type recognises generated vendor numbers starting with VND-."""
        assert detect_entity_type("VND-2026-00001") == "VENDOR"
        assert detect_entity_type("vnd-2026-99999") == "VENDOR"

    # 2. DEF-002 / UT_AUD_059
    def test_def_002_audit_log_readable_target_resolves_user_target(self, base_org_data):
        """readable_target resolves User targets to 'User <str(user)>' without falling back to #short_id."""
        req_role = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]
        user = User.objects.create(
            email="qa3321@unit.test",
            username="qa3321@unit.test",
            role=req_role,
        )
        log = AuditLog.objects.create(
            actor=user,
            action=AuditLog.ACTION_UPDATE,
            target_model="User",
            target_object_id=str(user.id),
        )
        expected = f"User {str(user)}"
        assert log.readable_target == expected
        assert "qa3321@unit.test" in log.readable_target

    # 3. DEF-003 / UT_CON_101
    def test_def_003_create_contract_service_rejects_none_contract_value(self, base_org_data):
        """create_contract_service rejects contract_value=None with ValidationError, avoiding DB IntegrityError."""
        pm_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        pm_user = User.objects.create(email="pm_contract@unit.test", username="pm_contract@unit.test", role=pm_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Alpha", "VND-2026-00010", "TIN0010")
        with pytest.raises(ValidationError) as exc_info:
            create_contract_service(
                title="Service Contract",
                vendor=vendor,
                contract_value=None,
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 12, 31),
                contract_owner=pm_user,
            )
        assert "Contract value" in str(exc_info.value)

    # 4. DEF-004 / UT_CON_168
    def test_def_004_create_contract_version_service_rejects_none_contract_value(self, base_org_data):
        """create_contract_version_service rejects contract_value=None with ValidationError."""
        pm_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        pm_user = User.objects.create(email="pm_version@unit.test", username="pm_version@unit.test", role=pm_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Beta", "VND-2026-00011", "TIN0011")
        contract = create_contract_service(
            title="Initial Contract",
            vendor=vendor,
            contract_value=Decimal("50000.00"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            contract_owner=pm_user,
        )
        contract.status = Contract.STATUS_ACTIVE
        contract.save()

        with pytest.raises(ValidationError) as exc_info:
            create_contract_version_service(
                contract=contract,
                user=pm_user,
                amendment_summary="Amend to None value",
                contract_value=None,
                start_date=datetime.date(2026, 1, 1),
                end_date=datetime.date(2026, 12, 31),
            )
        assert "Contract value" in str(exc_info.value)

    # 5. DEF-005 / UT_CON_181
    def test_def_005_add_contract_milestone_service_handles_malformed_due_date(self, base_org_data):
        """add_contract_milestone_service raises ValidationError for malformed due-date string."""
        pm_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        pm_user = User.objects.create(email="pm_ms@unit.test", username="pm_ms@unit.test", role=pm_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Gamma", "VND-2026-00012", "TIN0012")
        contract = create_contract_service(
            title="Milestone Contract",
            vendor=vendor,
            contract_value=Decimal("10000.00"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            contract_owner=pm_user,
        )
        with pytest.raises(ValidationError) as exc_info:
            add_contract_milestone_service(
                contract=contract,
                title="Phase 1",
                due_date="not-a-date",
                amount=Decimal("1000.00"),
                user=pm_user,
            )
        assert "Invalid due date format" in str(exc_info.value)

    # 6. DEF-006 / UT_CON_202
    def test_def_006_upload_contract_document_accepts_txt_files(self, base_org_data):
        """Contract document upload service accepts .txt files as advertised."""
        legal_role = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})[0]
        legal_user = User.objects.create(email="legal_doc@unit.test", username="legal_doc@unit.test", role=legal_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Delta", "VND-2026-00013", "TIN0013")
        contract = create_contract_service(
            title="Upload Contract",
            vendor=vendor,
            contract_value=Decimal("20000.00"),
            start_date=datetime.date(2026, 1, 1),
            end_date=datetime.date(2026, 12, 31),
            contract_owner=legal_user,
        )
        txt_file = SimpleUploadedFile("notes.txt", b"Contract terms and notes content.", content_type="text/plain")
        doc = upload_contract_document_service(
            contract=contract,
            user=legal_user,
            title="Terms Notes",
            file=txt_file,
        )
        assert doc.id is not None
        assert "notes" in doc.file.name

    # 7. DEF-007 / UT_COR_095
    def test_def_007_validate_file_upload_verifies_from_start_and_restores_pointer(self):
        """validate_file_upload verifies signature from start of file and restores original pointer."""
        pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
        f = BytesIO(pdf_bytes)
        f.name = "sample.pdf"
        f.size = len(pdf_bytes)
        f.content_type = "application/pdf"

        f.seek(3)
        assert f.tell() == 3

        # Validation must succeed even when pointer was at 3
        validate_file_upload(f)

        # File pointer must be restored to 3
        assert f.tell() == 3

    # 8. DEF-008 / UT_COR_125
    def test_def_008_request_id_middleware_replaces_empty_header_with_uuid(self):
        """Empty or blank X-Request-ID header is replaced by a generated UUID correlation id."""
        factory = RequestFactory()
        req = factory.get("/health", HTTP_X_REQUEST_ID="   ")

        class DummyResponse(dict):
            pass

        middleware = RequestIDMiddleware(lambda r: DummyResponse())
        resp = middleware(req)

        correlation_id = resp.get("X-Request-ID")
        assert correlation_id is not None
        assert correlation_id.strip() != ""
        assert len(correlation_id) == 36

    # 9. DEF-009 / UT_NOT_044
    def test_def_009_welcome_email_task_retries_only_once_on_failure(self):
        """send_user_welcome_email_task does not catch Celery Retry and retry twice."""
        req_role = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]
        user = User.objects.create(email="task_test@unit.test", username="task_test@unit.test", role=req_role)

        with patch("apps.notifications.email_service.send_user_welcome_email_direct") as mock_send:
            mock_send.return_value = ("FAILED", "SMTP connection timed out")
            with patch.object(send_user_welcome_email_task, "retry", side_effect=Retry()) as mock_retry:
                with pytest.raises(Retry):
                    send_user_welcome_email_task(str(user.id), "Temp#Pass1234", "http://localhost:8000/login/")

                # Must be invoked exactly once per failure
                assert mock_retry.call_count == 1

    # 10. DEF-010 / UT_ORD_031
    def test_def_010_approve_purchase_order_enforces_role_check_at_service_level(self, base_org_data):
        """approve_purchase_order_service rejects approval by non-approver roles like Auditor."""
        auditor_role = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})[0]
        auditor_user = User.objects.create(email="auditor_po@unit.test", username="auditor_po@unit.test", role=auditor_role)
        pe_role = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})[0]
        pe_user = User.objects.create(email="pe_po@unit.test", username="pe_po@unit.test", role=pe_role)

        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Epsilon", "VND-2026-00014", "TIN0014")
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-00010",
            vendor=vendor,
            cost_center=base_org_data["cc"],
            status=PurchaseOrder.STATUS_APPROVAL,
            submitted_by=pe_user,
            total_amount=Decimal("1000.00"),
        )
        with pytest.raises(ValidationError) as exc_info:
            approve_purchase_order_service(po=po, approver=auditor_user)
        assert "Permission Denied" in str(exc_info.value)

    # 11. DEF-011 / UT_ORD_049
    def test_def_011_reject_purchase_order_rejects_whitespace_only_reason(self, base_org_data):
        """reject_purchase_order_service rejects whitespace-only rejection reason."""
        pm_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        pm_user = User.objects.create(email="pm_reject@unit.test", username="pm_reject@unit.test", role=pm_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Zeta", "VND-2026-00015", "TIN0015")
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-00011",
            vendor=vendor,
            cost_center=base_org_data["cc"],
            status=PurchaseOrder.STATUS_APPROVAL,
            total_amount=Decimal("2000.00"),
        )
        with pytest.raises(ValidationError) as exc_info:
            reject_purchase_order_service(po=po, approver=pm_user, reason="   ")
        assert "Rejection reason is required" in str(exc_info.value)

    # 12. DEF-012 / UT_ORD_057
    def test_def_012_acknowledge_purchase_order_verifies_vendor_ownership(self, base_org_data):
        """acknowledge_purchase_order_service verifies acknowledging user belongs to the PO's vendor."""
        vendor_role = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})[0]
        vendor_a = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor A", "VND-2026-00016", "TINA")
        vendor_b = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor B", "VND-2026-00017", "TINB")
        user_vendor_b = User.objects.create(
            email="user_b@vendorb.local",
            username="user_b@vendorb.local",
            role=vendor_role,
            vendor=vendor_b,
        )
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-00012",
            vendor=vendor_a,
            cost_center=base_org_data["cc"],
            status=PurchaseOrder.STATUS_ISSUED,
            total_amount=Decimal("3000.00"),
        )
        with pytest.raises(ValidationError) as exc_info:
            acknowledge_purchase_order_service(po=po, vendor_user=user_vendor_b)
        assert "Permission Denied" in str(exc_info.value)

    # 13. DEF-013 / UT_ORD_066
    def test_def_013_cancelling_draft_po_does_not_deduct_other_pos_commitments(self, base_org_data):
        """Cancelling a DRAFT purchase order does not deduct commitments belonging to other POs."""
        pm_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        pm_user = User.objects.create(email="pm_cancel@unit.test", username="pm_cancel@unit.test", role=pm_role)
        budget = Budget.objects.create(
            cost_center=base_org_data["cc"],
            fiscal_period=base_org_data["fp"],
            allocated_amount=Decimal("10000.00"),
            committed_amount=Decimal("5000.00"),
            actual_amount=Decimal("0.00"),
        )
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Theta", "VND-2026-00018", "TINT")
        po_draft = PurchaseOrder.objects.create(
            po_number="PO-2026-00013",
            vendor=vendor,
            cost_center=base_org_data["cc"],
            status=PurchaseOrder.STATUS_DRAFT,
            total_amount=Decimal("1100.00"),
        )
        POLine.objects.create(
            po=po_draft,
            item_description="Uncommitted draft line",
            quantity=10,
            unit_price=Decimal("100.00"),
            quantity_received=0,
        )

        cancel_purchase_order_governance_service(po=po_draft, actor=pm_user, reason="Cancelled draft")

        budget.refresh_from_db()
        # Committed amount for other POs must remain exactly 5000.00
        assert budget.committed_amount == Decimal("5000.00")

    # 14. DEF-014 / UT_ORD_067
    def test_def_014_cancel_purchase_order_governance_enforces_role_check(self, base_org_data):
        """cancel_purchase_order_governance_service rejects cancellation by unauthorized roles like Requester."""
        req_role = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]
        req_user = User.objects.create(email="req_cancel@unit.test", username="req_cancel@unit.test", role=req_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Iota", "VND-2026-00019", "TINI")
        po = PurchaseOrder.objects.create(
            po_number="PO-2026-00014",
            vendor=vendor,
            cost_center=base_org_data["cc"],
            status=PurchaseOrder.STATUS_ISSUED,
            total_amount=Decimal("4000.00"),
        )
        with pytest.raises(ValidationError) as exc_info:
            cancel_purchase_order_governance_service(po=po, actor=req_user, reason="Requester attempting cancel")
        assert "Permission Denied" in str(exc_info.value)

    # 15. DEF-015 / UT_SCO_077
    def test_def_015_latest_scorecards_queryset_returns_latest_created_at(self, base_org_data):
        """latest_scorecards_queryset selects the newest scorecard by created_at per vendor."""
        eval_role = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})[0]
        evaluator = User.objects.create(email="eval_sc@unit.test", username="eval_sc@unit.test", role=eval_role)
        vendor = _make_vendor(base_org_data["org"], base_org_data["cat"], "Vendor Kappa", "VND-2026-00020", "TINK")
        # Create older scorecard
        sc_old = VendorScorecard.objects.create(
            vendor=vendor,
            evaluation_period="Q1-2026",
            evaluated_by=evaluator,
            delivery_score=Decimal("80.00"),
            quality_score=Decimal("80.00"),
            price_score=Decimal("80.00"),
            compliance_score=Decimal("80.00"),
            composite_score=Decimal("80.00"),
        )
        # Create newer scorecard
        sc_new = VendorScorecard.objects.create(
            vendor=vendor,
            evaluation_period="Q2-2026",
            evaluated_by=evaluator,
            delivery_score=Decimal("95.00"),
            quality_score=Decimal("95.00"),
            price_score=Decimal("95.00"),
            compliance_score=Decimal("95.00"),
            composite_score=Decimal("95.00"),
        )
        # Explicitly ensure created_at ordering
        VendorScorecard.objects.filter(id=sc_old.id).update(created_at=timezone.now() - datetime.timedelta(days=10))
        VendorScorecard.objects.filter(id=sc_new.id).update(created_at=timezone.now())

        latest_qs = latest_scorecards_queryset()
        scorecard_ids = list(latest_qs.values_list("id", flat=True))

        assert sc_new.id in scorecard_ids
        assert sc_old.id not in scorecard_ids

    # 16. DEF-016 / UT_SRC_069
    def test_def_016_parse_weight_rejects_nan_with_validation_error(self):
        """_parse_weight raises ValidationError (not decimal.InvalidOperation) for NaN."""
        with pytest.raises(ValidationError) as exc_info:
            _parse_weight("NaN", "Technical weight")
        assert "Technical weight" in str(exc_info.value)
        assert "between 0 and 100" in str(exc_info.value)

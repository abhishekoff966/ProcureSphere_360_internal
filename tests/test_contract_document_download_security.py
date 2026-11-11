import io
import os
import zipfile
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.contracts.models import ContractDocument
from apps.contracts.services import create_contract_service, upload_contract_document_service
from apps.organization.models import Department, Organization
from apps.vendors.models import VendorCategory
from apps.vendors.services import register_vendor_service


def _role(code, name):
    role, _ = Role.objects.get_or_create(code=code, defaults={"name": name})
    return role


@pytest.fixture
def contract_doc_sec_world(db, tmp_path):
    org_a = Organization.objects.create(name="Org Alpha", code="ORGA", is_active=True)
    org_b = Organization.objects.create(name="Org Beta", code="ORGB", is_active=True)
    dept_a = Department.objects.create(organization=org_a, name="Legal Alpha", code="LEG-A")
    dept_b = Department.objects.create(organization=org_b, name="Legal Beta", code="LEG-B")

    roles = {
        Role.SUPER_ADMIN: _role(Role.SUPER_ADMIN, "Super Admin"),
        Role.LEGAL_MGR: _role(Role.LEGAL_MGR, "Legal Manager"),
        Role.PROC_MGR: _role(Role.PROC_MGR, "Procurement Manager"),
        Role.PROC_EXEC: _role(Role.PROC_EXEC, "Procurement Executive"),
        Role.FINANCE_AP: _role(Role.FINANCE_AP, "Finance AP"),
        Role.AUDITOR: _role(Role.AUDITOR, "Auditor"),
        Role.REQUESTER: _role(Role.REQUESTER, "Requester"),
        Role.DEPT_APPROVER: _role(Role.DEPT_APPROVER, "Department Approver"),
        Role.STORES_RECEIVER: _role(Role.STORES_RECEIVER, "Stores Receiver"),
        Role.VENDOR_USER: _role(Role.VENDOR_USER, "Vendor User"),
    }

    legal_a = User.objects.create_user(
        email="legal.a@sec.test",
        password="Password123!",
        role=roles[Role.LEGAL_MGR],
        organization=org_a,
        department=dept_a,
    )
    legal_b = User.objects.create_user(
        email="legal.b@sec.test",
        password="Password123!",
        role=roles[Role.LEGAL_MGR],
        organization=org_b,
        department=dept_b,
    )
    proc_mgr_a = User.objects.create_user(
        email="procmgr.a@sec.test",
        password="Password123!",
        role=roles[Role.PROC_MGR],
        organization=org_a,
        department=dept_a,
    )
    proc_exec_a = User.objects.create_user(
        email="procexec.a@sec.test",
        password="Password123!",
        role=roles[Role.PROC_EXEC],
        organization=org_a,
        department=dept_a,
    )
    finance_a = User.objects.create_user(
        email="finance.a@sec.test",
        password="Password123!",
        role=roles[Role.FINANCE_AP],
        organization=org_a,
        department=dept_a,
    )
    auditor_a = User.objects.create_user(
        email="auditor.a@sec.test",
        password="Password123!",
        role=roles[Role.AUDITOR],
        organization=org_a,
        department=dept_a,
    )
    super_admin = User.objects.create_user(
        email="superadmin@sec.test",
        password="Password123!",
        role=roles[Role.SUPER_ADMIN],
    )
    requester_a = User.objects.create_user(
        email="requester.a@sec.test",
        password="Password123!",
        role=roles[Role.REQUESTER],
        organization=org_a,
        department=dept_a,
    )
    approver_a = User.objects.create_user(
        email="approver.a@sec.test",
        password="Password123!",
        role=roles[Role.DEPT_APPROVER],
        organization=org_a,
        department=dept_a,
    )
    stores_a = User.objects.create_user(
        email="stores.a@sec.test",
        password="Password123!",
        role=roles[Role.STORES_RECEIVER],
        organization=org_a,
        department=dept_a,
    )
    no_role_user = User.objects.create_user(
        email="norole@sec.test",
        password="Password123!",
    )

    category, _ = VendorCategory.objects.get_or_create(
        code="CAT-SEC-01", defaults={"name": "Security Hardware"}
    )
    vendor_a = register_vendor_service(
        legal_name="Vendor Alpha & Partners LLC",
        tax_identification_number="TAX-ALPHA-01",
        category=category,
        email="vendor.a@sec.test",
        address="1 Alpha Way",
    )
    vendor_a.organization = org_a
    vendor_a.save(update_fields=["organization"])

    vendor_b = register_vendor_service(
        legal_name="Vendor Beta Corp",
        tax_identification_number="TAX-BETA-01",
        category=category,
        email="vendor.b@sec.test",
        address="2 Beta Way",
    )
    vendor_b.organization = org_b
    vendor_b.save(update_fields=["organization"])

    vendor_user_a = User.objects.create_user(
        email="vuser.a@sec.test",
        password="Password123!",
        role=roles[Role.VENDOR_USER],
        vendor=vendor_a,
    )
    vendor_user_b = User.objects.create_user(
        email="vuser.b@sec.test",
        password="Password123!",
        role=roles[Role.VENDOR_USER],
        vendor=vendor_b,
    )

    today = timezone.now().date()
    contract_a = create_contract_service(
        title="R&D Master Services <Agreement>",
        vendor=vendor_a,
        contract_value=Decimal("100000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=365),
        renewal_notice_days=30,
        contract_owner=legal_a,
    )
    contract_b = create_contract_service(
        title="Beta Exclusive Contract",
        vendor=vendor_b,
        contract_value=Decimal("50000.00"),
        start_date=today,
        end_date=today + timezone.timedelta(days=180),
        renewal_notice_days=30,
        contract_owner=legal_b,
    )

    with override_settings(MEDIA_ROOT=tmp_path):
        doc_a = upload_contract_document_service(
            contract=contract_a,
            user=legal_a,
            title="Signed SLA & Terms <v1>",
            file=SimpleUploadedFile(
                "signed_sla_alpha.pdf",
                b"%PDF-1.4\nAlpha Confidential Contract Body",
                content_type="application/pdf",
            ),
        )
        doc_b = upload_contract_document_service(
            contract=contract_b,
            user=legal_b,
            title="Beta Executed MSA",
            file=SimpleUploadedFile(
                "signed_msa_beta.pdf",
                b"%PDF-1.4\nBeta Confidential Contract Body",
                content_type="application/pdf",
            ),
        )

    return {
        "media_root": tmp_path,
        "org_a": org_a,
        "org_b": org_b,
        "legal_a": legal_a,
        "legal_b": legal_b,
        "proc_mgr_a": proc_mgr_a,
        "proc_exec_a": proc_exec_a,
        "finance_a": finance_a,
        "auditor_a": auditor_a,
        "super_admin": super_admin,
        "requester_a": requester_a,
        "approver_a": approver_a,
        "stores_a": stores_a,
        "no_role_user": no_role_user,
        "vendor_user_a": vendor_user_a,
        "vendor_user_b": vendor_user_b,
        "contract_a": contract_a,
        "contract_b": contract_b,
        "doc_a": doc_a,
        "doc_b": doc_b,
    }


@pytest.mark.django_db
def test_contract_detail_renders_authorized_download_url_not_raw_media_url(
    client, contract_doc_sec_world
):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]
    doc_a = w["doc_a"]
    expected_url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
    )

    with override_settings(MEDIA_ROOT=w["media_root"]):
        client.force_login(w["legal_a"])
        resp = client.get(reverse("contract_detail", kwargs={"contract_id": contract_a.id}))
        assert resp.status_code == 200
        html = resp.content.decode()
        assert expected_url in html
        assert doc_a.file.url not in html


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor_key",
    ["legal_a", "proc_mgr_a", "proc_exec_a", "finance_a", "auditor_a", "super_admin"],
)
def test_authorized_roles_can_download_permitted_contract_document_and_audit_logged(
    client, contract_doc_sec_world, actor_key
):
    w = contract_doc_sec_world
    actor = w[actor_key]
    contract_a = w["contract_a"]
    doc_a = w["doc_a"]
    url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
    )

    with override_settings(MEDIA_ROOT=w["media_root"]):
        client.force_login(actor)
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp["Content-Disposition"].startswith("attachment")
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        assert b"Alpha Confidential Contract Body" in body

    assert AuditLog.objects.filter(
        actor=actor,
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "actor_key",
    [
        "requester_a",
        "approver_a",
        "stores_a",
        "vendor_user_a",
        "vendor_user_b",
        "legal_b",
        "no_role_user",
    ],
)
def test_unauthorized_users_receive_403_and_no_audit_log(
    client, contract_doc_sec_world, actor_key
):
    w = contract_doc_sec_world
    actor = w[actor_key]
    contract_a = w["contract_a"]
    doc_a = w["doc_a"]
    url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
    )

    before_count = AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).count()

    with override_settings(MEDIA_ROOT=w["media_root"]):
        client.force_login(actor)
        resp = client.get(url)
        assert resp.status_code == 403

    after_count = AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).count()
    assert after_count == before_count


@pytest.mark.django_db
def test_unauthenticated_download_redirects_to_login(client, contract_doc_sec_world):
    w = contract_doc_sec_world
    url = reverse(
        "contract_document_download",
        kwargs={"contract_id": w["contract_a"].id, "document_id": w["doc_a"].id},
    )
    resp = client.get(url)
    assert resp.status_code == 302
    assert "/login/" in resp["Location"]


@pytest.mark.django_db
def test_cross_contract_document_access_blocked_web_and_api(client, contract_doc_sec_world):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]
    doc_b = w["doc_b"]

    # Super admin / authorized legal user trying to mix contract_a with doc_b -> 404
    client.force_login(w["super_admin"])
    web_cross_url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": doc_b.id},
    )
    resp_web = client.get(web_cross_url)
    assert resp_web.status_code == 404

    api_client = APIClient()
    api_client.force_authenticate(user=w["super_admin"])
    resp_api = api_client.get(f"/api/v1/contracts/{contract_a.id}/documents/{doc_b.id}/download/")
    assert resp_api.status_code == 404

    # Unauthorized user tampering with cross-contract document URL on contract_a still gets 403
    client.force_login(w["requester_a"])
    resp_unauth = client.get(web_cross_url)
    assert resp_unauth.status_code == 403

    assert not AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_b.id),
    ).exists()


@pytest.mark.django_db
def test_missing_physical_file_serves_controlled_pdf_archive_notice_without_overwriting_disk(
    client, contract_doc_sec_world
):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]
    docx_buf = io.BytesIO()
    with zipfile.ZipFile(docx_buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types></Types>")
        zf.writestr("word/document.xml", "<w:document/>")

    with override_settings(MEDIA_ROOT=w["media_root"]):
        docx_doc = upload_contract_document_service(
            contract=contract_a,
            user=w["legal_a"],
            title="SOW & Annexure <2026>",
            file=SimpleUploadedFile(
                "sow_annexure.docx",
                docx_buf.getvalue(),
                content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            ),
        )
        physical_path = docx_doc.file.path
        assert os.path.exists(physical_path)
        os.remove(physical_path)
        assert not os.path.exists(physical_path)

        client.force_login(w["legal_a"])
        url = reverse(
            "contract_document_download",
            kwargs={"contract_id": contract_a.id, "document_id": docx_doc.id},
        )
        resp = client.get(url)
        assert resp.status_code == 200
        assert resp["Content-Type"] == "application/pdf"
        assert "archive_record.pdf" in resp["Content-Disposition"]
        body = b"".join(resp.streaming_content) if resp.streaming else resp.content
        assert body.startswith(b"%PDF")
        # Must NOT write fake PDF bytes over a missing .docx path
        assert not os.path.exists(physical_path)

    log = AuditLog.objects.filter(
        actor=w["legal_a"],
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(docx_doc.id),
    ).first()
    assert log is not None
    assert log.new_state.get("storage_fallback") is True


@pytest.mark.django_db
def test_empty_file_field_and_storage_errors_return_controlled_responses_without_audit_log(
    client, contract_doc_sec_world
):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]
    doc_a = w["doc_a"]

    empty_doc = ContractDocument.objects.create(
        contract=contract_a,
        title="Empty Reference Doc",
        file="",
        uploaded_by=w["legal_a"],
    )

    client.force_login(w["legal_a"])
    empty_url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": empty_doc.id},
    )
    resp_empty = client.get(empty_url)
    assert resp_empty.status_code == 404
    assert not AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(empty_doc.id),
    ).exists()

    valid_url = reverse(
        "contract_document_download",
        kwargs={"contract_id": contract_a.id, "document_id": doc_a.id},
    )
    before_logs = AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).count()

    with override_settings(MEDIA_ROOT=w["media_root"]):
        with patch("django.db.models.fields.files.FieldFile.open", side_effect=OSError("Storage offline")):
            resp_web_err = client.get(valid_url)
            assert resp_web_err.status_code == 503

            api_client = APIClient()
            api_client.force_authenticate(user=w["legal_a"])
            resp_api_err = api_client.get(
                f"/api/v1/contracts/{contract_a.id}/documents/{doc_a.id}/download/"
            )
            assert resp_api_err.status_code == 503
            assert resp_api_err.json()["error"]["code"] == "STORAGE_ERROR"

    after_logs = AuditLog.objects.filter(
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).count()
    assert after_logs == before_logs


@pytest.mark.django_db
def test_document_upload_listing_and_download_regression(client, contract_doc_sec_world):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]

    with override_settings(MEDIA_ROOT=w["media_root"]):
        client.force_login(w["legal_a"])
        upload_url = reverse("contract_document_upload", kwargs={"contract_id": contract_a.id})
        uploaded = SimpleUploadedFile(
            "addendum_2026.pdf",
            b"%PDF-1.4\nAddendum Executed Payload",
            content_type="application/pdf",
        )
        resp_up = client.post(
            upload_url,
            {"title": "Executed Addendum 2026", "file": uploaded},
            follow=True,
        )
        assert resp_up.status_code == 200
        html = resp_up.content.decode()
        assert "Executed Addendum 2026" in html

        new_doc = ContractDocument.objects.get(contract=contract_a, title="Executed Addendum 2026")
        dl_url = reverse(
            "contract_document_download",
            kwargs={"contract_id": contract_a.id, "document_id": new_doc.id},
        )
        assert dl_url in html

        resp_dl = client.get(dl_url)
        assert resp_dl.status_code == 200
        body = b"".join(resp_dl.streaming_content) if resp_dl.streaming else resp_dl.content
        assert b"Addendum Executed Payload" in body


@pytest.mark.django_db
def test_api_contract_document_download_rbac_and_missing_file(contract_doc_sec_world):
    w = contract_doc_sec_world
    contract_a = w["contract_a"]
    doc_a = w["doc_a"]
    api_url = f"/api/v1/contracts/{contract_a.id}/documents/{doc_a.id}/download/"

    api_client = APIClient()

    # 1. Authorized internal role can download via REST API & creates AuditLog
    api_client.force_authenticate(user=w["legal_a"])
    with override_settings(MEDIA_ROOT=w["media_root"]):
        resp_ok = api_client.get(api_url)
        assert resp_ok.status_code == 200
        assert resp_ok["Content-Disposition"].startswith("attachment")
        body = b"".join(resp_ok.streaming_content) if resp_ok.streaming else resp_ok.content
        assert b"Alpha Confidential Contract Body" in body

    assert AuditLog.objects.filter(
        actor=w["legal_a"],
        action=AuditLog.ACTION_EXPORT,
        target_model="ContractDocument",
        target_object_id=str(doc_a.id),
    ).exists()

    # 2. Unauthorized role & cross-org user cannot download via API
    api_client.force_authenticate(user=w["requester_a"])
    assert api_client.get(api_url).status_code == 403

    api_client.force_authenticate(user=w["legal_b"])
    assert api_client.get(api_url).status_code in [403, 404]

    # 3. Missing physical file returns controlled 404 FILE_NOT_FOUND JSON without extra AuditLog
    with override_settings(MEDIA_ROOT=w["media_root"]):
        os.remove(doc_a.file.path)
        before = AuditLog.objects.filter(
            action=AuditLog.ACTION_EXPORT,
            target_model="ContractDocument",
            target_object_id=str(doc_a.id),
        ).count()
        api_client.force_authenticate(user=w["legal_a"])
        resp_missing = api_client.get(api_url)
        assert resp_missing.status_code == 404
        assert resp_missing.json()["error"]["code"] == "FILE_NOT_FOUND"
        after = AuditLog.objects.filter(
            action=AuditLog.ACTION_EXPORT,
            target_model="ContractDocument",
            target_object_id=str(doc_a.id),
        ).count()
        assert after == before


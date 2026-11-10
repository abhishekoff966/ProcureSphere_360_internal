import pytest
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from apps.accounts.models import Role, User
from apps.vendors.models import Vendor, VendorCategory, VendorDocument, VendorCredentialDispatch

pytestmark = [pytest.mark.django_db]


@pytest.fixture
def test_setup():
    legal_role, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    legal_user = User.objects.create_user(
        email="legal.tester@hpe.com",
        password="Password123!",
        role=legal_role,
    )
    category, _ = VendorCategory.objects.get_or_create(code="CAT-HW-01", defaults={"name": "Hardware"})
    vendor = Vendor.objects.create(
        legal_name="Violet Technologies",
        vendor_number="VND-2026-00025",
        tax_identification_number="TAX-VIOLET-025",
        category=category,
        status=Vendor.STATUS_SUBMITTED,
    )
    # Document with non-existent file path simulating ephemeral storage restart
    doc = VendorDocument.objects.create(
        vendor=vendor,
        document_type=VendorDocument.DOC_TYPE_PAN,
        title="Permanent Account Number (PAN) Card",
        file="vendor_documents/2026/11/missing_pan_card.pdf",
    )
    vendor_role, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor"})
    vendor_user = User.objects.create_user(
        email="vendor.violet@test.com",
        password="Password123!",
        role=vendor_role,
    )
    dispatch = VendorCredentialDispatch.objects.create(
        vendor=vendor,
        user=vendor_user,
        sent_to="vendor.violet@test.com",
        created_by=legal_user,
        legal_verification_status="PENDING",
    )
    return {
        "legal_user": legal_user,
        "vendor": vendor,
        "doc": doc,
        "dispatch": dispatch,
    }


def test_bug_016_reports_hub_mandatory_indicator(client, test_setup):
    """BUG-016: Reports Hub must clearly display a red asterisk (*) on the Select Report Module label."""
    client.force_login(test_setup["legal_user"])
    resp = client.get("/reports/hub/")
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Select Report Module" in html
    # Mandatory red asterisk indicator must be present on label
    assert 'Select Report Module <span class="text-danger ms-1">*</span>' in html or 'Select Report Module *' in html
    assert 'name="report_type"' in html
    assert 'required' in html


def test_bug_017_vendor_document_download_does_not_404_when_file_missing(client, test_setup):
    """
    BUG-017: Clicking View/Download on a KYC document must stream a valid PDF record
    without raising 404, even if the physical file is absent from ephemeral storage.
    """
    client.force_login(test_setup["legal_user"])
    vendor = test_setup["vendor"]
    doc = test_setup["doc"]

    url = reverse("vendor_document_download", kwargs={"vendor_id": vendor.id, "doc_id": doc.id})
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "application/pdf"
    # PDF signature bytes
    pdf_bytes = b"".join(resp.streaming_content)
    assert pdf_bytes.startswith(b"%PDF")


def test_bug_017_session_persists_on_marketing_and_error_pages(client, test_setup):
    """
    BUG-017: Authenticated user sessions must NOT be destroyed when visiting marketing URLs
    or using browser back navigation. Error pages must link to /dashboard/ for authenticated users.
    """
    client.force_login(test_setup["legal_user"])

    # 1. Visiting root marketing page must NOT log the user out
    resp = client.get("/")
    assert resp.status_code == 200
    # Session cookie must persist
    assert client.session.get("_auth_user_id") is not None

    # 2. Visiting other marketing pages must NOT log the user out
    for path in ["/about/", "/solutions/", "/benefits/", "/process/", "/faq/"]:
        resp = client.get(path)
        assert resp.status_code == 200
        assert client.session.get("_auth_user_id") is not None

    # 3. 404 error page must provide a Return to Dashboard link without forcing sign-out
    resp_404 = client.get("/non-existent-module-path/")
    assert resp_404.status_code == 404
    html_404 = resp_404.content.decode()
    assert "/dashboard/" in html_404
    assert "Return to Dashboard" in html_404
    # User must remain authenticated after viewing error page
    assert client.session.get("_auth_user_id") is not None


def test_bug_018_modal_layout_and_mandatory_indicators(client, test_setup):
    """
    BUG-018:
    1. Rejection & KYC modals in legal dashboard must be positioned cleanly outside table-responsive.
    2. Rejection notes label must display a red asterisk (*) instead of '(Mandatory)' text.
    """
    client.force_login(test_setup["legal_user"])
    resp = client.get("/legal/")
    assert resp.status_code == 200
    html = resp.content.decode()

    # Asterisk instead of (Mandatory)
    assert "(Mandatory)" not in html
    assert 'Reviewer Notes &amp; Feedback <span class="text-danger">*</span>' in html or 'Reviewer Notes & Feedback <span class="text-danger">*</span>' in html

    # Verify modal is defined after table-responsive
    table_resp_pos = html.find('id="vendor-credential-verification-queue"')
    assert table_resp_pos != -1
    table_close = html.find('</table>', table_resp_pos)
    assert table_close != -1
    modal_pos = html.find(f'id="kycDocReviewModal{test_setup["dispatch"].id}"')
    assert modal_pos != -1
    # Modal must be located after the table, not nested inside <td>
    assert modal_pos > table_close

from django.core.files.uploadedfile import SimpleUploadedFile

from apps.core.validators import validate_pr_attachment


def test_tc_cpr_018_validate_pr_attachment_with_non_zero_stream_pointer():
    """
    TC-CPR-018 / DEF-001:
    Valid PDF whose stream pointer is not at byte 0 (e.g. already partly read by an earlier consumer)
    is uploaded as a PR attachment.

    Steps:
      1. Build a valid PDF upload (starts with %PDF-).
      2. Advance the file pointer (file.seek(4)).
      3. Call validate_pr_attachment(file).

    Expected:
      File accepted: signature check must read from the start of the file (seek(0)),
      and restore pointer afterwards.
    """
    valid_pdf = SimpleUploadedFile(
        "sample_requisition_document.pdf",
        b"%PDF-1.4\nTest PDF content with stream pointer initially advanced.",
        content_type="application/pdf",
    )
    # 2. Advance the file pointer
    valid_pdf.seek(4)
    assert valid_pdf.tell() == 4

    # 3. Call validate_pr_attachment(file)
    result = validate_pr_attachment(valid_pdf)

    # Expected: File accepted, not raising ValidationError('Invalid file format.')
    assert result == valid_pdf
    # File pointer is restored to initial position afterwards
    assert valid_pdf.tell() == 4


def test_tc_cpr_018_validate_pr_attachment_doc_and_docx_with_advanced_pointer():
    """
    Verifies stream pointer restoration for DOC and DOCX attachments as well.
    """
    valid_doc = SimpleUploadedFile(
        "sample.doc",
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1\x00\x00BinaryDocContent",
        content_type="application/msword",
    )
    valid_doc.seek(6)
    assert valid_doc.tell() == 6
    assert validate_pr_attachment(valid_doc) == valid_doc
    assert valid_doc.tell() == 6

    valid_docx = SimpleUploadedFile(
        "sample.docx",
        b"PK\x03\x04\x14\x00\x00\x00OOXMLDocxContent",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    valid_docx.seek(3)
    assert valid_docx.tell() == 3
    assert validate_pr_attachment(valid_docx) == valid_docx
    assert valid_docx.tell() == 3

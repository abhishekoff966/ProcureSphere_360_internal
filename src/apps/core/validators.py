import os
import re
import zipfile

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "doc", "docx", "xlsx", "csv"}
PR_ATTACHMENT_EXTENSIONS = {"pdf", "doc", "docx"}
MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10MB

# Indian Tax Identifier Validation Patterns
PAN_REGEX = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
GSTIN_REGEX = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def normalize_tax_identifier(val) -> str:
    """Trim whitespace and uppercase tax identifier strings."""
    if not val:
        return ""
    return str(val).strip().upper()


def is_valid_pan(pan) -> bool:
    """Returns True if normalized string matches PAN regex."""
    if not pan:
        return False
    norm = normalize_tax_identifier(pan)
    return bool(PAN_REGEX.match(norm))


def is_valid_gstin(gstin) -> bool:
    """Returns True if normalized string matches GSTIN regex."""
    if not gstin:
        return False
    norm = normalize_tax_identifier(gstin)
    return bool(GSTIN_REGEX.match(norm))


def extract_pan_from_gstin(gstin) -> str | None:
    """Extract embedded 10-character PAN from valid GSTIN (characters 3-12 / index 2-12)."""
    if not gstin:
        return None
    norm = normalize_tax_identifier(gstin)
    if is_valid_gstin(norm):
        return norm[2:12]
    return None


def validate_pan_number(value: str) -> str:
    """
    Validates PAN format: ^[A-Z]{5}[0-9]{4}[A-Z]{1}$.
    Returns normalized PAN string.
    Raises ValidationError if invalid non-empty value provided.
    """
    if not value or not str(value).strip():
        return ""
    norm = normalize_tax_identifier(value)
    if not PAN_REGEX.match(norm):
        raise ValidationError(
            _("Invalid PAN format. PAN must be 10 characters: 5 uppercase letters, 4 digits, 1 uppercase letter (e.g. ABCDE1234F).")
        )
    return norm


def validate_gstin(value: str) -> str:
    """
    Validates GSTIN format: ^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$.
    Returns normalized GSTIN string.
    Raises ValidationError if invalid non-empty value provided.
    """
    if not value or not str(value).strip():
        return ""
    norm = normalize_tax_identifier(value)
    if not GSTIN_REGEX.match(norm):
        raise ValidationError(
            _("Invalid GSTIN format. GSTIN must be 15 alphanumeric characters (e.g. 29ABCDE1234F1Z5).")
        )
    return norm


def validate_pan_gstin_consistency(pan: str, gstin: str) -> None:
    """
    When both PAN and GSTIN are provided, verify that the PAN embedded
    in the GSTIN (characters 3-12) matches the separate PAN field.
    """
    norm_pan = normalize_tax_identifier(pan)
    norm_gstin = normalize_tax_identifier(gstin)
    if norm_pan and norm_gstin and is_valid_gstin(norm_gstin):
        embedded_pan = norm_gstin[2:12]
        if norm_pan != embedded_pan:
            raise ValidationError(
                _(
                    f"GSTIN '{norm_gstin}' contains embedded PAN '{embedded_pan}' "
                    f"which does not match the separate PAN '{norm_pan}'."
                )
            )




CORPORATE_NAME_SUFFIXES = re.compile(
    r"\b(Inc|Incorporated|Corp|Corporation|Ltd|Limited|LLC|LC|Pvt|Private|LLP|GmbH|Co|Company|Enterprise|Enterprises|Solutions|Technologies|Services|Systems|International|Group|Industries|Holdings|Ventures|Labs|Logistics|Networks|Security|Associates|Consulting|Agency|Desk|Supply|Supplies)\b",
    re.IGNORECASE,
)

TAX_ID_PATTERN = re.compile(
    r"^(?:"
    r"[A-Za-z]{2,6}(?:-[A-Za-z0-9]+){1,3}|"
    r"\d{2}-\d{7}|"
    r"[A-Za-z]{5}\d{4}[A-Za-z]{1}|"
    r"\d{2}[A-Za-z]{5}\d{4}[A-Za-z]{1}[1-9A-Za-z]{1}Z[0-9A-Za-z]{1}|"
    r"[A-Za-z]{2}\d{8,12}"
    r")$",
    re.IGNORECASE,
)


def validate_legal_name(value: str) -> str:
    """
    Validates corporate legal name standards:
    - Must be between 3 and 200 characters.
    - Must contain alphabetic characters.
    - Cannot contain character-digit soup (e.g. tdgdtgd12).
    - Cannot be a single random word without a recognized corporate entity suffix (e.g. rejects 'sameer', 'ahdqhh', 'fsacdare').
    """
    val = (value or "").strip()
    if len(val) < 3 or len(val) > 200:
        raise ValidationError(_("Legal name must be between 3 and 200 characters."))
    if not any(c.isalpha() for c in val):
        raise ValidationError(_("Legal name must contain alphabetic characters."))
    if re.search(r"[^A-Za-z0-9\s\.&,'\-/()]", val):
        raise ValidationError(
            _("Legal name contains invalid special characters.")
        )
    if re.search(r"(?:[a-zA-Z]{2,}\d+|\d+[a-zA-Z]{2,})", val):
        raise ValidationError(
            _("Legal name cannot contain arbitrary character-digit soup (e.g. tdgdtgd12).")
        )
    if re.search(r"\b\d{4,}\b", val):
        raise ValidationError(
            _("Legal name cannot contain long numeric sequences (e.g. 12345678).")
        )
    for token in re.findall(r"[a-zA-Z]+", val):
        if len(token) >= 4 and not re.search(r"[aeiouy]", token, re.IGNORECASE):
            raise ValidationError(
                _("Legal name contains invalid or unvocalized character patterns.")
            )
    words = val.split()
    if len(words) < 2 and not CORPORATE_NAME_SUFFIXES.search(val):
        raise ValidationError(
            _(
                "Legal name must be a corporate business name (e.g. Acme Corp, Cisco Systems Inc) or contain a recognized business entity suffix (Ltd, Inc, LLC, Corp, Solutions)."
            )
        )
    return val


def validate_tax_id_number(value: str) -> str:
    """
    Validates Tax Identification Number:
    - Must be between 5 and 30 characters.
    - No consecutive hyphens (--) or slashes (//) or leading/trailing hyphens.
    - Pure raw unformatted digits (e.g. 142423153 or 1234567890) are rejected.
    - Must follow standard format (e.g. TAX-12345678, US EIN 12-3456789, PAN, or GSTIN).
    """
    val = (value or "").strip()
    if not val:
        raise ValidationError(_("Tax identification number is required."))
    if len(val) < 5 or len(val) > 30:
        raise ValidationError(
            _("Tax identification number must be between 6 and 30 characters.")
        )
    if "--" in val or "//" in val or val.startswith("-") or val.endswith("-"):
        raise ValidationError(
            _("Tax identification number cannot contain consecutive hyphens (--) or leading/trailing hyphens.")
        )
    if val.isdigit():
        raise ValidationError(
            _("Tax ID cannot be a raw unformatted number. Must follow a standard formatted tax identifier: e.g. TAX-12345678, US EIN (12-3456789), PAN, or GSTIN.")
        )
    if not TAX_ID_PATTERN.match(val):
        raise ValidationError(
            _("Invalid Tax ID format. Use standard format: e.g. TAX-12345678, US EIN (12-3456789), PAN (ABCDE1234F), or GSTIN (29ABCDE1234F1Z5).")
        )
    return val


def validate_optional_phone_number(value: str) -> str:
    r"""
    Validates optional vendor phone number:
    - Blank / whitespace -> returns "".
    - Strictly rejects alphabetic characters (e.g. 'abcde12345') or invalid punctuation.
    - Rejects consecutive dashes '--' or invalid separators.
    - Enforces strictly 10 subscriber digits (with optional +91 / 91 country prefix), starting with 6-9:
      * 9876543210 -> +919876543210
      * +91 9876543210 / 919876543210 -> +919876543210
      * Rejects 7-digit, 9-digit (even with +91, e.g. +91799358617), 11-digit, 13-digit, etc.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.search(r"[A-Za-z]", raw):
        raise ValidationError(
            _("Phone number cannot contain letters or alphabetic characters.")
        )
    if not re.match(r"^[0-9+\s\-().]+$", raw):
        raise ValidationError(
            _("Phone number contains invalid characters. Enter a valid 10-digit mobile number (e.g. 9876543210 or +91 9876543210).")
        )
    if "--" in raw or ".." in raw or raw.endswith("-") or raw.startswith("-"):
        raise ValidationError(
            _("Invalid phone number format. Enter a valid 10-digit mobile number (e.g. 9876543210 or +91 9876543210).")
        )

    digits = re.sub(r"\D", "", raw)
    compact = re.sub(r"[\s\-().]", "", raw)

    # Extract 10 subscriber digits (allow bare 10-digit OR +91 / 91 followed by 10 digits)
    if compact.startswith("+91"):
        subscriber = compact[3:]
    elif compact.startswith("91") and len(digits) == 12:
        subscriber = digits[2:]
    elif compact.startswith("+"):
        raise ValidationError(
            _("Phone number must be 10 digits (e.g. 9876543210 or +91 9876543210).")
        )
    else:
        subscriber = digits

    if not re.match(r"^\d{10}$", subscriber):
        raise ValidationError(
            _("Phone number must contain exactly 10 digits (e.g. 9876543210 or +91 9876543210).")
        )
    if len(set(subscriber)) == 1:
        raise ValidationError(_("Please enter a valid, non-repeating 10-digit mobile number."))
    if not re.match(r"^[6-9]\d{9}$", subscriber):
        raise ValidationError(
            _("10-digit mobile number must start with 6, 7, 8, or 9 (e.g. 9876543210).")
        )
    return "+91" + subscriber

def check_upload_rate_limit(key: str, max_requests: int = 10, window_seconds: int = 60):
    """
    Validates upload rate limiting using Django's caching backend.
    Tracks number of upload attempts per key within the time window.
    Zero external dependencies required.
    """
    cache_key = f"upload_ratelimit_{key}"
    try:
        current_count = cache.get(cache_key)
        if current_count is None:
            cache.set(cache_key, 1, timeout=window_seconds)
        elif current_count >= max_requests:
            raise ValidationError(
                _("Upload rate limit exceeded. Please wait a moment before uploading again.")
            )
        else:
            try:
                cache.incr(cache_key)
            except Exception:
                cache.set(cache_key, current_count + 1, timeout=window_seconds)
    except ValidationError:
        raise
    except Exception:
        # If cache backend is unavailable, fail open to avoid blocking legitimate operations
        pass


def validate_file_upload(file, allowed_extensions=None):
    """
    Validates uploaded file size, extension, and binary content signature.
    Enforces:
    1. Rejects missing and zero-byte files.
    2. Enforces maximum file size limit (10MB).
    3. Permitted extension whitelist.
    4. Magic-byte / content signature validation corresponding to the extension.
    5. Structural verification for ZIP-based formats (DOCX, XLSX).
    6. Text & null-byte safety verification for CSV.
    7. File pointer preservation before and after inspection.
    """
    if not file:
        raise ValidationError(_("No file was provided."))

    # 1. Non-empty check
    file_size = getattr(file, "size", None)
    if file_size is None or file_size == 0:
        raise ValidationError(_("Uploaded file is empty. Zero-byte files are not permitted."))

    # 2. Maximum file size check
    if file_size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(_("File size exceeds maximum allowed limit of 10MB."))

    # 3. Extension check
    file_name = getattr(file, "name", "")
    if not file_name or "." not in file_name:
        raise ValidationError(_("File must have a valid extension."))

    ext = os.path.splitext(file_name)[1][1:].lower()
    permitted_extensions = set(allowed_extensions) if allowed_extensions is not None else ALLOWED_EXTENSIONS
    if ext not in permitted_extensions:
        raise ValidationError(
            _(
                f"File extension '.{ext}' is not permitted. Allowed: {', '.join(sorted(permitted_extensions))}"
            )
        )

    # 3b. MIME type check if content_type is provided (VEN-07)
    content_type = getattr(file, "content_type", None)
    if content_type:
        raw_mime = content_type.split(";")[0].strip().lower()
        generic_fallbacks = {"application/octet-stream", "text/plain"}
        allowed_mimes = {
            "pdf": {"application/pdf"},
            "png": {"image/png"},
            "jpg": {"image/jpeg", "image/pjpeg"},
            "jpeg": {"image/jpeg", "image/pjpeg"},
            "doc": {"application/msword", "application/x-msword"},
            "docx": {
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "application/zip",
            },
            "xlsx": {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "application/zip",
            },
            "xls": {"application/vnd.ms-excel", "application/msexcel"},
            "csv": {"text/csv", "application/csv", "application/vnd.ms-excel"},
            "txt": {"text/plain"},
        }.get(ext, set())
        valid_mimes = allowed_mimes | generic_fallbacks
        if allowed_mimes and raw_mime not in valid_mimes:
            raise ValidationError(
                _(f"Invalid MIME type '{content_type}' for .{ext} file.")
            )

    # 4. Binary signature & content validation (with pointer preservation)
    initial_pos = None
    if hasattr(file, "tell"):
        try:
            initial_pos = file.tell()
        except (AttributeError, OSError):
            initial_pos = None

    if hasattr(file, "seek"):
        try:
            file.seek(0)
        except (AttributeError, OSError):
            pass

    try:
        if ext == "pdf":
            header = file.read(8)
            if not header.startswith(b"%PDF"):
                raise ValidationError(
                    _("Invalid file content for .pdf. The file is not a valid PDF document.")
                )

        elif ext == "png":
            header = file.read(8)
            if not header.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValidationError(
                    _("Invalid file content for .png. The file is not a valid PNG image.")
                )

        elif ext in ("jpg", "jpeg"):
            header = file.read(3)
            if not header.startswith(b"\xff\xd8\xff"):
                raise ValidationError(
                    _("Invalid file content for JPEG. The file is not a valid JPEG image.")
                )

        elif ext in ("doc", "xls"):
            header = file.read(8)
            if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
                raise ValidationError(
                    _("Invalid file content for .doc. The file is not a valid Word document.")
                )

        elif ext in ("docx", "xlsx"):
            # Check standard PK zip header first
            header = file.read(4)
            if not header.startswith(b"PK\x03\x04"):
                raise ValidationError(
                    _(
                        f"Invalid file content for .{ext}. The file does not have a valid ZIP/OpenXML container."
                    )
                )
            # Safely validate OpenXML ZIP directory structure without extracting contents
            if hasattr(file, "seek"):
                file.seek(0)
            try:
                with zipfile.ZipFile(file, "r") as zf:
                    names = zf.namelist()
                    if "[Content_Types].xml" not in names:
                        raise ValidationError(
                            _(
                                f"Invalid file content for .{ext}. Missing OpenXML content structure."
                            )
                        )
                    if ext == "docx" and not any(n.startswith("word/") for n in names):
                        raise ValidationError(
                            _("Invalid file content for .docx. Missing Word document structure.")
                        )
                    if ext == "xlsx" and not any(n.startswith("xl/") for n in names):
                        raise ValidationError(
                            _("Invalid file content for .xlsx. Missing Excel workbook structure.")
                        )
            except zipfile.BadZipFile:
                raise ValidationError(
                    _(f"Invalid file content for .{ext}. The file is corrupt or not a valid archive.")
                )

        elif ext == "csv":
            # For CSV, read small chunk (4KB) to avoid memory overload
            sample = file.read(4096)
            if not sample:
                raise ValidationError(_("CSV file is empty."))
            # Reject known binary executable headers first
            binary_headers = (
                b"MZ",                     # DOS / PE executable
                b"%PDF-",                  # PDF
                b"PK\x03\x04",             # ZIP / OpenXML
                b"\x7fELF",                # ELF executable
                b"\x89PNG",                # PNG
                b"\xff\xd8\xff",           # JPEG
                b"\xd0\xcf\x11\xe0",       # OLECF (doc/xls)
            )
            if any(sample.startswith(sig) for sig in binary_headers):
                raise ValidationError(
                    _("Invalid file content for .csv. Binary file disguised with .csv extension.")
                )
            # Reject null bytes (common indicator of binary payload)
            if b"\x00" in sample:
                raise ValidationError(
                    _(
                        "Invalid file content for .csv. Binary content and null bytes are not permitted."
                    )
                )
            # Ensure text is decodable as utf-8 or latin-1
            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    sample.decode("latin-1")
                except Exception:
                    raise ValidationError(
                        _("Invalid file content for .csv. File contains non-text binary data.")
                    )

        elif ext == "txt":
            sample = file.read(4096)
            binary_headers = (
                b"MZ",                     # DOS / PE executable
                b"%PDF-",                  # PDF
                b"PK\x03\x04",             # ZIP / OpenXML
                b"\x7fELF",                # ELF executable
                b"\x89PNG",                # PNG
                b"\xff\xd8\xff",           # JPEG
                b"\xd0\xcf\x11\xe0",       # OLECF (doc/xls)
            )
            if any(sample.startswith(sig) for sig in binary_headers):
                raise ValidationError(
                    _("Invalid file content for .txt. Binary file disguised with .txt extension.")
                )
            if b"\x00" in sample:
                raise ValidationError(
                    _(
                        "Invalid file content for .txt. Binary content and null bytes are not permitted."
                    )
                )
            try:
                sample.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    sample.decode("latin-1")
                except Exception:
                    raise ValidationError(
                        _("Invalid file content for .txt. File contains non-text binary data.")
                    )

    finally:
        if hasattr(file, "seek") and initial_pos is not None:
            try:
                file.seek(initial_pos)
            except (AttributeError, OSError):
                pass

    return file


def validate_pr_attachment(file):
    """
    Validates PR attachments: only PDF and Word documents (DOC/DOCX) are allowed.
    Enforces extension check, max size (10MB), and magic bytes binary signature check.
    """
    if not file:
        raise ValidationError(_("Invalid file format."))

    if file.size > MAX_FILE_SIZE_BYTES:
        raise ValidationError(_("File size exceeds the maximum allowed limit of 10MB."))

    ext = os.path.splitext(file.name)[1][1:].lower()
    if ext not in PR_ATTACHMENT_EXTENSIONS:
        raise ValidationError(_("Invalid file format."))

    # Validate Magic Bytes binary signature
    initial_pos = None
    if hasattr(file, "tell"):
        try:
            initial_pos = file.tell()
        except (AttributeError, OSError):
            initial_pos = None

    if hasattr(file, "seek"):
        try:
            file.seek(0)
        except (AttributeError, OSError):
            pass

    try:
        header = file.read(8)
    except Exception:
        raise ValidationError(_("Invalid file format."))
    finally:
        if hasattr(file, "seek"):
            try:
                file.seek(initial_pos if initial_pos is not None else 0)
            except (AttributeError, OSError):
                pass

    if not header:
        raise ValidationError(_("Invalid file format."))

    if ext == "pdf":
        if not header.startswith(b"%PDF-"):
            raise ValidationError(_("Invalid file format."))
    elif ext == "doc":
        if not header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValidationError(_("Invalid file format."))
    elif ext == "docx":
        if not header.startswith(b"PK\x03\x04"):
            raise ValidationError(_("Invalid file format."))
    else:
        raise ValidationError(_("Invalid file format."))

    return file


_SCRIPT_STYLE_BLOCK_RE = re.compile(
    r"<\s*(script|style)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_UNSAFE_XSS_PATTERN_RE = re.compile(
    r"(<\s*/?\s*(?:script|iframe|object|embed|svg|img|style|link|meta|form)\b"
    r"|<|>"
    r"|javascript\s*:"
    r"|vbscript\s*:"
    r"|data\s*:\s*text/html"
    r"|\bon[a-z]+\s*=)",
    re.IGNORECASE,
)


def validate_no_html_or_script(value: str, field_label: str = "Input") -> str:
    """Reject raw HTML tags, <script> payloads, and active script attributes/URIs."""
    val = (value or "").strip()
    if not val:
        return ""
    if _UNSAFE_XSS_PATTERN_RE.search(val):
        raise ValidationError(
            _(f"{field_label} cannot contain HTML tags, script payloads, or active markup.")
        )
    return val


def validate_invoice_number(value: str) -> str:
    """
    Validates supplier invoice number format.
    Rules:
    - Required, 3–50 characters
    - Must start with a letter or digit, not a special character
    - Alphanumeric with hyphens (-), underscores (_), and slashes (/) allowed
      e.g. INV-2026-991, CISCO/INV/0012, INVTEST001
    - Rejects HTML/script content and special characters like !@#$%^&*()
    """
    val = (value or "").strip()
    if not val:
        raise ValidationError(_("Invoice Number is required."))

    validate_no_html_or_script(val, field_label="Invoice Number")

    if len(val) < 3 or len(val) > 50:
        raise ValidationError(
            _("Invoice Number must be between 3 and 50 characters.")
        )

    # Allow: start/end with alphanumeric, middle may include hyphens, underscores, slashes
    # Also allow pure alphanumeric (no special chars) of length 3-50
    if not re.match(
        r"^[A-Za-z0-9]([A-Za-z0-9\-_/]{0,48}[A-Za-z0-9])?$",
        val,
    ):
        raise ValidationError(
            _(
                "Invoice Number must be alphanumeric and may include hyphens (-), "
                "underscores (_), or slashes (/). Special characters like !@#$%^&*() "
                "are not permitted. Example: INV-2026-991 or CISCO/INV/0012."
            )
        )

    return val


def sanitize_plain_display_text(value: str, fallback: str = "[Sanitized invalid text]") -> str:
    """
    Strip legacy <script>/<style>/HTML tags before autoescaped template rendering.
    Never returns mark_safe HTML; strips residual angle brackets safely.
    """
    if value is None:
        return ""
    text = str(value)
    if not text.strip():
        return ""
    had_tags = bool(
        _SCRIPT_STYLE_BLOCK_RE.search(text)
        or _HTML_TAG_RE.search(text)
        or "<" in text
        or ">" in text
    )
    cleaned = _SCRIPT_STYLE_BLOCK_RE.sub("", text)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = cleaned.replace("<", "").replace(">", "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if not cleaned and had_tags:
        return fallback
    return cleaned


def validate_resolution_notes(value: str) -> str:
    """
    Validates that match exception resolution notes contain meaningful audit justification.
    Rejects:
    - Empty or whitespace-only input
    - Text shorter than 10 characters
    - Numeric-only or special-character-only strings (e.g. 111111111111111111 or !@#$%^&*)
    - Repetitive character sequences (e.g. aaaaaaa, 111111)
    - Raw HTML or script tags
    """
    val = (value or "").strip()
    if not val:
        raise ValidationError(_("Resolution notes are required and cannot be empty."))

    validate_no_html_or_script(val, field_label="Resolution notes")

    if len(val) < 10:
        raise ValidationError(
            _("Resolution notes must be at least 10 characters long to provide an adequate audit justification.")
        )

    if len(val) > 2000:
        raise ValidationError(_("Resolution notes cannot exceed 2000 characters."))

    # Check for meaningful alphabetic words
    letters = re.findall(r"[a-zA-Z]", val)
    if len(letters) < 5:
        raise ValidationError(
            _("Resolution notes must contain meaningful explanatory text with at least 5 letters, not solely numbers or symbols.")
        )

    # Check against repetitive characters (e.g. 1111111111, aaaaaaaa)
    cleaned_no_spaces = re.sub(r"\s+", "", val)
    if len(set(cleaned_no_spaces)) < 4:
        raise ValidationError(
            _("Resolution notes cannot consist of repeated characters or gibberish. Please provide a descriptive justification.")
        )

    if re.search(r"(.)\1{5,}", val):
        raise ValidationError(
            _("Resolution notes contain repetitive character sequences. Please provide a descriptive justification.")
        )

    return val


def validate_payment_reference(value: str) -> str:
    """
    Validates financial transaction reference / UTR #.
    Standard banking formats (NEFT/RTGS/ACH/SWIFT/Wire):
    - Alphanumeric, hyphens, and slashes permitted (e.g. UTR-2026-981023, CMS123456789)
    - Length between 6 and 50 characters
    - Rejects disallowed special characters like !@#$%^&*()
    - Rejects raw HTML or script tags
    """
    val = (value or "").strip()
    if not val:
        raise ValidationError(_("Transaction / Payment Reference ID (UTR / Check #) is required."))

    validate_no_html_or_script(val, field_label="Payment reference")

    if len(val) < 6 or len(val) > 50:
        raise ValidationError(
            _("Payment Reference / UTR must be between 6 and 50 characters in length.")
        )

    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9\-_/]{4,48}[A-Za-z0-9]$", val):
        raise ValidationError(
            _("Invalid Transaction Reference / UTR format. Only alphanumeric characters, hyphens (-), and slashes (/) are permitted (e.g., UTR-2026-981023, CMS1234567890). Special characters like !@#$%^&*() are prohibited.")
        )

    return val

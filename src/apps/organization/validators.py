import re
from typing import Optional

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Organization Code: 2-50 characters, uppercase alphanumeric, hyphens, and underscores only.
ORG_CODE_REGEX = re.compile(r"^[A-Z0-9_\-]+$")

# Organization Name: 2-255 characters, letters, numbers, spaces, and standard enterprise punctuation (&, ., ,, -, ', /, ()).
ORG_NAME_REGEX = re.compile(r"^[A-Za-z0-9\s&.,'/\(\)\-]+$")

# Tax Identifier: 2-100 characters, alphanumeric, underscores, hyphens, periods, slashes, spaces.
ORG_TAX_ID_REGEX = re.compile(r"^[A-Za-z0-9_\-\.\/ ]+$")


def validate_org_code(value: str) -> str:
    """
    Validates organization code:
    - Bounded length: 2 to 50 characters.
    - Disallows arbitrary characters (must be uppercase alphanumeric, '-', or '_').
    """
    code = (value or "").strip().upper()
    if not code:
        raise ValidationError("Organization code is required.")
    if len(code) < 2:
        raise ValidationError("Organization code must be at least 2 characters in length.")
    if len(code) > 50:
        raise ValidationError("Organization code cannot exceed 50 characters in length.")
    if not ORG_CODE_REGEX.match(code):
        raise ValidationError(
            "Organization code contains invalid characters. Only uppercase letters, numbers, hyphens (-), and underscores (_) are allowed."
        )
    return code


def validate_org_name(value: str) -> str:
    """
    Validates organization legal/display name:
    - Bounded length: 2 to 255 characters.
    - Rejects control characters, HTML/script tags, and arbitrary symbols.
    - Must contain at least one letter.
    """
    name = (value or "").strip()
    if not name:
        raise ValidationError("Organization name is required.")
    if len(name) < 2:
        raise ValidationError("Organization name must be at least 2 characters in length.")
    if len(name) > 255:
        raise ValidationError("Organization name cannot exceed 255 characters in length.")
    if any(ord(c) < 32 for c in name):
        raise ValidationError("Organization name cannot contain control characters.")
    if "<" in name or ">" in name:
        raise ValidationError("Organization name cannot contain HTML or script characters.")
    if not ORG_NAME_REGEX.match(name):
        raise ValidationError(
            "Organization name contains invalid characters. Only letters, numbers, spaces, and standard punctuation (&, ., ,, -, ', /, ()) are allowed."
        )
    if not re.search(r"[A-Za-z]", name):
        raise ValidationError("Organization name cannot contain only numbers or special characters. It must contain at least one letter.")
    return name


def validate_name_has_letters(value: str, field_label: str = "Name", min_len: int = 2, max_len: int = 150) -> str:
    """
    Validates enterprise entity name (Department, Cost Center, Location, Fiscal Period):
    - Must contain at least one alphabetical letter.
    - Rejects numbers-only and special-characters-only inputs.
    - Allows mixing numbers and alphabets (e.g. 'Engineering 2', 'Logistics 01', 'FY2026-Q1').
    """
    val = (value or "").strip()
    if not val:
        raise ValidationError(f"{field_label} is required.")
    if len(val) < min_len:
        raise ValidationError(f"{field_label} must be at least {min_len} characters in length.")
    if len(val) > max_len:
        raise ValidationError(f"{field_label} cannot exceed {max_len} characters in length.")
    if any(ord(c) < 32 for c in val):
        raise ValidationError(f"{field_label} cannot contain control characters.")
    if "<" in val or ">" in val:
        raise ValidationError(f"{field_label} cannot contain HTML or script characters.")
    if val.isdigit():
        raise ValidationError(f"{field_label} cannot contain only numbers. It must contain at least one letter (alphabets and numbers may be mixed).")
    if not re.search(r"[A-Za-z]", val):
        raise ValidationError(f"{field_label} cannot contain only numbers or special characters. It must contain at least one letter.")
    if not re.match(r"^[A-Za-z0-9\s&.,'/\(\)\-_]+$", val):
        raise ValidationError(
            f"{field_label} contains invalid characters. Only letters, numbers, spaces, and standard punctuation (&, ., ,, -, _, ', /, ()) are allowed."
        )
    return val


def validate_code_has_letters(value: str, field_label: str = "Code", min_len: int = 2, max_len: int = 50) -> str:
    """
    Validates enterprise entity code (Department, Cost Center, Location):
    - Must contain at least one alphabetical letter.
    - Rejects numbers-only and special-characters-only inputs.
    - Allows uppercase letters, numbers, hyphens, underscores, dots, and slashes.
    """
    code = (value or "").strip().upper()
    if not code:
        raise ValidationError(f"{field_label} is required.")
    if len(code) < min_len:
        raise ValidationError(f"{field_label} must be at least {min_len} characters in length.")
    if len(code) > max_len:
        raise ValidationError(f"{field_label} cannot exceed {max_len} characters in length.")
    if code.isdigit():
        raise ValidationError(f"{field_label} cannot contain only numbers. It must contain at least one letter (e.g. LOC-01, CC-101, DEPT-A).")
    if not re.search(r"[A-Z]", code):
        raise ValidationError(f"{field_label} cannot contain only numbers or special characters. It must contain at least one letter.")
    if not re.match(r"^[A-Z0-9_\-\.\/]+$", code):
        raise ValidationError(
            f"{field_label} contains invalid characters. Only uppercase letters, numbers, hyphens (-), underscores (_), dots (.), and slashes (/) are allowed."
        )
    return code


def validate_city_has_letters(value: str, field_label: str = "City", min_len: int = 2, max_len: int = 100) -> str:
    """
    Validates location city:
    - Must contain at least one alphabetical letter.
    - Rejects numbers-only inputs (e.g. 1111111111).
    """
    city = (value or "").strip()
    if not city:
        raise ValidationError(f"{field_label} is required.")
    if len(city) < min_len:
        raise ValidationError(f"{field_label} must be at least {min_len} characters in length.")
    if len(city) > max_len:
        raise ValidationError(f"{field_label} cannot exceed {max_len} characters in length.")
    if city.isdigit():
        raise ValidationError(f"{field_label} cannot contain only numbers. It must contain at least one letter.")
    if not re.search(r"[A-Za-z]", city):
        raise ValidationError(f"{field_label} must contain at least one letter.")
    if not re.match(r"^[A-Za-z0-9\s\.\-']+$", city):
        raise ValidationError(f"{field_label} contains invalid characters.")
    return city


def validate_org_tax_identifier(value: str) -> str:
    """
    Validates tax identifier (optional):
    - Bounded length: max 100 characters.
    - Rejects control characters, HTML/script tags, and arbitrary symbols.
    """
    tax_id = (value or "").strip()
    if not tax_id:
        return ""
    if len(tax_id) < 2:
        raise ValidationError("Tax identifier must be at least 2 characters if provided.")
    if len(tax_id) > 100:
        raise ValidationError("Tax identifier cannot exceed 100 characters in length.")
    if any(ord(c) < 32 for c in tax_id) or "<" in tax_id or ">" in tax_id:
        raise ValidationError("Tax identifier cannot contain HTML, script, or control characters.")
    if not ORG_TAX_ID_REGEX.match(tax_id):
        raise ValidationError(
            "Tax identifier contains invalid characters. Only letters, numbers, underscores (_), hyphens (-), dots (.), slashes (/), and spaces are allowed."
        )
    return tax_id


def validate_org_address(value: str) -> str:
    """
    Validates organization address (optional):
    - Bounded length: max 500 characters.
    - Rejects illegal control characters and script tags.
    """
    address = (value or "").strip()
    if not address:
        return ""
    if len(address) > 500:
        raise ValidationError("Registered address cannot exceed 500 characters in length.")
    if any(ord(c) < 32 and c not in ("\r", "\n", "\t") for c in address):
        raise ValidationError("Address contains illegal control characters.")
    if "<script" in address.lower() or "</script" in address.lower():
        raise ValidationError("Address cannot contain executable script tags.")
    return address


def validate_org_admin_email(value: str, is_create: bool = True, admin_user: Optional[object] = None) -> str:
    """
    Validates admin email:
    - Mandatory on create if no existing admin_user is selected.
    - Bounded length: max 254 characters (RFC 5321).
    - Standard RFC email format check.
    """
    email = (value or "").strip().lower()
    if is_create and not admin_user and not email:
        raise ValidationError(
            "Admin Email is mandatory (*). Please provide an email address for the organization administrator."
        )
    if email:
        if len(email) > 254:
            raise ValidationError("Admin Email cannot exceed 254 characters in length.")
        try:
            validate_email(email)
        except ValidationError:
            raise ValidationError("Please enter a valid email address (e.g. admin@organization.com).")
    return email


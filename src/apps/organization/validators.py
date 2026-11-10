import re
from typing import Optional

from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# Organization Code: 2-50 characters, uppercase alphanumeric, hyphens, and underscores only.
ORG_CODE_REGEX = re.compile(r"^[A-Z0-9_\-]+$")

# Organization Name: 2-255 characters, letters, numbers, spaces, and standard enterprise punctuation (&, ., ,, -, ', /, ()).
ORG_NAME_REGEX = re.compile(r"^[A-Za-z0-9\s&.,'/\(\)\-]+$")

# Tax Identifier: 2-100 characters, alphanumeric, hyphens, periods, slashes, spaces.
ORG_TAX_ID_REGEX = re.compile(r"^[A-Za-z0-9\-\.\/ ]+$")


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
    return name


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
            "Tax identifier contains invalid characters. Only letters, numbers, hyphens (-), dots (.), slashes (/), and spaces are allowed."
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


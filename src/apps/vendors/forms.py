from django import forms

from apps.core.validators import (
    normalize_tax_identifier,
    validate_gstin,
    validate_pan_gstin_consistency,
    validate_pan_number,
)

from .models import Vendor, VendorCategory, VendorRiskRecord


class VendorEditForm(forms.Form):
    """
    Procurement Executive: edit vendor master information.
    Allows updating name, email, phone, address, bank and category.
    Tax ID / registration number changes are intentionally excluded (require legal justification).
    """
    legal_name = forms.CharField(max_length=255, label="Legal name")
    trade_name = forms.CharField(max_length=255, required=False, label="Trade / brand name")
    email = forms.EmailField(label="Primary email")
    phone = forms.CharField(max_length=30, required=False, label="Phone")
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}), label="Address")
    category = forms.ModelChoiceField(
        queryset=VendorCategory.objects.order_by("name"), label="Category"
    )
    bank_name = forms.CharField(max_length=150, required=False, label="Bank name")
    bank_account_number = forms.CharField(max_length=100, required=False, label="Bank account no.")
    bank_routing_code = forms.CharField(max_length=100, required=False, label="Bank routing / IFSC")


class VendorRegistrationForm(forms.Form):
    legal_name = forms.CharField(max_length=255)
    trade_name = forms.CharField(max_length=255, required=False)
    tax_identification_number = forms.CharField(max_length=100)
    pan_number = forms.CharField(
        max_length=10,
        required=False,
        label="PAN",
        error_messages={
            "max_length": "Invalid PAN format. PAN must be 10 characters: 5 uppercase letters, 4 digits, 1 uppercase letter (e.g. ABCDE1234F).",
        },
    )
    gstin = forms.CharField(
        max_length=15,
        required=False,
        label="GSTIN",
        error_messages={
            "max_length": "Invalid GSTIN format. GSTIN must be 15 alphanumeric characters (e.g. 29ABCDE1234F1Z5).",
        },
    )
    registration_number = forms.CharField(max_length=100, required=False)
    category = forms.ModelChoiceField(queryset=VendorCategory.objects.order_by("name"))
    email = forms.EmailField()
    phone = forms.CharField(max_length=30, required=False)
    address = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))
    bank_name = forms.CharField(max_length=150, required=False)
    bank_account_number = forms.CharField(max_length=100, required=False)
    bank_routing_code = forms.CharField(max_length=100, required=False)

    def clean_tax_identification_number(self):
        tin = self.cleaned_data["tax_identification_number"].strip()
        if Vendor.objects.filter(tax_identification_number=tin).exists():
            raise forms.ValidationError("A vendor with this tax ID already exists.")
        return tin

    def clean_pan_number(self):
        pan = normalize_tax_identifier(self.cleaned_data.get("pan_number", ""))
        if pan:
            validate_pan_number(pan)
        return pan

    def clean_gstin(self):
        gst = normalize_tax_identifier(self.cleaned_data.get("gstin", ""))
        if gst:
            validate_gstin(gst)
        return gst

    def clean(self):
        cleaned_data = super().clean()
        pan = cleaned_data.get("pan_number")
        gst = cleaned_data.get("gstin")
        if pan and gst:
            try:
                validate_pan_gstin_consistency(pan, gst)
            except forms.ValidationError as exc:
                self.add_error("gstin", exc)
        return cleaned_data


class RiskAssessmentForm(forms.Form):
    risk_level = forms.ChoiceField(
        choices=VendorRiskRecord.RISK_CHOICES,
        widget=forms.Select(attrs={"class": "form-select form-select-sm"}),
    )
    risk_flags = forms.MultipleChoiceField(
        choices=VendorRiskRecord.RISK_FLAG_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control form-control-sm"})
    )


class GovernanceStatusForm(forms.Form):
    status = forms.ChoiceField(choices=Vendor.STATUS_CHOICES)
    notes = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))


class VendorProfileUpdateForm(forms.Form):
    """
    Vendor Dashboard self-service company profile update form.
    Validates phone number to strictly enforce country code (+91) followed by 10 digits.
    Allows updating PAN and GSTIN with consistency validation.
    """
    trade_name = forms.CharField(max_length=255, required=False)
    address = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 2}),
        required=True,
        error_messages={
            "required": "Registered office address is required.",
        },
    )
    phone = forms.CharField(
        max_length=25,
        required=True,
        error_messages={
            "required": "Contact phone number is required.",
        },
    )
    pan_number = forms.CharField(
        max_length=10,
        required=False,
        label="PAN",
        error_messages={
            "max_length": "Invalid PAN format. PAN must be 10 characters: 5 uppercase letters, 4 digits, 1 uppercase letter (e.g. ABCDE1234F).",
        },
    )
    gstin = forms.CharField(
        max_length=15,
        required=False,
        label="GSTIN",
        error_messages={
            "max_length": "Invalid GSTIN format. GSTIN must be 15 alphanumeric characters (e.g. 29ABCDE1234F1Z5).",
        },
    )
    bank_name = forms.CharField(max_length=150, required=False)
    bank_account_number = forms.CharField(max_length=100, required=False)
    bank_routing_code = forms.CharField(max_length=100, required=False)

    def clean_address(self):
        address = self.cleaned_data.get("address", "").strip()
        if not address:
            raise forms.ValidationError("Registered office address is required.")
        return address

    def clean_phone(self):
        import re

        raw_phone = self.cleaned_data.get("phone", "").strip()
        if not raw_phone:
            raise forms.ValidationError("Contact phone number is required.")
        clean_phone = re.sub(r"[\s\-\(\)\.]", "", raw_phone)

        # If user submits 12 digits starting with 91 (e.g. 919876543210), normalize to +91XXXXXXXXXX
        if re.match(r"^91\d{10}$", clean_phone):
            clean_phone = f"+{clean_phone}"

        # Strictly enforce +91 followed by exactly 10 digits
        if not re.match(r"^\+91\d{10}$", clean_phone):
            raise forms.ValidationError(
                "Phone number must have country code (+91) followed by exactly 10 digits (e.g., +919876543210)."
            )

        # Strictly enforce Indian mobile number prefix (starts with 6, 7, 8, or 9)
        if not re.match(r"^\+91[6-9]\d{9}$", clean_phone):
            raise forms.ValidationError(
                "Invalid Indian mobile number prefix. Mobile number must begin with 6, 7, 8, or 9 (e.g., +919876543210)."
            )
        return clean_phone

    def clean_pan_number(self):
        pan = normalize_tax_identifier(self.cleaned_data.get("pan_number", ""))
        if pan:
            validate_pan_number(pan)
        return pan

    def clean_gstin(self):
        gst = normalize_tax_identifier(self.cleaned_data.get("gstin", ""))
        if gst:
            validate_gstin(gst)
        return gst

    def clean(self):
        cleaned_data = super().clean()
        pan = cleaned_data.get("pan_number")
        gst = cleaned_data.get("gstin")
        if pan and gst:
            try:
                validate_pan_gstin_consistency(pan, gst)
            except forms.ValidationError as exc:
                self.add_error("gstin", exc)
        return cleaned_data


class VendorContactForm(forms.Form):
    """
    Form for adding and editing authorized vendor contacts.
    Strictly validates primary/secondary contact email and phone format.
    Allows unchanged contact details to be safely re-submitted.
    """
    first_name = forms.CharField(
        max_length=100,
        required=True,
        error_messages={"required": "First name is required."},
    )
    last_name = forms.CharField(
        max_length=100,
        required=True,
        error_messages={"required": "Last name is required."},
    )
    email = forms.EmailField(
        required=True,
        error_messages={
            "required": "Email address is required.",
            "invalid": "Please enter a valid email address.",
        },
    )
    phone = forms.CharField(max_length=25, required=False)
    designation = forms.CharField(max_length=100, required=False)
    is_primary = forms.BooleanField(required=False)

    def __init__(self, *args, contact=None, **kwargs):
        self.contact = contact
        super().__init__(*args, **kwargs)

    def clean_first_name(self):
        fn = self.cleaned_data.get("first_name", "").strip()
        if not fn:
            raise forms.ValidationError("First name is required.")
        return fn

    def clean_last_name(self):
        ln = self.cleaned_data.get("last_name", "").strip()
        if not ln:
            raise forms.ValidationError("Last name is required.")
        return ln

    def clean_phone(self):
        import re

        raw_phone = self.cleaned_data.get("phone", "").strip()
        if not raw_phone:
            return ""

        # Allow unchanged phone if editing an existing contact
        if self.contact:
            existing_phone = getattr(self.contact, "phone", "") or ""
            existing_digits = getattr(self.contact, "phone_digits", "") or ""
            if raw_phone == existing_phone or raw_phone == existing_digits:
                return existing_phone or raw_phone

        clean_phone = re.sub(r"[\s\-\(\)\.]", "", raw_phone)
        if re.match(r"^\d{10}$", clean_phone):
            digits = clean_phone
        elif re.match(r"^91\d{10}$", clean_phone):
            digits = clean_phone[2:]
        elif re.match(r"^\+91\d{10}$", clean_phone):
            digits = clean_phone[3:]
        else:
            raise forms.ValidationError(
                "Phone number must be exactly 10 digits (e.g., 9876543210)."
            )

        # Enforce Indian mobile prefix rule (starts with 6, 7, 8, or 9)
        if not re.match(r"^[6-9]\d{9}$", digits):
            raise forms.ValidationError(
                "Invalid Indian mobile number prefix. Phone number must start with 6, 7, 8, or 9."
            )
        return digits


class VendorChangesRequestForm(forms.Form):
    """
    Form for procurement reviewers to request corrections/updates from a vendor.
    """
    notes = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control", "placeholder": "Specify changes required..."}),
        required=True,
        error_messages={"required": "Please provide detailed instructions on what changes are required."},
    )



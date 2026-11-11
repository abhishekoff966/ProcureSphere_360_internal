"""
Tests for BUG-010 to BUG-014 fixes (Procurement Executive sheet).
"""
import pytest


@pytest.mark.django_db
class TestBug010PhoneValidation:
    """BUG-010: Phone field validation in VendorRegistrationForm and VendorEditForm."""

    def test_valid_10digit_mobile_accepted(self):
        from apps.vendors.forms import VendorRegistrationForm
        f = VendorRegistrationForm(data={"phone": "9876543210"})
        f.is_valid()
        assert "phone" not in f.errors, f.errors

    def test_valid_plus91_mobile_accepted(self):
        from apps.vendors.forms import VendorRegistrationForm
        f = VendorRegistrationForm(data={"phone": "+91 9876543210"})
        f.is_valid()
        assert "phone" not in f.errors, f.errors

    def test_empty_phone_accepted_optional(self):
        from apps.vendors.forms import VendorRegistrationForm
        f = VendorRegistrationForm(data={"phone": ""})
        f.is_valid()
        assert "phone" not in f.errors, f.errors

    def test_malformed_phone_rejected(self):
        from apps.vendors.forms import VendorRegistrationForm
        f = VendorRegistrationForm(data={"phone": "1234567890-0987654321"})
        f.is_valid()
        # 20 digits - should fail length or format check
        assert "phone" in f.errors, "Expected phone validation error for malformed input"

    def test_edit_form_valid_phone(self):
        from apps.vendors.forms import VendorEditForm
        f = VendorEditForm(data={"phone": "+91 8765432109"})
        f.is_valid()
        assert "phone" not in f.errors, f.errors

    def test_edit_form_invalid_phone(self):
        from apps.vendors.forms import VendorEditForm
        f = VendorEditForm(data={"phone": "not-a-phone!!"})
        f.is_valid()
        assert "phone" in f.errors, "Expected phone validation error for nonsense input"


@pytest.mark.django_db
class TestBug011FilterEmptyLabels:
    """BUG-011: Filter dropdowns should have descriptive empty labels."""

    def test_category_filter_has_empty_label(self):
        from apps.vendors.filters import VendorFilter
        from apps.vendors.models import Vendor
        f = VendorFilter(data={}, queryset=Vendor.objects.none())
        # The form widget for category should have an empty choice with descriptive text
        category_field = f.form.fields.get("category")
        assert category_field is not None
        # empty_label is set on the filter, django-filter passes it through
        empty_label = getattr(f.filters.get("category"), "field", None)
        assert f.filters["category"].field.empty_label == "All Categories", \
            f"Expected 'All Categories', got '{f.filters['category'].field.empty_label}'"

    def test_risk_level_filter_has_empty_label(self):
        from apps.vendors.filters import VendorFilter
        # ChoiceFilter does not have empty_label attr same way - check choices include empty
        f = VendorFilter(data={})
        choices = dict(f.form.fields["risk_level"].choices)
        # The first choice should be empty string key mapped to "All Risk Levels"
        assert "" in choices, "Empty choice not found in risk_level filter"
        assert choices[""] == "All Risk Levels", f"Got '{choices.get('')}'"

    def test_restriction_filter_has_empty_label(self):
        from apps.vendors.filters import VendorFilter
        f = VendorFilter(data={})
        choices = dict(f.form.fields["restriction"].choices)
        assert "" in choices
        assert choices[""] == "All Restrictions"

    def test_performance_filter_has_empty_label(self):
        from apps.vendors.filters import VendorFilter
        f = VendorFilter(data={})
        choices = dict(f.form.fields["performance"].choices)
        assert "" in choices
        assert choices[""] == "All Performance"


class TestBug012SearchBarClass:
    """BUG-012: Search bar should use form-control not form-control-sm."""

    def test_search_input_uses_form_control(self):
        with open(r"templates\pages\vendors\list.html", encoding="utf-8") as fh:
            content = fh.read()
        # Must NOT have form-control-sm on the search input
        assert 'id="f-q"' in content
        # Find the f-q input line
        for line in content.splitlines():
            if 'id="f-q"' in line:
                assert "form-control-sm" not in line, \
                    f"Search input still has form-control-sm: {line.strip()}"
                assert "form-control" in line
                break


class TestBug013Placeholders:
    """BUG-013: Instructional placeholders should exist on vendor form fields."""

    def test_create_html_has_trade_name_placeholder(self):
        with open(r"templates\pages\vendors\create.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "Acme Solutions (trading name)" in content, \
            "trade_name placeholder missing from create.html"

    def test_create_html_has_registration_number_placeholder(self):
        with open(r"templates\pages\vendors\create.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "CIN U12345" in content, \
            "registration_number placeholder missing from create.html"

    def test_create_html_has_phone_placeholder(self):
        with open(r"templates\pages\vendors\create.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "+91 9876543210" in content, \
            "phone placeholder missing from create.html"

    def test_create_html_has_bank_name_placeholder(self):
        with open(r"templates\pages\vendors\create.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "HDFC Bank" in content, \
            "bank_name placeholder missing from create.html"

    def test_modal_has_phone_placeholder(self):
        with open(r"templates\vendors\partials\htmx_register_modal.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "+91 9876543210" in content, \
            "phone placeholder missing from htmx_register_modal.html"

    def test_edit_modal_has_phone_placeholder(self):
        with open(r"templates\vendors\partials\htmx_edit_modal.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "+91 9876543210" in content, \
            "phone placeholder missing from htmx_edit_modal.html"

    def test_edit_modal_has_bank_placeholders(self):
        with open(r"templates\vendors\partials\htmx_edit_modal.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "HDFC Bank" in content
        assert "IFSC/SWIFT" in content


class TestBug014SaveButtonDisabled:
    """BUG-014: Edit modal Save button should be disabled by default until changes made."""

    def test_save_btn_has_disabled_attribute(self):
        with open(r"templates\vendors\partials\htmx_edit_modal.html", encoding="utf-8") as fh:
            content = fh.read()
        assert 'id="vendor-edit-save-btn"' in content
        # Find the button line and verify it has disabled
        for line in content.splitlines():
            if 'vendor-edit-save-btn' in line:
                assert "disabled" in line, \
                    f"Save button is missing 'disabled' attribute: {line.strip()}"
                break

    def test_save_btn_has_dirty_check_script(self):
        with open(r"templates\vendors\partials\htmx_edit_modal.html", encoding="utf-8") as fh:
            content = fh.read()
        assert "checkDirty" in content, "Dirty-state JS function not found in edit modal"
        assert "saveBtn.disabled = !dirty" in content, \
            "Dirty-state toggle logic not found in edit modal"

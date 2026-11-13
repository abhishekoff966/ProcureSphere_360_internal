import pytest
import re
from django.template.loader import render_to_string


def test_po_detail_modal_heading_typography_and_contrast():
    """Verify Issue 6: Purchase Order Inspection Modal Heading has high contrast text and background."""
    with open('templates/manager/purchase_orders.html', 'r', encoding='utf-8') as f:
        content = f.read()

    # Verify #poDetailModal exists
    assert 'id="poDetailModal"' in content
    # Verify .bg-hpe-navy is NOT on the header
    assert 'class="modal-header bg-hpe-navy text-white px-4 py-3"' not in content
    # Verify high contrast style on header and #modalPoNumber
    assert 'style="color: #111827 !important;' in content
    # Verify dark neutral background on modal header
    assert 'background-color: #F9FAFB !important;' in content
    # Verify standard dark close button (not btn-close-white on white background)
    assert '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' in content


def test_po_cancellation_modal_layout_and_inline_feedback():
    """Verify Issue 5: PO cancellation modal has fixed overlay, inline validation, and no layout shifting."""
    with open('templates/manager/purchase_orders.html', 'r', encoding='utf-8') as f:
        content = f.read()

    # Verify modal exists
    assert 'id="poCancelModal"' in content
    # Verify fixed viewport overlay CSS exists
    assert '#poCancelModal.modal' in content
    assert 'position: fixed !important;' in content
    assert 'z-index: 1060 !important;' in content
    # Verify inline error alert exists
    assert 'id="poCancelErrorAlert"' in content
    # Verify inline feedback for reason field exists
    assert 'id="poCancelReasonFeedback"' in content
    assert 'Reason for cancellation is mandatory.' in content
    # Verify modals relocated to document.body on DOMContentLoaded
    assert 'document.body.appendChild(cancelModal)' in content
    assert 'document.body.appendChild(detailModal)' in content
    # Verify submit button prevents duplicate submission
    assert 'submitBtn.disabled = true' in content
    # Verify Reason label
    assert 'Reason for Cancellation' in content


def test_vendor_risk_search_placeholder_and_responsiveness():
    """Verify Issue 7: Vendor Risk search input has updated placeholder and responsive width."""
    with open('templates/manager/vendor_risk.html', 'r', encoding='utf-8') as f:
        content = f.read()

    # Verify search input placeholder
    assert 'id="vendorSearchInput"' in content
    assert 'placeholder="Search by supplier name, TIN, or code"' in content
    # Verify old placeholder is gone
    assert 'placeholder="Search supplier, TIN, code, email..."' not in content
    # Verify expanded responsive column container (col-md-4 or col-xl-4)
    assert 'col-12 col-md-4' in content


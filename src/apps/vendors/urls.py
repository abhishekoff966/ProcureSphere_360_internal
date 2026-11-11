from django.urls import path

from . import views

urlpatterns = [
    path("vendors/", views.vendor_list_view, name="vendor_list"),
    path("vendors/governance/", views.vendor_governance_view, name="vendor_governance"),
    path("vendors/onboarding/", views.vendor_onboarding_view, name="vendor_onboarding"),
    path("vendors/create/", views.vendor_create_view, name="vendor_create"),
    path("vendors/htmx/register/", views.vendor_register_modal_view, name="vendors_register_modal"),
    path("vendors/import/", views.vendor_import_view, name="vendor_import"),
    path(
        "vendors/webhooks/brevo/",
        views.brevo_webhook_view,
        name="vendor_brevo_webhook",
    ),
    path(
        "api/vendors/brevo-webhook/",
        views.brevo_webhook_view,
        name="vendor_brevo_webhook_api",
    ),
    path(
        "vendors/<uuid:vendor_id>/credentials/",
        views.vendor_issue_credentials_view,
        name="vendor_issue_credentials",
    ),
    # ── Independent vendor detail pages (real routes, shared header/nav) ──
    path(
        "vendors/<uuid:vendor_id>/overview/",
        views.vendor_overview_view,
        name="vendor_detail_overview",
    ),
    path(
        "vendors/<uuid:vendor_id>/status/",
        views.vendor_status_page_view,
        name="vendor_detail_status",
    ),
    path("vendors/<uuid:vendor_id>/risk/", views.vendor_risk_page_view, name="vendor_detail_risk"),
    path(
        "vendors/<uuid:vendor_id>/performance/",
        views.vendor_performance_page_view,
        name="vendor_detail_performance",
    ),
    path(
        "vendors/<uuid:vendor_id>/documents/",
        views.vendor_documents_page_view,
        name="vendor_detail_documents",
    ),
    path(
        "vendors/<uuid:vendor_id>/contacts/",
        views.vendor_contacts_page_view,
        name="vendor_detail_contacts",
    ),
    path(
        "vendors/<uuid:vendor_id>/sourcing/",
        views.vendor_sourcing_page_view,
        name="vendor_detail_sourcing",
    ),
    path(
        "vendors/<uuid:vendor_id>/history/",
        views.vendor_history_page_view,
        name="vendor_detail_history",
    ),
    # POST actions for the Status page (unchanged reverse names, moved off the page path)
    path(
        "vendors/<uuid:vendor_id>/governance/transition/",
        views.vendor_status_change_view,
        name="vendor_status_change",
    ),
    # POST action for the Risk page
    path(
        "vendors/<uuid:vendor_id>/risk/assess/",
        views.vendor_risk_assess_view,
        name="vendor_risk_assess",
    ),
    # Legacy landing URL — resolves to the Overview page (ASSUMP-038)
    path("vendors/<uuid:vendor_id>/", views.vendor_overview_view, name="vendor_detail"),
    path(
        "vendors/<uuid:vendor_id>/kyc/<slug:kyc_action>/",
        views.vendor_kyc_action_view,
        name="vendor_kyc_action",
    ),
    path(
        "vendors/<uuid:vendor_id>/documents/<uuid:doc_id>/download/",
        views.vendor_document_download_view,
        name="vendor_document_download",
    ),
    # Procurement Executive Dashboard — Pillar 1 HTMX fragments
    path(
        "vendors/htmx/qualification/",
        views.vendor_qualification_tab_view,
        name="vendors_qualification_tab",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/documents/",
        views.vendor_documents_modal_view,
        name="vendors_documents_modal",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/risk/",
        views.vendor_risk_modal_view,
        name="vendors_risk_modal",
    ),
    path(
        "vendors/<uuid:vendor_id>/htmx/kyc/<slug:kyc_action>/",
        views.vendor_kyc_htmx_view,
        name="vendors_kyc_htmx",
    ),
    # Edit vendor master data (HTMX modal)
    path(
        "vendors/<uuid:vendor_id>/edit/",
        views.vendor_edit_view,
        name="vendor_edit",
    ),
    # Delete vendor (POST-only, DRAFT/REJECTED only)
    path(
        "vendors/<uuid:vendor_id>/delete/",
        views.vendor_delete_view,
        name="vendor_delete",
    ),
]

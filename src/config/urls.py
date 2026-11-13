from django.contrib import admin
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import include, path, re_path
from django.views.decorators.http import require_GET
from django.views.generic.base import RedirectView
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.accounts.api_views import (
    SessionKeepAliveView,
    SessionLoginView,
    SessionLogoutView,
    SessionRefreshView,
)
from apps.accounts.views import (
    change_password_view,
    password_change_view,
    portal_login_view,
    portal_logout_view,
)
from apps.contracts import views as contracts_views
from apps.core import superadmin_views
from apps.core.seo_views import robots_txt, sitemap_xml
from apps.core.views import (
    custom_404_view,
    custom_api_404_view,
    protected_media_view,
    protected_vendor_document_view,
)
from apps.invoices import views as invoices_views
from apps.notifications.views import (
    notification_list_view,
    notification_mark_all_read_view,
    notification_open_view,
)
from apps.orders import views as orders_views
from apps.receipts import views as receipts_views
from apps.requisitions import views as requisitions_views
from apps.sourcing import views as sourcing_views
from apps.vendors import views as vendors_views

from .healthchecks import health, readyz


@require_GET
def api_schema_dispatch_view(request):
    """
    Serves Swagger UI (HTML) by default on /api/v1/schema/ and OpenAPI specification
    when ?format=json (or Accept: application/json) is requested (TC_API_009).
    """
    format_param = request.GET.get("format", "").lower()
    accept_header = request.META.get("HTTP_ACCEPT", "").lower()
    if format_param in ["json", "yaml"] or (
        "application/json" in accept_header and "text/html" not in accept_header
    ):
        return SpectacularAPIView.as_view()(request)
    return SpectacularSwaggerView.as_view(url_name="schema")(request)


@require_GET
def admin_dashboard_dispatch_view(request):
    """
    Intelligent dispatch for /admin/dashboard/:
    - Anonymous: forward to /login/
    - Platform Superadmin (superuser without assigned organization): /admin/
    - Tenant / Org Admin & others: canonical /superadmin/
    """
    if not request.user.is_authenticated:
        return redirect("/login/")
    if request.user.is_superuser and not getattr(request.user, "organization_id", None):
        return redirect("/admin/")
    return redirect("/superadmin/")


urlpatterns = [
    path("robots.txt", robots_txt, name="robots_txt"),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    path(
        "favicon.ico",
        RedirectView.as_view(url="/static/icons/favicon.ico", permanent=False),
        name="favicon",
    ),
    path("health", health, name="health"),
    path("readyz", readyz, name="readyz"),
    re_path(r"^[Qq]uit/?$", RedirectView.as_view(url="/", permanent=False)),
    re_path(r"^.*prompts\.json$", lambda request: JsonResponse({})),
    path("admin/dashboard/", admin_dashboard_dispatch_view, name="admin_dashboard"),
    path("admin/logout/", portal_logout_view, name="admin_logout_override"),
    path("admin/notifications/", notification_list_view, name="admin_notifications"),
    path(
        "admin/notifications/<uuid:notification_id>/open/",
        notification_open_view,
        name="admin_notification_open",
    ),
    path(
        "admin/notifications/mark-all-read/",
        notification_mark_all_read_view,
        name="admin_notification_mark_all_read",
    ),
    path("admin/orders/", orders_views.list_view, name="admin_orders_list"),
    path("admin/orders/<uuid:pk>/", orders_views.detail_view, name="admin_order_detail"),
    path("admin/purchase-orders/", orders_views.list_view, name="admin_purchase_orders_list"),
    path("admin/purchase-orders/<uuid:pk>/", orders_views.detail_view, name="admin_purchase_order_detail"),
    path("admin/requisitions/", requisitions_views.list_view, name="admin_requisitions_list"),
    path("admin/requisitions/<uuid:pk>/", requisitions_views.detail_view, name="admin_requisition_detail"),
    path("admin/invoices/", invoices_views.list_view, name="admin_invoices_list"),
    path("admin/invoices/<uuid:invoice_id>/", invoices_views.invoice_detail_view, name="admin_invoice_detail"),
    path("admin/contracts/", contracts_views.list_view, name="admin_contracts_list"),
    path("admin/contracts/<uuid:contract_id>/", contracts_views.detail_view, name="admin_contract_detail"),
    path("admin/vendors/", vendors_views.vendor_list_view, name="admin_vendors_list"),
    path("admin/vendors/<uuid:vendor_id>/", vendors_views.vendor_overview_view, name="admin_vendor_detail"),
    path("admin/receipts/<uuid:grn_id>/", receipts_views.receipt_detail_view, name="admin_receipt_detail"),
    path("admin/receipts/<uuid:grn_id>/inspect/", receipts_views.receipt_inspect_view, name="admin_receipt_inspect"),
    path("admin/sourcing-events/", sourcing_views.sourcing_list_view, name="admin_sourcing_list"),
    path("admin/sourcing-events/<uuid:event_id>/", sourcing_views.sourcing_detail_view, name="admin_sourcing_detail"),
    path("admin/organizations/", superadmin_views.org_list_view, name="admin_organizations"),
    path("admin/organizations/create/", superadmin_views.org_create_view, name="admin_org_create"),
    path("admin/organizations/<uuid:org_id>/", superadmin_views.org_dashboard_view, name="admin_org_dashboard"),
    path("admin/organizations/<uuid:org_id>/view/", superadmin_views.org_detail_view, name="admin_org_detail"),
    path("admin/organizations/<uuid:org_id>/edit/", superadmin_views.org_edit_view, name="admin_org_edit"),
    path("admin/organizations/<uuid:org_id>/delete/", superadmin_views.org_delete_view, name="admin_org_delete"),
    path(
        "admin/organizations/<uuid:org_id>/resend-credentials/",
        superadmin_views.org_resend_credentials_view,
        name="admin_org_resend_credentials",
    ),
    path("admin/contact-enquiries/", superadmin_views.contact_enquiry_list_view, name="admin_contact_enquiries"),
    path(
        "admin/contact-enquiries/<uuid:enquiry_id>/status/",
        superadmin_views.contact_enquiry_status_view,
        name="admin_contact_enquiry_status",
    ),
    path("admin/", admin.site.urls),
    path("login/", portal_login_view, name="login"),
    path("login", portal_login_view),
    path("logout", portal_logout_view),
    path("logout/", portal_logout_view, name="logout"),
    path("password-change/", password_change_view, name="password_change"),
    path("accounts/change-password/", change_password_view, name="change_password"),
    # Superadmin, Manager & Vendor Portals
    path("superadmin/", include("apps.core.superadmin_urls")),
    path("manager/", include("apps.core.manager_urls")),
    path("manager/api/", include("apps.core.manager_api_urls")),
    path("vendor/", include("apps.vendors.vendor_dashboard_urls")),
    # Public landing website - included BEFORE apps.core.urls so that
    # anonymous traffic to "/" resolves to landing_home. Authenticated
    # users are forwarded inside landing_home via direct Python call.
    path("", include("apps.core.landing_urls")),
    # Core & Auth & Page Views
    path("", include("apps.core.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.requisitions.urls")),
    path("", include("apps.approvals.urls")),
    path("", include("apps.vendors.urls")),
    path("", include("apps.sourcing.urls")),
    path("", include("apps.orders.urls")),
    path("", include("apps.receipts.urls")),
    path("invoices/", include("apps.invoices.urls")),
    path("", include("apps.contracts.urls")),
    path("", include("apps.scorecards.urls")),
    path("", include("apps.notifications.urls")),
    path("reports/", include("apps.reports.urls")),
    path("", include("apps.audit.urls")),
    path("budgets/", include("apps.budgets.urls")),
    # REST API v1
    path("api/v1/auth/login/", SessionLoginView.as_view(), name="session_login"),
    path("api/v1/auth/token/", SessionLoginView.as_view(), name="token_obtain_pair"),
    path("api/v1/auth/token/refresh/", SessionRefreshView.as_view(), name="token_refresh"),
    path("api/v1/auth/session-keepalive/", SessionKeepAliveView.as_view(), name="session_keepalive"),
    path("api/v1/auth/logout/", SessionLogoutView.as_view(), name="session_logout"),
    path("api/v1/auth/", include("apps.accounts.api_urls")),
    path("api/v1/accounts/", include("apps.accounts.api_urls")),
    path("api/v1/organization/", include("apps.organization.api_urls")),
    path("api/v1/approvals/", include("apps.approvals.api_urls")),
    path("api/v1/vendors/", include("apps.vendors.api_urls")),
    path("api/v1/requisitions/", include("apps.requisitions.api_urls")),
    path("api/v1/sourcing/", include("apps.sourcing.api_urls")),
    path("api/v1/sourcing-events/", include("apps.sourcing.api_urls")),
    path("api/v1/orders/", include("apps.orders.api_urls")),
    path("api/v1/purchase-orders/", include("apps.orders.api_urls")),
    path("api/v1/receipts/", include("apps.receipts.api_urls")),
    path("api/v1/invoices/", include("apps.invoices.api_urls")),
    path("api/v1/contracts/", include("apps.contracts.api_urls")),
    path("api/v1/scorecards/", include("apps.scorecards.api_urls")),
    path("api/v1/budgets/", include("apps.budgets.api_urls")),
    path("api/v1/notifications/", include("apps.notifications.api_urls")),
    path("api/v1/reports/", include("apps.reports.api_urls")),
    path("api/v1/audit/", include("apps.audit.api_urls")),
    path("api/v1/manager/", include("apps.core.manager_api_urls")),
    # OpenAPI Schema & Documentation
    path("api/schema", SpectacularAPIView.as_view()),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/v1/schema", api_schema_dispatch_view),
    path("api/v1/schema/", api_schema_dispatch_view, name="v1-schema"),
    path("api/v1/docs", SpectacularSwaggerView.as_view(url_name="schema")),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="v1-swagger-ui"),
    # Catch-all for unknown /api/ routes ensuring consistent JSON error response
    re_path(r"^api/.*$", custom_api_404_view, name="api_not_found"),
    # Protected Media Serving (Audit-controlled access)
    re_path(
        r"^media/vendor_docs/(?P<file_path>.*)$",
        protected_vendor_document_view,
        name="protected_vendor_document",
    ),
    path(
        "media/vendor_documents/<path:file_path>",
        protected_vendor_document_view,
        name="protected_vendor_document_alt",
    ),
    re_path(r"^media/(?P<file_path>.*)$", protected_media_view, name="protected_media"),
    # Catch-all fallback for non-existent routes (ensures branded 404 is rendered even in DEBUG mode)
    re_path(r"^.*$", custom_404_view, name="custom_404_fallback"),
]

# Custom HTTP Error Handlers
handler404 = custom_404_view
handler500 = "apps.core.views.custom_500_view"

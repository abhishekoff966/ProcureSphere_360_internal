from django.urls import path

from .dashboard_api_views import (
    AdminDashboardAPIView,
    DepartmentApproverDashboardAPIView,
    DepartmentApproverDecisionAPIView,
    DepartmentApproverQueueAPIView,
    SuperAdminDashboardAPIView,
)

urlpatterns = [
    path("superadmin/dashboard/", SuperAdminDashboardAPIView.as_view(), name="api_superadmin_dashboard"),
    path("admin/dashboard/", AdminDashboardAPIView.as_view(), name="api_admin_dashboard"),
    path("approver/dashboard/", DepartmentApproverDashboardAPIView.as_view(), name="api_approver_dashboard"),
    path("approver/queue/", DepartmentApproverQueueAPIView.as_view(), name="api_approver_queue"),
    path("approver/decision/", DepartmentApproverDecisionAPIView.as_view(), name="api_approver_decision"),
]


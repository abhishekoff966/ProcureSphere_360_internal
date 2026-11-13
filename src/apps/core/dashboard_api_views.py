from decimal import Decimal

from django.db.models import Q
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Role, User
from apps.audit.models import AuditLog
from apps.budgets.models import Budget
from apps.core.superadmin_views import _is_super_admin, get_user_scoped_org
from apps.invoices.models import MatchException
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PurchaseRequisition


# --- Serializers for OpenAPI Schema Documentation ---

class SuperAdminDashboardResponseSerializer(serializers.Serializer):
    total_organizations = serializers.IntegerField(help_text="Total number of tenant organizations")
    active_tenants = serializers.IntegerField(help_text="Number of active tenant organizations")
    total_users = serializers.IntegerField(help_text="Total registered user accounts")
    total_departments = serializers.IntegerField(help_text="Total departments across scopes")
    total_cost_centers = serializers.IntegerField(help_text="Total cost centers across scopes")
    total_budgets = serializers.IntegerField(help_text="Total budget allocations")
    pending_prs_count = serializers.IntegerField(help_text="Requisitions currently awaiting approval")
    recent_audits_count = serializers.IntegerField(help_text="System audit events logged today")
    scoped_organization = serializers.CharField(allow_null=True, help_text="Scope restricted organization code if tenant admin")


class AdminDashboardResponseSerializer(serializers.Serializer):
    organization_name = serializers.CharField(help_text="Scoped organization name")
    organization_code = serializers.CharField(help_text="Scoped organization code")
    total_users = serializers.IntegerField(help_text="Total users in this organization")
    total_departments = serializers.IntegerField(help_text="Total departments in this organization")
    total_cost_centers = serializers.IntegerField(help_text="Total cost centers in this organization")
    total_budgets = serializers.IntegerField(help_text="Total budgets configured")
    pending_prs_count = serializers.IntegerField(help_text="Pending PRs in this organization")
    open_exceptions_count = serializers.IntegerField(help_text="Open 3-way match exceptions")


class DepartmentApproverDashboardResponseSerializer(serializers.Serializer):
    department_name = serializers.CharField(help_text="Approver's designated department name")
    department_code = serializers.CharField(help_text="Approver's designated department code")
    organization_name = serializers.CharField(help_text="Organization name")
    total_requisitions = serializers.IntegerField(help_text="Total purchase requisitions in department")
    approvals_executed = serializers.IntegerField(help_text="Requisitions approved or rejected by this approver")
    pending_approvals = serializers.IntegerField(help_text="Requisitions currently in pending queue")
    available_budget = serializers.DecimalField(max_digits=16, decimal_places=2, help_text="Available budget across department cost centers")


class PendingRequisitionItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text="Requisition UUID")
    pr_number = serializers.CharField(help_text="Formatted PR number e.g. PR-2026-0001")
    title = serializers.CharField(help_text="Requisition title")
    requester_email = serializers.CharField(help_text="Requester user email")
    department_name = serializers.CharField(help_text="Department name")
    cost_center_code = serializers.CharField(allow_null=True, help_text="Cost center code")
    total_amount = serializers.DecimalField(max_digits=14, decimal_places=2, help_text="Total amount")
    currency = serializers.CharField(help_text="Currency code e.g. USD, INR")
    status = serializers.CharField(help_text="Requisition workflow status")
    created_at = serializers.DateTimeField(help_text="Creation timestamp")
    urgency = serializers.CharField(help_text="Urgency level e.g. LOW, MEDIUM, HIGH, CRITICAL")


class ApproverDecisionRequestSerializer(serializers.Serializer):
    requisition_id = serializers.UUIDField(help_text="UUID of the purchase requisition to decide upon")
    action = serializers.ChoiceField(choices=["APPROVE", "REJECT"], help_text="Decision action: APPROVE or REJECT")
    comments = serializers.CharField(required=False, default="", allow_blank=True, help_text="Review comments or rejection rationale")


class ApproverDecisionResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField(help_text="Whether the approval action succeeded")
    requisition_id = serializers.UUIDField(help_text="Requisition UUID")
    new_status = serializers.CharField(help_text="New requisition workflow status")
    message = serializers.CharField(help_text="Confirmation message")


# --- API Views ---

class SuperAdminDashboardAPIView(APIView):
    """
    Super Admin Control Center metrics: organizations, active tenants, users, departments, and audit summary.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Super Admin Dashboard"],
        summary="Get Super Admin Dashboard KPIs and Control Center Summary",
        description="Returns system-wide or scoped metrics including organizations, tenants, departments, cost centers, and pending PRs.",
        responses={200: SuperAdminDashboardResponseSerializer},
    )
    def get(self, request):
        if not _is_super_admin(request.user):
            return Response({"error": {"code": "FORBIDDEN", "message": "Super Admin access required."}}, status=status.HTTP_403_FORBIDDEN)

        scoped_org = get_user_scoped_org(request.user)
        today = timezone.localdate()

        orgs_qs = Organization.objects.all()
        users_qs = User.objects.all()
        dept_qs = Department.objects.all()
        cc_qs = CostCenter.objects.all()
        budgets_qs = Budget.objects.all()
        prs_qs = PurchaseRequisition.objects.filter(
            status__in=[
                PurchaseRequisition.STATUS_SUBMITTED,
                PurchaseRequisition.STATUS_MANAGER_REVIEW,
                PurchaseRequisition.STATUS_BUDGET_REVIEW,
            ]
        )
        audits_qs = AuditLog.objects.filter(timestamp__date=today)

        if scoped_org:
            orgs_qs = orgs_qs.filter(id=scoped_org.id)
            users_qs = users_qs.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org))
            dept_qs = dept_qs.filter(organization=scoped_org)
            cc_qs = cc_qs.filter(department__organization=scoped_org)
            budgets_qs = budgets_qs.filter(cost_center__department__organization=scoped_org)
            prs_qs = prs_qs.filter(department__organization=scoped_org)
            audits_qs = audits_qs.filter(
                Q(actor__organization=scoped_org) | Q(actor__department__organization=scoped_org)
            )

        data = {
            "total_organizations": orgs_qs.count(),
            "active_tenants": orgs_qs.filter(is_active=True).count(),
            "total_users": users_qs.count(),
            "total_departments": dept_qs.count(),
            "total_cost_centers": cc_qs.count(),
            "total_budgets": budgets_qs.count(),
            "pending_prs_count": prs_qs.count(),
            "recent_audits_count": audits_qs.count(),
            "scoped_organization": scoped_org.code if scoped_org else None,
        }
        return Response(data, status=status.HTTP_200_OK)


class AdminDashboardAPIView(APIView):
    """
    Tenant Admin Workspace metrics: department, cost center, user counts, and open exceptions.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Admin Dashboard"],
        summary="Get Tenant Admin Workspace Overview",
        description="Returns organization-scoped metrics for Tenant Administrators including users, departments, cost centers, and financial controls.",
        responses={200: AdminDashboardResponseSerializer},
    )
    def get(self, request):
        if not _is_super_admin(request.user):
            return Response({"error": {"code": "FORBIDDEN", "message": "Admin access required."}}, status=status.HTTP_403_FORBIDDEN)

        scoped_org = get_user_scoped_org(request.user) or getattr(request.user, "effective_organization", None) or getattr(request.user, "organization", None)
        if not scoped_org:
            scoped_org = Organization.objects.filter(is_active=True).order_by("name").first()

        org_name = scoped_org.name if scoped_org else "Platform"
        org_code = scoped_org.code if scoped_org else "GLOBAL"

        users_count = User.objects.filter(Q(organization=scoped_org) | Q(department__organization=scoped_org)).count() if scoped_org else User.objects.count()
        depts_count = Department.objects.filter(organization=scoped_org).count() if scoped_org else Department.objects.count()
        ccs_count = CostCenter.objects.filter(department__organization=scoped_org).count() if scoped_org else CostCenter.objects.count()
        budgets_count = Budget.objects.filter(cost_center__department__organization=scoped_org).count() if scoped_org else Budget.objects.count()
        pending_prs = PurchaseRequisition.objects.filter(
            status__in=[
                PurchaseRequisition.STATUS_SUBMITTED,
                PurchaseRequisition.STATUS_MANAGER_REVIEW,
                PurchaseRequisition.STATUS_BUDGET_REVIEW,
            ]
        )
        if scoped_org:
            pending_prs = pending_prs.filter(department__organization=scoped_org)

        open_exceptions = MatchException.objects.filter(status=MatchException.STATUS_OPEN)
        if scoped_org:
            open_exceptions = open_exceptions.filter(invoice__po__requisition__department__organization=scoped_org)

        data = {
            "organization_name": org_name,
            "organization_code": org_code,
            "total_users": users_count,
            "total_departments": depts_count,
            "total_cost_centers": ccs_count,
            "total_budgets": budgets_count,
            "pending_prs_count": pending_prs.count(),
            "open_exceptions_count": open_exceptions.count(),
        }
        return Response(data, status=status.HTTP_200_OK)


class DepartmentApproverDashboardAPIView(APIView):
    """
    Department Approver Workspace overview: pending queue counts, executed approvals, and available department budget.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Department Approver Dashboard"],
        summary="Get Department Approver Workspace Metrics",
        description="Returns KPIs for the authenticated Department Approver including total PRs, pending queue count, completed approvals, and available budget.",
        responses={200: DepartmentApproverDashboardResponseSerializer},
    )
    def get(self, request):
        user = request.user
        role_code = getattr(user, "role_code", None) or (user.role.code if getattr(user, "role", None) else None)
        if not user.is_superuser and role_code not in [Role.DEPT_APPROVER, Role.SUPER_ADMIN]:
            return Response({"error": {"code": "FORBIDDEN", "message": "Department Approver access required."}}, status=status.HTTP_403_FORBIDDEN)

        user_dept = getattr(user, "department", None)
        if not user_dept:
            managed_cc = CostCenter.objects.filter(manager=user).select_related("department").first()
            if managed_cc and managed_cc.department:
                user_dept = managed_cc.department
            elif getattr(user, "organization", None):
                user_dept = Department.objects.filter(organization=user.organization).first()

        user_org = getattr(user, "organization", None) or (user_dept.organization if user_dept else None)

        prs_qs = PurchaseRequisition.objects.all()
        if user_dept:
            prs_qs = prs_qs.filter(department=user_dept)
        elif user_org:
            prs_qs = prs_qs.filter(department__organization=user_org)

        total_requisitions = prs_qs.count()

        pending_statuses = [
            PurchaseRequisition.STATUS_SUBMITTED,
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
        ]
        pending_approvals = prs_qs.filter(status__in=pending_statuses).count()

        from apps.approvals.models import ApprovalAction
        approvals_executed = ApprovalAction.objects.filter(actor=user).count()

        available_budget = Decimal("0.00")
        if user_dept:
            ccs = CostCenter.objects.filter(department=user_dept)
            budgets = Budget.objects.filter(cost_center__in=ccs)
            for b in budgets:
                available_budget += (b.allocated_amount - b.actual_spend - b.committed_spend)
        elif user_org:
            budgets = Budget.objects.filter(cost_center__department__organization=user_org)
            for b in budgets:
                available_budget += (b.allocated_amount - b.actual_spend - b.committed_spend)

        data = {
            "department_name": user_dept.name if user_dept else "All Departments",
            "department_code": user_dept.code if user_dept else "ALL",
            "organization_name": user_org.name if user_org else "ProcureSphere 360",
            "total_requisitions": total_requisitions,
            "approvals_executed": approvals_executed,
            "pending_approvals": pending_approvals,
            "available_budget": max(Decimal("0.00"), available_budget),
        }
        return Response(data, status=status.HTTP_200_OK)


class DepartmentApproverQueueAPIView(APIView):
    """
    Pending Purchase Requisitions awaiting Department Approver review.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Department Approver Dashboard"],
        summary="List Pending Approval Queue",
        description="Returns the list of purchase requisitions currently awaiting review and decision by the department approver.",
        responses={200: PendingRequisitionItemSerializer(many=True)},
    )
    def get(self, request):
        user = request.user
        role_code = getattr(user, "role_code", None) or (user.role.code if getattr(user, "role", None) else None)
        if not user.is_superuser and role_code not in [Role.DEPT_APPROVER, Role.SUPER_ADMIN]:
            return Response({"error": {"code": "FORBIDDEN", "message": "Department Approver access required."}}, status=status.HTTP_403_FORBIDDEN)

        user_dept = getattr(user, "department", None)
        user_org = getattr(user, "organization", None) or (user_dept.organization if user_dept else None)

        pending_statuses = [
            PurchaseRequisition.STATUS_SUBMITTED,
            PurchaseRequisition.STATUS_MANAGER_REVIEW,
        ]
        prs_qs = PurchaseRequisition.objects.filter(status__in=pending_statuses).select_related(
            "requester", "department", "cost_center"
        ).order_by("-created_at")

        if user_dept:
            prs_qs = prs_qs.filter(Q(department=user_dept) | Q(department__organization=user_dept.organization))
        elif user_org:
            prs_qs = prs_qs.filter(department__organization=user_org)

        results = []
        for pr in prs_qs:
            results.append({
                "id": pr.id,
                "pr_number": pr.pr_number,
                "title": pr.title,
                "requester_email": pr.requester.email if pr.requester else "Unknown",
                "department_name": pr.department.name if pr.department else "Unassigned",
                "cost_center_code": pr.cost_center.code if pr.cost_center else None,
                "total_amount": pr.total_amount,
                "currency": pr.currency,
                "status": pr.status,
                "created_at": pr.created_at,
                "urgency": pr.urgency,
            })
        return Response(results, status=status.HTTP_200_OK)


class DepartmentApproverDecisionAPIView(APIView):
    """
    Submit approval or rejection decision for a pending purchase requisition.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=["Department Approver Dashboard"],
        summary="Approve or Reject Purchase Requisition",
        description="Records an approval or rejection action by the Department Approver on a pending purchase requisition.",
        request=ApproverDecisionRequestSerializer,
        responses={200: ApproverDecisionResponseSerializer},
    )
    def post(self, request):
        user = request.user
        role_code = getattr(user, "role_code", None) or (user.role.code if getattr(user, "role", None) else None)
        if not user.is_superuser and role_code not in [Role.DEPT_APPROVER, Role.SUPER_ADMIN]:
            return Response({"error": {"code": "FORBIDDEN", "message": "Department Approver access required."}}, status=status.HTTP_403_FORBIDDEN)

        serializer = ApproverDecisionRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        req_id = serializer.validated_data["requisition_id"]
        action_name = serializer.validated_data["action"].upper()
        comments = serializer.validated_data.get("comments", "").strip()

        pr = PurchaseRequisition.objects.filter(id=req_id).first()
        if not pr:
            return Response({"error": {"code": "NOT_FOUND", "message": f"Requisition '{req_id}' not found."}}, status=status.HTTP_404_NOT_FOUND)

        from apps.approvals.models import ApprovalAction
        from apps.approvals.services import process_approval_action_service

        db_action = ApprovalAction.ACTION_APPROVE if action_name == "APPROVE" else ApprovalAction.ACTION_REJECT
        try:
            process_approval_action_service(
                target_object=pr,
                actor=user,
                action=db_action,
                comments=comments or f"Decision {action_name} executed via Approver API.",
            )
            pr.refresh_from_db()
            return Response({
                "success": True,
                "requisition_id": pr.id,
                "new_status": pr.status,
                "message": f"Requisition {pr.pr_number} successfully {pr.status.lower()}.",
            }, status=status.HTTP_200_OK)
        except Exception as e:
            return Response({"error": {"code": "DECISION_FAILED", "message": str(e)}}, status=status.HTTP_400_BAD_REQUEST)


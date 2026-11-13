from decimal import Decimal

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role, User
from apps.audit.permissions import AuditorReadOnlyPermission

from .models import PRAttachment, PRLine, PurchaseRequisition


class PRLineSerializer(serializers.ModelSerializer):
    estimated_unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)
    unit_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, required=False, write_only=True
    )

    class Meta:
        model = PRLine
        fields = [
            "id",
            "requisition",
            "item_description",
            "quantity",
            "unit_of_measure",
            "estimated_unit_price",
            "unit_price",
            "estimated_total",
            "specifications",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "requisition", "estimated_total", "created_at", "updated_at"]

    def validate(self, attrs):
        if self.instance and self.instance.status != PurchaseRequisition.STATUS_DRAFT:
            raise serializers.ValidationError(
                f"Requisitions in status '{self.instance.status}' cannot be modified. Only DRAFT requisitions can be edited."
            )
        if "unit_price" in attrs and "estimated_unit_price" not in attrs:
            attrs["estimated_unit_price"] = attrs.pop("unit_price")
        elif "unit_price" in attrs:
            attrs.pop("unit_price")
        if "estimated_unit_price" not in attrs:
            raise serializers.ValidationError({"estimated_unit_price": "This field is required."})
        if attrs.get("quantity") is not None:
            if attrs["quantity"] <= Decimal("0.00"):
                raise serializers.ValidationError(
                    {"quantity": "Quantity must be greater than zero."}
                )
            if attrs["quantity"] % 1 != 0:
                raise serializers.ValidationError(
                    {"quantity": "Quantity must be a whole number (no decimal points allowed)."}
                )
        if attrs.get("estimated_unit_price") is not None and attrs[
            "estimated_unit_price"
        ] < Decimal("0.00"):
            raise serializers.ValidationError(
                {"estimated_unit_price": "Estimated unit price cannot be negative."}
            )
        return attrs


class PRAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = PRAttachment
        fields = "__all__"


class RequisitionApproveSerializer(serializers.Serializer):
    comments = serializers.CharField(required=False, allow_blank=True, default="", help_text="Approval or rejection notes.")


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    lines = PRLineSerializer(many=True, required=False)
    attachments = PRAttachmentSerializer(many=True, read_only=True)
    requester_email = serializers.CharField(source="requester.email", read_only=True)
    department_name = serializers.CharField(source="department.name", read_only=True)
    resolved_route = serializers.SerializerMethodField()

    class Meta:
        model = PurchaseRequisition
        fields = "__all__"
        read_only_fields = [
            "id",
            "pr_number",
            "status",
            "total_amount",
            "requester",
            "created_at",
            "updated_at",
            "resolved_route",
        ]

    def get_resolved_route(self, obj):
        from apps.approvals.models import ApprovalPolicy
        from apps.approvals.services import evaluate_approval_chain

        amount = getattr(obj, "total_amount", None) or Decimal("0.00")
        dept = getattr(obj, "department", None)
        cc = getattr(obj, "cost_center", None)
        try:
            chain = evaluate_approval_chain(
                module=ApprovalPolicy.MODULE_PR,
                amount=amount,
                department=dept,
                cost_center=cc,
            )
            steps = []
            for s in chain:
                role_obj = s.get("approver_role")
                spec_approver = s.get("specific_approver")
                role_name = (
                    "Finance / AP"
                    if (role_obj and role_obj.code == Role.FINANCE_AP)
                    else (role_obj.name if role_obj else "")
                )
                steps.append({
                    "step_number": s["step_number"],
                    "role": role_name,
                    "role_code": role_obj.code if role_obj else "",
                    "description": s.get("description", ""),
                    "approver": spec_approver.email if spec_approver else None,
                    "is_mandatory": s.get("is_mandatory", True),
                })
            return {"steps": steps}
        except Exception:
            return {"steps": []}

    def validate_requested_delivery_date(self, value):
        from django.utils import timezone

        if value and value < timezone.now().date():
            raise serializers.ValidationError("Requested delivery date cannot be in the past.")
        return value

    def validate_department(self, value):
        request = self.context.get("request")
        if request and request.user and request.user.is_authenticated and not request.user.is_superuser:
            role_code = getattr(request.user, "role_code", None) or (
                request.user.role.code if hasattr(request.user, "role") and request.user.role else None
            )
            if role_code == Role.REQUESTER and getattr(request.user, "department_id", None):
                if value.id != request.user.department_id:
                    raise serializers.ValidationError(
                        "Requesters can only raise purchase requisitions for their assigned department."
                    )
        return value

    def validate(self, attrs):
        department = attrs.get("department") or getattr(self.instance, "department", None)
        cost_center = attrs.get("cost_center") or getattr(self.instance, "cost_center", None)

        if not cost_center:
            raise serializers.ValidationError({"cost_center": ["Cost center is required."]})
        if not department:
            raise serializers.ValidationError({"department": ["Department is required."]})

        if cost_center.department_id != department.id:
            raise serializers.ValidationError(
                {"cost_center": ["Cost center must belong to the selected department."]}
            )

        request = self.context.get("request")
        user = getattr(request, "user", None)
        if user and not user.is_superuser and getattr(user, "role_code", None) == Role.REQUESTER:
            if getattr(user, "department_id", None) and cost_center:
                if cost_center.department_id != user.department_id:
                    from rest_framework.exceptions import PermissionDenied
                    raise PermissionDenied(
                        {"code": "forbidden", "fields": {"cost_center": ["Cost center not authorized for this requester."]}}
                    )

        # Organization / Tenant isolation check
        if request and request.user and request.user.is_authenticated and not request.user.is_superuser:
            user_org = getattr(request.user, "organization", None) or getattr(request.user, "effective_organization", None)
            if user_org:
                if getattr(department, "organization_id", None) and department.organization_id != user_org.id:
                    raise serializers.ValidationError(
                        {"department": ["Department does not belong to your organization."]}
                    )
                if cost_center.department and getattr(cost_center.department, "organization_id", None) and cost_center.department.organization_id != user_org.id:
                    raise serializers.ValidationError(
                        {"cost_center": ["Cost center does not belong to your organization."]}
                    )

        lines_data = self.initial_data.get("line_items", self.initial_data.get("lines"))
        if self.instance is None:
            if lines_data is None or not isinstance(lines_data, list) or len(lines_data) == 0:
                raise serializers.ValidationError({"lines": ["A requisition must contain at least one line item."]})
        else:
            if lines_data is not None and (not isinstance(lines_data, list) or len(lines_data) == 0):
                raise serializers.ValidationError({"lines": ["A requisition must contain at least one line item."]})

        if lines_data and isinstance(lines_data, list):
            for idx, item in enumerate(lines_data):
                if not isinstance(item, dict):
                    raise serializers.ValidationError(
                        {"lines": [f"Line item {idx + 1} must be an object."]}
                    )
                desc = str(item.get("item_description", "")).strip()
                if not desc:
                    raise serializers.ValidationError(
                        {"lines": [f"Item description is required for line item {idx + 1}."]}
                    )
                qty = item.get("quantity")
                if qty is not None:
                    try:
                        d_qty = Decimal(str(qty))
                        if d_qty <= Decimal("0.00"):
                            raise serializers.ValidationError(
                                {"lines": [f"Quantity must be greater than zero for line item {idx + 1}."]}
                            )
                        if d_qty % 1 != 0:
                            raise serializers.ValidationError(
                                {"lines": [f"Quantity must be a whole number (no decimal points allowed) for line item {idx + 1}."]}
                            )
                    except (TypeError, ValueError, ArithmeticError) as e:
                        if isinstance(e, serializers.ValidationError):
                            raise
                        raise serializers.ValidationError(
                            {"lines": [f"Invalid quantity for line item {idx + 1}."]}
                        )
                price = item.get("estimated_unit_price") if item.get("estimated_unit_price") is not None else item.get("unit_price")
                if price is not None:
                    try:
                        d_price = Decimal(str(price))
                        if d_price < Decimal("0.00"):
                            raise serializers.ValidationError(
                                {"lines": [f"Estimated unit price cannot be negative for line item {idx + 1}."]}
                            )
                    except (TypeError, ValueError, ArithmeticError):
                        raise serializers.ValidationError(
                            {"lines": [f"Invalid estimated unit price for line item {idx + 1}."]}
                        )

        return attrs

    def create(self, validated_data):
        from django.db import transaction

        with transaction.atomic():
            lines_data = self.initial_data.get("line_items", self.initial_data.get("lines", []))
            if not lines_data:
                raise serializers.ValidationError({"lines": ["At least one line item is required."]})
            validated_data.pop("lines", None)

            if not validated_data.get("pr_number"):
                from apps.core.services import generate_document_number

                validated_data["pr_number"] = generate_document_number(
                    PurchaseRequisition, "pr_number", "PR"
                )

            pr = super().create(validated_data)

            total = Decimal("0.00")
            for item in lines_data:
                qty = Decimal(str(item.get("quantity", 1)))
                if qty <= Decimal("0.00"):
                    raise serializers.ValidationError(
                        {"quantity": "Quantity must be greater than zero."}
                    )
                if qty % 1 != 0:
                    raise serializers.ValidationError(
                        {"quantity": "Quantity must be a whole number (no decimal points allowed)."}
                    )
                price = Decimal(
                    str(
                        item.get("estimated_unit_price")
                        if item.get("estimated_unit_price") is not None
                        else item.get("unit_price", 0)
                    )
                )
                line_tot = qty * price
                total += line_tot
                PRLine.objects.create(
                    requisition=pr,
                    item_description=item.get("item_description", "Line Item"),
                    quantity=qty,
                    unit_of_measure=item.get("unit_of_measure", "EA"),
                    estimated_unit_price=price,
                    estimated_total=line_tot,
                    specifications=item.get("specifications", ""),
                )

            if total > Decimal("0.00"):
                pr.total_amount = total
                pr.save(update_fields=["total_amount"])

            from apps.audit.models import AuditLog

            req_user = (
                self.context.get("request").user
                if self.context.get("request")
                else getattr(pr, "requester", None)
            )
            AuditLog.objects.create(
                actor=req_user if req_user and req_user.is_authenticated else None,
                action=AuditLog.ACTION_CREATE,
                target_model="PurchaseRequisition",
                target_object_id=str(pr.id),
                new_state={
                    "pr_number": pr.pr_number,
                    "status": pr.status,
                    "total_amount": str(pr.total_amount),
                },
            )

            return pr

    def update(self, instance, validated_data):
        lines_data = self.initial_data.get("line_items", self.initial_data.get("lines", None))
        validated_data.pop("lines", None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if lines_data is not None:
            instance.lines.all().delete()
            total = Decimal("0.00")
            for item in lines_data:
                qty = Decimal(str(item.get("quantity", 1)))
                if qty <= Decimal("0.00"):
                    raise serializers.ValidationError(
                        {"quantity": "Quantity must be greater than zero."}
                    )
                if qty % 1 != 0:
                    raise serializers.ValidationError(
                        {"quantity": "Quantity must be a whole number (no decimal points allowed)."}
                    )
                price = Decimal(
                    str(
                        item.get("estimated_unit_price")
                        if item.get("estimated_unit_price") is not None
                        else item.get("unit_price", 0)
                    )
                )
                line_tot = qty * price
                total += line_tot
                PRLine.objects.create(
                    requisition=instance,
                    item_description=item.get("item_description", "Line Item"),
                    quantity=qty,
                    unit_of_measure=item.get("unit_of_measure", "EA"),
                    estimated_unit_price=price,
                    estimated_total=line_tot,
                    specifications=item.get("specifications", ""),
                )
            if total > Decimal("0.00"):
                instance.total_amount = total
                instance.save(update_fields=["total_amount"])

        return instance


class CanCreateRequisitionPermission(permissions.BasePermission):
    """
    Requisition RBAC:
    - Vendors are completely blocked from internal requisitions (403).
    - Writes (create, update, delete) are strictly limited to Requesters and Super Admins.
    - Auditors, approvers, vendors receive 403 before payload validation.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if hasattr(request.user, "role") and request.user.role else None
        )

        if not request.user.is_superuser and (
            getattr(request.user, "is_vendor", False)
            or role_code in [Role.VENDOR_USER]
        ):
            return False

        if request.method in ["POST", "PUT", "PATCH", "DELETE"]:
            if request.user.is_superuser:
                return True
            action = getattr(view, "action", None)
            if action in ["approve", "reject"]:
                return role_code in [
                    Role.DEPT_APPROVER,
                    Role.FINANCE_AP,
                    Role.SUPER_ADMIN,
                ]
            if action in ["submit", "cancel"]:
                return role_code in [Role.REQUESTER, Role.PROC_MGR, Role.SUPER_ADMIN]
            return role_code in [Role.REQUESTER, Role.SUPER_ADMIN]

        return True


class PurchaseRequisitionViewSet(viewsets.ModelViewSet):
    queryset = (
        PurchaseRequisition.objects.select_related("requester", "department", "cost_center")
        .prefetch_related("lines", "attachments")
        .all()
        .order_by("-created_at")
    )
    serializer_class = PurchaseRequisitionSerializer
    permission_classes = [
        IsAuthenticated,
        AuditorReadOnlyPermission,
        CanCreateRequisitionPermission,
    ]

    def get_queryset(self):
        user = self.request.user
        if not user or not user.is_authenticated:
            return self.queryset.none()

        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )

        # External vendor users have zero access to requisitions
        if getattr(user, "is_vendor", False) or role_code in [Role.VENDOR_USER, "VENDOR_USER"]:
            from rest_framework.exceptions import PermissionDenied
            raise PermissionDenied("Access to purchase requisitions is denied for your role.")

        if user.is_superuser or role_code in [
            Role.SUPER_ADMIN,
            Role.PROC_EXEC,
            Role.PROC_MGR,
            Role.AUDITOR,
            Role.FINANCE_AP,
            Role.STORES_RECEIVER,
            Role.LEGAL_MGR,
        ]:
            qs = self.queryset
        elif role_code == Role.DEPT_APPROVER:
            from django.db.models import Q
            user_org = getattr(user, "organization", None) or (
                user.department.organization if user.department else None
            )
            if user.department_id:
                from apps.organization.models import Department

                depts_with_approvers = Department.objects.filter(
                    organization=user_org,
                    users__role__code=Role.DEPT_APPROVER,
                    users__is_active=True,
                ).exclude(id=user.department_id)

                qs = self.queryset.filter(
                    Q(department_id=user.department_id)
                    | Q(cost_center__manager=user)
                    | (
                        Q(department__organization=user_org)
                        & ~Q(department__in=depts_with_approvers)
                    )
                ).distinct()
            elif user_org:
                qs = self.queryset.filter(
                    Q(department__organization=user_org) | Q(cost_center__manager=user)
                ).distinct()
            else:
                qs = self.queryset.all()
        else:
            # Requesters are strictly scoped to their own requisitions (prevents IDOR)
            qs = self.queryset.filter(requester=user)

        # Filter by search
        search_param = (
            self.request.query_params.get("search", "").strip()
            or self.request.query_params.get("q", "").strip()
        )
        if search_param:
            from django.db.models import Q
            qs = qs.filter(
                Q(pr_number__icontains=search_param)
                | Q(title__icontains=search_param)
                | Q(justification__icontains=search_param)
            )

        # Filter by status
        status_param = self.request.query_params.get("status", "").strip().upper()
        if status_param:
            valid_statuses = [c[0] for c in PurchaseRequisition.STATUS_CHOICES]
            if status_param not in valid_statuses:
                raise serializers.ValidationError(
                    {
                        "status": f"Invalid status '{status_param}'. Valid choices: {', '.join(valid_statuses)}"
                    }
                )
            qs = qs.filter(status=status_param)

        return qs

    def perform_create(self, serializer):
        user = self.request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if not user.is_superuser and role_code not in [Role.REQUESTER, Role.SUPER_ADMIN]:
            from rest_framework.exceptions import PermissionDenied

            raise PermissionDenied(
                "Only users with the Requester role can raise Purchase Requisitions."
            )
        serializer.save(requester=user)

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.status != PurchaseRequisition.STATUS_DRAFT:
            raise serializers.ValidationError(
                {"status": [f"Requisitions in '{instance.status}' status cannot be edited. Only DRAFT requisitions can be edited."]}
            )
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status != PurchaseRequisition.STATUS_DRAFT:
            raise serializers.ValidationError(
                {"status": [f"Requisitions in '{instance.status}' status cannot be deleted."]}
            )
        instance.delete()

    @action(detail=True, methods=["post"], url_path="submit")
    def submit(self, request, pk=None):
        pr = self.get_object()
        from apps.requisitions.services import submit_purchase_requisition_service

        try:
            updated_pr = submit_purchase_requisition_service(requisition=pr, user=request.user)
        except Exception as e:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": str(e),
                        "details": e.messages if hasattr(e, "messages") else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @extend_schema(exclude=True)
    @action(detail=True, methods=["get"], url_path="approval-route")
    def approval_route(self, request, pk=None):
        pr = self.get_object()
        serializer = self.get_serializer(pr)
        route = serializer.data.get("resolved_route") or {"steps": []}
        return Response(route, status=status.HTTP_200_OK)

    @extend_schema(
        summary="Approve Purchase Requisition",
        description="Approves a submitted purchase requisition. Requires authorized approver.",
        request=RequisitionApproveSerializer,
        responses={
            200: PurchaseRequisitionSerializer,
            400: OpenApiResponse(description="Validation error or invalid status."),
            403: OpenApiResponse(description="Permission denied or self-approval."),
            404: OpenApiResponse(description="Requisition not found."),
        },
    )
    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if pr.requester_id == user.id and not user.is_superuser:
            raise permissions.exceptions.PermissionDenied(
                "Self-approval is strictly prohibited: Requesters cannot approve their own Purchase Requisitions."
            )
        if not user.is_superuser and role_code not in [
            Role.DEPT_APPROVER,
            Role.PROC_MGR,
            Role.FINANCE_AP,
            Role.SUPER_ADMIN,
        ]:
            raise permissions.exceptions.PermissionDenied(
                "You do not have permission to approve Purchase Requisitions."
            )
        if role_code == Role.DEPT_APPROVER:
            req_dept = pr.department or (pr.cost_center.department if pr.cost_center else None)
            if (user.department_id and req_dept and user.department_id != req_dept.id) and not (pr.cost_center and user.id == pr.cost_center.manager_id):
                has_own_approver = (
                    User.objects.filter(
                        role__code=Role.DEPT_APPROVER,
                        department=req_dept,
                        is_active=True,
                    )
                    .exclude(id=user.id)
                    .exists()
                )
                if has_own_approver:
                    raise permissions.exceptions.PermissionDenied(
                        "Department Approver can only approve requisitions for their own department."
                    )
        from apps.requisitions.services import approve_purchase_requisition_service

        comments = request.data.get("comments", "")
        try:
            updated_pr = approve_purchase_requisition_service(
                requisition=pr, approver=user, comments=comments
            )
        except Exception as e:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": str(e),
                        "details": e.messages if hasattr(e, "messages") else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if pr.status in [PurchaseRequisition.STATUS_SUBMITTED, PurchaseRequisition.STATUS_MANAGER_REVIEW]:
            if not user.is_superuser and role_code not in [Role.DEPT_APPROVER, Role.PROC_MGR, Role.SUPER_ADMIN]:
                raise permissions.exceptions.PermissionDenied(
                    "Only Department Approvers, Procurement Managers, or Super Admins can reject requisitions in Manager Review."
                )
        elif pr.status == PurchaseRequisition.STATUS_BUDGET_REVIEW:
            if not user.is_superuser and role_code not in [Role.FINANCE_AP, Role.PROC_MGR, Role.SUPER_ADMIN]:
                raise permissions.exceptions.PermissionDenied(
                    "Only Finance/AP or Procurement Managers can reject requisitions in Budget Review."
                )
        else:
            if not user.is_superuser and role_code not in [Role.DEPT_APPROVER, Role.PROC_MGR, Role.SUPER_ADMIN]:
                raise permissions.exceptions.PermissionDenied(
                    "You do not have permission to reject Purchase Requisitions."
                )
        comments = (request.data.get("comments") or request.data.get("reason") or "").strip()
        if not comments:
            raise serializers.ValidationError(
                {"comments": "Rejection comments/justification are mandatory."}
            )
        from apps.requisitions.services import reject_purchase_requisition_service

        updated_pr = reject_purchase_requisition_service(
            requisition=pr, approver=user, comments=comments
        )
        return Response(self.get_serializer(updated_pr).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="cancel")
    def cancel(self, request, pk=None):
        pr = self.get_object()
        user = request.user
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        if pr.requester_id != user.id and not user.is_superuser and role_code != Role.PROC_MGR:
            raise permissions.exceptions.PermissionDenied(
                "Only the original requester or a procurement manager can cancel this requisition."
            )
        if pr.status in [
            PurchaseRequisition.STATUS_PO_ISSUED,
            PurchaseRequisition.STATUS_CANCELLED,
            PurchaseRequisition.STATUS_SOURCING,
            PurchaseRequisition.STATUS_REJECTED,
        ]:
            raise serializers.ValidationError(
                {"status": f"Cannot cancel requisition in '{pr.status}' status."}
            )
        from apps.requisitions.services import release_budget_reservation_service

        previous_status = pr.status
        pr.status = PurchaseRequisition.STATUS_CANCELLED
        pr.save(update_fields=["status", "updated_at"])
        release_budget_reservation_service(requisition=pr, user=user, reason="PR cancelled by user")

        from apps.audit.models import AuditLog

        AuditLog.objects.create(
            actor=user if user and user.is_authenticated else None,
            action=AuditLog.ACTION_UPDATE,
            target_model="PurchaseRequisition",
            target_object_id=str(pr.id),
            previous_state={"status": previous_status},
            new_state={"status": PurchaseRequisition.STATUS_CANCELLED},
        )
        return Response(self.get_serializer(pr).data, status=status.HTTP_200_OK)

    def destroy(self, request, *args, **kwargs):
        pr = self.get_object()
        user = request.user
        if pr.status != PurchaseRequisition.STATUS_DRAFT:
            raise serializers.ValidationError(
                {
                    "status": f"Cannot delete requisition in '{pr.status}' status. Only DRAFT requisitions can be deleted."
                }
            )
        role_code = getattr(user, "role_code", None) or (
            user.role.code if hasattr(user, "role") and user.role else None
        )
        is_admin = user.is_superuser or role_code == Role.SUPER_ADMIN
        is_owner_draft = (
            role_code == Role.REQUESTER
            and pr.requester_id == user.id
            and pr.status == PurchaseRequisition.STATUS_DRAFT
        )
        if not (is_admin or is_owner_draft):
            raise permissions.exceptions.PermissionDenied(
                "Permission Denied: Requisition deletion is strictly restricted to Super Admin, or the requester for draft requisitions."
            )
        # Soft-cancel instead of hard delete to preserve audit history (PR-02)
        previous_status = pr.status
        pr.status = PurchaseRequisition.STATUS_CANCELLED
        pr.save(update_fields=["status", "updated_at"])

        from apps.audit.models import AuditLog

        AuditLog.objects.create(
            actor=user if user and user.is_authenticated else None,
            action=AuditLog.ACTION_DELETE,
            target_model="PurchaseRequisition",
            target_object_id=str(pr.id),
            previous_state={"status": previous_status},
            new_state={"status": PurchaseRequisition.STATUS_CANCELLED},
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


router = DefaultRouter()
router.register(r"", PurchaseRequisitionViewSet, basename="requisition")

urlpatterns = router.urls

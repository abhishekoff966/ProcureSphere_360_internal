import django_filters
from django.apps import apps
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import filters, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.accounts.permissions import IsAuditorReadOnly, IsNotVendorUser

from .models import ApprovalAction, ApprovalPolicy, ApprovalStep
from .services import process_approval_action_service, record_approval_action_service


class CanManageApprovalPolicy(BasePermission):
    """
    Super Admin and Procurement Manager can manage approval policies.
    Internal staff can view them.
    External vendor users and requesters/auditors cannot mutate policies.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if (
            getattr(request.user, "is_vendor", False)
            or getattr(request.user, "role_code", None) == Role.VENDOR_USER
        ):
            return False

        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        if request.method in SAFE_METHODS:
            if not request.user.is_superuser and role_code in [Role.STORES_RECEIVER, Role.LEGAL_MGR]:
                return False
            return True

        return (
            request.user.is_superuser
            or role_code in [Role.SUPER_ADMIN, Role.PROC_MGR]
        )


class ApprovalPolicySerializer(serializers.ModelSerializer):
    class Meta:
        model = ApprovalPolicy
        fields = "__all__"

    def validate(self, attrs):
        from decimal import Decimal
        min_amount = attrs.get("min_amount", getattr(self.instance, "min_amount", Decimal("0.00")))
        max_amount = attrs.get("max_amount", getattr(self.instance, "max_amount", None))
        if min_amount is not None and min_amount < Decimal("0.00"):
            raise serializers.ValidationError(
                {"min_amount": ["Minimum amount cannot be negative."]}
            )
        if max_amount is not None and min_amount is not None and min_amount > max_amount:
            raise serializers.ValidationError(
                {"min_amount": ["Minimum amount cannot be greater than maximum amount."]}
            )
        if "is_active" in attrs:
            if attrs["is_active"] is False:
                attrs["status"] = ApprovalPolicy.STATUS_ARCHIVED
            elif attrs["is_active"] is True and attrs.get("status") in [None, ApprovalPolicy.STATUS_ARCHIVED]:
                attrs["status"] = ApprovalPolicy.STATUS_ACTIVE
        elif attrs.get("status") in [ApprovalPolicy.STATUS_DRAFT, ApprovalPolicy.STATUS_ARCHIVED]:
            attrs["is_active"] = False
        return attrs


class ApprovalActionSerializer(serializers.ModelSerializer):
    actor_email = serializers.CharField(source="actor.email", read_only=True)

    class Meta:
        model = ApprovalAction
        fields = "__all__"


class ApprovalActionFilter(django_filters.FilterSet):
    status = django_filters.CharFilter(field_name="action", lookup_expr="iexact")
    action = django_filters.CharFilter(lookup_expr="iexact")
    target_model = django_filters.CharFilter(
        field_name="target_model_name", lookup_expr="icontains"
    )
    target_object_id = django_filters.CharFilter(method="filter_target_object_id")

    class Meta:
        model = ApprovalAction
        fields = ["status", "action", "target_model_name", "target_object_id"]

    def filter_target_object_id(self, queryset, name, value):
        if not value:
            return queryset
        import uuid
        try:
            val_uuid = uuid.UUID(str(value).strip())
            return queryset.filter(target_object_id=val_uuid)
        except (ValueError, AttributeError):
            return queryset.none()


class ApprovalPolicyViewSet(viewsets.ModelViewSet):
    queryset = ApprovalPolicy.objects.all().order_by("name")
    serializer_class = ApprovalPolicySerializer
    permission_classes = [IsAuthenticated, CanManageApprovalPolicy]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "module", "description"]
    ordering_fields = ["name", "min_amount", "created_at"]


class ApprovalStepSerializer(serializers.ModelSerializer):
    approver_role_name = serializers.CharField(source="approver_role.name", read_only=True)
    approver_role_code = serializers.CharField(source="approver_role.code", read_only=True)

    class Meta:
        model = ApprovalStep
        fields = "__all__"

    def validate(self, attrs):
        policy = attrs.get("policy") or (self.instance.policy if self.instance else None)
        step_number = (
            attrs.get("step_number")
            if "step_number" in attrs
            else (self.instance.step_number if self.instance else None)
        )
        if policy and step_number is not None:
            qs = ApprovalStep.objects.filter(policy=policy, step_number=step_number)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError(
                    {"step_number": [f"Step number {step_number} already exists for this policy."]}
                )
        return attrs


class ApprovalStepViewSet(viewsets.ModelViewSet):
    queryset = ApprovalStep.objects.select_related("policy", "approver_role", "specific_approver").all()
    serializer_class = ApprovalStepSerializer
    permission_classes = [IsAuthenticated, CanManageApprovalPolicy]
    filter_backends = [DjangoFilterBackend, filters.OrderingFilter]
    filterset_fields = ["policy", "approver_role", "step_number"]
    ordering_fields = ["step_number", "created_at"]


class ApprovalActionViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = ApprovalAction.objects.select_related("actor", "policy_step").order_by("-created_at")
    serializer_class = ApprovalActionSerializer
    permission_classes = [IsAuthenticated, IsNotVendorUser, IsAuditorReadOnly]
    filter_backends = [DjangoFilterBackend, filters.SearchFilter, filters.OrderingFilter]
    filterset_class = ApprovalActionFilter
    search_fields = ["target_model_name", "comments", "actor__email"]
    ordering_fields = ["created_at", "action"]

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if not user or not user.is_authenticated:
            return qs.none()
        if user.is_superuser:
            return qs

        from django.db.models import Q
        user_org = getattr(user, "effective_organization", None) or getattr(user, "organization", None)
        if not user_org:
            return qs.filter(actor=user)

        return qs.filter(
            Q(actor__organization=user_org)
            | Q(policy_step__policy__organization=user_org)
        )

    def _check_approver_authorization(self, request):
        user = request.user
        if user.is_superuser:
            return None
        role_code = getattr(user, "role_code", None) or (
            user.role.code if getattr(user, "role", None) else None
        )
        if role_code in [Role.REQUESTER, Role.VENDOR_USER, Role.AUDITOR, Role.STORES_RECEIVER]:
            return Response(
                {
                    "error": {
                        "code": "PERMISSION_DENIED",
                        "message": "Only authorized approvers can approve or reject approval actions.",
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )
        return None

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        denied_response = self._check_approver_authorization(request)
        if denied_response:
            return denied_response

        action_obj = self.get_object()
        if action_obj.action in [ApprovalAction.ACTION_APPROVE, ApprovalAction.ACTION_REJECT]:
            return Response(
                {
                    "error": {
                        "code": "ALREADY_PROCESSED",
                        "message": f"This approval action has already been processed (action: {action_obj.action}).",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        comment = request.data.get("comment", "")
        target_obj = None
        for model in apps.get_models():
            if model.__name__.lower() == action_obj.target_model_name.lower():
                target_obj = model.objects.filter(id=action_obj.target_object_id).first()
                break

        if target_obj and hasattr(target_obj, "status") and target_obj.status in ["APPROVED", "REJECTED", "CANCELLED"]:
            return Response(
                {
                    "error": {
                        "code": "ALREADY_PROCESSED",
                        "message": f"Target object is already in terminal state '{target_obj.status}'.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            if target_obj:
                new_action = process_approval_action_service(
                    target_object=target_obj,
                    actor=request.user,
                    action=ApprovalAction.ACTION_APPROVE,
                    comments=comment,
                )
            else:
                new_action = record_approval_action_service(
                    target_object_id=action_obj.target_object_id,
                    target_model_name=action_obj.target_model_name,
                    actor=request.user,
                    action=ApprovalAction.ACTION_APPROVE,
                    previous_state=action_obj.new_state,
                    new_state="APPROVED",
                    comments=comment,
                )
        except Exception as e:
            from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
            if isinstance(e, DRFPermissionDenied):
                return Response(
                    {"error": {"code": "PERMISSION_DENIED", "message": str(e)}},
                    status=status.HTTP_403_FORBIDDEN,
                )
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": msg[0] if isinstance(msg, list) else str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if isinstance(new_action, ApprovalAction):
            res_data = ApprovalActionSerializer(new_action).data
        else:
            res_data = {
                "id": str(getattr(new_action, "id", "")),
                "status": "APPROVED",
                "comments": comment,
            }

        return Response(res_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reject")
    def reject(self, request, pk=None):
        denied_response = self._check_approver_authorization(request)
        if denied_response:
            return denied_response

        action_obj = self.get_object()
        if action_obj.action in [ApprovalAction.ACTION_APPROVE, ApprovalAction.ACTION_REJECT]:
            return Response(
                {
                    "error": {
                        "code": "ALREADY_PROCESSED",
                        "message": f"This approval action has already been processed (action: {action_obj.action}).",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = request.data.get("reason", request.data.get("comment", "")).strip()
        if not reason:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": "Rejection justification reason is mandatory."}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        target_obj = None
        for model in apps.get_models():
            if model.__name__.lower() == action_obj.target_model_name.lower():
                target_obj = model.objects.filter(id=action_obj.target_object_id).first()
                break

        try:
            if target_obj:
                new_action = process_approval_action_service(
                    target_object=target_obj,
                    actor=request.user,
                    action=ApprovalAction.ACTION_REJECT,
                    comments=reason,
                )
            else:
                new_action = record_approval_action_service(
                    target_object_id=action_obj.target_object_id,
                    target_model_name=action_obj.target_model_name,
                    actor=request.user,
                    action=ApprovalAction.ACTION_REJECT,
                    previous_state=action_obj.new_state,
                    new_state="REJECTED",
                    comments=reason,
                )
        except Exception as e:
            from rest_framework.exceptions import PermissionDenied as DRFPermissionDenied
            if isinstance(e, DRFPermissionDenied):
                return Response(
                    {"error": {"code": "PERMISSION_DENIED", "message": str(e)}},
                    status=status.HTTP_403_FORBIDDEN,
                )
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": msg[0] if isinstance(msg, list) else str(e)}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if isinstance(new_action, ApprovalAction):
            res_data = ApprovalActionSerializer(new_action).data
        else:
            res_data = {
                "id": str(getattr(new_action, "id", "")),
                "status": "REJECTED",
                "comments": reason,
            }

        return Response(res_data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="comment")
    def comment(self, request, pk=None):
        action_obj = self.get_object()
        comment_text = str(request.data.get("comment", "")).strip()
        if not comment_text:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": "Comment text cannot be empty."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        new_action = record_approval_action_service(
            target_object_id=action_obj.target_object_id,
            target_model_name=action_obj.target_model_name,
            actor=request.user,
            action=action_obj.action,
            previous_state=action_obj.new_state,
            new_state=action_obj.new_state,
            comments=comment_text,
        )
        return Response(ApprovalActionSerializer(new_action).data, status=status.HTTP_200_OK)


router = DefaultRouter()
router.register(r"policies", ApprovalPolicyViewSet, basename="approval-policy")
router.register(r"steps", ApprovalStepViewSet, basename="approval-step")
router.register(r"actions", ApprovalActionViewSet, basename="approval-action")

urlpatterns = router.urls

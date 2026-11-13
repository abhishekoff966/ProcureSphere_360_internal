from rest_framework import filters, serializers, viewsets
from rest_framework.permissions import SAFE_METHODS, BasePermission
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role

from .models import CostCenter, Department, Organization


class OrganizationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organization
        fields = "__all__"

    def validate_code(self, value):
        val = value.strip()
        qs = Organization.objects.filter(code__iexact=val)
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise serializers.ValidationError("An organization with this code already exists (case-insensitive).")
        return val


class DepartmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Department
        fields = "__all__"

    def validate(self, attrs):
        code = attrs.get("code", getattr(self.instance, "code", None))
        org = attrs.get("organization", getattr(self.instance, "organization", None))
        if code and org:
            val = code.strip()
            qs = Department.objects.filter(organization=org, code__iexact=val)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"code": "A department with this code already exists in this organization."})
        return attrs


class CostCenterSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostCenter
        fields = "__all__"

    def validate(self, attrs):
        code = attrs.get("code", getattr(self.instance, "code", None))
        dept = attrs.get("department", getattr(self.instance, "department", None))
        if code and dept:
            val = code.strip()
            qs = CostCenter.objects.filter(department=dept, code__iexact=val)
            if self.instance:
                qs = qs.exclude(pk=self.instance.pk)
            if qs.exists():
                raise serializers.ValidationError({"code": "A cost center with this code already exists in this department."})
        return attrs


class IsAdminOrReadOnly(BasePermission):
    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        if (
            getattr(request.user, "is_vendor", False)
            or role_code in [Role.VENDOR_USER, "VENDOR_USER"]
        ):
            return False
        if (
            request.user.is_superuser
            or role_code in [Role.SUPER_ADMIN, "SUPER_ADMIN"]
        ):
            return True
        return request.method in SAFE_METHODS


class OrganizationViewSet(viewsets.ModelViewSet):
    queryset = Organization.objects.all().order_by("name")
    serializer_class = OrganizationSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]

    def perform_create(self, serializer):
        instance = serializer.save()
        from apps.audit.models import AuditLog
        from apps.audit.services import create_audit_log_service
        actor = self.request.user if getattr(self.request, "user", None) and self.request.user.is_authenticated else None
        create_audit_log_service(
            actor=actor,
            action=AuditLog.ACTION_CREATE,
            target_model="Organization",
            target_object_id=str(instance.id),
            new_state={"name": instance.name, "code": instance.code, "is_active": instance.is_active},
            request=self.request,
        )

    def perform_update(self, serializer):
        instance = serializer.save()
        from apps.audit.models import AuditLog
        from apps.audit.services import create_audit_log_service
        actor = self.request.user if getattr(self.request, "user", None) and self.request.user.is_authenticated else None
        create_audit_log_service(
            actor=actor,
            action=AuditLog.ACTION_UPDATE,
            target_model="Organization",
            target_object_id=str(instance.id),
            new_state={"name": instance.name, "code": instance.code, "is_active": instance.is_active},
            request=self.request,
        )

    def perform_destroy(self, instance):
        if hasattr(instance, "departments") and instance.departments.exists():
            raise serializers.ValidationError(
                {"detail": "Cannot delete organization: active child departments exist."}
            )
        if hasattr(instance, "users") and instance.users.exists():
            raise serializers.ValidationError(
                {"detail": "Cannot delete organization: active users are assigned to this organization."}
            )
        from apps.audit.models import AuditLog
        from apps.audit.services import create_audit_log_service
        actor = self.request.user if getattr(self.request, "user", None) and self.request.user.is_authenticated else None
        create_audit_log_service(
            actor=actor,
            action=AuditLog.ACTION_DELETE,
            target_model="Organization",
            target_object_id=str(instance.id),
            previous_state={"name": instance.name, "code": instance.code},
            request=self.request,
        )
        instance.delete()


class DepartmentViewSet(viewsets.ModelViewSet):
    queryset = Department.objects.all().order_by("name")
    serializer_class = DepartmentSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]

    def perform_destroy(self, instance):
        if hasattr(instance, "cost_centers") and instance.cost_centers.exists():
            raise serializers.ValidationError(
                {"detail": "Cannot delete department: active child cost centers exist."}
            )
        if hasattr(instance, "users") and instance.users.exists():
            raise serializers.ValidationError(
                {"detail": "Cannot delete department: active users are assigned to this department."}
            )
        instance.delete()

    def get_queryset(self):
        qs = super().get_queryset()
        org_param = self.request.query_params.get("organization") or self.request.query_params.get("organization_id")
        if org_param:
            qs = qs.filter(organization_id=org_param)
        return qs


class CostCenterViewSet(viewsets.ModelViewSet):
    queryset = CostCenter.objects.all().order_by("name")
    serializer_class = CostCenterSerializer
    permission_classes = [IsAdminOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["name", "code"]

    def perform_destroy(self, instance):
        if hasattr(instance, "budgets") and instance.budgets.exists():
            raise serializers.ValidationError(
                {"detail": "Cannot delete cost center: financial budget records exist."}
            )
        instance.delete()

    def get_queryset(self):
        user = self.request.user
        qs = super().get_queryset()
        if not user or not user.is_authenticated:
            return qs.none()

        department_param = self.request.query_params.get("department") or self.request.query_params.get("department_id")
        if department_param:
            qs = qs.filter(department_id=department_param)

        if user.is_superuser or getattr(user, "role_code", None) in [
            Role.SUPER_ADMIN,
            Role.FINANCE_AP,
            Role.AUDITOR,
            Role.PROC_MGR,
            Role.PROC_EXEC,
        ]:
            return qs
        if getattr(user, "role_code", None) == Role.REQUESTER:
            if getattr(user, "department_id", None):
                return qs.filter(department_id=user.department_id)
            return qs.none()
        return qs


router = DefaultRouter()
router.register(r"entities", OrganizationViewSet, basename="organization")
router.register(r"departments", DepartmentViewSet, basename="department")
router.register(r"cost-centers", CostCenterViewSet, basename="cost-center")

urlpatterns = router.urls

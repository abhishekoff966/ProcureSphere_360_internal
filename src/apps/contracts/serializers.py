from rest_framework import serializers

from .models import (
    Contract,
    ContractAlert,
    ContractDocument,
    ContractMilestone,
    ContractObligation,
    ContractVersion,
)


class ContractVersionSerializer(serializers.ModelSerializer):
    approved_by_email = serializers.CharField(source="approved_by.email", read_only=True)

    class Meta:
        model = ContractVersion
        fields = "__all__"


class ContractMilestoneSerializer(serializers.ModelSerializer):
    due_status = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    days_remaining = serializers.ReadOnlyField()

    class Meta:
        model = ContractMilestone
        fields = "__all__"


class ContractObligationSerializer(serializers.ModelSerializer):
    due_status = serializers.ReadOnlyField()
    is_overdue = serializers.ReadOnlyField()
    days_remaining = serializers.ReadOnlyField()

    class Meta:
        model = ContractObligation
        fields = "__all__"


class ContractDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.CharField(source="uploaded_by.email", read_only=True)

    class Meta:
        model = ContractDocument
        fields = "__all__"


class ContractAlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = ContractAlert
        fields = "__all__"


class ContractSerializer(serializers.ModelSerializer):
    milestones = ContractMilestoneSerializer(many=True, read_only=True)
    obligations = ContractObligationSerializer(many=True, read_only=True)
    documents = ContractDocumentSerializer(many=True, read_only=True)
    versions = ContractVersionSerializer(many=True, read_only=True)
    alerts = ContractAlertSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)
    owner_email = serializers.CharField(source="contract_owner.email", read_only=True)
    termination_reason = serializers.SerializerMethodField()
    terminated_at = serializers.SerializerMethodField()

    class Meta:
        model = Contract
        fields = "__all__"
        read_only_fields = (
            "contract_number",
            "status",
            "version",
            "created_at",
            "updated_at",
            "contract_owner",
            "termination_reason",
            "terminated_at",
        )

    def get_termination_reason(self, obj):
        if obj.status != Contract.STATUS_TERMINATED:
            return None
        from apps.audit.models import AuditLog
        log = (
            AuditLog.objects.filter(
                target_model="Contract",
                target_object_id=str(obj.id),
                new_state__status=Contract.STATUS_TERMINATED,
            )
            .order_by("-timestamp")
            .first()
        )
        if log and isinstance(log.new_state, dict):
            return log.new_state.get("reason")
        return None

    def get_terminated_at(self, obj):
        if obj.status != Contract.STATUS_TERMINATED:
            return None
        from apps.audit.models import AuditLog
        log = (
            AuditLog.objects.filter(
                target_model="Contract",
                target_object_id=str(obj.id),
                new_state__status=Contract.STATUS_TERMINATED,
            )
            .order_by("-timestamp")
            .first()
        )
        if log:
            return log.timestamp.isoformat()
        return obj.updated_at.isoformat()

    def validate(self, attrs):
        title = attrs.get("title", getattr(self.instance, "title", None))
        vendor = attrs.get("vendor", getattr(self.instance, "vendor", None))
        start_date = attrs.get("start_date", getattr(self.instance, "start_date", None))
        end_date = attrs.get("end_date", getattr(self.instance, "end_date", None))
        if self.instance is None and title and vendor and start_date and end_date:
            if Contract.objects.filter(
                vendor=vendor,
                title__iexact=str(title).strip(),
                start_date=start_date,
                end_date=end_date,
            ).exists():
                raise serializers.ValidationError(
                    {"title": ["A contract with the same title, vendor, and term dates already exists."]}
                )
        return attrs

from django.db.models import Q
from django.http import HttpResponse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import Role
from apps.audit.models import AuditLog
from apps.audit.permissions import AuditorReadOnlyPermission, IsAuditorOrSuperAdmin
from apps.audit.selectors import (
    get_approval_history_audit,
    get_audit_metrics,
    get_auditor_dashboard_data,
    get_contract_changes_audit,
    get_entity_audit_trail,
    get_invoice_exceptions_audit,
    get_po_changes_audit,
    get_scoped_audit_logs_queryset,
    get_security_events_audit,
    get_sourcing_activity_audit,
    get_vendor_compliance_audit,
)
from apps.audit.serializers import (
    ApprovalHistoryQuerySerializer,
    AuditFilterParamsSerializer,
    AuditLogDetailSerializer,
    AuditLogListSerializer,
    AuditorExportRequestSerializer,
    ContractChangesQuerySerializer,
    EntityAuditTrailQuerySerializer,
    InvoiceExceptionsQuerySerializer,
    POChangesQuerySerializer,
    SecurityEventsQuerySerializer,
    SourcingActivityQuerySerializer,
    TransactionLifecycleQuerySerializer,
    VendorComplianceQuerySerializer,
)
from apps.audit.services import export_auditor_data_service, get_transaction_lifecycle_service


class AuditLogPagination(LimitOffsetPagination):
    default_limit = 25
    max_limit = 100

    def paginate_queryset(self, queryset, request, view=None):
        if "page_size" in request.query_params and "limit" not in request.query_params:
            try:
                self.limit = int(request.query_params["page_size"])
            except (ValueError, TypeError):
                pass
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        return Response({
            "total_count": self.count,
            "count": self.count,
            "limit": self.limit,
            "offset": self.offset,
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })


def _get_auditor_org(request):
    user = getattr(request, "user", None) if request else None
    if not user or getattr(user, "is_superuser", False):
        return None

    req_org = None
    if hasattr(request, "query_params"):
        req_org = request.query_params.get("org") or request.query_params.get("organization")
    if not req_org and hasattr(request, "GET"):
        req_org = request.GET.get("org") or request.GET.get("organization")
    if req_org:
        from apps.organization.models import Organization
        try:
            return Organization.objects.filter(Q(id=req_org) | Q(code=req_org)).first()
        except Exception:
            pass

    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )
    if role_code in [Role.AUDITOR, Role.SUPER_ADMIN, "AUDITOR", "SUPER_ADMIN"]:
        return None

    return getattr(user, "effective_organization", None) or getattr(user, "organization", None)


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    Read-only API ViewSet providing access to immutable AuditLog records,
    statistical metrics, full-chain Source-to-Pay transaction lifecycle graphs,
    entity audit trails, domain audit logs, and export services.
    """

    serializer_class = AuditLogListSerializer
    pagination_class = AuditLogPagination
    permission_classes = [IsAuthenticated, IsAuditorOrSuperAdmin, AuditorReadOnlyPermission]
    ordering = ["-timestamp", "-id"]

    def get_queryset(self):
        org = _get_auditor_org(self.request)
        return get_scoped_audit_logs_queryset(
            AuditLog.objects.select_related("actor").all(), organization=org
        ).order_by("-timestamp", "-id")

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AuditLogDetailSerializer
        return AuditLogListSerializer

    def list(self, request, *args, **kwargs):
        filter_serializer = AuditFilterParamsSerializer(data=request.query_params)
        filter_serializer.is_valid(raise_exception=True)
        data = filter_serializer.validated_data

        qs = self.get_queryset()
        from django.db.models import Q
        actor_param = data.get("actor") or request.query_params.get("actor")
        if actor_param:
            actor_s = str(actor_param).strip()
            qs = qs.filter(
                Q(actor__email__icontains=actor_s)
                | Q(actor__first_name__icontains=actor_s)
                | Q(actor__last_name__icontains=actor_s)
            )
        elif data.get("actor_id"):
            qs = qs.filter(actor_id=data["actor_id"])

        obj_param = data.get("object") or request.query_params.get("object")
        if obj_param:
            obj_s = str(obj_param).strip()
            qs = qs.filter(
                Q(target_object_id__icontains=obj_s)
                | Q(target_model__icontains=obj_s)
            )
        elif data.get("target_object_id") or data.get("object_id") or request.query_params.get("object_id"):
            oid = data.get("target_object_id") or data.get("object_id") or request.query_params.get("object_id")
            qs = qs.filter(target_object_id__icontains=str(oid).strip())

        if data.get("action"):
            qs = qs.filter(action=data["action"])
        model_param = data.get("target_model") or data.get("object_type") or request.query_params.get("object_type")
        if model_param:
            qs = qs.filter(target_model__iexact=str(model_param).strip())

        from_param = request.query_params.get("from") or request.query_params.get("date_from")
        to_param = request.query_params.get("to") or request.query_params.get("date_to")
        if from_param and to_param and str(from_param) > str(to_param):
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "Start date ('from') must be on or before end date ('to').",
                        "fields": {"from": ["Start date must be before end date."]},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if from_param:
            qs = qs.filter(timestamp__gte=from_param)
        if to_param:
            qs = qs.filter(timestamp__lte=to_param)
        if data.get("search"):
            from django.db.models import Q
            s = data["search"]
            matched_ids = set()
            try:
                from apps.contracts.models import Contract
                from apps.invoices.models import SupplierInvoice
                from apps.orders.models import PurchaseOrder
                from apps.requisitions.models import PurchaseRequisition
                from apps.vendors.models import Vendor

                matched_ids.update(
                    str(uid) for uid in PurchaseRequisition.objects.filter(
                        Q(pr_number__icontains=s) | Q(title__icontains=s)
                    ).values_list("id", flat=True)[:50]
                )
                matched_ids.update(
                    str(uid) for uid in PurchaseOrder.objects.filter(
                        po_number__icontains=s
                    ).values_list("id", flat=True)[:50]
                )
                matched_ids.update(
                    str(uid) for uid in SupplierInvoice.objects.filter(
                        Q(invoice_number__icontains=s) | Q(internal_reference__icontains=s)
                    ).values_list("id", flat=True)[:50]
                )
                matched_ids.update(
                    str(uid) for uid in Contract.objects.filter(
                        contract_number__icontains=s
                    ).values_list("id", flat=True)[:50]
                )
                matched_ids.update(
                    str(uid) for uid in Vendor.objects.filter(
                        Q(vendor_number__icontains=s) | Q(legal_name__icontains=s)
                    ).values_list("id", flat=True)[:50]
                )
            except Exception:
                pass

            search_filter = (
                Q(actor__email__icontains=s)
                | Q(action__icontains=s)
                | Q(target_model__icontains=s)
                | Q(target_object_id__icontains=s)
                | Q(new_state__pr_number__icontains=s)
                | Q(previous_state__pr_number__icontains=s)
            )
            if matched_ids:
                search_filter |= Q(target_object_id__in=matched_ids)
            qs = qs.filter(search_filter)
        if data.get("start_date"):
            qs = qs.filter(timestamp__gte=data["start_date"])
        if data.get("end_date"):
            qs = qs.filter(timestamp__lte=data["end_date"])
        if data.get("request_id"):
            qs = qs.filter(request_id=data["request_id"])

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "period_days", int, description="Time window in days for metrics (default: 30)"
            ),
        ],
        description="Get statistical aggregations, action breakdown, and daily activity trends.",
    )
    @action(detail=False, methods=["get"], url_path="metrics")
    def metrics(self, request):
        period_days_raw = request.query_params.get("period_days")
        if period_days_raw is not None:
            try:
                period_days = int(period_days_raw)
                if period_days < 0 or period_days > 3650:
                    return Response(
                        {"error": {"code": "VALIDATION_ERROR", "message": "period_days must be between 0 and 3650."}},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except (ValueError, TypeError, OverflowError):
                return Response(
                    {"error": {"code": "VALIDATION_ERROR", "message": "period_days must be a valid integer."}},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            period_days = 30

        org = _get_auditor_org(request)
        metrics_data = get_audit_metrics(period_days=period_days, organization=org)
        return Response(metrics_data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "identifier",
                str,
                required=True,
                description="Document/Entity number or UUID (e.g. PR-2026-0001, PO-2026-0001, INV-2026-0001)",
            ),
            OpenApiParameter(
                "entity_type",
                str,
                description="Optional entity domain code (PR, PO, SOURCING, INVOICE, CONTRACT, VENDOR, RECEIPT)",
            ),
        ],
        description="Retrieve complete end-to-end Source-to-Pay transaction lifecycle graph and linked audit trail.",
    )
    @action(detail=False, methods=["get"], url_path="lifecycle")
    def lifecycle(self, request):
        query_serializer = TransactionLifecycleQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        identifier = query_serializer.validated_data.get("identifier")
        if not identifier:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": "Query parameter 'identifier' is required."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        entity_type = query_serializer.validated_data.get("entity_type", "AUTO")
        org = _get_auditor_org(request)

        lifecycle_data = get_transaction_lifecycle_service(
            entity_type=entity_type,
            entity_identifier=identifier,
            organization=org,
        )

        if lifecycle_data.get("is_unauthorized"):
            return Response(
                {
                    "error": {
                        "code": "ACCESS_DENIED",
                        "message": "Access to cross-organization transaction records is strictly forbidden.",
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        if not lifecycle_data.get("is_found"):
            return Response(
                {
                    "error": {
                        "code": "ENTITY_NOT_FOUND",
                        "message": f"No entity matching identifier '{identifier}' was found.",
                    },
                    "lifecycle": lifecycle_data,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(lifecycle_data, status=status.HTTP_200_OK)

    @extend_schema(
        parameters=[
            OpenApiParameter(
                "target_model",
                str,
                required=True,
                description="Name of the model (e.g. PurchaseRequisition, PurchaseOrder)",
            ),
            OpenApiParameter(
                "target_object_id",
                str,
                required=True,
                description="UUID or ID of the target object",
            ),
        ],
        description="Retrieve all chronological audit log events and approval actions for a specific target entity.",
    )
    @action(detail=False, methods=["get"], url_path="entity-trail")
    def entity_trail(self, request):
        query_serializer = EntityAuditTrailQuerySerializer(data=request.query_params)
        query_serializer.is_valid(raise_exception=True)

        model_name = query_serializer.validated_data.get("target_model")
        obj_id = query_serializer.validated_data.get("target_object_id")
        if not model_name or not obj_id:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": "Query parameters 'target_model' and 'target_object_id' are required."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        org = _get_auditor_org(request)

        trail_data = get_entity_audit_trail(
            target_model=model_name,
            target_object_id=obj_id,
            organization=org,
        )

        if trail_data.get("is_unauthorized"):
            return Response(
                {
                    "error": {
                        "code": "ACCESS_DENIED",
                        "message": "Access to entity audit trail across organizations is forbidden.",
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        logs_serializer = AuditLogDetailSerializer(trail_data["logs"], many=True)
        return Response(
            {
                "target_model": trail_data["target_model"],
                "target_object_id": trail_data["target_object_id"],
                "logs_count": trail_data["logs_count"],
                "approvals_count": trail_data["approvals_count"],
                "logs": logs_serializer.data,
                "approvals": [
                    {
                        "id": str(a.id),
                        "actor": a.actor.email if a.actor else "System",
                        "action": a.action,
                        "comments": a.comments,
                        "previous_state": a.previous_state,
                        "new_state": a.new_state,
                        "timestamp": a.created_at.isoformat(),
                    }
                    for a in trail_data["approvals"]
                ],
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="dashboard-data")
    def dashboard_data(self, request):
        try:
            period_days = int(request.query_params.get("period_days", 30))
            if period_days > 3650:
                period_days = 365
        except (ValueError, TypeError, OverflowError):
            period_days = 30

        org = _get_auditor_org(request)
        data = get_auditor_dashboard_data(period_days=period_days, user=request.user, organization=org)
        data["recent_logs"] = AuditLogListSerializer(data["recent_logs"], many=True).data
        data["recent_approvals"] = [
            {
                "id": str(a.id),
                "actor": a.actor.email if a.actor else "System",
                "action": a.action,
                "comments": a.comments,
                "target_model": a.target_model_name,
                "target_id": str(a.target_object_id),
                "timestamp": a.created_at.isoformat(),
            }
            for a in data["recent_approvals"]
        ]
        data["open_exceptions"] = [
            {
                "id": str(e.id),
                "invoice_number": e.invoice.invoice_number,
                "vendor": e.invoice.vendor.legal_name,
                "type": e.get_exception_type_display(),
                "variance_amount": float(e.variance_amount),
                "created_at": e.created_at.isoformat(),
            }
            for e in data["open_exceptions"]
        ]
        data["suspended_vendors"] = [
            {
                "id": str(v.id),
                "vendor_number": v.vendor_number,
                "legal_name": v.legal_name,
                "status": v.get_status_display(),
                "category": v.category.name if v.category else None,
            }
            for v in data["suspended_vendors"]
        ]
        data["overdue_obligations"] = [
            {
                "id": str(o.id),
                "contract_number": o.contract.contract_number,
                "title": o.title,
                "responsible_party": o.responsible_party,
                "due_date": str(o.due_date),
            }
            for o in data["overdue_obligations"]
        ]
        data["active_contract_alerts"] = [
            {
                "id": str(a.id),
                "contract_number": a.contract.contract_number,
                "alert_type": a.get_alert_type_display(),
                "message": a.message,
                "triggered_at": a.triggered_at.isoformat(),
            }
            for a in data["active_contract_alerts"]
        ]

        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="vendor-compliance")
    def vendor_compliance(self, request):
        serializer = VendorComplianceQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_vendor_compliance_audit(
            status=data.get("status"),
            kyc_status=data.get("kyc_status"),
            search=data.get("search"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="approval-history")
    def approval_history(self, request):
        serializer = ApprovalHistoryQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_approval_history_audit(
            target_model=data.get("target_model"),
            action=data.get("action"),
            actor_id=str(data.get("actor_id")) if data.get("actor_id") else None,
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="sourcing-activity")
    def sourcing_activity(self, request):
        serializer = SourcingActivityQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_sourcing_activity_audit(
            status=data.get("status"),
            event_type=data.get("event_type"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="po-changes")
    def po_changes(self, request):
        serializer = POChangesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_po_changes_audit(
            search=data.get("search"),
            status=data.get("status"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="invoice-exceptions")
    def invoice_exceptions(self, request):
        serializer = InvoiceExceptionsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_invoice_exceptions_audit(
            status=data.get("status"),
            exception_type=data.get("exception_type"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="contract-changes")
    def contract_changes(self, request):
        serializer = ContractChangesQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_contract_changes_audit(
            search=data.get("search"),
            status=data.get("status"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="security-events")
    def security_events(self, request):
        serializer = SecurityEventsQuerySerializer(data=request.query_params)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        result = get_security_events_audit(
            actor_id=str(data.get("actor_id")) if data.get("actor_id") else None,
            action=data.get("action"),
            search=data.get("search"),
            start_date=data.get("start_date"),
            end_date=data.get("end_date"),
            limit=data.get("limit", 50),
            offset=data.get("offset", 0),
            organization=org,
        )
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get", "post"], url_path="export")
    def export_data(self, request):
        if request.method == "POST":
            serializer = AuditorExportRequestSerializer(data=request.data)
        else:
            serializer = AuditorExportRequestSerializer(data=request.query_params)

        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        org = _get_auditor_org(request)

        if request.data.get("async") or request.query_params.get("async") == "true":
            from apps.audit.tasks import generate_auditor_export_task
            task = generate_auditor_export_task.delay(
                export_type=data["export_type"],
                export_format=data["export_format"],
                filters=data.get("filters", {}),
                actor_id=str(request.user.id) if request.user else None,
            )
            return Response(
                {
                    "task_id": str(task.id),
                    "status": "PENDING",
                    "message": "Auditor report export queued for asynchronous background generation.",
                    "export_type": data["export_type"],
                    "export_format": data["export_format"],
                },
                status=status.HTTP_202_ACCEPTED,
            )

        export_res = export_auditor_data_service(
            export_type=data["export_type"],
            export_format=data["export_format"],
            filters=data.get("filters", {}),
            actor=request.user,
            request=request,
            organization=org,
        )

        response = HttpResponse(
            export_res["content"],
            content_type=export_res["content_type"],
        )
        response["Content-Disposition"] = f'attachment; filename="{export_res["filename"]}"'
        response["X-Export-Ref-ID"] = export_res["export_ref_id"]
        response["X-Export-Checksum"] = export_res["checksum"]
        response["X-Export-Algorithm"] = export_res["algorithm"]
        return response


# --- Direct Standalone Functional API Views matching project URL standards ---


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_dashboard_summary_api_view(request):
    """
    Returns executive auditor dashboard KPIs, compliance alerts, and activity distribution.
    """
    try:
        period_days = int(request.query_params.get("period_days", 30))
        if period_days <= 0:
            period_days = 30
    except (ValueError, TypeError):
        period_days = 30
    org = _get_auditor_org(request)
    data = get_auditor_dashboard_data(period_days=period_days, user=request.user, organization=org)
    data["recent_logs"] = AuditLogListSerializer(data["recent_logs"], many=True).data
    data["recent_approvals"] = [
        {
            "id": str(a.id),
            "actor": a.actor.email if a.actor else "System",
            "action": a.action,
            "comments": a.comments,
            "target_model": a.target_model_name,
            "target_id": str(a.target_object_id),
            "timestamp": a.created_at.isoformat(),
        }
        for a in data["recent_approvals"]
    ]
    data["open_exceptions"] = [
        {
            "id": str(e.id),
            "invoice_number": e.invoice.invoice_number,
            "vendor": e.invoice.vendor.legal_name,
            "type": e.get_exception_type_display(),
            "variance_amount": float(e.variance_amount),
            "created_at": e.created_at.isoformat(),
        }
        for e in data["open_exceptions"]
    ]
    data["suspended_vendors"] = [
        {
            "id": str(v.id),
            "vendor_number": v.vendor_number,
            "legal_name": v.legal_name,
            "status": v.get_status_display(),
            "category": v.category.name if v.category else None,
        }
        for v in data["suspended_vendors"]
    ]
    data["overdue_obligations"] = [
        {
            "id": str(o.id),
            "contract_number": o.contract.contract_number,
            "title": o.title,
            "responsible_party": o.responsible_party,
            "due_date": str(o.due_date),
        }
        for o in data["overdue_obligations"]
    ]
    data["active_contract_alerts"] = [
        {
            "id": str(a.id),
            "contract_number": a.contract.contract_number,
            "alert_type": a.get_alert_type_display(),
            "message": a.message,
            "triggered_at": a.triggered_at.isoformat(),
        }
        for a in data["active_contract_alerts"]
    ]
    return Response(data, status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_vendor_compliance_api_view(request):
    serializer = VendorComplianceQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    org = _get_auditor_org(request)
    return Response(
        get_vendor_compliance_audit(**serializer.validated_data, organization=org), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_approval_history_api_view(request):
    serializer = ApprovalHistoryQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if "actor_id" in data:
        data["actor_id"] = str(data["actor_id"])
    org = _get_auditor_org(request)
    return Response(get_approval_history_audit(**data, organization=org), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_sourcing_activity_api_view(request):
    serializer = SourcingActivityQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    org = _get_auditor_org(request)
    return Response(
        get_sourcing_activity_audit(**serializer.validated_data, organization=org), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_po_changes_api_view(request):
    serializer = POChangesQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    org = _get_auditor_org(request)
    return Response(get_po_changes_audit(**serializer.validated_data, organization=org), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_invoice_exceptions_api_view(request):
    serializer = InvoiceExceptionsQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    org = _get_auditor_org(request)
    return Response(
        get_invoice_exceptions_audit(**serializer.validated_data, organization=org), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_contract_changes_api_view(request):
    serializer = ContractChangesQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    org = _get_auditor_org(request)
    return Response(
        get_contract_changes_audit(**serializer.validated_data, organization=org), status=status.HTTP_200_OK
    )


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_security_events_api_view(request):
    serializer = SecurityEventsQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    if "actor_id" in data:
        data["actor_id"] = str(data["actor_id"])
    org = _get_auditor_org(request)
    return Response(get_security_events_audit(**data, organization=org), status=status.HTTP_200_OK)


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_lifecycle_api_view(request):
    serializer = TransactionLifecycleQuerySerializer(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    identifier = data.get("identifier")
    if not identifier:
        return Response(
            {"error": {"code": "VALIDATION_ERROR", "message": "Query parameter 'identifier' is required."}},
            status=status.HTTP_400_BAD_REQUEST,
        )
    org = _get_auditor_org(request)
    lifecycle_data = get_transaction_lifecycle_service(
        entity_type=data.get("entity_type", "AUTO"),
        entity_identifier=identifier,
        organization=org,
    )
    if lifecycle_data.get("is_unauthorized"):
        return Response(
            {
                "error": {
                    "code": "ACCESS_DENIED",
                    "message": "Access to cross-organization transaction records is strictly forbidden.",
                }
            },
            status=status.HTTP_403_FORBIDDEN,
        )
    if not lifecycle_data.get("is_found"):
        return Response(
            {
                "error": {
                    "code": "ENTITY_NOT_FOUND",
                    "message": f"No entity matching identifier '{data['identifier']}' was found.",
                },
                "lifecycle": lifecycle_data,
            },
            status=status.HTTP_404_NOT_FOUND,
        )
    return Response(lifecycle_data, status=status.HTTP_200_OK)


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated, IsAuditorOrSuperAdmin])
def auditor_export_api_view(request):
    if request.method == "POST":
        serializer = AuditorExportRequestSerializer(data=request.data)
    else:
        serializer = AuditorExportRequestSerializer(data=request.query_params)

    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    org = _get_auditor_org(request)

    if request.data.get("async") or request.GET.get("async") == "true":
        from apps.audit.tasks import generate_auditor_export_task
        task = generate_auditor_export_task.delay(
            export_type=data["export_type"],
            export_format=data["export_format"],
            filters=data.get("filters", {}),
            actor_id=str(request.user.id) if request.user else None,
        )
        return Response(
            {
                "task_id": str(task.id),
                "status": "PENDING",
                "message": "Auditor report export queued for asynchronous background generation.",
                "export_type": data["export_type"],
                "export_format": data["export_format"],
            },
            status=status.HTTP_202_ACCEPTED,
        )

    export_res = export_auditor_data_service(
        export_type=data["export_type"],
        export_format=data["export_format"],
        filters=data.get("filters", {}),
        actor=request.user,
        request=request,
        organization=org,
    )

    response = HttpResponse(
        export_res["content"],
        content_type=export_res["content_type"],
    )
    response["Content-Disposition"] = f'attachment; filename="{export_res["filename"]}"'
    response["X-Export-Ref-ID"] = export_res["export_ref_id"]
    response["X-Export-Checksum"] = export_res["checksum"]
    response["X-Export-Algorithm"] = export_res["algorithm"]
    return response


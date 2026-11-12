from decimal import Decimal

from django.core.exceptions import ValidationError
from drf_spectacular.utils import extend_schema
from rest_framework import filters, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import SAFE_METHODS, BasePermission, IsAuthenticated
from rest_framework.response import Response
from rest_framework.routers import DefaultRouter

from apps.accounts.models import Role
from apps.orders.models import PurchaseOrder
from apps.vendors.models import Vendor

from .models import InvoiceLine, MatchException, SupplierInvoice
from .selectors import scope_invoices_queryset
from .services import (
    create_supplier_invoice_service,
    mark_invoice_paid_service,
    resolve_match_exception_service,
    run_3_way_match_service,
)


class CanManageSupplierInvoice(BasePermission):
    """
    Enforces RBAC on supplier invoices:
    - Safe methods (GET, HEAD, OPTIONS): Authenticated users (vendor-scoped).
    - Auditors: Strictly read-only (SAFE_METHODS).
    - Vendors: Cannot pay invoices or resolve exceptions.
    - Payment: Restricted to FINANCE_AP and SUPER_ADMIN.
    - Write/Create: FINANCE_AP, SUPER_ADMIN (internal), VENDOR_USER (vendor-scoped PO).
    - Requester, Dept Approver, Legal Mgr: Strictly blocked (HTTP 403).
    - Default deny for all other roles.
    """

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        is_vendor = (
            getattr(request.user, "is_vendor", False)
            or role_code in [Role.VENDOR_USER, "VENDOR_USER"]
        )

        # Vendors cannot pay, run match, create line-item invoices via internal endpoint, or access match exceptions
        if is_vendor:
            basename = getattr(view, "basename", "")
            action = getattr(view, "action", "")
            if "exception" in basename or "exception" in action:
                return False
            if action == "create_invoice":
                return False
            if action in ["pay_invoice", "pay", "run_match", "run_3_way_match"]:
                return False
            if request.method in SAFE_METHODS:
                return True
            if request.method == "POST" and basename == "invoice" and action in ["create", None]:
                return True
            return False

        # Superuser has full access
        if request.user.is_superuser:
            return True

        # Roles with no invoice access in the RBAC matrix (Requester, Dept Approver, Legal Mgr)
        if role_code in [
            Role.REQUESTER,
            Role.DEPT_APPROVER,
            Role.LEGAL_MGR,
            "REQUESTER",
            "DEPT_APPROVER",
            "LEGAL_MGR",
        ]:
            return False

        # Specific restricted actions
        if getattr(view, "action", None) in ["pay_invoice", "pay", "resolve_exception", "resolve"]:
            return role_code in [Role.SUPER_ADMIN, Role.FINANCE_AP, "SUPER_ADMIN", "FINANCE_AP"]

        if getattr(view, "action", None) in ["run_match", "run_3_way_match"]:
            return role_code in [Role.SUPER_ADMIN, Role.FINANCE_AP, "SUPER_ADMIN", "FINANCE_AP"]

        # Internal read-only roles on invoices per RBAC matrix (Proc Exec, Proc Mgr, Stores Receiver, Auditor)
        if role_code in [
            Role.PROC_EXEC,
            Role.PROC_MGR,
            Role.STORES_RECEIVER,
            Role.AUDITOR,
            "PROC_EXEC",
            "PROC_MGR",
            "STORES_RECEIVER",
            "AUDITOR",
        ]:
            return request.method in SAFE_METHODS

        # Finance AP and Super Admin roles have full access
        if role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN, "FINANCE_AP", "SUPER_ADMIN"]:
            return True

        # Default deny for any unassigned or unrecognized roles
        return False

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser:
            return True

        role_code = getattr(request.user, "role_code", None) or (
            request.user.role.code if getattr(request.user, "role", None) else None
        )
        is_vendor = (
            getattr(request.user, "is_vendor", False)
            or role_code in [Role.VENDOR_USER, "VENDOR_USER"]
        )

        if is_vendor:
            # Vendor can only view their own vendor invoices
            if getattr(obj, "vendor_id", None) != getattr(request.user, "vendor_id", None):
                return False
            return request.method in SAFE_METHODS

        if role_code in [
            Role.REQUESTER,
            Role.DEPT_APPROVER,
            Role.LEGAL_MGR,
            "REQUESTER",
            "DEPT_APPROVER",
            "LEGAL_MGR",
        ]:
            return False

        if role_code in [
            Role.PROC_EXEC,
            Role.PROC_MGR,
            Role.STORES_RECEIVER,
            Role.AUDITOR,
            "PROC_EXEC",
            "PROC_MGR",
            "STORES_RECEIVER",
            "AUDITOR",
        ]:
            return request.method in SAFE_METHODS

        if role_code in [Role.FINANCE_AP, Role.SUPER_ADMIN, "FINANCE_AP", "SUPER_ADMIN"]:
            return True

        return False


class InvoiceLineSerializer(serializers.ModelSerializer):
    class Meta:
        model = InvoiceLine
        fields = "__all__"

    def validate_quantity(self, value):
        if value <= Decimal("0.00"):
            raise serializers.ValidationError(
                "Total Quantity Billed must be greater than zero. The count cannot be less than zero or negative."
            )
        return value

    def validate_unit_price(self, value):
        if value < Decimal("0.00"):
            raise serializers.ValidationError(
                "Unit price cannot be less than zero or negative."
            )
        return value


class MatchExceptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = MatchException
        fields = "__all__"

    def create(self, validated_data):
        validated_data["status"] = MatchException.STATUS_OPEN
        return super().create(validated_data)


class SupplierInvoiceSerializer(serializers.ModelSerializer):
    lines = InvoiceLineSerializer(many=True, read_only=True)
    exceptions = MatchExceptionSerializer(many=True, read_only=True)
    vendor_name = serializers.CharField(source="vendor.legal_name", read_only=True)

    class Meta:
        model = SupplierInvoice
        fields = "__all__"
        read_only_fields = ["status", "created_at", "updated_at"]

    def validate(self, attrs):
        if self.instance and self.instance.status in [
            SupplierInvoice.STATUS_PAID,
            SupplierInvoice.STATUS_READY_FOR_PAYMENT,
            SupplierInvoice.STATUS_APPROVAL,
        ]:
            raise serializers.ValidationError(
                {"status": [f"Invoices in '{self.instance.status}' status cannot be edited."]}
            )
        vendor = attrs.get("vendor", getattr(self.instance, "vendor", None))
        po = attrs.get("po", getattr(self.instance, "po", None))
        if po:
            if po.status not in [
                PurchaseOrder.STATUS_ISSUED,
                PurchaseOrder.STATUS_ACKNOWLEDGED,
                PurchaseOrder.STATUS_PARTIAL_RECEIPT,
                PurchaseOrder.STATUS_COMPLETED,
            ]:
                raise serializers.ValidationError(
                    {"po": [f"Cannot create invoice against PO in status '{po.status}'. Invoices require an issued purchase order."]}
                )
        if not vendor and po:
            vendor = po.vendor
        if not vendor and hasattr(self, "context") and "request" in self.context:
            req_user = self.context["request"].user
            if getattr(req_user, "vendor", None):
                vendor = req_user.vendor

        if vendor and getattr(vendor, "status", None) != "ACTIVE":
            raise serializers.ValidationError(
                {"vendor": [f"Cannot create invoice: Vendor '{vendor.legal_name}' is in status '{vendor.status}'. Vendor must be ACTIVE."]}
            )

        invoice_number = attrs.get("invoice_number", getattr(self.instance, "invoice_number", None))
        if invoice_number:
            clean_inv_num = str(invoice_number).strip()
            attrs["invoice_number"] = clean_inv_num
            if vendor:
                qs = SupplierInvoice.objects.filter(vendor=vendor, invoice_number__iexact=clean_inv_num)
                if self.instance:
                    qs = qs.exclude(pk=self.instance.pk)
                if qs.exists():
                    raise serializers.ValidationError(
                        {
                            "invoice_number": [
                                f"Duplicate invoice detected: Invoice '{clean_inv_num}' already exists for vendor '{vendor.legal_name}'."
                            ]
                        }
                    )

        po = attrs.get("po", getattr(self.instance, "po", None))
        if po and vendor and po.vendor_id != vendor.id:
            raise serializers.ValidationError(
                {
                    "po": [
                        f"Purchase Order {po.po_number} does not belong to vendor '{vendor.legal_name}'."
                    ]
                }
            )

        subtotal = attrs.get("subtotal", getattr(self.instance, "subtotal", None))
        tax_amount = attrs.get("tax_amount", getattr(self.instance, "tax_amount", Decimal("0.00")))
        if tax_amount is None:
            tax_amount = Decimal("0.00")
        total_amount = attrs.get("total_amount", getattr(self.instance, "total_amount", None))
        if subtotal is not None and subtotal < Decimal("0.00"):
            raise serializers.ValidationError({"subtotal": ["Subtotal cannot be negative."]})
        if tax_amount is not None and tax_amount < Decimal("0.00"):
            raise serializers.ValidationError({"tax_amount": ["Tax amount cannot be negative."]})
        if total_amount is not None and total_amount < Decimal("0.00"):
            raise serializers.ValidationError(
                {"total_amount": ["Total amount cannot be negative."]}
            )
        if subtotal is not None and total_amount is not None:
            expected_total = subtotal + tax_amount
            if abs(total_amount - expected_total) > Decimal("0.01"):
                raise serializers.ValidationError(
                    {
                        "total_amount": [
                            f"Total amount ({total_amount}) must equal subtotal ({subtotal}) plus tax ({tax_amount}) = {expected_total}."
                        ]
                    }
                )
            if po and total_amount:
                from django.db.models import Sum
                prior_inv_qs = SupplierInvoice.objects.filter(po=po).exclude(status=SupplierInvoice.STATUS_REJECTED)
                if self.instance:
                    prior_inv_qs = prior_inv_qs.exclude(pk=self.instance.pk)
                prior_sum = prior_inv_qs.aggregate(s=Sum("total_amount"))["s"] or Decimal("0.00")
                if prior_sum + total_amount > po.total_amount:
                    raise serializers.ValidationError(
                        {
                            "total_amount": [
                                f"Cumulative invoiced amount (${prior_sum + total_amount:,.2f}) would exceed Purchase Order total (${po.total_amount:,.2f})."
                            ]
                        }
                    )

        invoice_date = attrs.get("invoice_date", getattr(self.instance, "invoice_date", None))
        due_date = attrs.get("due_date", getattr(self.instance, "due_date", None))
        if invoice_date and due_date and due_date < invoice_date:
            raise serializers.ValidationError(
                {"due_date": ["Due date cannot be before invoice date."]}
            )

        return attrs


class InvoiceLineItemPayloadSerializer(serializers.Serializer):
    description = serializers.CharField(required=False, default="", allow_blank=True)
    quantity = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    unit_price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.00"))
    po_line_id = serializers.CharField(required=False, allow_null=True)


class InvoiceCreatePayloadSerializer(serializers.Serializer):
    vendor_id = serializers.CharField(required=True, help_text="ID of the vendor")
    po_id = serializers.CharField(required=True, help_text="ID of the purchase order")
    invoice_number = serializers.CharField(required=True, max_length=100)
    invoice_date = serializers.DateField(required=True)
    due_date = serializers.DateField(required=False, allow_null=True)
    line_items = InvoiceLineItemPayloadSerializer(many=True, required=True)
    notes = serializers.CharField(required=False, allow_blank=True, default="")


class SupplierInvoiceViewSet(viewsets.ModelViewSet):
    queryset = (
        SupplierInvoice.objects.select_related("vendor", "po")
        .prefetch_related("lines", "exceptions")
        .order_by("-created_at", "-id")
    )
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["invoice_number", "vendor__legal_name", "po__po_number"]
    serializer_class = SupplierInvoiceSerializer
    permission_classes = [IsAuthenticated, CanManageSupplierInvoice]

    def get_queryset(self):
        user = self.request.user
        qs = scope_invoices_queryset(self.queryset, user)

        status_param = self.request.query_params.get("status", "").strip().upper()
        if status_param:
            valid_statuses = [choice[0] for choice in SupplierInvoice.STATUS_CHOICES]
            if status_param not in valid_statuses:
                raise serializers.ValidationError(
                    {
                        "status": [
                            f"Invalid status '{status_param}'. Valid choices are: {', '.join(valid_statuses)}"
                        ]
                    }
                )
            qs = qs.filter(status=status_param)

        po_param = self.request.query_params.get("po")
        if po_param:
            qs = qs.filter(po_id=po_param)

        return qs

    def perform_update(self, serializer):
        instance = self.get_object()
        if instance.status in [SupplierInvoice.STATUS_PAID, SupplierInvoice.STATUS_READY_FOR_PAYMENT, SupplierInvoice.STATUS_APPROVAL]:
            raise serializers.ValidationError(
                {"status": [f"Invoices in '{instance.status}' status cannot be edited."]}
            )
        serializer.save()

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status in [
            SupplierInvoice.STATUS_PAID,
            SupplierInvoice.STATUS_READY_FOR_PAYMENT,
            SupplierInvoice.STATUS_APPROVAL,
        ]:
            return Response(
                {
                    "error": {
                        "code": "INVALID_STATE",
                        "message": f"Invoices in '{instance.status}' status cannot be deleted.",
                        "details": {"status": [f"Invoices in '{instance.status}' status cannot be deleted."]},
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        return super().destroy(request, *args, **kwargs)

    def perform_destroy(self, instance):
        if instance.status in [SupplierInvoice.STATUS_PAID, SupplierInvoice.STATUS_READY_FOR_PAYMENT, SupplierInvoice.STATUS_APPROVAL]:
            raise serializers.ValidationError(
                {"status": [f"Invoices in '{instance.status}' status cannot be deleted."]}
            )
        instance.delete()

    @extend_schema(
        request=InvoiceCreatePayloadSerializer,
        responses={201: SupplierInvoiceSerializer},
        summary="Create supplier invoice",
        description="Create a new supplier invoice against a purchase order with line items.",
    )
    @action(detail=False, methods=["post"], url_path="create-invoice")
    def create_invoice(self, request):
        vendor_id = request.data.get("vendor_id")
        po_id = request.data.get("po_id")
        invoice_number = request.data.get("invoice_number")
        invoice_date = request.data.get("invoice_date")
        due_date = request.data.get("due_date")
        line_items = request.data.get("line_items", [])
        notes = request.data.get("notes", "")

        if not vendor_id or not po_id or not invoice_number or not invoice_date:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "vendor_id, po_id, invoice_number, and invoice_date are required.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not isinstance(line_items, list) or not all(isinstance(i, dict) for i in line_items):
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": "line_items must be a list of JSON objects.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            vendor = Vendor.objects.get(pk=vendor_id)
        except (Vendor.DoesNotExist, ValueError):
            return Response(
                {"error": {"code": "NOT_FOUND", "message": f"Vendor '{vendor_id}' not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            po = PurchaseOrder.objects.get(pk=po_id)
        except (PurchaseOrder.DoesNotExist, ValueError):
            return Response(
                {"error": {"code": "NOT_FOUND", "message": f"Purchase Order '{po_id}' not found."}},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            invoice = create_supplier_invoice_service(
                vendor=vendor,
                po=po,
                invoice_number=invoice_number,
                invoice_date=invoice_date,
                due_date=due_date,
                line_items=line_items,
                notes=notes,
                created_by_user=request.user,
            )
            return Response(SupplierInvoiceSerializer(invoice).data, status=status.HTTP_201_CREATED)
        except (ValidationError, ValueError) as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="run-match")
    def run_match(self, request, pk=None):
        invoice = self.get_object()
        if invoice.status == SupplierInvoice.STATUS_PAID:
            raise serializers.ValidationError(
                {"status": "Cannot re-run 3-way matching: Paid invoices are immutable and cannot be re-matched."}
            )
        if invoice.status in [SupplierInvoice.STATUS_PAID, SupplierInvoice.STATUS_READY_FOR_PAYMENT]:
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": f"Cannot re-run 3-way matching on invoice in '{invoice.status}' status.",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            updated_invoice, exceptions = run_3_way_match_service(
                invoice=invoice, user=request.user
            )
            return Response(
                {
                    "invoice": SupplierInvoiceSerializer(updated_invoice).data,
                    "exceptions_count": len(exceptions),
                },
                status=status.HTTP_200_OK,
            )
        except ValidationError as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["post"], url_path="pay")
    def pay_invoice(self, request, pk=None):
        invoice = self.get_object()
        payment_reference = request.data.get("payment_reference")
        payment_method = request.data.get("payment_method", "ACH_TRANSFER")
        payment_date = request.data.get("payment_date")
        try:
            paid_invoice = mark_invoice_paid_service(
                invoice=invoice,
                user=request.user,
                payment_reference=payment_reference,
                payment_method=payment_method,
                payment_date=payment_date,
            )
            return Response(SupplierInvoiceSerializer(paid_invoice).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


class MatchExceptionViewSet(viewsets.ModelViewSet):
    queryset = MatchException.objects.select_related("invoice", "resolved_by").all()
    serializer_class = MatchExceptionSerializer
    permission_classes = [IsAuthenticated, CanManageSupplierInvoice]

    def perform_create(self, serializer):
        serializer.save(status=MatchException.STATUS_OPEN)

    def get_queryset(self):
        user = self.request.user
        qs = self.queryset
        if not user or not user.is_authenticated:
            return qs.none()
        if getattr(user, "is_vendor", False) or getattr(user, "role_code", None) == Role.VENDOR_USER:
            if getattr(user, "vendor_id", None):
                return qs.filter(invoice__vendor_id=user.vendor_id)
            return qs.none()
        return qs

    @action(detail=True, methods=["post"], url_path="resolve")
    def resolve_exception(self, request, pk=None):
        exception = self.get_object()
        notes = str(request.data.get("resolution_notes", "")).strip()
        if not notes:
            return Response(
                {"error": {"code": "VALIDATION_ERROR", "message": "Resolution notes / justification are required to resolve a match exception."}},
                status=status.HTTP_400_BAD_REQUEST,
            )
        action_type = request.data.get("action", "RESOLVE")
        try:
            resolved = resolve_match_exception_service(
                match_exception=exception,
                resolved_by_user=request.user,
                resolution_notes=notes,
                action=action_type,
            )
            return Response(MatchExceptionSerializer(resolved).data, status=status.HTTP_200_OK)
        except ValidationError as e:
            msg = e.messages if hasattr(e, "messages") else [str(e)]
            return Response(
                {
                    "error": {
                        "code": "VALIDATION_ERROR",
                        "message": msg[0] if isinstance(msg, list) else str(e),
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )


router = DefaultRouter()
router.register(r"exceptions", MatchExceptionViewSet, basename="match-exception")
router.register(r"", SupplierInvoiceViewSet, basename="invoice")

urlpatterns = router.urls

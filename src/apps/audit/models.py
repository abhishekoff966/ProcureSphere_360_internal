import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

_TARGET_MODEL_MAP = {
    "PurchaseRequisition": ("requisitions", "PurchaseRequisition"),
    "PRLine": ("requisitions", "PRLine"),
    "PurchaseOrder": ("orders", "PurchaseOrder"),
    "POLine": ("orders", "POLine"),
    "POAmendment": ("orders", "POAmendment"),
    "Vendor": ("vendors", "Vendor"),
    "VendorDocument": ("vendors", "VendorDocument"),
    "SupplierInvoice": ("invoices", "SupplierInvoice"),
    "InvoiceLine": ("invoices", "InvoiceLine"),
    "MatchException": ("invoices", "MatchException"),
    "Contract": ("contracts", "Contract"),
    "ContractVersion": ("contracts", "ContractVersion"),
    "ContractObligation": ("contracts", "ContractObligation"),
    "SourcingEvent": ("sourcing", "SourcingEvent"),
    "VendorBid": ("sourcing", "VendorBid"),
    "User": ("accounts", "User"),
    "Role": ("accounts", "Role"),
    "Budget": ("budgets", "Budget"),
}
_READABLE_TARGET_CACHE = {}


class AuditLogQuerySet(models.QuerySet):
    """
    Guarantees strict append-only immutability at the queryset level.
    Prevents bulk updates and bulk deletions via QuerySet.update() and QuerySet.delete().
    """

    def update(self, **kwargs):
        raise ValidationError(
            "AuditLog records are append-only and immutable. Bulk updates are strictly prohibited."
        )

    def delete(self):
        raise ValidationError(
            "AuditLog records are append-only and immutable. Bulk deletions are strictly prohibited."
        )


class AuditLog(models.Model):
    """
    Append-only immutable audit trail capturing state changes across ProcureSphere 360.
    Enforces strict immutability by preventing modifications and deletions.
    """

    ACTION_CREATE = "CREATE"
    ACTION_UPDATE = "UPDATE"
    ACTION_DELETE = "DELETE"
    ACTION_APPROVE = "APPROVE"
    ACTION_REJECT = "REJECT"
    ACTION_LOGIN = "LOGIN"
    ACTION_LOGOUT = "LOGOUT"
    ACTION_EXPORT = "EXPORT"
    ACTION_AMEND = "AMEND"
    ACTION_SUBMIT = "SUBMIT"
    ACTION_CANCEL = "CANCEL"
    ACTION_OVERRIDE = "OVERRIDE"
    ACTION_ACKNOWLEDGE = "ACKNOWLEDGE"
    ACTION_VERIFY = "VERIFY"
    ACTION_LOGIN_FAILED = "LOGIN_FAILED"
    ACTION_PUBLISH = "PUBLISH"

    objects = AuditLogQuerySet.as_manager()

    ACTION_CHOICES = [
        (ACTION_CREATE, "Create"),
        (ACTION_UPDATE, "Update"),
        (ACTION_DELETE, "Delete"),
        (ACTION_APPROVE, "Approve"),
        (ACTION_REJECT, "Reject"),
        (ACTION_LOGIN, "Login"),
        (ACTION_LOGIN_FAILED, "Login Failed"),
        (ACTION_LOGOUT, "Logout"),
        (ACTION_EXPORT, "Export Data"),
        (ACTION_AMEND, "Amend / Version"),
        (ACTION_SUBMIT, "Submit for Approval"),
        (ACTION_CANCEL, "Cancel"),
        (ACTION_OVERRIDE, "Override Exception"),
        (ACTION_ACKNOWLEDGE, "Acknowledge"),
        (ACTION_VERIFY, "Verify KYC / Compliance"),
        (ACTION_PUBLISH, "Publish Event"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)

    actor = models.ForeignKey(
        "accounts.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="audit_logs"
    )
    action = models.CharField(max_length=50, choices=ACTION_CHOICES, db_index=True)

    target_model = models.CharField(max_length=100, db_index=True)
    target_object_id = models.CharField(max_length=100, db_index=True)

    previous_state = models.JSONField(null=True, blank=True)
    new_state = models.JSONField(null=True, blank=True)

    ip_address = models.GenericIPAddressField(null=True, blank=True)
    request_id = models.CharField(max_length=100, blank=True, db_index=True)
    user_agent = models.TextField(blank=True)

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["target_model", "target_object_id"], name="audit_target_idx"),
            models.Index(fields=["timestamp", "action"], name="audit_ts_action_idx"),
            models.Index(fields=["actor", "timestamp"], name="audit_actor_ts_idx"),
            models.Index(fields=["request_id"], name="audit_req_id_idx"),
        ]

    @property
    def created_at(self):
        return self.timestamp

    @property
    def entity_name(self):
        return self.target_model

    @property
    def entity_id(self):
        return self.target_object_id

    @property
    def changes_summary(self):
        if self.new_state:
            return str(self.new_state)
        if self.previous_state:
            return str(self.previous_state)
        return ""

    def __str__(self):
        actor_str = self.actor.email if self.actor else "System"
        return f"Audit {self.action} on {self.target_model}:{self.target_object_id} by {actor_str} at {self.timestamp}"

    @property
    def readable_target(self):
        # 1. Fast path from state snapshot without any DB query
        state = self.new_state or self.previous_state
        if isinstance(state, dict):
            for key in (
                "pr_number",
                "po_number",
                "vendor_number",
                "invoice_number",
                "contract_number",
                "event_number",
                "email",
                "legal_name",
                "name",
                "title",
            ):
                val = state.get(key)
                if val:
                    return f"{self.target_model} {val}"

        tid_str = str(self.target_object_id or "").strip()
        if not tid_str:
            return self.target_model

        # 2. Fast path if ID is already an explicit business number or email
        if any(tid_str.startswith(prefix) for prefix in ("PR-", "PO-", "VND-", "INV-", "CON-", "RFQ-", "RFP-", "SE-")):
            return f"{self.target_model} {tid_str}"
        if "@" in tid_str:
            return f"{self.target_model} {tid_str}"

        # 3. Check in-memory cache
        cache_key = (self.target_model, tid_str)
        if cache_key in _READABLE_TARGET_CACHE:
            return _READABLE_TARGET_CACHE[cache_key]

        # 4. Direct model lookup with caching
        from django.apps import apps
        from django.contrib.auth import get_user_model

        resolved = None
        try:
            user_model = get_user_model()
            if self.target_model in ("User", user_model._meta.object_name):
                try:
                    obj = user_model.objects.get(pk=tid_str)
                    resolved = f"{self.target_model} {str(obj)}"
                except (user_model.DoesNotExist, ValueError):
                    pass

            if not resolved:
                app_info = _TARGET_MODEL_MAP.get(self.target_model)
                if app_info:
                    try:
                        model = apps.get_model(app_info[0], app_info[1])
                        obj = model.objects.get(pk=tid_str)
                        resolved = f"{self.target_model} {str(obj)}"
                    except Exception:
                        pass

            if not resolved:
                for app_config in apps.get_app_configs():
                    try:
                        model = app_config.get_model(self.target_model)
                        if getattr(model._meta, "swapped", False):
                            continue
                        obj = model.objects.get(pk=tid_str)
                        resolved = f"{self.target_model} {str(obj)}"
                        break
                    except (LookupError, AttributeError, model.DoesNotExist):
                        continue
        except Exception:
            pass

        if not resolved:
            short_id = tid_str[:8]
            resolved = f"{self.target_model} #{short_id}"

        # Cache resolved string (bounded cache size to prevent memory leak)
        if len(_READABLE_TARGET_CACHE) > 5000:
            _READABLE_TARGET_CACHE.clear()
        _READABLE_TARGET_CACHE[cache_key] = resolved
        return resolved

    @property
    def actor_email(self):
        return self.actor.email if self.actor else "System / Automated"

    @property
    def actor_role(self):
        if self.actor and hasattr(self.actor, "role_code") and self.actor.role_code:
            return self.actor.role_code
        return "SYSTEM"

    @property
    def summary(self):
        if self.new_state and isinstance(self.new_state, dict):
            status = self.new_state.get("status")
            if status:
                return f"Status transitioned to {status}"
            title = (
                self.new_state.get("title")
                or self.new_state.get("legal_name")
                or self.new_state.get("number")
            )
            if title:
                return f"{self.action} on {title}"
        return f"{self.get_action_display()} on {self.target_model}"

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError(
                "AuditLog records are append-only and immutable. Edits are strictly prohibited."
            )
        if not self.ip_address:
            try:
                from apps.core.middleware import get_client_ip
                client_ip = get_client_ip()
                if client_ip:
                    self.ip_address = client_ip
            except Exception:
                pass
        if not self.request_id:
            try:
                from apps.core.middleware import get_current_request_id
                req_id = get_current_request_id()
                if req_id:
                    self.request_id = req_id
            except Exception:
                pass
        if not self.user_agent:
            try:
                from apps.core.middleware import get_current_user_agent
                ua = get_current_user_agent()
                if ua:
                    self.user_agent = ua
            except Exception:
                pass
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(
            "AuditLog records are append-only and immutable. Deletions are strictly prohibited."
        )


@receiver(pre_delete, sender=AuditLog)
def protect_audit_log_delete(sender, instance, **kwargs):
    raise ValidationError(
        "AuditLog records are append-only and immutable. Deletions are strictly prohibited."
    )


@receiver(pre_save, sender=AuditLog)
def protect_audit_log_update(sender, instance, **kwargs):
    if instance.pk and AuditLog.objects.filter(pk=instance.pk).exists():
        raise ValidationError(
            "AuditLog records are append-only and immutable. Edits are strictly prohibited."
        )

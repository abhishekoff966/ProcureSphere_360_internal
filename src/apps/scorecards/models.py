from decimal import Decimal

import django
from django.db import models

from apps.core.models import TimeStampedModel

_CHECK_KW = "condition" if django.VERSION >= (5, 1) else "check"

# Performance bands shared across all consumer modules (Vendor Master, Vendor Governance,
# dashboards). Calculation stays single-sourced here; consumers only read these.
PERFORMANCE_GOOD_THRESHOLD = Decimal("85.00")
PERFORMANCE_ISSUE_THRESHOLD = Decimal("70.00")
PERFORMANCE_BANDS = ("NO_DATA", "GOOD", "WATCH", "ISSUE")


def band_for_composite(score):
    """Upper-case band label for a composite score (None → NO_DATA). Single band authority."""
    if score is None:
        return "NO_DATA"
    if score >= PERFORMANCE_GOOD_THRESHOLD:
        return "GOOD"
    if score >= PERFORMANCE_ISSUE_THRESHOLD:
        return "WATCH"
    return "ISSUE"


class VendorScorecard(TimeStampedModel):
    vendor = models.ForeignKey(
        "vendors.Vendor", on_delete=models.CASCADE, related_name="scorecards"
    )
    evaluation_period = models.CharField(max_length=50)  # e.g., Q1-2026

    # All indicator scores are 0-100 and NULL when no transactional data exists for the
    # indicator (never fabricated). PRD 3.3: delivery, quality, price, responsiveness,
    # compliance and SLA.
    delivery_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100: share of GRNs received on/before the expected delivery date",
    )
    quality_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100: accepted quantity / received quantity",
    )
    price_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100: invoiced value adherence to PO value (over-billing penalised)",
    )
    compliance_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100 based on vendor KYC/governance status",
    )
    responsiveness_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100: RFQ invitation response rate and PO acknowledgement rate",
    )
    sla_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="0-100: vendor contract obligations fulfilled on/before due date",
    )

    composite_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Weighted score over available core indicators",
    )
    evaluator_comments = models.TextField(blank=True)
    evaluated_by = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="evaluated_scorecards"
    )

    # Existing approved weighting for the four core indicators. Responsiveness and SLA are
    # reported but not weighted (see docs/assumptions.md ASSUMP-012, OPEN DECISION).
    CORE_WEIGHTS = {
        "delivery_score": Decimal("0.30"),
        "quality_score": Decimal("0.30"),
        "price_score": Decimal("0.20"),
        "compliance_score": Decimal("0.20"),
    }

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["vendor", "created_at"], name="scorecard_vendor_ts_idx")]

    @property
    def overall_score(self):
        return self.composite_score

    def save(self, *args, **kwargs):
        # Weighted average re-normalised over indicators that have data.
        available = {
            field: weight
            for field, weight in self.CORE_WEIGHTS.items()
            if getattr(self, field) is not None
        }
        total_weight = sum(available.values(), Decimal("0"))
        if total_weight and self.composite_score is None:
            weighted = sum(
                (Decimal(getattr(self, f)) * w for f, w in available.items()), Decimal("0")
            )
            self.composite_score = round(weighted / total_weight, 2)
        elif not total_weight and self.composite_score is None:
            self.composite_score = None
        super().save(*args, **kwargs)

    @property
    def performance_band(self):
        return band_for_composite(self.composite_score)

    @property
    def band_display(self):
        band = self.performance_band
        return {
            "GOOD": "Good",
            "WATCH": "Watch",
            "ISSUE": "Issue",
            "NO_DATA": "No data",
        }[band]

    @property
    def indicator_rows(self):
        return [
            ("Delivery", self.delivery_score),
            ("Quality", self.quality_score),
            ("Price", self.price_score),
            ("Responsiveness", self.responsiveness_score),
            ("Compliance", self.compliance_score),
            ("SLA", self.sla_score),
        ]

    def __str__(self):
        return f"Scorecard: {self.vendor.legal_name} [{self.evaluation_period}] Score: {self.composite_score}"


class VendorScorecardSLAMetric(TimeStampedModel):
    """
    Per-scorecard snapshot of contract SLA obligation performance at evaluation time.
    One OVERALL row plus one row per contract with due vendor obligations, so the
    obligation data used for the SLA score is preserved as evaluation history.
    """

    MetricCodes = models.TextChoices("MetricCodes", "OVERALL CONTRACT")

    scorecard = models.ForeignKey(
        VendorScorecard, on_delete=models.CASCADE, related_name="sla_metrics"
    )
    contract = models.ForeignKey(
        "contracts.Contract",
        on_delete=models.CASCADE,
        related_name="sla_metric_snapshots",
        null=True,
        blank=True,
        help_text="Set only when metric_code is CONTRACT",
    )
    metric_code = models.CharField(max_length=20, choices=MetricCodes.choices)
    target_pct = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal("100.00"))
    actual_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True, help_text="None when no data"
    )
    achievement_pct = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    breaches = models.PositiveIntegerField(default=0)
    observations = models.PositiveIntegerField(default=0, help_text="Due obligations counted")
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["scorecard__created_at", "metric_code", "contract__contract_number"]
        indexes = [
            models.Index(fields=["scorecard", "metric_code"], name="sla_metric_card_code_idx")
        ]
        constraints = [
            models.CheckConstraint(
                **{
                    _CHECK_KW: models.Q(contract__isnull=False, metric_code="CONTRACT")
                    | models.Q(contract__isnull=True, metric_code="OVERALL")
                },
                name="sla_metric_code_contract_consistency",
            )
        ]

    def __str__(self):
        label = getattr(self.contract, "contract_number", None) or "Overall"
        return f"SLA metric [{self.metric_code}] {label}: {self.actual_pct}"

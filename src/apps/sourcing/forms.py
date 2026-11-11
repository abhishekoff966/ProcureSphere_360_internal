from decimal import Decimal

from django import forms

from apps.requisitions.models import PurchaseRequisition

from .models import SourcingEvent

SUPPORTED_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M",
    "%d/%m/%Y %I:%M %p",
    "%m/%d/%Y %I:%M %p",
]

DATETIME_INPUT = forms.DateTimeInput(
    attrs={"type": "datetime-local", "class": "form-control"}, format="%Y-%m-%dT%H:%M"
)


class SourcingEventForm(forms.Form):
    title = forms.CharField(max_length=255, widget=forms.TextInput(attrs={"class": "form-control"}))
    event_type = forms.ChoiceField(
        choices=SourcingEvent.EVENT_TYPE_CHOICES,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    requisition = forms.ModelChoiceField(
        queryset=PurchaseRequisition.objects.none(),
        required=False,
        empty_label="Select a requisition (optional)...",
        widget=forms.Select(attrs={"class": "form-select"}),
        empty_label="-- Select Requisition (Optional) --",
        help_text="Approved requisition this event sources (optional).",
    )
    bid_start_date = forms.DateTimeField(
        widget=DATETIME_INPUT, input_formats=SUPPORTED_DATETIME_FORMATS, label="Bid start"
    )
    bid_end_date = forms.DateTimeField(
        widget=DATETIME_INPUT, input_formats=SUPPORTED_DATETIME_FORMATS, label="Bid deadline"
    )
    description = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "form-control"}),
        label="Scope / requirements",
    )
    technical_requirements = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"})
    )
    commercial_requirements = forms.CharField(
        required=False, widget=forms.Textarea(attrs={"rows": 4, "class": "form-control"})
    )
    required_documents = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 2, "class": "form-control"}),
        label="Required supporting documents",
    )
    technical_weight = forms.DecimalField(
        min_value=0,
        max_value=100,
        initial=Decimal("50"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )
    commercial_weight = forms.DecimalField(
        min_value=0,
        max_value=100,
        initial=Decimal("50"),
        widget=forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["requisition"].queryset = PurchaseRequisition.objects.filter(
            status="APPROVED"
        ).order_by("-created_at")
        from django.utils import timezone
        now_str = timezone.localtime(timezone.now()).strftime("%Y-%m-%dT%H:%M")
        self.fields["bid_start_date"].widget.attrs["min"] = now_str
        self.fields["bid_end_date"].widget.attrs["min"] = now_str

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("bid_start_date"), cleaned.get("bid_end_date")
        from datetime import timedelta

        from django.utils import timezone
        now = timezone.now()
        buffer_time = now - timedelta(minutes=5)
        if start:
            start_aware = start if timezone.is_aware(start) else timezone.make_aware(start)
            if start_aware < buffer_time:
                self.add_error(
                    "bid_start_date",
                    "Bid start date cannot be in the past. Must start from the current date or a future date.",
                )
        if end:
            end_aware = end if timezone.is_aware(end) else timezone.make_aware(end)
            if end_aware < buffer_time:
                self.add_error("bid_end_date", "Bid deadline cannot be in the past.")
        if start and end and start >= end:
            self.add_error("bid_end_date", "Bid deadline must be after the bid start.")
        tw, cw = cleaned.get("technical_weight"), cleaned.get("commercial_weight")
        if tw is not None and cw is not None and tw + cw != Decimal("100"):
            self.add_error("commercial_weight", "Technical and commercial weights must total 100.")
        return cleaned

    @classmethod
    def initial_from_event(cls, event):
        from django.utils import timezone
        now = timezone.now()
        start = event.bid_start_date
        if start and start < now:
            start = now
        end = event.bid_end_date
        if end and end < now:
            end = now
        return {
            "title": event.title,
            "event_type": event.event_type,
            "requisition": event.requisition_id,
            "bid_start_date": start,
            "bid_end_date": end,
            "description": event.description,
            "technical_requirements": event.technical_requirements,
            "commercial_requirements": event.commercial_requirements,
            "required_documents": event.required_documents,
            "technical_weight": event.technical_weight,
            "commercial_weight": event.commercial_weight,
        }

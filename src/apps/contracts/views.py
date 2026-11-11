import io
import logging
import mimetypes
import os
from xml.sax.saxutils import escape

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import Role
from apps.audit.models import AuditLog

from .forms import (
    ContractAmendmentForm,
    ContractCreateForm,
    ContractDocumentForm,
    ContractMilestoneForm,
    ContractObligationForm,
    ContractRenewalForm,
)
from .models import Contract, ContractDocument, ContractMilestone, ContractObligation
from .selectors import (
    get_contract_by_id,
    get_contracts_qs,
    get_legal_dashboard_metrics,
)
from .services import (
    add_contract_milestone_service,
    add_contract_obligation_service,
    approve_business_service,
    approve_legal_review_service,
    complete_contract_milestone_service,
    create_contract_service,
    create_contract_version_service,
    fulfill_contract_obligation_service,
    reject_legal_review_service,
    renew_contract_service,
    submit_for_legal_review_service,
    terminate_contract_service,
    upload_contract_document_service,
)

logger = logging.getLogger(__name__)


def _enforce_internal_contracts_access(request):
    user = request.user
    role_code = getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )
    if not user.is_authenticated:
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("Authentication required.")
    if user.is_superuser:
        return
    if role_code == Role.VENDOR_USER or getattr(user, "is_vendor", False):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("External vendors are not authorized to access internal contracts. Use the Vendor Portal.")



def _get_user_role(user):
    if not user or not user.is_authenticated:
        return None
    return getattr(user, "role_code", None) or (
        user.role.code if getattr(user, "role", None) else None
    )


def _is_auditor(user):
    return _get_user_role(user) == Role.AUDITOR


def _validation_error_text(exc):
    return " ".join(exc.messages) if hasattr(exc, "messages") else str(exc)


def _form_error_text(form, prefix):
    details = "; ".join(
        f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
    )
    return f"{prefix} {details}".strip() if details else prefix


def _can_view_contract_list(user):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    role_code = _get_user_role(user)
    if role_code in [Role.REQUESTER, Role.DEPT_APPROVER, Role.STORES_RECEIVER, Role.VENDOR_USER]:
        return False
    return role_code in [
        Role.SUPER_ADMIN,
        Role.LEGAL_MGR,
        Role.PROC_MGR,
        Role.PROC_EXEC,
        Role.FINANCE_AP,
        Role.AUDITOR,
    ]


def _can_view_contract(user, contract):
    if not user or not user.is_authenticated:
        return False
    if user.is_superuser:
        return True
    role_code = _get_user_role(user)
    if role_code in [Role.REQUESTER, Role.DEPT_APPROVER, Role.STORES_RECEIVER]:
        return False
    if role_code == Role.VENDOR_USER:
        vendor = getattr(user, "vendor", None)
        return vendor is not None and contract.vendor_id == vendor.id
    if role_code in [
        Role.SUPER_ADMIN,
        Role.LEGAL_MGR,
        Role.PROC_MGR,
        Role.PROC_EXEC,
        Role.FINANCE_AP,
        Role.AUDITOR,
    ]:
        if role_code == Role.SUPER_ADMIN:
            return True
        return get_contracts_qs(user=user).filter(id=contract.id).exists()
    return False


@login_required(login_url="/login/")
def list_view(request):
    """
    Contract Register List View with filtering by status, search query, metrics, and pagination.
    """
    _enforce_internal_contracts_access(request)
    if not _can_view_contract_list(request.user):
        messages.error(
            request,
            "Permission Denied: You do not have permission to access the contract register.",
        )
        return redirect("home")

    status_filter = request.GET.get("status", "").strip()
    search_query = request.GET.get("q", "").strip()

    items = get_contracts_qs(user=request.user, status=status_filter, search=search_query)
    metrics = get_legal_dashboard_metrics(user=request.user)

    paginator = Paginator(items, 10)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    query_params = request.GET.copy()
    query_params.pop("page", None)

    context = {
        "items": page_obj.object_list,
        "page_obj": page_obj,
        "metrics": metrics,
        "current_status": status_filter,
        "search_query": search_query,
        "querystring": query_params.urlencode(),
        "statuses": Contract.STATUS_CHOICES,
    }
    return render(request, "pages/contracts/list.html", context)


@login_required(login_url="/login/")
def create_view(request):
    """
    Creates a new Contract in DRAFT state.
    """
    _enforce_internal_contracts_access(request)
    role_code = getattr(request.user, "role_code", None) or (
        request.user.role.code if getattr(request.user, "role", None) else None
    )
    allowed_roles = [
        Role.SUPER_ADMIN,
        Role.LEGAL_MGR,
        Role.PROC_MGR,
        Role.PROC_EXEC,
        "SUPER_ADMIN",
        "LEGAL_MGR",
        "PROC_MGR",
        "PROC_EXEC",
    ]
    if not (request.user.is_superuser or role_code in allowed_roles):
        messages.error(
            request,
            "Permission Denied: Contract drafting is restricted to Legal and Procurement Managers.",
        )
        return redirect("contracts_list")

    if request.method == "POST":
        form = ContractCreateForm(request.POST)
        if form.is_valid():
            try:
                contract = create_contract_service(
                    title=form.cleaned_data["title"],
                    vendor=form.cleaned_data["vendor"],
                    contract_value=form.cleaned_data["contract_value"],
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    renewal_notice_days=form.cleaned_data["renewal_notice_days"],
                    sourcing_event=form.cleaned_data.get("sourcing_event"),
                    po=form.cleaned_data.get("po"),
                    contract_owner=request.user,
                )
                messages.success(
                    request, f"Contract '{contract.contract_number}' drafted successfully."
                )
                return redirect("contract_detail", contract_id=contract.id)
            except ValidationError as e:
                messages.error(request, str(e))
            except Exception as e:
                messages.error(request, f"Failed to create contract: {str(e)}")
    else:
        form = ContractCreateForm()

    return render(request, "pages/contracts/create.html", {"form": form})


@login_required(login_url="/login/")
def detail_view(request, contract_id):
    """
    Contract Lifecycle & SLA Workspace Detail View.
    """
    contract = get_contract_by_id(contract_id)
    if not contract:
        messages.error(request, "Contract record not found.")
        return redirect("contracts_list")

    if not _can_view_contract(request.user, contract):
        messages.error(
            request,
            "Permission Denied: You do not have permission to view this contract.",
        )
        if _can_view_contract_list(request.user):
            return redirect("contracts_list")
        return redirect("home")

    audit_logs = AuditLog.objects.filter(
        target_model="Contract", target_object_id=str(contract.id)
    ).order_by("-timestamp")

    amendment_form = ContractAmendmentForm(
        initial={
            "contract_value": contract.contract_value,
            "start_date": contract.start_date,
            "end_date": contract.end_date,
        }
    )
    milestone_form = ContractMilestoneForm()
    obligation_form = ContractObligationForm()
    document_form = ContractDocumentForm()
    renewal_form = ContractRenewalForm(initial={"new_value": contract.contract_value})

    role_code = _get_user_role(request.user)
    is_legal_mgr = bool(request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.SUPER_ADMIN])
    is_proc_mgr = bool(request.user.is_superuser or role_code in [Role.PROC_MGR, Role.SUPER_ADMIN])
    can_business_approve = bool(is_proc_mgr or role_code == Role.DEPT_APPROVER)
    can_manage_contracts = bool(is_legal_mgr or is_proc_mgr)
    can_manage_sla = bool(
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    )
    can_submit_legal = bool(can_manage_sla or contract.contract_owner_id == request.user.id)

    context = {
        "contract": contract,
        "versions": contract.versions.all().order_by("-version_number"),
        "milestones": contract.milestones.all().order_by("due_date"),
        "obligations": contract.obligations.all().order_by("due_date"),
        "documents": contract.documents.all().order_by("-created_at"),
        "alerts": contract.alerts.all().order_by("-triggered_at"),
        "audit_logs": audit_logs,
        "amendment_form": amendment_form,
        "milestone_form": milestone_form,
        "obligation_form": obligation_form,
        "document_form": document_form,
        "renewal_form": renewal_form,
        "is_legal_mgr": is_legal_mgr,
        "is_proc_mgr": is_proc_mgr,
        "can_business_approve": can_business_approve,
        "can_manage_contracts": can_manage_contracts,
        "can_manage_sla": can_manage_sla,
        "can_submit_legal": can_submit_legal,
    }
    return render(request, "pages/contracts/detail.html", context)


@login_required(login_url="/login/")
@require_POST
def submit_legal_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.SUPER_ADMIN, Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC]
        or contract.contract_owner_id == request.user.id
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to submit contracts for legal review.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    notes = request.POST.get("notes", "")
    try:
        submit_for_legal_review_service(contract=contract, user=request.user, notes=notes)
        messages.success(
            request, f"Contract '{contract.contract_number}' submitted for Legal Review."
        )
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def legal_approve_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.SUPER_ADMIN]):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers or Super Admins can approve legal reviews.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    notes = request.POST.get("notes", "")
    try:
        approve_legal_review_service(contract=contract, user=request.user, notes=notes)
        messages.success(
            request,
            f"Contract '{contract.contract_number}' Legal Review APPROVED. Status moved to Business Approval.",
        )
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def legal_reject_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.SUPER_ADMIN]):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers or Super Admins can reject legal reviews.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Rejection reason is required.")
        return redirect("contract_detail", contract_id=contract.id)
    try:
        reject_legal_review_service(contract=contract, user=request.user, reason=reason)
        messages.warning(
            request,
            f"Contract '{contract.contract_number}' Legal Review REJECTED and returned to Draft.",
        )
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def business_approve_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.PROC_MGR, Role.DEPT_APPROVER, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: Only Procurement Managers or Department Approvers can perform business approval.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    notes = request.POST.get("notes", "")
    try:
        approve_business_service(contract=contract, user=request.user, notes=notes)
        messages.success(
            request,
            f"Contract '{contract.contract_number}' Business Approval completed! Contract is now ACTIVE.",
        )
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def amend_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers, Procurement Managers, or Super Admins can amend contracts.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    form = ContractAmendmentForm(request.POST)
    if form.is_valid():
        try:
            version_record = create_contract_version_service(
                contract=contract,
                user=request.user,
                amendment_summary=form.cleaned_data["amendment_summary"],
                contract_value=form.cleaned_data["contract_value"],
                start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"],
            )
            messages.success(
                request,
                f"Contract '{contract.contract_number}' amended to Version {version_record.version_number}.",
            )
        except ValidationError as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Invalid amendment form details.")
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def milestone_create_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to create milestones.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    form = ContractMilestoneForm(request.POST)
    if form.is_valid():
        try:
            add_contract_milestone_service(
                contract=contract,
                title=form.cleaned_data["title"],
                due_date=form.cleaned_data["due_date"],
                amount=form.cleaned_data["amount"],
                user=request.user,
            )
            messages.success(request, f"Milestone '{form.cleaned_data['title']}' added successfully.")
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
    else:
        messages.error(request, _form_error_text(form, "Invalid milestone details."))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def milestone_toggle_view(request, contract_id, milestone_id):
    milestone = get_object_or_404(ContractMilestone, id=milestone_id, contract_id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to modify milestones.",
        )
        return redirect("contract_detail", contract_id=contract_id)
    try:
        complete_contract_milestone_service(milestone=milestone, user=request.user)
        messages.success(request, f"Milestone '{milestone.title}' marked as COMPLETED.")
    except ValidationError as exc:
        messages.error(request, _validation_error_text(exc))
    return redirect("contract_detail", contract_id=contract_id)


@login_required(login_url="/login/")
@require_POST
def obligation_create_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to create obligations.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    form = ContractObligationForm(request.POST)
    if form.is_valid():
        try:
            add_contract_obligation_service(
                contract=contract,
                title=form.cleaned_data["title"],
                responsible_party=form.cleaned_data["responsible_party"],
                due_date=form.cleaned_data["due_date"],
                user=request.user,
            )
            messages.success(request, f"Obligation '{form.cleaned_data['title']}' added successfully.")
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
    else:
        messages.error(request, _form_error_text(form, "Invalid obligation details."))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def obligation_toggle_view(request, contract_id, obligation_id):
    obligation = get_object_or_404(ContractObligation, id=obligation_id, contract_id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to fulfill obligations.",
        )
        return redirect("contract_detail", contract_id=contract_id)
    try:
        fulfill_contract_obligation_service(obligation=obligation, user=request.user)
        messages.success(request, f"Obligation '{obligation.title}' marked as FULFILLED.")
    except ValidationError as exc:
        messages.error(request, _validation_error_text(exc))
    return redirect("contract_detail", contract_id=contract_id)


@login_required(login_url="/login/")
@require_POST
def document_upload_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser
        or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.PROC_EXEC, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: You do not have permission to upload contract documents.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    form = ContractDocumentForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            upload_contract_document_service(
                contract=contract,
                user=request.user,
                title=form.cleaned_data["title"],
                file=form.cleaned_data["file"],
            )
            messages.success(request, f"Document '{form.cleaned_data['title']}' uploaded successfully.")
        except ValidationError as exc:
            messages.error(request, _validation_error_text(exc))
    else:
        messages.error(request, _form_error_text(form, "Invalid file upload submission."))
    return redirect("contract_detail", contract_id=contract.id)


def _build_missing_contract_document_pdf(contract, document, retrieved_by):
    """
    Generates an in-memory official PDF Archive Notice when a ContractDocument
    physical file is missing on ephemeral storage without mutating stored files.
    """
    buf = io.BytesIO()
    original_name = os.path.basename(getattr(document.file, "name", "") or "contract_document")
    uploaded_by_email = getattr(getattr(document, "uploaded_by", None), "email", "N/A")
    uploaded_at = (
        document.created_at.strftime("%Y-%m-%d %H:%M UTC")
        if getattr(document, "created_at", None)
        else "Recorded"
    )
    retrieved_at = timezone.now().strftime("%Y-%m-%d %H:%M UTC")
    vendor_name = getattr(getattr(contract, "vendor", None), "legal_name", "N/A")
    retriever_email = getattr(retrieved_by, "email", "Authenticated User")

    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.platypus import (
            HRFlowable,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
            Table,
            TableStyle,
        )

        pdf_doc = SimpleDocTemplate(
            buf,
            pagesize=letter,
            leftMargin=40,
            rightMargin=40,
            topMargin=40,
            bottomMargin=40,
        )
        styles = getSampleStyleSheet()
        title_style = ParagraphStyle(
            "ContractArchiveTitle",
            parent=styles["Heading1"],
            fontSize=15,
            textColor=colors.HexColor("#0F172A"),
            spaceAfter=4,
        )
        notice_style = ParagraphStyle(
            "ContractArchiveNotice",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=14,
            textColor=colors.HexColor("#7C2D12"),
        )
        meta_style = ParagraphStyle(
            "ContractArchiveMeta",
            parent=styles["Normal"],
            fontSize=9.5,
            leading=13,
            textColor=colors.HexColor("#1E293B"),
        )

        rows = [
            ["Contract Number", escape(str(contract.contract_number))],
            ["Contract Title", escape(str(contract.title))],
            ["Vendor", escape(str(vendor_name))],
            ["Contract Status", escape(str(contract.get_status_display()))],
            ["Document ID", escape(str(document.id))],
            ["Document Title", escape(str(document.title))],
            ["Original Filename", escape(original_name)],
            ["Uploaded By", escape(str(uploaded_by_email))],
            ["Uploaded At", escape(str(uploaded_at))],
            ["Archive Requested By", escape(str(retriever_email))],
            ["Generated At", escape(str(retrieved_at))],
        ]
        table = Table(
            [[Paragraph(f"<b>{escape(k)}</b>", meta_style), Paragraph(v, meta_style)] for k, v in rows],
            colWidths=[160, 355],
        )
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F8FAFC")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E1")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ]
            )
        )

        elements = [
            Paragraph("<b>ProcureSphere 360</b> — Contract Document Archive Notice", title_style),
            HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CBD5E1")),
            Spacer(1, 10),
            Paragraph(
                "<b>Notice:</b> The original uploaded binary file is currently unavailable on "
                "ephemeral application storage. This archive notice confirms the verified contract "
                "document record registered in PostgreSQL. Please re-upload the executed document "
                "if the original binary attachment is needed.",
                notice_style,
            ),
            Spacer(1, 12),
            table,
        ]
        pdf_doc.build(elements)
        buf.seek(0)
        return buf
    except Exception:
        logger.warning(
            "ReportLab fallback failed for contract document %s; emitting minimal PDF archive notice.",
            document.id,
            exc_info=True,
        )
        fallback = io.BytesIO(
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Count 1/Kids[3 0 R]>>endobj\n"
            b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R/Resources<<>>>>endobj\n"
            b"xref\n0 4\n0000000000 65535 f \n0000000009 00000 n \n"
            b"0000000052 00000 n \n0000000101 00000 n \n"
            b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n178\n%%EOF\n"
        )
        fallback.seek(0)
        return fallback


@login_required(login_url="/login/")
def document_download_view(request, contract_id, document_id):
    """
    Safely downloads/retrieves a contract document with RBAC, object scoping,
    storage-safe streaming, ephemeral fallback handling, and audit logging.
    """
    contract = get_object_or_404(Contract, id=contract_id)
    if not _can_view_contract(request.user, contract):
        raise PermissionDenied(
            "Permission Denied: You are not authorized to access documents for this contract."
        )

    document = get_object_or_404(ContractDocument, id=document_id, contract=contract)

    if not document.file or not getattr(document.file, "name", ""):
        return render(request, "404.html", status=404)

    safe_filename = (
        os.path.basename(document.file.name).replace("\r", "").replace("\n", "")
        or f"contract_document_{document.id}"
    )
    content_type, _ = mimetypes.guess_type(safe_filename)

    try:
        file_handle = document.file.open("rb")
    except FileNotFoundError:
        logger.warning(
            "ContractDocument %s physical file missing on storage (%s); serving archive notice PDF.",
            document.id,
            document.file.name,
        )
        pdf_buffer = _build_missing_contract_document_pdf(contract, document, request.user)
        archive_stem = os.path.splitext(safe_filename)[0] or f"contract_document_{document.id}"
        archive_filename = f"{archive_stem}_archive_record.pdf"
        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.ACTION_EXPORT,
            target_model="ContractDocument",
            target_object_id=str(document.id),
            new_state={
                "document_title": document.title,
                "file_name": safe_filename,
                "archive_file_name": archive_filename,
                "storage_fallback": True,
            },
        )
        return FileResponse(
            pdf_buffer,
            as_attachment=True,
            filename=archive_filename,
            content_type="application/pdf",
        )
    except ValueError:
        logger.warning(
            "ContractDocument %s has an invalid storage file reference (%s).",
            document.id,
            document.file.name,
            exc_info=True,
        )
        return render(request, "404.html", status=404)
    except Exception:
        logger.error(
            "Storage error opening ContractDocument %s (%s).",
            document.id,
            document.file.name,
            exc_info=True,
        )
        return HttpResponse(
            "Contract document storage is temporarily unavailable. Please try again later.",
            status=503,
            content_type="text/plain",
        )

    try:
        AuditLog.objects.create(
            actor=request.user,
            action=AuditLog.ACTION_EXPORT,
            target_model="ContractDocument",
            target_object_id=str(document.id),
            new_state={
                "document_title": document.title,
                "file_name": safe_filename,
            },
        )
    except Exception:
        file_handle.close()
        raise

    return FileResponse(
        file_handle,
        as_attachment=True,
        filename=safe_filename,
        content_type=content_type or "application/octet-stream",
    )


@login_required(login_url="/login/")
@require_POST
def renew_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers, Procurement Managers, or Super Admins can renew contracts.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    form = ContractRenewalForm(request.POST)
    if form.is_valid():
        try:
            renew_contract_service(
                contract=contract,
                user=request.user,
                new_end_date=form.cleaned_data["new_end_date"],
                new_value=form.cleaned_data.get("new_value"),
                notes=form.cleaned_data.get("notes", ""),
            )
            messages.success(
                request, f"Contract '{contract.contract_number}' successfully RENEWED."
            )
        except ValidationError as e:
            messages.error(request, str(e))
    else:
        messages.error(request, "Invalid renewal date or details.")
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
@require_POST
def terminate_view(request, contract_id):
    contract = get_object_or_404(Contract, id=contract_id)
    role_code = _get_user_role(request.user)
    if not (
        request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.PROC_MGR, Role.SUPER_ADMIN]
    ):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers, Procurement Managers, or Super Admins can terminate contracts.",
        )
        return redirect("contract_detail", contract_id=contract.id)
    reason = request.POST.get("reason", "").strip()
    if not reason:
        messages.error(request, "Termination reason is required.")
        return redirect("contract_detail", contract_id=contract.id)
    try:
        terminate_contract_service(contract=contract, user=request.user, reason=reason)
        messages.warning(request, f"Contract '{contract.contract_number}' TERMINATED.")
    except ValidationError as e:
        messages.error(request, str(e))
    return redirect("contract_detail", contract_id=contract.id)


@login_required(login_url="/login/")
def dashboard_view(request):
    """
    Legal & SLA Governance Desk Dashboard View for Legal Managers.
    """
    role_code = _get_user_role(request.user)
    if not (request.user.is_superuser or role_code in [Role.LEGAL_MGR, Role.SUPER_ADMIN]):
        messages.error(
            request,
            "Permission Denied: Access to Legal Dashboard is restricted to Legal Managers and Super Admins.",
        )
        return redirect("home")

    from .selectors import (
        get_contracts_pending_legal_review,
        get_expiring_contracts,
        get_legal_dashboard_metrics,
        get_pending_milestones,
        get_pending_obligations,
        get_pending_vendor_verifications_qs,
    )

    pending_legal = get_contracts_pending_legal_review(user=request.user)
    expiring_contracts = get_expiring_contracts(days=30, user=request.user)
    pending_obligations = get_pending_obligations(user=request.user)
    pending_milestones = get_pending_milestones(user=request.user)
    metrics = get_legal_dashboard_metrics(user=request.user)
    pending_vendor_verifications = get_pending_vendor_verifications_qs(user=request.user)

    context = {
        "metrics": metrics,
        "pending_legal_list": pending_legal[:10],
        "expiring_contracts_list": expiring_contracts[:10],
        "pending_obligations_list": pending_obligations[:10],
        "pending_milestones_list": pending_milestones[:10],
        "pending_vendor_verifications": pending_vendor_verifications,
    }
    return render(request, "pages/dashboards/legal_dashboard.html", context)


@login_required(login_url="/login/")
@require_POST
def legal_validate_vendor_credentials_view(request, dispatch_id):
    """
    POST action for Legal Manager to approve and activate vendor credentials and portal access.
    """
    from apps.vendors.permissions import can_legal_verify_vendor
    from apps.vendors.services import legal_validate_vendor_credentials_service

    if not can_legal_verify_vendor(request.user):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers and Super Admins can verify vendor credentials.",
        )
        return redirect("contracts_dashboard")

    notes = request.POST.get("notes", "").strip()
    try:
        dispatch = legal_validate_vendor_credentials_service(
            dispatch_id=dispatch_id, legal_user=request.user, notes=notes
        )
        messages.success(
            request,
            f"Vendor portal credentials for '{dispatch.vendor.legal_name}' ({dispatch.sent_to}) successfully verified and activated.",
        )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    except Exception as exc:
        messages.error(request, f"Failed to validate vendor credentials: {exc}")

    return redirect("contracts_dashboard")


@login_required(login_url="/login/")
@require_POST
def legal_reject_vendor_credentials_view(request, dispatch_id):
    """
    POST action for Legal Manager to reject vendor credentials and request changes.
    """
    from apps.vendors.permissions import can_legal_verify_vendor
    from apps.vendors.services import legal_reject_vendor_credentials_service

    if not can_legal_verify_vendor(request.user):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers and Super Admins can verify vendor credentials.",
        )
        return redirect("contracts_dashboard")

    notes = request.POST.get("notes", "").strip()
    if not notes:
        messages.error(request, "Reviewer notes are required when rejecting vendor credentials.")
        return redirect("contracts_dashboard")

    rejection_type = request.POST.get("rejection_type", "CHANGES_REQUIRED")
    try:
        dispatch = legal_reject_vendor_credentials_service(
            dispatch_id=dispatch_id, legal_user=request.user, notes=notes, rejection_type=rejection_type
        )
        if rejection_type.strip().upper() == "REJECT":
            messages.warning(
                request,
                f"Vendor '{dispatch.vendor.legal_name}' registration formally REJECTED.",
            )
        else:
            messages.warning(
                request,
                f"Vendor credentials for '{dispatch.vendor.legal_name}' rejected. Status set to Changes Required.",
            )
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    except Exception as exc:
        messages.error(request, f"Failed to reject vendor credentials: {exc}")

    return redirect("contracts_dashboard")


@login_required(login_url="/login/")
@require_POST
def legal_document_decision_view(request, document_id):
    """
    POST action for Legal Manager to record a document-level verification decision:
    VERIFY, REJECT, or REQUEST_CORRECTION.
    """
    from apps.vendors.permissions import can_legal_verify_vendor
    from apps.vendors.services import verify_vendor_document_decision_service

    if not can_legal_verify_vendor(request.user):
        messages.error(
            request,
            "Permission Denied: Only Legal Managers and Super Admins can verify vendor documents.",
        )
        return redirect("contracts_dashboard")

    decision = request.POST.get("decision", "").strip().upper()
    notes = request.POST.get("reviewer_notes", "").strip()

    try:
        doc = verify_vendor_document_decision_service(
            document_id=document_id,
            reviewer=request.user,
            decision=decision,
            reviewer_notes=notes,
        )
        if decision == "VERIFY":
            messages.success(request, f"Document '{doc.title}' has been successfully verified.")
        elif decision == "REJECT":
            messages.warning(request, f"Document '{doc.title}' rejected.")
        else:
            messages.info(request, f"Corrections requested for document '{doc.title}'.")
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages) if hasattr(exc, "messages") else str(exc))
    except Exception as exc:
        messages.error(request, f"Failed to record document decision: {exc}")

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "contracts_dashboard"
    return redirect(next_url)

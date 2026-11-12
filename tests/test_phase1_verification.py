import pytest
from decimal import Decimal
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient
from django.test import Client
from axes.models import AccessAttempt
from axes.handlers.proxy import AxesProxyHandler
from apps.accounts.models import Role, User
from apps.organization.models import Organization, Department, CostCenter, FiscalPeriod
from apps.budgets.models import Budget
from apps.vendors.models import Vendor, VendorCategory
from apps.orders.models import PurchaseOrder, POLine
from apps.invoices.models import SupplierInvoice, InvoiceLine
from apps.audit.models import AuditLog


pytestmark = [pytest.mark.django_db]


@pytest.fixture(autouse=True)
def clean_axes_state(db):
    AccessAttempt.objects.all().delete()
    yield
    AccessAttempt.objects.all().delete()


@pytest.fixture
def phase1_env(db):
    # Organizations
    org_a = Organization.objects.create(name="Acme Corp", code="ACME")
    org_b = Organization.objects.create(name="Beta Industries", code="BETA")

    dept_a = Department.objects.create(name="Engineering", code="ENG-A", organization=org_a)
    dept_b = Department.objects.create(name="Operations", code="OPS-B", organization=org_b)

    cc_a = CostCenter.objects.create(name="Cost Center A", code="CC-A", department=dept_a)
    cc_b = CostCenter.objects.create(name="Cost Center B", code="CC-B", department=dept_b)

    year = timezone.now().year
    fp, _ = FiscalPeriod.objects.get_or_create(
        organization=org_a,
        year=year,
        period_number=1,
        defaults={
            "name": f"FY{year}-P1",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
            "is_closed": False,
        },
    )
    Budget.objects.get_or_create(
        cost_center=cc_a,
        fiscal_period=fp,
        defaults={
            "allocated_amount": Decimal("100000.00"),
            "committed_amount": Decimal("10000.00"),
            "allow_overspend": True,
        },
    )
    Budget.objects.get_or_create(
        cost_center=cc_b,
        fiscal_period=fp,
        defaults={
            "allocated_amount": Decimal("100000.00"),
            "committed_amount": Decimal("10000.00"),
            "allow_overspend": True,
        },
    )

    # Roles
    r_admin, _ = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})
    r_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    r_dept, _ = Role.objects.get_or_create(code=Role.DEPT_APPROVER, defaults={"name": "Dept Approver"})
    r_exec, _ = Role.objects.get_or_create(code=Role.PROC_EXEC, defaults={"name": "Procurement Executive"})
    r_mgr, _ = Role.objects.get_or_create(code=Role.PROC_MGR, defaults={"name": "Procurement Manager"})
    r_fin, _ = Role.objects.get_or_create(code=Role.FINANCE_AP, defaults={"name": "Finance AP"})
    r_recv, _ = Role.objects.get_or_create(code=Role.STORES_RECEIVER, defaults={"name": "Stores Receiver"})
    r_legal, _ = Role.objects.get_or_create(code=Role.LEGAL_MGR, defaults={"name": "Legal Manager"})
    r_aud, _ = Role.objects.get_or_create(code=Role.AUDITOR, defaults={"name": "Auditor"})
    r_vend, _ = Role.objects.get_or_create(code=Role.VENDOR_USER, defaults={"name": "Vendor User"})

    # Users in Org A
    user_admin = User.objects.create_user(
        email="admin@acme.test", password="Password123!", role=r_admin, organization=org_a, is_staff=True, is_superuser=True
    )
    user_req = User.objects.create_user(
        email="req@acme.test", password="Password123!", role=r_req, organization=org_a, department=dept_a, cost_center=cc_a
    )
    user_dept = User.objects.create_user(
        email="dept@acme.test", password="Password123!", role=r_dept, organization=org_a, department=dept_a
    )
    user_exec = User.objects.create_user(
        email="exec@acme.test", password="Password123!", role=r_exec, organization=org_a
    )
    user_mgr = User.objects.create_user(
        email="mgr@acme.test", password="Password123!", role=r_mgr, organization=org_a
    )
    user_fin = User.objects.create_user(
        email="fin@acme.test", password="Password123!", role=r_fin, organization=org_a
    )
    user_recv = User.objects.create_user(
        email="recv@acme.test", password="Password123!", role=r_recv, organization=org_a
    )
    user_legal = User.objects.create_user(
        email="legal@acme.test", password="Password123!", role=r_legal, organization=org_a
    )
    user_aud = User.objects.create_user(
        email="aud@acme.test", password="Password123!", role=r_aud, organization=org_a
    )

    # Secondary user for multi-account login tests
    user_other = User.objects.create_user(
        email="other_colleague@acme.test", password="Password123!", role=r_req, organization=org_a
    )

    cat, _ = VendorCategory.objects.get_or_create(name="IT Services", code="IT-01")

    # Vendor A in Org A
    vendor_a = Vendor.objects.create(
        organization=org_a,
        legal_name="Vendor A Technologies",
        vendor_number="VND-A-001",
        tax_identification_number="TIN-A-001",
        email="contact@vendora.test",
        address="100 Tech Way",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )
    user_vendor_a = User.objects.create_user(
        email="vendor_a@vendora.test",
        password="Password123!",
        role=r_vend,
        organization=org_a,
        vendor=vendor_a,
    )

    # Vendor B in Org B
    vendor_b = Vendor.objects.create(
        organization=org_b,
        legal_name="Vendor B Logistics",
        vendor_number="VND-B-001",
        tax_identification_number="TIN-B-001",
        email="contact@vendorb.test",
        address="200 Freight Ave",
        category=cat,
        status=Vendor.STATUS_ACTIVE,
    )
    user_vendor_b = User.objects.create_user(
        email="vendor_b@vendorb.test",
        password="Password123!",
        role=r_vend,
        organization=org_b,
        vendor=vendor_b,
    )

    # PO and Invoice in Org A
    po_a = PurchaseOrder.objects.create(
        po_number="PO-ACME-001",
        vendor=vendor_a,
        cost_center=cc_a,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("10000.00"),
    )
    po_line_a = POLine.objects.create(
        po=po_a,
        item_description="Server Hardware",
        quantity=Decimal("100.00"),
        unit_price=Decimal("100.00"),
    )
    inv_a = SupplierInvoice.objects.create(
        vendor=vendor_a,
        po=po_a,
        invoice_number="INV-A-1001",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("1000.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("1000.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )
    InvoiceLine.objects.create(
        invoice=inv_a,
        po_line=po_line_a,
        item_description="Server Hardware",
        quantity=Decimal("10.00"),
        unit_price=Decimal("100.00"),
        line_total=Decimal("1000.00"),
    )

    # PO and Invoice in Org B
    po_b = PurchaseOrder.objects.create(
        po_number="PO-BETA-001",
        vendor=vendor_b,
        cost_center=cc_b,
        status=PurchaseOrder.STATUS_ISSUED,
        total_amount=Decimal("500.00"),
    )
    po_line_b = POLine.objects.create(
        po=po_b,
        item_description="Office Chairs",
        quantity=Decimal("5.00"),
        unit_price=Decimal("100.00"),
    )
    inv_b = SupplierInvoice.objects.create(
        vendor=vendor_b,
        po=po_b,
        invoice_number="INV-B-2001",
        invoice_date=timezone.now().date(),
        due_date=timezone.now().date() + timezone.timedelta(days=30),
        subtotal=Decimal("500.00"),
        tax_amount=Decimal("0.00"),
        total_amount=Decimal("500.00"),
        status=SupplierInvoice.STATUS_RECEIVED,
    )
    InvoiceLine.objects.create(
        invoice=inv_b,
        po_line=po_line_b,
        item_description="Office Chairs",
        quantity=Decimal("5.00"),
        unit_price=Decimal("100.00"),
        line_total=Decimal("500.00"),
    )

    return {
        "org_a": org_a,
        "org_b": org_b,
        "admin": user_admin,
        "req": user_req,
        "dept": user_dept,
        "exec": user_exec,
        "mgr": user_mgr,
        "fin": user_fin,
        "recv": user_recv,
        "legal": user_legal,
        "aud": user_aud,
        "other": user_other,
        "vend_a": user_vendor_a,
        "vend_b": user_vendor_b,
        "vendor_a": vendor_a,
        "vendor_b": vendor_b,
        "po_a": po_a,
        "po_line_a": po_line_a,
        "po_b": po_b,
        "inv_a": inv_a,
        "inv_b": inv_b,
    }


# =========================================================================
# DEF-001 – Authentication Account Lockout Scenarios
# =========================================================================
class TestDef001AuthenticationAccountLockout:

    def test_five_consecutive_failed_logins_triggers_429_account_locked(self, phase1_env):
        """Scenario: Five consecutive incorrect login attempts trigger HTTP 429 with ACCOUNT_LOCKED."""
        client = APIClient()
        email = phase1_env["req"].email

        for i in range(1, 5):
            res = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")
            assert res.status_code == status.HTTP_401_UNAUTHORIZED
            assert res.json()["error"]["code"] == "AUTHENTICATION_FAILED"

        # 5th failed attempt triggers lockout
        res_5th = client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")
        assert res_5th.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        data_5th = res_5th.json()
        assert data_5th["error"]["code"] == "ACCOUNT_LOCKED"
        assert "locked" in data_5th["error"]["message"].lower()

    def test_correct_password_while_account_locked_returns_429_account_locked(self, phase1_env):
        """Scenario: Entering the correct password while account is locked is rejected with HTTP 429."""
        client = APIClient()
        email = phase1_env["req"].email

        # Cause lockout with 5 failed attempts
        for _ in range(5):
            client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")

        # Now submit the REAL correct password
        res_correct = client.post("/api/v1/auth/login/", {"email": email, "password": "Password123!"}, format="json")
        assert res_correct.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        data = res_correct.json()
        assert data["error"]["code"] == "ACCOUNT_LOCKED"
        assert "retry_after_seconds" in data["error"].get("details", {})

    def test_successful_login_after_legitimate_unlock_reset(self, phase1_env):
        """Scenario: Successful login occurs after legitimate reset of the account lockout counter."""
        client = APIClient()
        email = phase1_env["req"].email

        # Cause lockout
        for _ in range(5):
            client.post("/api/v1/auth/login/", {"email": email, "password": "WrongPassword!"}, format="json")

        # Verify locked
        res_locked = client.post("/api/v1/auth/login/", {"email": email, "password": "Password123!"}, format="json")
        assert res_locked.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # Legitimate administrator / system unlock: reset axes counters for this user
        AccessAttempt.objects.filter(username=email.lower()).delete()

        # Attempt login again with correct credentials
        res_success = client.post("/api/v1/auth/login/", {"email": email, "password": "Password123!"}, format="json")
        assert res_success.status_code == status.HTTP_200_OK
        assert res_success.json()["user"]["email"] == email

    def test_login_with_different_account_from_same_ip_succeeds(self, phase1_env):
        """Scenario: Account lockout is per-user, so another account from the same IP can log in cleanly."""
        client = APIClient()
        victim_email = phase1_env["req"].email
        colleague_email = phase1_env["other"].email

        # Lock victim's account from this client
        for _ in range(5):
            client.post("/api/v1/auth/login/", {"email": victim_email, "password": "WrongPassword!"}, format="json")

        # Confirm victim is locked
        res_victim = client.post("/api/v1/auth/login/", {"email": victim_email, "password": "Password123!"}, format="json")
        assert res_victim.status_code == status.HTTP_429_TOO_MANY_REQUESTS

        # Colleague on the same client / IP attempts login with correct credentials
        res_colleague = client.post(
            "/api/v1/auth/login/", {"email": colleague_email, "password": "Password123!"}, format="json"
        )
        assert res_colleague.status_code == status.HTTP_200_OK
        assert res_colleague.json()["user"]["email"] == colleague_email

    def test_proxy_ip_handling_x_forwarded_for(self, phase1_env):
        """Scenario: Proxy IP handling behind Render/load balancers resolves true client IP and records it."""
        client = APIClient()
        email = phase1_env["req"].email
        proxy_client_ip = "198.51.100.77"

        # Submit failed login through proxy header
        res = client.post(
            "/api/v1/auth/login/",
            {"email": email, "password": "WrongPassword!"},
            format="json",
            HTTP_X_FORWARDED_FOR=f"{proxy_client_ip}, 10.0.0.1",
        )
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # Verify audit log recorded client IP
        latest_audit = AuditLog.objects.filter(
            target_model="User", action=AuditLog.ACTION_LOGIN_FAILED
        ).order_by("-timestamp").first()
        assert latest_audit is not None
        assert latest_audit.ip_address == proxy_client_ip


# =========================================================================
# DEF-002 – Invoice Read Permissions Scenarios
# =========================================================================
class TestDef002InvoiceReadPermissions:

    def test_requester_dept_approver_legal_mgr_receive_403(self, phase1_env):
        """Scenario: REQUESTER, DEPT_APPROVER, and LEGAL_MGR receive HTTP 403 for invoice list access."""
        api = APIClient()

        # Requester
        api.force_authenticate(user=phase1_env["req"])
        res_req = api.get("/api/v1/invoices/")
        assert res_req.status_code == status.HTTP_403_FORBIDDEN

        # Dept Approver
        api.force_authenticate(user=phase1_env["dept"])
        res_dept = api.get("/api/v1/invoices/")
        assert res_dept.status_code == status.HTTP_403_FORBIDDEN

        # Legal Mgr
        api.force_authenticate(user=phase1_env["legal"])
        res_legal = api.get("/api/v1/invoices/")
        assert res_legal.status_code == status.HTTP_403_FORBIDDEN

    def test_authorized_roles_receive_200(self, phase1_env):
        """Scenario: Authorized roles (Finance AP, Proc Mgr, Proc Exec, Stores Receiver, Auditor, Super Admin) receive HTTP 200."""
        api = APIClient()

        authorized_users = [
            phase1_env["fin"],
            phase1_env["mgr"],
            phase1_env["exec"],
            phase1_env["recv"],
            phase1_env["aud"],
            phase1_env["admin"],
        ]

        for user in authorized_users:
            api.force_authenticate(user=user)
            res = api.get("/api/v1/invoices/")
            assert res.status_code == status.HTTP_200_OK, f"Role {user.role_code} was denied HTTP 200 on invoices"

        # Vendor User also receives HTTP 200 scoped to their own vendor
        api.force_authenticate(user=phase1_env["vend_a"])
        res_vend = api.get("/api/v1/invoices/")
        assert res_vend.status_code == status.HTTP_200_OK
        data = res_vend.json()
        items = data.get("results", data)
        assert len(items) >= 1
        assert all(str(item["vendor"]) == str(phase1_env["vendor_a"].id) for item in items)

    def test_cross_tenant_invoice_access_denied(self, phase1_env):
        """Scenario: Cross-tenant invoice access is denied; tenant records remain isolated."""
        api = APIClient()

        # Finance user from Org A lists invoices
        api.force_authenticate(user=phase1_env["fin"])
        res_list = api.get("/api/v1/invoices/")
        assert res_list.status_code == status.HTTP_200_OK
        data = res_list.json()
        items = data.get("results", data)
        returned_ids = [item["id"] for item in items]

        # Invoices belonging to Org A must be present, Org B must NOT be present
        assert str(phase1_env["inv_a"].id) in returned_ids
        assert str(phase1_env["inv_b"].id) not in returned_ids

        # Attempting direct detail access to Org B's invoice returns 404
        res_detail = api.get(f"/api/v1/invoices/{phase1_env['inv_b'].id}/")
        assert res_detail.status_code == status.HTTP_404_NOT_FOUND

    def test_existing_invoice_submission_and_payment_workflows(self, phase1_env):
        """Scenario: Full existing invoice submission, matching, and payment workflow remains functional."""
        api = APIClient()

        # 1. Vendor submits an invoice against an issued PO
        api.force_authenticate(user=phase1_env["vend_a"])
        res_create = api.post(
            "/api/v1/invoices/",
            data={
                "vendor": str(phase1_env["vendor_a"].id),
                "po": str(phase1_env["po_a"].id),
                "invoice_number": "INV-NEW-9901",
                "invoice_date": str(timezone.now().date()),
                "due_date": str(timezone.now().date() + timezone.timedelta(days=30)),
                "subtotal": "1000.00",
                "tax_amount": "0.00",
                "total_amount": "1000.00",
            },
            format="json",
        )
        assert res_create.status_code == status.HTTP_201_CREATED
        new_inv_id = res_create.json()["id"]

        # 2. Finance AP views the invoice and marks it READY_FOR_PAYMENT
        invoice = SupplierInvoice.objects.get(pk=new_inv_id)
        invoice.status = SupplierInvoice.STATUS_READY_FOR_PAYMENT
        invoice.save(update_fields=["status"])

        # 3. Finance AP executes payment
        api.force_authenticate(user=phase1_env["fin"])
        res_pay = api.post(
            f"/api/v1/invoices/{new_inv_id}/pay/",
            data={
                "payment_reference": "PAY-REF-9901",
                "payment_method": "ACH_TRANSFER",
                "payment_date": str(timezone.now().date()),
            },
            format="json",
        )
        assert res_pay.status_code == status.HTTP_200_OK
        invoice.refresh_from_db()
        assert invoice.status == SupplierInvoice.STATUS_PAID

        # 4. Attempting to modify or delete a PAID invoice is rejected
        res_patch = api.patch(f"/api/v1/invoices/{new_inv_id}/", data={"notes": "Illegal edit"}, format="json")
        assert res_patch.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_409_CONFLICT)

        res_delete = api.delete(f"/api/v1/invoices/{new_inv_id}/")
        assert res_delete.status_code in (status.HTTP_400_BAD_REQUEST, status.HTTP_403_FORBIDDEN, status.HTTP_409_CONFLICT)

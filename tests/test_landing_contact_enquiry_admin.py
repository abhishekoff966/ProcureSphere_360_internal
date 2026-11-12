import pytest
from django.test import Client
from django.urls import reverse

from apps.accounts.models import Role, User
from apps.core.models import ContactEnquiry


@pytest.mark.django_db
class TestLandingContactEnquiryAdmin:
    def test_contact_form_submission_persists_to_db(self):
        """
        Public user submits the landing page contact form.
        Enquiry must be saved to PostgreSQL database as ContactEnquiry with status 'NEW'.
        """
        client = Client()
        response = client.post(
            reverse("contact_submit"),
            data={
                "name": "Aarav Patel",
                "email": "aarav.patel@example.com",
                "country_code": "+91",
                "phone": "9876543210",
                "message": "We need an enterprise procurement setup for 3 manufacturing plants.",
            },
            HTTP_USER_AGENT="Mozilla/5.0 TestBrowser",
            REMOTE_ADDR="192.168.1.50",
        )

        assert response.status_code == 200
        assert "Thank you" in response.content.decode("utf-8") or "Aarav Patel" in response.content.decode("utf-8")

        enquiry = ContactEnquiry.objects.filter(email="aarav.patel@example.com").first()
        assert enquiry is not None
        assert enquiry.name == "Aarav Patel"
        assert enquiry.country_code == "+91"
        assert enquiry.phone == "9876543210"
        assert enquiry.formatted_phone == "+91 9876543210"
        assert enquiry.status == ContactEnquiry.STATUS_NEW
        assert enquiry.message == "We need an enterprise procurement setup for 3 manufacturing plants."
        assert enquiry.user_agent == "Mozilla/5.0 TestBrowser"
        assert enquiry.ip_address == "192.168.1.50"

    def test_contact_form_invalid_input_does_not_persist(self):
        """
        Invalid submissions (bad email, mismatched phone digits) must fail validation and not persist.
        """
        client = Client()
        initial_count = ContactEnquiry.objects.count()

        # Invalid phone (only 5 digits for India)
        response = client.post(
            reverse("contact_submit"),
            data={
                "name": "Invalid User",
                "email": "invalid@example.com",
                "country_code": "+91",
                "phone": "12345",
                "message": "This is a short test message.",
            },
        )
        assert response.status_code == 200
        assert "Mobile number for" in response.content.decode("utf-8")
        assert ContactEnquiry.objects.count() == initial_count

    def test_django_admin_superadmin_can_view_and_manage_enquiries(self):
        """
        Superadmin (superadmin@procuresphere.com) logs into Django admin
        and can view the Contact Enquiry change list and details.
        """
        enquiry = ContactEnquiry.objects.create(
            name="Rohan Verma",
            email="rohan.verma@example.com",
            country_code="+91",
            phone="9123456789",
            message="Looking for vendor scorecard automation.",
        )

        sa_role = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})[0]
        superadmin = User.objects.create(
            email="superadmin_test@procuresphere.com",
            role=sa_role,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )

        client = Client()
        client.force_login(superadmin)

        # Admin index lists Contact Enquiry
        index_res = client.get("/admin/")
        assert index_res.status_code == 200
        assert "contactenquiry" in index_res.content.decode("utf-8").lower()

        # Contact Enquiry change list displays the record
        list_res = client.get("/admin/core/contactenquiry/")
        assert list_res.status_code == 200
        content = list_res.content.decode("utf-8")
        assert "Rohan Verma" in content
        assert "rohan.verma@example.com" in content
        assert "+91 9123456789" in content

        # Admin change detail view
        detail_res = client.get(f"/admin/core/contactenquiry/{enquiry.id}/change/")
        assert detail_res.status_code == 200
        assert "Looking for vendor scorecard automation." in detail_res.content.decode("utf-8")

        # Admin action to update status to CONTACTED
        action_res = client.post(
            "/admin/core/contactenquiry/",
            data={
                "action": "mark_as_contacted",
                "_selected_action": [str(enquiry.id)],
            },
            follow=True,
        )
        assert action_res.status_code == 200
        enquiry.refresh_from_db()
        assert enquiry.status == ContactEnquiry.STATUS_CONTACTED

    def test_unauthorized_user_cannot_access_admin_enquiries(self):
        """
        Regular users (e.g. Requester or unauthenticated) cannot access /admin/core/contactenquiry/.
        """
        req_role = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]
        requester = User.objects.create(
            email="requester_test@example.com",
            role=req_role,
            is_staff=False,
            is_superuser=False,
        )

        client = Client()

        # Unauthenticated redirects to login
        res_anon = client.get("/admin/core/contactenquiry/")
        assert res_anon.status_code == 302
        assert "/login" in res_anon.url or "/admin/login" in res_anon.url

        # Non-staff requester redirects or is forbidden
        client.force_login(requester)
        res_req = client.get("/admin/core/contactenquiry/")
        assert res_req.status_code in (302, 403)

    def test_contact_form_submission_redirects_to_django_platform_admin(self):
        """
        Submitting the public contact form authenticates as Platform Admin
        and redirects directly to the Django Platform Admin dashboard (/admin/).
        """
        # Ensure superadmin user exists
        sa_role = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})[0]
        User.objects.get_or_create(
            email="superadmin@procuresphere.com",
            defaults={
                "role": sa_role,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )

        client = Client()
        # HTMX submission (default landing page behavior)
        response = client.post(
            reverse("contact_submit"),
            data={
                "name": "Ananya Sharma",
                "email": "ananya.sharma@enterprise.com",
                "country_code": "+91",
                "phone": "9876543210",
                "message": "We would like to inspect the complete enterprise platform.",
            },
            HTTP_HX_REQUEST="true",
        )

        assert response.status_code == 200
        assert response.headers.get("HX-Redirect") == "/admin/"
        assert response.wsgi_request.user.is_authenticated
        assert response.wsgi_request.user.email == "superadmin@procuresphere.com"
        assert response.wsgi_request.user.is_superuser

        # Following to /admin/ delivers the Platform Admin dashboard
        admin_res = client.get("/admin/")
        assert admin_res.status_code == 200
        content = admin_res.content.decode("utf-8")
        assert "Super Admin Control Center" in content or "Platform Admin" in content
        assert "admin-index-layout-container" in content
        assert "saSidebar" in content
        assert "sidebarToggleBtn" in content
        assert "Contact Enquiries" in content

    def test_platform_admin_layout_contains_clean_offsets_and_toggle(self):
        """
        Verify /admin/ includes the standardized header, sidebar, and layout offset classes.
        """
        sa_role = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})[0]
        superadmin, _ = User.objects.get_or_create(
            email="superadmin@procuresphere.com",
            defaults={
                "role": sa_role,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )

        client = Client()
        client.force_login(superadmin)
        res = client.get("/admin/")
        assert res.status_code == 200
        content = res.content.decode("utf-8")

        assert "admin-index-layout-container" in content
        assert 'id="saSidebar"' in content
        assert 'id="sidebarToggleBtn"' in content
        assert "initSidebarToggle" in content
        assert "Total Organizations" in content or "Total Users" in content

    def test_superadmin_orgs_show_more_clean_models_and_contact_enquiries_ui(self):
        """
        Verify:
        1. Global superadmin /admin/ & /superadmin/organizations/ limit initial org list with Show More.
        2. Clean model index cards replace broken app_list table layout on /admin/.
        3. Manage Users & Roles and Audit History buttons are removed for Global Super Admin.
        4. Stylish Contact Enquiries UI works across sidebar links and supports status/notes triage.
        """
        from apps.organization.models import Organization

        sa_role = Role.objects.get_or_create(code=Role.SUPER_ADMIN, defaults={"name": "Super Admin"})[0]
        superadmin = User.objects.create_user(
            email="superadmin.ui@procuresphere.com",
            password="StrongPassword!123",
            role=sa_role,
            is_staff=True,
            is_superuser=True,
            is_active=True,
        )
        req_role = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})[0]
        requester = User.objects.create_user(
            email="req.enq@procuresphere.com",
            password="StrongPassword!123",
            role=req_role,
        )

        for i in range(7):
            Organization.objects.create(code=f"ORG-UI-{i:02d}", name=f"UI Test Org {i:02d}")

        enq = ContactEnquiry.objects.create(
            name="Priya Nair",
            email="priya.nair@enterprise.in",
            country_code="+91",
            phone="9876501234",
            message="Interested in multi-entity sourcing workflows.",
        )

        client = Client()
        client.force_login(superadmin)

        # 1 & 2 & 3 on /admin/
        admin_res = client.get("/admin/")
        assert admin_res.status_code == 200
        admin_html = admin_res.content.decode("utf-8")
        assert "admin-org-extra-row d-none" in admin_html
        assert "toggleAdminOrgsRows(" in admin_html
        assert "Show More (" in admin_html
        assert 'href="/admin/organizations/"' in admin_html
        assert 'href="/admin/contact-enquiries/"' in admin_html
        assert 'href="/admin/notifications/"' in admin_html
        assert "Provisioned Admins" not in admin_html
        assert "Manage Users &amp; Roles" not in admin_html
        assert "Manage Users & Roles" not in admin_html
        assert "Audit History" not in admin_html
<<<<<<< Updated upstream
        assert "SUPER ADMIN WORKSPACE" in admin_html
        assert 'title="Sign out"' not in admin_html
=======
>>>>>>> Stashed changes
        assert "/admin/core/contactenquiry/" in admin_html

        # 5 on /admin/organizations/ (and backwards compatibility on /superadmin/organizations/)
        orgs_res = client.get("/admin/organizations/")
        assert orgs_res.status_code == 200
        orgs_html = orgs_res.content.decode("utf-8")
<<<<<<< Updated upstream
        assert "Super Admin Workspace" in orgs_html
=======
>>>>>>> Stashed changes
        assert 'href="/admin/contact-enquiries/"' in orgs_html
        assert 'href="/admin/organizations/' in orgs_html
        assert "Contact Enquiries" in orgs_html
        assert "Manage Users &amp; Roles" not in orgs_html
<<<<<<< Updated upstream
        assert 'title="Sign out"' not in orgs_html
=======
>>>>>>> Stashed changes
        assert "org-extra-row d-none" in orgs_html
        assert "Show More (" in orgs_html
        assert client.get("/superadmin/organizations/").status_code == 200

        # 4 on /admin/contact-enquiries/ (and no Raw Admin View button)
        enq_res = client.get("/admin/contact-enquiries/")
        assert enq_res.status_code == 200
        enq_html = enq_res.content.decode("utf-8")
        assert "Priya Nair" in enq_html
        assert "priya.nair@enterprise.in" in enq_html
        assert "View &amp; Triage" in enq_html
        assert "Raw Admin View" not in enq_html

        # Legacy Notification with /admin/core/contactenquiry/ opens clean /admin/contact-enquiries/
        from apps.notifications.models import Notification

        legacy_notif = Notification.objects.create(
            recipient=superadmin,
            notification_type="SYSTEM",
            title=f"New Landing Contact Enquiry: {enq.name}",
            message="Legacy notification target check",
            target_url="/admin/core/contactenquiry/",
        )
        notif_page = client.get("/admin/notifications/")
        assert notif_page.status_code == 200
        notif_html = notif_page.content.decode("utf-8")
        assert "saSidebar" in notif_html
        assert f"/admin/notifications/{legacy_notif.id}/open/" in notif_html
        open_res = client.post(f"/admin/notifications/{legacy_notif.id}/open/", follow=False)
        assert open_res.status_code == 302
        assert open_res.url == "/admin/contact-enquiries/"

        # PO/Procurement notifications opened by Global Superadmin also stay under /admin/...
        import uuid

        po_notif = Notification.objects.create(
            recipient=superadmin,
            notification_type="APPROVAL",
            title="PO Approval Required: PO-TEST-001",
            message="Purchase Order requires approval.",
            target_url=f"/orders/{uuid.uuid4()}/",
        )
        po_open_res = client.post(f"/admin/notifications/{po_notif.id}/open/", follow=False)
        assert po_open_res.status_code == 302
        assert po_open_res.url.startswith("/admin/orders/")

        # Status & notes update persists to DB via /admin/contact-enquiries/<id>/status/
        upd_res = client.post(
            f"/admin/contact-enquiries/{enq.id}/status/",
            data={"status": ContactEnquiry.STATUS_RESOLVED, "notes": "Demo completed cleanly."},
        )
        assert upd_res.status_code == 302
        assert upd_res.url == "/admin/contact-enquiries/"
        enq.refresh_from_db()
        assert enq.status == ContactEnquiry.STATUS_RESOLVED
        assert enq.notes == "Demo completed cleanly."

        # Non-superadmin denied on /admin/contact-enquiries/ and /superadmin/contact-enquiries/
        client.force_login(requester)
        denied_res = client.get("/admin/contact-enquiries/")
        assert denied_res.status_code == 403
        denied_legacy = client.get("/superadmin/contact-enquiries/")
        assert denied_legacy.status_code == 403



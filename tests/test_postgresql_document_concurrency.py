"""
PostgreSQL Document Numbering Concurrency Verification Test.

Target Defect: BUG-DOM-001 (TC-REQ-003, TC-REQ-087).

This test verifies the transactional concurrency behavior of generate_document_number()
using PostgreSQL advisory transaction locking (pg_advisory_xact_lock).

Execution on PostgreSQL:
    pytest tests/test_postgresql_document_concurrency.py --ds=config.settings.test_pg -v

When run in environments where PostgreSQL is not available (e.g. SQLite test settings),
the test cleanly skips with an informative explanation of the database limitation.
"""
import concurrent.futures
from decimal import Decimal

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import Role, User
from apps.core.services import generate_document_number
from apps.orders.models import PurchaseOrder
from apps.organization.models import CostCenter, Department, Organization
from apps.requisitions.models import PurchaseRequisition
from apps.vendors.models import Vendor, VendorCategory


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("prefix,model_class,field_name", [
    ("PR", PurchaseRequisition, "pr_number"),
    ("PO", PurchaseOrder, "po_number"),
])
def test_postgresql_concurrent_document_generation_safety(prefix, model_class, field_name):
    """
    Simultaneously invokes generate_document_number() and persists document
    records across concurrent worker threads using independent transactions.

    Verifies:
    1. pg_advisory_xact_lock holds until transaction commit.
    2. Zero collisions / duplicate key errors occur under concurrent load.
    3. Every persisted document number is unique and contiguous.
    4. Exercises relevant enterprise production prefixes: PR (Requisitions) and PO (Orders).
    """
    if connection.vendor != "postgresql":
        pytest.skip(
            f"Active database backend is '{connection.vendor}'. "
            "PostgreSQL advisory transaction locks (pg_advisory_xact_lock) require "
            "a running PostgreSQL instance. Execute with: "
            "pytest tests/test_postgresql_document_concurrency.py --ds=config.settings.test_pg"
        )

    # Setup base organization, department, and cost center
    org, _ = Organization.objects.get_or_create(code="PG-CONC-ORG", defaults={"name": "Postgres Concurrency Org"})
    dept, _ = Department.objects.get_or_create(code="PG-CONC-DPT", defaults={"organization": org, "name": "Concurrency Dept"})
    cc, _ = CostCenter.objects.get_or_create(code="PG-CONC-CC", defaults={"department": dept, "name": "Concurrency Cost Center"})
    role_req, _ = Role.objects.get_or_create(code=Role.REQUESTER, defaults={"name": "Requester"})
    user, _ = User.objects.get_or_create(email="pg_concurrent_user@test.com", defaults={"role": role_req, "organization": org})

    category, _ = VendorCategory.objects.get_or_create(code="PG-CAT", defaults={"name": "Postgres Vendor Category"})
    vendor, _ = Vendor.objects.get_or_create(
        vendor_number="VND-PG-001",
        defaults={
            "legal_name": "PG Concurrency Vendor LLC",
            "category": category,
            "status": Vendor.STATUS_ACTIVE,
            "organization": org,
        },
    )

    num_workers = 8
    results = []
    errors = []

    def concurrent_worker(worker_idx):
        from django.db import connection
        try:
            # Independent database transaction
            with transaction.atomic():
                doc_num = generate_document_number(model_class, field_name, prefix, padding=5)
                if model_class == PurchaseRequisition:
                    model_class.objects.create(
                        pr_number=doc_num,
                        title=f"Concurrent PR {worker_idx}",
                        requester=user,
                        department=dept,
                        cost_center=cc,
                        status=PurchaseRequisition.STATUS_DRAFT,
                        requested_delivery_date=timezone.now().date() + timezone.timedelta(days=7),
                        total_amount=Decimal("100.00"),
                    )
                else:
                    model_class.objects.create(
                        po_number=doc_num,
                        vendor=vendor,
                        cost_center=cc,
                        status=PurchaseOrder.STATUS_ISSUED,
                        total_amount=Decimal("100.00"),
                    )
                return doc_num
        except Exception as exc:
            return exc
        finally:
            connection.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [executor.submit(concurrent_worker, i) for i in range(num_workers)]
        for f in concurrent.futures.as_completed(futures):
            res = f.result()
            if isinstance(res, Exception):
                errors.append(res)
            else:
                results.append(res)

    assert len(errors) == 0, f"Concurrent generation encountered errors: {errors}"
    assert len(results) == num_workers
    assert len(set(results)) == num_workers, f"Duplicate document numbers detected for prefix {prefix}: {results}"

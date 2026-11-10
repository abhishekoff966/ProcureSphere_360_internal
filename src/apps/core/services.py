import zlib

from django.db import connection
from django.utils import timezone


def generate_document_number(model_class, field_name: str, prefix: str, padding: int = 5, offset: int = 0) -> str:
    """
    Safely generates the next sequential document number (e.g. VND-2026-00001, PR-2026-00001, CON-2026-00001)
    by finding the highest existing sequence number for the current year/prefix.
    Uses PostgreSQL transaction advisory locking to guarantee atomic, collision-free
    number generation under heavy parallel load (resolves DEF-001).
    """
    year = timezone.now().strftime("%Y")
    base_prefix = f"{prefix}-{year}-"

    # Acquire an exclusive advisory transaction lock for this prefix and year
    if connection.vendor == "postgresql":
        lock_id = zlib.crc32(f"ps360_doc_{prefix}_{year}".encode("utf-8")) & 0x7FFFFFFF
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock_id])

    latest_obj = (
        model_class.objects.filter(**{f"{field_name}__startswith": base_prefix})
        .order_by(f"-{field_name}")
        .first()
    )
    next_num = 1
    if latest_obj:
        val = getattr(latest_obj, field_name, "")
        try:
            suffix_num = int(val[len(base_prefix):])
            next_num = suffix_num + 1
        except (ValueError, TypeError):
            next_num = model_class.objects.count() + 1

    next_num += offset
    # Guarantee uniqueness even in concurrent or deleted row edge cases
    while model_class.objects.filter(**{field_name: f"{base_prefix}{next_num:0{padding}d}"}).exists():
        next_num += 1

    return f"{base_prefix}{next_num:0{padding}d}"

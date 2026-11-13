import sys, os
sys.path.insert(0, 'src')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings.dev')
import django
django.setup()
import re
from apps.accounts.models import User
from apps.audit.views import (
    _get_auditor_org,
    audit_contract_changes_view,
    auditor_dashboard_view,
    audit_vendors_view,
    audit_approvals_view,
    audit_po_changes_view,
    audit_invoice_exceptions_view,
    audit_security_events_view,
    audit_sourcing_activity_view,
)
from django.test import RequestFactory

auditors = [
    User.objects.filter(role__code='AUDITOR').first(),
    User.objects.filter(email='brucebanner70707@gmail.com').first()
]

for auditor in auditors:
    if not auditor:
        continue
    print('\n============================================================')
    print('Testing with auditor:', auditor.email, 'Role:', auditor.role.name, 'Org:', auditor.organization)

    rf = RequestFactory()

    # 1. Test _get_auditor_org
    req = rf.get('/audit/contract-changes/')
    req.user = auditor
    org = _get_auditor_org(req)
    print('_get_auditor_org(req):', org)
    assert org is None, 'Auditor must have global read-only visibility (org is None)'

# 2. Test Contract Changes View
response = audit_contract_changes_view(req)
assert response.status_code == 200
content = response.content.decode('utf-8')

active_match = re.search(r'ACTIVE CONTRACTS.*?font-heading mt-1">([^<]+)<', content, re.DOTALL)
val_match = re.search(r'TOTAL CONTRACT VALUE.*?font-heading mt-1">([^<]+)<', content, re.DOTALL)
ob_match = re.search(r'OVERDUE OBLIGATIONS.*?font-heading mt-1">([^<]+)<', content, re.DOTALL)

print('Contract Compliance KPIs:')
print('  Active Contracts:', active_match.group(1).strip() if active_match else 'MISSING')
print('  Total Contract Value:', val_match.group(1).strip() if val_match else 'MISSING')
print('  Overdue Obligations:', ob_match.group(1).strip() if ob_match else 'MISSING')

# 3. Test Sourcing Activity View
req_src = rf.get('/audit/sourcing-activity/')
req_src.user = auditor
res_src = audit_sourcing_activity_view(req_src)
assert res_src.status_code == 200
content_src = res_src.content.decode('utf-8')

events_match = re.search(r'TOTAL SOURCING EVENTS.*?font-heading mt-1">([^<]+)<', content_src, re.DOTALL)
active_bids_match = re.search(r'ACTIVE BIDDING WINDOWS.*?font-heading mt-1">([^<]+)<', content_src, re.DOTALL)
awarded_match = re.search(r'AWARDED EVENTS.*?font-heading mt-1">([^<]+)<', content_src, re.DOTALL)
sealed_bids_match = re.search(r'TOTAL SEALED BIDS.*?font-heading mt-1">([^<]+)<', content_src, re.DOTALL)

print('Sourcing Activity KPIs:')
print('  Total Sourcing Events:', events_match.group(1).strip() if events_match else 'MISSING')
print('  Active Bidding Windows:', active_bids_match.group(1).strip() if active_bids_match else 'MISSING')
print('  Awarded Events:', awarded_match.group(1).strip() if awarded_match else 'MISSING')
print('  Total Sealed Bids:', sealed_bids_match.group(1).strip() if sealed_bids_match else 'MISSING')

# 4. Test Vendor Compliance View
req_v = rf.get('/audit/vendors/')
req_v.user = auditor
res_v = audit_vendors_view(req_v)
assert res_v.status_code == 200
content_v = res_v.content.decode('utf-8')
comp_rate = re.search(r'COMPLIANCE RATE.*?font-heading mt-1">([^<]+)<', content_v, re.DOTALL)
active_v = re.search(r'ACTIVE VENDORS.*?font-heading mt-1">([^<]+)<', content_v, re.DOTALL)
print('Vendor Compliance KPIs:')
print('  Compliance Rate:', comp_rate.group(1).strip() if comp_rate else 'MISSING')
print('  Active Vendors:', active_v.group(1).strip() if active_v else 'MISSING')

# 5. Test Approvals View
req_app = rf.get('/audit/approvals/')
req_app.user = auditor
res_app = audit_approvals_view(req_app)
assert res_app.status_code == 200
content_app = res_app.content.decode('utf-8')
tot_sig = re.search(r'TOTAL SIGNATURES.*?font-heading mt-1">([^<]+)<', content_app, re.DOTALL)
print('Approvals KPIs:')
print('  Total Signatures:', tot_sig.group(1).strip() if tot_sig else 'MISSING')

# 6. Test PO Changes View
req_po = rf.get('/audit/po-changes/')
req_po.user = auditor
res_po = audit_po_changes_view(req_po)
assert res_po.status_code == 200
content_po = res_po.content.decode('utf-8')
tot_pos = re.search(r'TOTAL PURCHASE ORDERS.*?font-heading mt-1">([^<]+)<', content_po, re.DOTALL)
committed_v = re.search(r'TOTAL COMMITTED VALUE.*?font-heading mt-1">([^<]+)<', content_po, re.DOTALL)
print('PO Changes KPIs:')
print('  Total POs:', tot_pos.group(1).strip() if tot_pos else 'MISSING')
print('  Committed Value:', committed_v.group(1).strip() if committed_v else 'MISSING')

# 7. Test Invoice Exceptions View
req_inv = rf.get('/audit/invoice-exceptions/')
req_inv.user = auditor
res_inv = audit_invoice_exceptions_view(req_inv)
assert res_inv.status_code == 200
content_inv = res_inv.content.decode('utf-8')
open_exc = re.search(r'OPEN EXCEPTIONS.*?font-heading mt-1">([^<]+)<', content_inv, re.DOTALL)
var_exp = re.search(r'TOTAL VARIANCE EXPOSURE.*?font-heading mt-1">([^<]+)<', content_inv, re.DOTALL)
print('Invoice Exceptions KPIs:')
print('  Open Exceptions:', open_exc.group(1).strip() if open_exc else 'MISSING')
print('  Variance Exposure:', var_exp.group(1).strip() if var_exp else 'MISSING')

# 8. Test Security Events View
req_sec = rf.get('/audit/security-events/')
req_sec.user = auditor
res_sec = audit_security_events_view(req_sec)
assert res_sec.status_code == 200
content_sec = res_sec.content.decode('utf-8')
tot_auth = re.search(r'TOTAL AUTH EVENTS.*?font-heading mt-1">([^<]+)<', content_sec, re.DOTALL)
print('Security Events KPIs:')
print('  Total Auth Events:', tot_auth.group(1).strip() if tot_auth else 'MISSING')

# 9. Test Auditor Dashboard View
req_dash = rf.get('/audit/dashboard/')
req_dash.user = auditor
res_dash = auditor_dashboard_view(req_dash)
assert res_dash.status_code == 200
content_dash = res_dash.content.decode('utf-8')
print('Auditor Dashboard loaded successfully, status 200')
print('ALL AUDITOR MODULE CHECKS PASSED!')

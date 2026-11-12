from django.db import migrations


def update_pr_step2_to_proc_mgr(apps, schema_editor):
    ApprovalPolicy = apps.get_model("approvals", "ApprovalPolicy")
    Role = apps.get_model("accounts", "Role")

    proc_mgr_role = Role.objects.filter(code="PROC_MGR").first()
    if not proc_mgr_role:
        return

    pr_policies = ApprovalPolicy.objects.filter(module="PR")
    for policy in pr_policies:
        step2 = policy.steps.filter(step_number=2).first()
        if step2:
            step2.approver_role = proc_mgr_role
            step2.description = "Procurement Manager Approval"
            step2.save(update_fields=["approver_role", "description"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("approvals", "0006_approvalpolicy_all_cost_centers_and_more"),
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(update_pr_step2_to_proc_mgr, noop),
    ]


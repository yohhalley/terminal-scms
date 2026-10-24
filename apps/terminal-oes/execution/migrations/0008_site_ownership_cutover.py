import uuid

import django.db.models.deletion
from django.db import migrations, models


ENTERPRISE_ID = uuid.uuid5(uuid.NAMESPACE_URL, "scms:enterprise:terminal-velocity")


def attach_legacy_rows(apps, schema_editor):
    Enterprise = apps.get_model("execution", "EnterpriseProjection")
    Site = apps.get_model("execution", "SiteProjection")
    Order = apps.get_model("execution", "ExecutionOrder")
    Equipment = apps.get_model("execution", "EquipmentState")
    identities = set(
        Order.objects.filter(site__isnull=True).values_list("terminal_id", "terminal_code")
    )
    identities.update(
        Equipment.objects.filter(site__isnull=True).values_list("terminal_id", "terminal_code")
    )
    if not identities:
        return

    enterprise, _ = Enterprise.objects.get_or_create(
        code="terminal-velocity",
        defaults={
            "source_id": ENTERPRISE_ID,
            "name": "Terminal Velocity",
            "source_payload": {"migration": "legacy OES ownership cutover"},
        },
    )
    for location_id, terminal_code in identities:
        code = terminal_code or f"legacy-site-{location_id}"
        site, _ = Site.objects.get_or_create(
            enterprise=enterprise,
            code=code,
            defaults={
                "source_id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"scms:legacy-site:{enterprise.code}:{location_id}:{code}",
                ),
                "source_location_id": location_id,
                "name": code,
                "source_payload": {"migration": "legacy OES ownership cutover"},
            },
        )
        Order.objects.filter(
            site__isnull=True, terminal_id=location_id, terminal_code=terminal_code,
        ).update(site=site)
        Equipment.objects.filter(
            site__isnull=True, terminal_id=location_id, terminal_code=terminal_code,
        ).update(site=site)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [("execution", "0007_sparkplug_runtime")]

    operations = [
        migrations.RunPython(attach_legacy_rows, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="executionorder",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="orders",
                to="execution.siteprojection",
            ),
        ),
        migrations.AlterField(
            model_name="equipmentstate",
            name="site",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="equipment",
                to="execution.siteprojection",
            ),
        ),
        migrations.RemoveField(model_name="executionorder", name="terminal_id"),
        migrations.RemoveField(model_name="executionorder", name="terminal_code"),
        migrations.RemoveField(model_name="equipmentstate", name="terminal_id"),
        migrations.RemoveField(model_name="equipmentstate", name="terminal_code"),
    ]

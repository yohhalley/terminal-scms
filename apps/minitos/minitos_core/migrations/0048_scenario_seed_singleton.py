# TCL-012 follow-up (BE review nit N-2 on #38, 15 Nov 2026): ScenarioSeed holds
# at most one row. Adds ``key`` (always 'current'), keeps only the newest row
# if an older database somehow holds several (the one ``scenario_seed()``
# already reads: highest pk), then adds the unique + check constraints.
# Depends on the single minitos_core leaf on main (0047_tcl012_scenario_seed,
# which itself depends on both 0046 and 0047_visit_status_revision).

from django.db import migrations, models


def keep_newest_seed(apps, schema_editor):
    ScenarioSeed = apps.get_model('minitos_core', 'ScenarioSeed')
    newest = ScenarioSeed.objects.order_by('-pk').first()
    if newest is not None:
        ScenarioSeed.objects.exclude(pk=newest.pk).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0047_tcl012_scenario_seed'),
    ]

    operations = [
        migrations.AddField(
            model_name='scenarioseed',
            name='key',
            field=models.CharField(default='current', editable=False, max_length=16),
        ),
        migrations.RunPython(keep_newest_seed, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name='scenarioseed',
            constraint=models.UniqueConstraint(fields=('key',), name='scenario_seed_singleton'),
        ),
        migrations.AddConstraint(
            model_name='scenarioseed',
            constraint=models.CheckConstraint(
                check=models.Q(('key', 'current')), name='scenario_seed_key_is_singleton',
            ),
        ),
    ]

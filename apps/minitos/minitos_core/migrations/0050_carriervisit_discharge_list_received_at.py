# Demo screens (D-031, TCL-demo-screens): CarrierVisit.discharge_list_received_at,
# the time the terminal received the vessel call's discharge list. Nullable, no
# data migration: existing visits show "—" until a value is set (the import_v1
# seed sets one). Depends on the single minitos_core leaf on main
# (0049_tcl010c_lane_code_rename, #53).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0049_tcl010c_lane_code_rename'),
    ]

    operations = [
        migrations.AddField(
            model_name='carriervisit',
            name='discharge_list_received_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]

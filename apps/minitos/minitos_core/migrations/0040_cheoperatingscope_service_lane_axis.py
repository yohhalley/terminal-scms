from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0039_transfer_interface_road_graph'),
    ]

    operations = [
        migrations.AddField(
            model_name='cheoperatingscope',
            name='service_lane_axis',
            field=models.ForeignKey(
                blank=True,
                help_text='Road lane used by the scoped equipment while serving this area.',
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='service_lane_scopes',
                to='minitos_core.linearaxis',
            ),
        ),
    ]

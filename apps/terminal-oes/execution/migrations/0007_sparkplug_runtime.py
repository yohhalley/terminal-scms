import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("execution", "0006_simulation_session"),
    ]

    operations = [
        migrations.AddField(
            model_name="simulationsession",
            name="last_node_birth_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="simulationsession",
            name="last_node_death_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="simulationsession",
            name="node_connected",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="simulationsession",
            name="sequence_gap_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="simulationsession",
            name="sparkplug_bdseq",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="simulationsession",
            name="sparkplug_last_sequence",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="data_valid",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="device_connected",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="last_birth_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="last_death_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="simulation_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="equipment_states",
                to="execution.simulationsession",
            ),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="sparkplug_bdseq",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="sparkplug_device_id",
            field=models.CharField(blank=True, max_length=160),
        ),
        migrations.AddField(
            model_name="equipmentstate",
            name="sparkplug_last_sequence",
            field=models.PositiveSmallIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentcommand",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="equipmentcommand",
            name="simulation_session",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="commands",
                to="execution.simulationsession",
            ),
        ),
        migrations.AddField(
            model_name="outboxevent",
            name="payload_bytes",
            field=models.BinaryField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="CommandResultReceipt",
            fields=[
                ("result_id", models.UUIDField(editable=False, primary_key=True, serialize=False)),
                ("event", models.CharField(max_length=40)),
                ("raw_metrics", models.JSONField(default=dict)),
                ("received_at", models.DateTimeField(auto_now_add=True)),
                (
                    "command",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="result_receipts",
                        to="execution.equipmentcommand",
                    ),
                ),
            ],
            options={"ordering": ["received_at"]},
        ),
    ]

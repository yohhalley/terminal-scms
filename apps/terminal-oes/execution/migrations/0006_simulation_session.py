import uuid

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("execution", "0005_multisite_execution_identity"),
    ]

    operations = [
        migrations.CreateModel(
            name="SimulationSession",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("configuration_revision", models.PositiveIntegerField()),
                ("configuration_checksum", models.CharField(max_length=64)),
                ("bootstrap_payload", models.JSONField(default=dict)),
                ("desired_state", models.CharField(choices=[("running", "Running"), ("stopped", "Stopped")], default="running", max_length=12)),
                ("actual_state", models.CharField(choices=[("pending", "Pending"), ("claimed", "Claimed"), ("starting", "Starting"), ("running", "Running"), ("stopping", "Stopping"), ("stopped", "Stopped"), ("failed", "Failed")], default="pending", max_length=12)),
                ("simulator_version", models.CharField(default="local", max_length=80)),
                ("time_scale", models.FloatField(default=1.0, validators=[django.core.validators.MinValueValidator(0.01), django.core.validators.MaxValueValidator(100.0)])),
                ("sparkplug_group_id", models.CharField(max_length=120)),
                ("sparkplug_node_id", models.CharField(max_length=160)),
                ("runner_identity", models.CharField(blank=True, max_length=160)),
                ("runner_status", models.CharField(choices=[("unclaimed", "Unclaimed"), ("claimed", "Claimed"), ("starting", "Starting"), ("running", "Running"), ("stopped", "Stopped"), ("failed", "Failed")], default="unclaimed", max_length=12)),
                ("token_hash", models.CharField(blank=True, max_length=64)),
                ("token_expires_at", models.DateTimeField(blank=True, null=True)),
                ("claimed_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("stopped_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("failure_reason", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("site", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="simulation_sessions", to="execution.siteprojection")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="simulationsession",
            constraint=models.UniqueConstraint(
                condition=models.Q(actual_state__in=["pending", "claimed", "starting", "running", "stopping"]),
                fields=("site",),
                name="one_active_simulation_per_site",
            ),
        ),
    ]

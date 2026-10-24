import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('execution', '0003_equipment_telemetry_and_operation_coordinates'),
    ]

    operations = [
        migrations.CreateModel(
            name='EnterpriseProjection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_id', models.UUIDField(unique=True)),
                ('code', models.SlugField(max_length=80, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(default=True)),
                ('source_payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='SiteProjection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_id', models.UUIDField(unique=True)),
                ('source_location_id', models.PositiveBigIntegerField(db_index=True)),
                ('code', models.SlugField(max_length=80)),
                ('name', models.CharField(max_length=120)),
                ('lifecycle_status', models.CharField(default='draft', max_length=12)),
                ('projection_status', models.CharField(choices=[('draft', 'Draft'), ('syncing', 'Synchronizing'), ('ready', 'Ready'), ('error', 'Error'), ('archived', 'Archived')], default='draft', max_length=12)),
                ('source_revision', models.PositiveIntegerField(default=0)),
                ('snapshot_schema', models.CharField(blank=True, max_length=100)),
                ('snapshot_checksum', models.CharField(blank=True, max_length=64)),
                ('snapshot_path', models.CharField(blank=True, max_length=300)),
                ('snapshot_payload', models.JSONField(default=dict)),
                ('simulation_enabled', models.BooleanField(default=True)),
                ('last_reconciled_at', models.DateTimeField(blank=True, null=True)),
                ('last_error', models.TextField(blank=True)),
                ('source_payload', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enterprise', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sites', to='execution.enterpriseprojection')),
            ],
            options={
                'ordering': ['enterprise__name', 'name'],
                'constraints': [models.UniqueConstraint(fields=('enterprise', 'code'), name='unique_projected_site_code')],
            },
        ),
        migrations.AddField(
            model_name='executionorder',
            name='site',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='orders', to='execution.siteprojection'),
        ),
        migrations.AddField(
            model_name='equipmentstate',
            name='site',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='equipment', to='execution.siteprojection'),
        ),
    ]

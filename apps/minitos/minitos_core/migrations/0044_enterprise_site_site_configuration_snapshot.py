import uuid

import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone
from django.utils.text import slugify


def seed_enterprise_and_sites(apps, schema_editor):
    Enterprise = apps.get_model('minitos_core', 'Enterprise')
    Site = apps.get_model('minitos_core', 'Site')
    Location = apps.get_model('minitos_core', 'Location')
    IntegrationOutbox = apps.get_model('minitos_core', 'IntegrationOutbox')

    enterprise, _ = Enterprise.objects.get_or_create(
        code='terminal-velocity',
        defaults={'name': 'Terminal Velocity', 'is_active': True},
    )
    for location in Location.objects.filter(location_type__in=['TL', 'ID']).order_by('pk'):
        if Site.objects.filter(location_id=location.pk).exists():
            continue
        base = slugify(location.name)[:70] or f'site-{location.pk}'
        code = base
        suffix = 2
        while Site.objects.filter(enterprise=enterprise, code=code).exists():
            code = f'{base[:74]}-{suffix}'
            suffix += 1
        site = Site.objects.create(
            enterprise=enterprise,
            location=location,
            code=code,
        )
        event_id = uuid.uuid4()
        schema = 'scms.site.created.v1'
        IntegrationOutbox.objects.create(
            event_id=event_id,
            topic=f'scms/v2/{enterprise.code}/{site.code}/tos/site/created',
            schema=schema,
            payload={
                'schema': schema,
                'event_id': str(event_id),
                'occurred_at': timezone.now().isoformat(),
                'enterprise': {
                    'id': str(enterprise.pk),
                    'code': enterprise.code,
                    'name': enterprise.name,
                },
                'site': {
                    'id': str(site.pk),
                    'code': site.code,
                    'name': location.name,
                    'source_location_id': location.pk,
                    'location_type': location.location_type,
                },
                'data': {
                    'lifecycle_status': 'draft',
                    'configuration_revision': 0,
                    'simulation_enabled': True,
                },
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ('minitos_core', '0043_integrationinbox_integrationoutbox'),
    ]

    operations = [
        migrations.CreateModel(
            name='Enterprise',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.SlugField(max_length=80, unique=True)),
                ('name', models.CharField(max_length=120)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={'ordering': ['name']},
        ),
        migrations.CreateModel(
            name='Site',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.SlugField(max_length=80)),
                ('lifecycle_status', models.CharField(choices=[('draft', 'Draft'), ('published', 'Published'), ('archived', 'Archived')], default='draft', max_length=12)),
                ('configuration_revision', models.PositiveIntegerField(default=0)),
                ('simulation_enabled', models.BooleanField(default=True)),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('enterprise', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='sites', to='minitos_core.enterprise')),
                ('location', models.OneToOneField(on_delete=django.db.models.deletion.RESTRICT, related_name='site', to='minitos_core.location')),
            ],
            options={
                'ordering': ['enterprise__name', 'location__name'],
                'constraints': [models.UniqueConstraint(fields=('enterprise', 'code'), name='unique_site_code_per_enterprise')],
            },
        ),
        migrations.CreateModel(
            name='SiteConfigurationSnapshot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('revision', models.PositiveIntegerField()),
                ('schema', models.CharField(default='scms.site.execution-snapshot.v1', max_length=100)),
                ('checksum', models.CharField(max_length=64)),
                ('payload', models.JSONField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('layout_version', models.ForeignKey(on_delete=django.db.models.deletion.RESTRICT, related_name='site_configuration_snapshots', to='minitos_core.layoutversion')),
                ('site', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='configuration_snapshots', to='minitos_core.site')),
            ],
            options={
                'ordering': ['site_id', '-revision'],
                'constraints': [models.UniqueConstraint(fields=('site', 'revision'), name='unique_site_configuration_revision')],
            },
        ),
        migrations.RunPython(seed_enterprise_and_sites, migrations.RunPython.noop),
    ]

from django.db import migrations, models


PRESET_CODES = ('ASC_BLOCK', 'RTG_BLOCK')


def set_default_spacing(apps, schema_editor):
    LayoutPreset = apps.get_model('minitos_core', 'LayoutPreset')
    for preset in LayoutPreset.objects.filter(code__in=PRESET_CODES, is_system=True):
        definition = dict(preset.definition or {})
        editor_defaults = dict(definition.get('editor_defaults') or {})
        editor_defaults.update({'bay_spacing_m': 0.4, 'row_spacing_m': 0.4})
        definition['editor_defaults'] = editor_defaults
        preset.definition = definition
        preset.save(update_fields=['definition'])


def remove_default_spacing(apps, schema_editor):
    LayoutPreset = apps.get_model('minitos_core', 'LayoutPreset')
    for preset in LayoutPreset.objects.filter(code__in=PRESET_CODES, is_system=True):
        definition = dict(preset.definition or {})
        editor_defaults = dict(definition.get('editor_defaults') or {})
        editor_defaults.pop('bay_spacing_m', None)
        editor_defaults.pop('row_spacing_m', None)
        definition['editor_defaults'] = editor_defaults
        preset.definition = definition
        preset.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('minitos_core', '0040_cheoperatingscope_service_lane_axis'),
    ]

    operations = [
        migrations.AlterField(
            model_name='storagegrid',
            name='bay_spacing_m',
            field=models.FloatField(default=0.4, help_text='Spacing between bays in meters'),
        ),
        migrations.AlterField(
            model_name='storagegrid',
            name='row_spacing_m',
            field=models.FloatField(default=0.4, help_text='Spacing between rows in meters'),
        ),
        migrations.RunPython(set_default_spacing, remove_default_spacing),
    ]

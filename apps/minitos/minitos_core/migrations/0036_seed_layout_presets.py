from django.db import migrations


PRESETS = [
    {
        'code': 'ASC_BLOCK',
        'name': 'Automated stacking block',
        'root_asset_kind': 'yard_block',
        'description': 'Storage grid with crane rail and ground-buffer transfer positions.',
        'definition': {
            'composition': 'asc_block',
            'editor_defaults': {
                'grid_kind': 'automated', 'max_bays': 30, 'max_rows': 10,
                'max_tier': 6,
            },
        },
    },
    {
        'code': 'RTG_BLOCK',
        'name': 'RTG stacking block',
        'root_asset_kind': 'yard_block',
        'description': 'Storage grid with a vehicle transfer lane along the block.',
        'definition': {
            'composition': 'rtg_block',
            'editor_defaults': {
                'grid_kind': 'manual', 'max_bays': 30, 'max_rows': 7,
                'max_tier': 5,
            },
        },
    },
    {
        'code': 'QUAY_BERTH',
        'name': 'Quay and berth',
        'root_asset_kind': 'quay',
        'description': 'Quay polygon with quay-face axis, berth segment, and bollards.',
        'definition': {
            'composition': 'quay',
            'editor_defaults': {
                'bollard_count': 15, 'default_bollard_spacing_m': 20.0,
            },
        },
    },
    {
        'code': 'ROAD_SPINE',
        'name': 'Road spine',
        'root_asset_kind': 'road',
        'description': 'Road corridor with a bidirectional routing centerline.',
        'definition': {'composition': 'road', 'editor_defaults': {}},
    },
    {
        'code': 'RAIL_TERMINAL',
        'name': 'Rail working area',
        'root_asset_kind': 'rail_area',
        'description': 'Rail working area with an initial bidirectional rail track.',
        'definition': {'composition': 'rail', 'editor_defaults': {}},
    },
]


def seed_presets(apps, schema_editor):
    LayoutPreset = apps.get_model('minitos_core', 'LayoutPreset')
    for values in PRESETS:
        LayoutPreset.objects.update_or_create(
            code=values['code'], version=1,
            defaults={**values, 'version': 1, 'is_system': True, 'is_active': True},
        )


def unseed_presets(apps, schema_editor):
    LayoutPreset = apps.get_model('minitos_core', 'LayoutPreset')
    LayoutPreset.objects.filter(
        code__in=[values['code'] for values in PRESETS], version=1, is_system=True,
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('minitos_core', '0035_accesspolicy_berthsegment_cheoperatingscope_and_more')]
    operations = [migrations.RunPython(seed_presets, unseed_presets)]

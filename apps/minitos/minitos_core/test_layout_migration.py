from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class LayoutArchitectureMigrationTests(TransactionTestCase):
    migrate_from = [('minitos_core', '0034_migrate_che_component_data')]
    migrate_to = [('minitos_core', '0041_storage_grid_default_spacing')]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        Location = old_apps.get_model('minitos_core', 'Location')
        EquipmentType = old_apps.get_model('minitos_core', 'EquipmentType')
        Block = old_apps.get_model('minitos_core', 'Block')
        Area = old_apps.get_model('minitos_core', 'Area')
        Che = old_apps.get_model('minitos_core', 'Che')

        location = Location.objects.create(
            name='Legacy Layout Terminal', location_type='TL',
            latitude=41.35, longitude=2.17,
            map_origin_latitude=41.35, map_origin_longitude=2.17,
        )
        equipment_type = EquipmentType.objects.create(name='Legacy TT', equipment_type='TT')
        block = Block.objects.create(
            location=location, name='A01', max_bays=2, max_rows=2,
            max_tier=2, latitude=41.35, longitude=2.17,
        )
        Area.objects.create(
            location=location, block=block, name='A01 shape',
            area_type='yard block', area=100,
            coordinates=[[2.17, 41.35], [2.171, 41.35], [2.171, 41.351]],
        )
        self.location_pk = location.pk
        self.che_pk = Che.objects.create(
            name='LEGACY-TT-01', equipment_type=equipment_type,
            location=location, current_block=block,
        ).pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_layout_migration_retires_legacy_graph_and_preserves_terminal_fleet(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        Location = apps.get_model('minitos_core', 'Location')
        Che = apps.get_model('minitos_core', 'Che')
        LayoutPreset = apps.get_model('minitos_core', 'LayoutPreset')
        StorageGrid = apps.get_model('minitos_core', 'StorageGrid')

        self.assertTrue(Location.objects.filter(pk=self.location_pk).exists())
        che = Che.objects.get(pk=self.che_pk)
        self.assertIsNone(che.current_asset_id)
        self.assertEqual(LayoutPreset.objects.filter(is_system=True).count(), 5)
        for code in ['ASC_BLOCK', 'RTG_BLOCK']:
            defaults = LayoutPreset.objects.get(code=code).definition['editor_defaults']
            self.assertEqual(defaults['bay_spacing_m'], 0.4)
            self.assertEqual(defaults['row_spacing_m'], 0.4)
        self.assertEqual(StorageGrid._meta.get_field('bay_spacing_m').default, 0.4)
        self.assertEqual(StorageGrid._meta.get_field('row_spacing_m').default, 0.4)
        for retired in ['Area', 'Block', 'Berth', 'Bollard', 'Road', 'RoutingNode', 'TerminalConnection']:
            with self.assertRaises(LookupError):
                apps.get_model('minitos_core', retired)

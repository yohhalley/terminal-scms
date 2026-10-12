from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CheComponentDataMigrationTests(TransactionTestCase):
    migrate_from = [('minitos_core', '0032_businessprocess_processversion_processhierarchy_and_more')]
    migrate_to = [('minitos_core', '0034_migrate_che_component_data')]

    def setUp(self):
        super().setUp()
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_from)
        old_apps = self.executor.loader.project_state(self.migrate_from).apps
        Location = old_apps.get_model('minitos_core', 'Location')
        EquipmentType = old_apps.get_model('minitos_core', 'EquipmentType')
        Che = old_apps.get_model('minitos_core', 'Che')
        EquipmentPart = old_apps.get_model('minitos_core', 'EquipmentPart')

        location = Location.objects.create(name='Migration Terminal', location_type='TL')
        equipment_type = EquipmentType.objects.create(
            name='Migration SC',
            equipment_type='SC',
            energy_source='Diesel',
            driving_speed_empty_kmh=18.0,
            driving_speed_full_load_kmh=14.4,
            lifting_speed_empty_m_per_min=30.0,
            lifting_speed_full_load_m_per_min=24.0,
            turning_radius_m=6.5,
            engine_type='Legacy engine',
            power_rating_kw=225.0,
            fuel_tank_capacity=420.0,
        )
        EquipmentPart.objects.create(
            name='Legacy hydraulic system', equipment_type=equipment_type, running_hours=320
        )
        self.che_pk = Che.objects.create(
            name='MIGRATION-SC-01',
            equipment_type=equipment_type,
            location=location,
            powered_on=True,
            working=False,
            is_available=True,
            fuel_level=67.25,
            running_hours=1250,
            running_hours_since_last_maintenance=75,
            twistlock_closed=True,
        ).pk
        self.equipment_type_pk = equipment_type.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def test_forward_and_reverse_migrations_preserve_legacy_values(self):
        self.executor = MigrationExecutor(connection)
        self.executor.migrate(self.migrate_to)
        apps = self.executor.loader.project_state(self.migrate_to).apps
        Che = apps.get_model('minitos_core', 'Che')
        CurrentSignalValue = apps.get_model('minitos_core', 'CurrentSignalValue')
        Engine = apps.get_model('minitos_core', 'Engine')
        EquipmentTypeComponent = apps.get_model('minitos_core', 'EquipmentTypeComponent')

        che = Che.objects.get(pk=self.che_pk)
        roles = set(che.components.values_list('role', flat=True))
        self.assertTrue(
            {'equipment', 'drive', 'hoist', 'spreader', 'engine', 'fuel_tank'}.issubset(roles)
        )
        self.assertTrue(any(role.startswith('legacy_part_') for role in roles))
        engine = Engine.objects.get(component__che=che)
        self.assertEqual(engine.engine_type, 'Legacy engine')
        self.assertEqual(float(engine.running_hours), 1250.0)
        self.assertEqual(float(engine.hours_since_maintenance), 75.0)
        fuel = CurrentSignalValue.objects.get(
            binding__component__che=che,
            binding__signal_definition__code='energystoragesystem.energy.level',
        )
        self.assertEqual(fuel.value, 67.25)
        self.assertTrue(
            EquipmentTypeComponent.objects.filter(
                equipment_type_id=self.equipment_type_pk,
                default_configuration__engine_type='Legacy engine',
            ).exists()
        )

        reverse_executor = MigrationExecutor(connection)
        reverse_executor.migrate(self.migrate_from)
        old_apps = reverse_executor.loader.project_state(self.migrate_from).apps
        old_che = old_apps.get_model('minitos_core', 'Che').objects.get(pk=self.che_pk)
        old_type = old_apps.get_model('minitos_core', 'EquipmentType').objects.get(
            pk=self.equipment_type_pk
        )
        EquipmentPart = old_apps.get_model('minitos_core', 'EquipmentPart')
        self.assertEqual(old_che.fuel_level, 67.25)
        self.assertEqual(old_che.running_hours, 1250)
        self.assertTrue(old_che.twistlock_closed)
        self.assertEqual(old_type.engine_type, 'Legacy engine')
        self.assertEqual(old_type.fuel_tank_capacity, 420.0)
        self.assertTrue(EquipmentPart.objects.filter(name='Legacy hydraulic system').exists())

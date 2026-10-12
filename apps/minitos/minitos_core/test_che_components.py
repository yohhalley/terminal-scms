from datetime import timedelta

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import (
    Battery,
    Che,
    ComponentType,
    CurrentSignalValue,
    Engine,
    EquipmentType,
    Location,
    MotionAxis,
    OperationalStateInterval,
    SignalDefinition,
)


class CheComponentArchitectureTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username='component-user', password='password')
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.location = Location.objects.create(
            name='Component Terminal',
            location_type=Location.TL,
            latitude=41.35,
            longitude=2.17,
        )
        self.other_location = Location.objects.create(
            name='Other Component Terminal',
            location_type=Location.TL,
            latitude=40.41,
            longitude=-3.70,
        )

    def _diesel_sc(self, name='Component SC'):
        return EquipmentType.objects.create(
            name=name,
            equipment_type=EquipmentType.SC,
            energy_source=EquipmentType.Diesel,
            driving_speed_empty_kmh=21.6,
            driving_speed_full_load_kmh=18.0,
            lifting_speed_empty_m_per_min=30.0,
            lifting_speed_full_load_m_per_min=24.0,
            turning_radius_m=7.5,
            engine_type='Diesel test engine',
            power_rating_kw=250.0,
            fuel_tank_capacity=450.0,
        )

    def test_equipment_type_and_che_materialize_typed_components(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(
            name='SC-COMPONENT-01', equipment_type=equipment_type, location=self.location,
            powered_on=True, fuel_level=73.5, twistlock_closed=True,
        )

        self.assertEqual(
            set(equipment_type.component_profiles.filter(is_active=True).values_list('role', flat=True)),
            {'equipment', 'drive', 'hoist', 'spreader', 'engine', 'fuel_tank'},
        )
        self.assertEqual(
            set(che.components.filter(is_active=True).values_list('role', flat=True)),
            {'equipment', 'drive', 'hoist', 'spreader', 'engine', 'fuel_tank'},
        )
        engine = Engine.objects.get(component__che=che)
        drive = MotionAxis.objects.get(component__che=che, component__role='drive')
        fuel_tank = che.components.get(role='fuel_tank')
        self.assertEqual(engine.engine_type, 'Diesel test engine')
        self.assertEqual(engine.rated_power_kw, 250.0)
        self.assertAlmostEqual(drive.maximum_speed_empty_m_s, 6.0)
        self.assertEqual(engine.component.component_type.code, ComponentType.POWER_SOURCE)
        self.assertEqual(fuel_tank.component_type.code, ComponentType.ENERGY_STORE)
        self.assertEqual(
            SignalDefinition.objects.get(code='energysupply.running.status').standard_version,
            '2026.019',
        )
        self.assertEqual(che.fuel_level, 73.5)
        self.assertTrue(che.twistlock_closed)
        self.assertEqual(che.state_intervals.get(ended_at__isnull=True).state, OperationalStateInterval.IDLE)

    def test_hybrid_mobile_equipment_has_engine_battery_and_both_energy_stores(self):
        equipment_type = EquipmentType.objects.create(
            name='Hybrid TT',
            equipment_type=EquipmentType.TT,
            energy_source=EquipmentType.Hybrid,
            power_rating_kw=300,
            fuel_tank_capacity=350,
        )
        che = Che.objects.create(name='HYBRID-TT-01', equipment_type=equipment_type, location=self.location)

        roles = set(che.components.values_list('role', flat=True))
        self.assertTrue({'engine', 'electric_power_source', 'fuel_tank', 'battery'}.issubset(roles))
        self.assertTrue(Engine.objects.filter(component__che=che).exists())
        self.assertTrue(Battery.objects.filter(component__che=che).exists())

    def test_legacy_che_api_updates_component_signals_and_state_intervals(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(
            name='SC-API-01', equipment_type=equipment_type, location=self.location,
            powered_on=True, is_available=True,
        )
        url = reverse('minitos_core:che-detail', args=[self.location.pk, che.pk])

        response = self.client.patch(url, {
            'working': True,
            'is_dispatched': True,
            'is_available': False,
            'fuel_level': 61.25,
            'twistlock_closed': True,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['fuel_level'], 61.25)
        self.assertTrue(response.data['twistlock_closed'])
        self.assertEqual(response.data['active_operational_state'], OperationalStateInterval.WORKING)
        self.assertTrue(any(component['role'] == 'engine' for component in response.data['components']))
        self.assertEqual(
            CurrentSignalValue.objects.get(
                binding__component__che=che,
                binding__signal_definition__code='energystoragesystem.energy.level',
            ).value,
            61.25,
        )
        self.assertEqual(che.state_intervals.filter(ended_at__isnull=False).count(), 1)

    def test_component_and_signal_routes_are_parent_scoped(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(
            name='SC-SCOPE-01', equipment_type=equipment_type,
            location=self.location, powered_on=True,
        )
        drive = che.components.get(role='drive')

        component_list = reverse(
            'minitos_core:che-component-list', args=[self.location.pk, che.pk]
        )
        wrong_component_list = reverse(
            'minitos_core:che-component-list', args=[self.other_location.pk, che.pk]
        )
        self.assertEqual(self.client.get(component_list).status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(wrong_component_list).data, [])

        binding_list = reverse(
            'minitos_core:component-signal-binding-list',
            args=[self.location.pk, che.pk, drive.pk],
        )
        definition = SignalDefinition.objects.get(code='drive.driving.speed')
        created = self.client.post(binding_list, {
            'signal_definition': definition.pk,
            'source_protocol': 'sparkplug',
            'source_metric': 'Drive/Speed',
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        current_url = reverse(
            'minitos_core:component-signal-binding-current-value',
            args=[self.location.pk, che.pk, drive.pk, created.data['id']],
        )
        current = self.client.put(current_url, {
            'value': 3.25,
            'source_timestamp': timezone.now(),
            'quality': 'good',
            'sequence': 12,
        }, format='json')
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)
        self.assertEqual(current.data['value'], 3.25)
        che.refresh_from_db()
        self.assertTrue(che.working)

        stopped = self.client.patch(current_url, {'value': 0.0}, format='json')
        self.assertEqual(stopped.status_code, status.HTTP_200_OK, stopped.data)
        che.refresh_from_db()
        self.assertFalse(che.working)

        invalid = self.client.patch(current_url, {'value': 'fast'}, format='json')
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('value', invalid.data)

    def test_component_details_update_and_cross_che_parent_is_rejected(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(
            name='SC-DETAIL-01', equipment_type=equipment_type, location=self.location
        )
        other_che = Che.objects.create(
            name='SC-DETAIL-02', equipment_type=equipment_type, location=self.location
        )
        drive = che.components.get(role='drive')
        other_root = other_che.components.get(role='equipment')
        detail_url = reverse(
            'minitos_core:che-component-detail',
            args=[self.location.pk, che.pk, drive.pk],
        )

        updated = self.client.patch(detail_url, {
            'motion_axis': {
                'maximum_speed_empty_m_s': 7.25,
                'motion_deadband_m_s': 0.05,
            },
        }, format='json')

        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        drive.motion_axis.refresh_from_db()
        self.assertEqual(drive.motion_axis.maximum_speed_empty_m_s, 7.25)
        self.assertEqual(drive.motion_axis.motion_deadband_m_s, 0.05)

        component_list = reverse(
            'minitos_core:che-component-list', args=[self.location.pk, che.pk]
        )
        rejected = self.client.post(component_list, {
            'component_type': ComponentType.objects.get(code=ComponentType.OTHER).pk,
            'parent': other_root.pk,
            'role': 'external_controller',
            'name': 'External controller',
        }, format='json')

        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('parent', rejected.data)

    def test_canonical_state_signal_cannot_bypass_che_invariants(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(
            name='SC-STATE-SIGNAL-01', equipment_type=equipment_type,
            location=self.location, powered_on=False,
        )
        root = che.components.get(role='equipment')
        binding = root.signal_bindings.get(
            signal_definition__code='che.working.status'
        )
        current_url = reverse(
            'minitos_core:component-signal-binding-current-value',
            args=[self.location.pk, che.pk, root.pk, binding.pk],
        )

        response = self.client.patch(current_url, {'value': True}, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        che.refresh_from_db()
        self.assertFalse(che.working)

    def test_equipment_type_component_api_exposes_profiles(self):
        equipment_type = self._diesel_sc()
        url = reverse('minitos_core:equipment-type-component-list', args=[equipment_type.pk])

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual({profile['role'] for profile in response.data}, {
            'equipment', 'drive', 'hoist', 'spreader', 'engine', 'fuel_tank'
        })

    def test_handling_cycle_rejects_an_end_before_its_start(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(name='SC-CYCLE-01', equipment_type=equipment_type, location=self.location)
        url = reverse('minitos_core:che-handling-cycle-list', args=[self.location.pk, che.pk])
        started_at = timezone.now()

        response = self.client.post(url, {
            'started_at': started_at,
            'ended_at': started_at - timedelta(seconds=1),
            'result': 'completed',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('ended_at', response.data)

    def test_signal_definition_rejects_wrong_component_type(self):
        equipment_type = self._diesel_sc()
        che = Che.objects.create(name='SC-SIGNAL-01', equipment_type=equipment_type, location=self.location)
        drive = che.components.get(role='drive')
        wrong_definition = SignalDefinition.objects.get(code='hoist.hoisting.speed')
        url = reverse(
            'minitos_core:component-signal-binding-list',
            args=[self.location.pk, che.pk, drive.pk],
        )

        response = self.client.post(url, {
            'signal_definition': wrong_definition.pk,
            'source_metric': 'Wrong/Metric',
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('signal_definition', response.data)

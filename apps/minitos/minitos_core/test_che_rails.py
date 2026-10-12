from minitos_core.models import BerthSegment, Che, CheOperatingScope, EquipmentType, LayoutAsset, LayoutVersion, LinearAxis, Location
from minitos_core.serializers import CheSerializer
from minitos_core.services.che_rails import align_che_pose, che_rail_motion, project_rail
from minitos_core.services.coordinates import offset_geographic
from minitos_core.services.layout import geographic_distance_m
from minitos_core.test_yard_grid import LayoutTestCase


class CheRailTests(LayoutTestCase):
    def setUp(self):
        super().setUp()
        self.asset = self.create_grid().layout_asset
        lat, lon = offset_geographic(40, -3, 0, 100)
        self.rail = LinearAxis.objects.create(asset=self.asset, code='A01.RAIL', name='Logical rail',
                                             axis_kind='crane_rail', points=[[-3, 40], [lon, lat]])

    def che(self, code='RMG', **overrides):
        equipment = EquipmentType.objects.create(name=f'{code} type {EquipmentType.objects.count()}', equipment_type=code, width_m=5)
        values = dict(name=f'{code}01', location=self.location, current_asset=self.asset,
                      equipment_type=equipment, latitude=40.0003, longitude=-2.9998, rotation=90)
        values.update(overrides)
        return Che.objects.create(**values)

    def test_api_create_aligns_fixed_crane_to_assigned_axis(self):
        equipment = EquipmentType.objects.create(name='ASC type', equipment_type='ARMG')
        response = self.client.post(f'/minitos_core/locations/{self.location.pk}/ches/',
                                    dict(name='ASC01', equipment_type=equipment.pk, current_asset=self.asset.pk,
                                         latitude=40.0003, longitude=-2.9998, rotation=90), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertAlmostEqual(response.data['longitude'], -3)
        self.assertAlmostEqual(response.data['rotation'], 0)
        self.assertEqual(response.data['rail_motion']['mode'], 'fixed')
        self.assertEqual(response.data['rail_motion']['assigned_axis_id'], self.rail.pk)

    def test_api_places_crane_by_rail_chainage_and_saves_gantry_envelope(self):
        equipment = EquipmentType.objects.create(name='Rail chainage ARMG', equipment_type='ARMG')
        response = self.client.post(
            f'/minitos_core/locations/{self.location.pk}/ches/',
            {
                'name': 'ASC-CHAINAGE-01',
                'equipment_type': equipment.pk,
                'current_asset': self.asset.pk,
                'rail_axis': self.rail.pk,
                'rail_position_m': 60,
                'rail_min_position_m': 10,
                'rail_max_position_m': 90,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        che = Che.objects.get(pk=response.data['pk'])
        scope = che.operating_scopes.get()
        self.assertEqual((scope.asset, scope.axis), (self.asset, self.rail))
        self.assertEqual((scope.min_chainage_m, scope.max_chainage_m), (10, 90))
        self.assertAlmostEqual(
            project_rail(self.rail.points, [che.longitude, che.latitude])['chainage_m'],
            60,
            delta=.02,
        )
        self.assertEqual(response.data['rail_assignment']['axis_id'], self.rail.pk)
        self.assertAlmostEqual(response.data['rail_assignment']['position_m'], 60, delta=.02)
        self.assertEqual(response.data['rail_assignment']['min_position_m'], 10)
        self.assertEqual(response.data['rail_assignment']['max_position_m'], 90)

        updated = self.client.patch(
            f'/minitos_core/locations/{self.location.pk}/ches/{che.pk}/',
            {
                'rail_axis': self.rail.pk,
                'rail_position_m': 75,
                'rail_min_position_m': 5,
                'rail_max_position_m': 95,
            },
            format='json',
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        che.refresh_from_db()
        self.assertEqual(che.operating_scopes.count(), 1)
        self.assertAlmostEqual(
            project_rail(self.rail.points, [che.longitude, che.latitude])['chainage_m'],
            75,
            delta=.02,
        )
        self.assertEqual(updated.data['rail_assignment']['min_position_m'], 5)
        self.assertEqual(updated.data['rail_assignment']['max_position_m'], 95)

    def test_api_rejects_position_outside_gantry_envelope_and_axis_length(self):
        equipment = EquipmentType.objects.create(name='Bounded RMG', equipment_type='RMG')
        url = f'/minitos_core/locations/{self.location.pk}/ches/'
        outside_envelope = self.client.post(
            url,
            {
                'name': 'RMG-OUTSIDE-ENVELOPE',
                'equipment_type': equipment.pk,
                'current_asset': self.asset.pk,
                'rail_axis': self.rail.pk,
                'rail_position_m': 5,
                'rail_min_position_m': 10,
                'rail_max_position_m': 90,
            },
            format='json',
        )
        self.assertEqual(outside_envelope.status_code, 400, outside_envelope.data)
        self.assertIn('rail_position_m', outside_envelope.data)

        beyond_rail = self.client.post(
            url,
            {
                'name': 'RMG-BEYOND-RAIL',
                'equipment_type': equipment.pk,
                'current_asset': self.asset.pk,
                'rail_axis': self.rail.pk,
                'rail_position_m': 60,
                'rail_min_position_m': 0,
                'rail_max_position_m': 120,
            },
            format='json',
        )
        self.assertEqual(beyond_rail.status_code, 400, beyond_rail.data)
        self.assertIn('rail_max_position_m', beyond_rail.data)
        self.assertFalse(Che.objects.filter(name__in=['RMG-OUTSIDE-ENVELOPE', 'RMG-BEYOND-RAIL']).exists())

    def test_scope_bounds_clamp_pose_and_expose_trimmed_polyline(self):
        che = self.che()
        CheOperatingScope.objects.create(che=che, axis=self.rail, min_chainage_m=20, max_chainage_m=40)
        response = self.client.patch(f'/minitos_core/locations/{self.location.pk}/ches/{che.pk}/',
                                     dict(latitude=40.001, longitude=-2.999, rotation=270), format='json')
        self.assertEqual(response.status_code, 200, response.data)
        che.refresh_from_db()
        self.assertAlmostEqual(project_rail(self.rail.points, [che.longitude, che.latitude])['chainage_m'], 40, delta=.02)
        points = response.data['rail_motion']['rails'][0]['points']
        self.assertAlmostEqual(geographic_distance_m(*points), 20, delta=.02)
        self.assertEqual(che.rotation, 0)

    def test_quay_face_is_sts_fallback_but_crane_rail_has_priority(self):
        quay = LayoutAsset.objects.create(layout_version=self.layout, code='QUAY', name='Quay', asset_kind='quay')
        face = LinearAxis.objects.create(asset=quay, code='FACE', name='Face', axis_kind='quay_face', points=[[-3, 40], [-2.999, 40]])
        che = self.che('STS', current_asset=quay)
        self.assertEqual(che_rail_motion(che)['assigned_axis_id'], face.pk)
        rail = LinearAxis.objects.create(asset=quay, code='RAIL', name='STS rail', axis_kind='crane_rail', points=[[-3, 40.0005], [-2.999, 40.0005]])
        self.assertEqual(che_rail_motion(che)['assigned_axis_id'], rail.pk)

    def test_sts_stays_perpendicular_and_faces_quay_water_side(self):
        quay = LayoutAsset.objects.create(layout_version=self.layout, code='WATER-QUAY', name='Water quay', asset_kind='quay')
        face = LinearAxis.objects.create(asset=quay, code='WATER-FACE', name='Quay face',
                                         axis_kind='quay_face', points=[[-3, 40], [-2.999, 40]])
        berth = BerthSegment.objects.create(quay_asset=quay, quay_face_axis=face, code='B1', name='Berth 1',
                                            start_chainage_m=0, end_chainage_m=100, water_side='left')
        rail_asset = LayoutAsset.objects.create(layout_version=self.layout, parent=quay, code='STS-TRACK',
                                                name='STS track', asset_kind='rail_area')
        rail = LinearAxis.objects.create(asset=rail_asset, code='WATER-RAIL', name='STS rail',
                                         axis_kind='crane_rail', points=[[-3, 40.0001], [-2.999, 40.0001]])
        che = self.che('STS', current_asset=rail_asset, latitude=40.0001, longitude=-2.9995)
        motion = che_rail_motion(che)
        self.assertEqual(motion['rails'][0]['heading_offset_deg'], -90)
        self.assertEqual(motion['rails'][0]['water_side'], 'left')
        self.assertAlmostEqual(align_che_pose(che)['rotation'], 0, delta=.01)
        berth.water_side = 'right'
        berth.save(update_fields=['water_side'])
        self.assertAlmostEqual(align_che_pose(che)['rotation'], 180, delta=.01)
        rail.points = list(reversed(rail.points))
        rail.save(update_fields=['points'])
        reversed_motion = che_rail_motion(che)
        self.assertEqual(reversed_motion['rails'][0]['heading_offset_deg'], -90)
        self.assertAlmostEqual(align_che_pose(che)['rotation'], 180, delta=.01)

    def test_sts_assigned_to_quay_accepts_child_crane_rail_chainage(self):
        quay = LayoutAsset.objects.create(
            layout_version=self.layout,
            code='CHAINAGE-QUAY',
            name='Chainage quay',
            asset_kind='quay',
            metadata={'editor_generator': {'water_side': 'left'}},
        )
        rail_area = LayoutAsset.objects.create(
            layout_version=self.layout,
            parent=quay,
            code='CHAINAGE-QUAY-RAIL',
            name='STS rail area',
            asset_kind='rail_area',
        )
        rail = LinearAxis.objects.create(
            asset=rail_area,
            code='CHAINAGE-QUAY.RAIL',
            name='STS crane rail',
            axis_kind='crane_rail',
            points=[[-3, 40], [-2.999, 40]],
        )
        equipment = EquipmentType.objects.create(name='Chainage STS', equipment_type='STS')

        response = self.client.post(
            f'/minitos_core/locations/{self.location.pk}/ches/',
            {
                'name': 'STS-CHAINAGE-01',
                'equipment_type': equipment.pk,
                'current_asset': quay.pk,
                'rail_axis': rail.pk,
                'rail_position_m': 40,
                'rail_min_position_m': 5,
                'rail_max_position_m': 75,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['rail_assignment']['asset_id'], rail_area.pk)
        self.assertAlmostEqual(response.data['rail_assignment']['position_m'], 40, delta=.02)
        self.assertAlmostEqual(response.data['rotation'], 0, delta=.01)

    def test_child_transfer_area_inherits_parent_crane_rail(self):
        child = LayoutAsset.objects.create(layout_version=self.layout, parent=self.asset, code='BUFFER', name='Buffer', asset_kind='transfer_zone')
        che = self.che(current_asset=child)
        self.assertEqual(che_rail_motion(che)['assigned_axis_id'], self.rail.pk)

    def test_rtg_uses_terminal_rails_and_scope_controls_repositioning(self):
        che = self.che('RTG')
        rail_area = LayoutAsset.objects.create(layout_version=self.layout, code='OTHER', name='Other', asset_kind='yard_block')
        other = LinearAxis.objects.create(asset=rail_area, code='OTHER.RAIL', name='Other rail', axis_kind='crane_rail', points=[[-2.99, 40], [-2.99, 40.001]])
        self.assertEqual({r['axis_id'] for r in che_rail_motion(che)['rails']}, {self.rail.pk, other.pk})
        self.assertTrue(all(r['can_exit'] for r in che_rail_motion(che)['rails']))
        CheOperatingScope.objects.create(che=che, axis=self.rail, can_reposition=False)
        motion = che_rail_motion(che)
        self.assertEqual(len(motion['rails']), 1)
        self.assertFalse(motion['rails'][0]['can_exit'])

    def test_rtg_scope_limits_do_not_create_midrail_exit_points(self):
        che = self.che('RTG')
        CheOperatingScope.objects.create(che=che, axis=self.rail, min_chainage_m=20, max_chainage_m=40, can_reposition=True)
        rail = che_rail_motion(che)['rails'][0]
        self.assertTrue(rail['can_exit'])
        self.assertFalse(rail['entry_at_start'])
        self.assertFalse(rail['entry_at_end'])

    def test_free_driving_equipment_is_not_aligned_or_constrained(self):
        che = self.che('SC')
        response = self.client.patch(f'/minitos_core/locations/{self.location.pk}/ches/{che.pk}/', {'rotation': 71.4}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['rotation'], 71.4)
        self.assertEqual(response.data['rail_motion'], {'mode': 'free', 'rails': [], 'assigned_axis_id': None})

    def test_serializing_existing_che_and_patching_flags_do_not_rewrite_pose(self):
        che = self.che()
        before = (che.longitude, che.latitude, che.rotation)
        self.assertEqual(CheSerializer(che).data['rail_motion']['assigned_axis_id'], self.rail.pk)
        response = self.client.patch(f'/minitos_core/locations/{self.location.pk}/ches/{che.pk}/', {'powered_on': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        che.refresh_from_db()
        self.assertEqual((che.longitude, che.latitude, che.rotation), before)

    def test_disabled_rails_and_foreign_scopes_are_excluded(self):
        che = self.che('RTG')
        foreign = Location.objects.create(name='Foreign', location_type='TL')
        version = LayoutVersion.objects.create(location=foreign, version=1)
        asset = LayoutAsset.objects.create(layout_version=version, code='FOREIGN', name='Foreign', asset_kind='yard_block')
        rail = LinearAxis.objects.create(asset=asset, code='F.RAIL', name='Foreign rail', axis_kind='crane_rail', points=[[-3, 40], [-3, 40.001]])
        CheOperatingScope.objects.create(equipment_type=che.equipment_type, axis=rail)
        self.assertEqual(che_rail_motion(che)['rails'], [])
        CheOperatingScope.objects.all().delete()
        self.rail.is_enabled = False
        self.rail.save()
        self.assertEqual(che_rail_motion(che)['rails'], [])
        response = self.client.patch(f'/minitos_core/locations/{self.location.pk}/ches/{che.pk}/', {'current_asset': asset.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)

    def test_polyline_projection_uses_local_segment_heading(self):
        points = [[-3, 40], [-3, 40.001], [-2.999, 40.001]]
        projection = project_rail(points, [-2.9995, 40.0011])
        self.assertAlmostEqual(projection['heading_deg'], 90)
        self.assertAlmostEqual(projection['point'][1], 40.001)

    def test_scope_api_assigns_a_quay_service_lane_and_rejects_other_assets(self):
        quay = LayoutAsset.objects.create(
            layout_version=self.layout, code='Q-SCOPE', name='Scope quay', asset_kind='quay',
        )
        crane_rail = LinearAxis.objects.create(
            asset=quay, code='Q-SCOPE.RAIL', name='Crane rail', axis_kind='crane_rail',
            points=[[-3, 40], [-2.999, 40]],
        )
        service_lane = LinearAxis.objects.create(
            asset=quay, code='Q-SCOPE.SERVICE.01', name='Service lane', axis_kind='road_centerline',
            points=[[-3, 40.0001], [-2.999, 40.0001]],
        )
        foreign_lane = LinearAxis.objects.create(
            asset=self.asset, code='A01.SERVICE', name='Other service lane', axis_kind='road_centerline',
            points=[[-3, 40.0002], [-2.999, 40.0002]],
        )
        che = self.che('STS', current_asset=quay)
        url = f'/minitos_core/locations/{self.location.pk}/che-operating-scopes/'
        created = self.client.post(url, {
            'che': che.pk, 'asset': quay.pk, 'axis': crane_rail.pk,
            'service_lane_axis': service_lane.pk,
        }, format='json')
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['service_lane_axis'], service_lane.pk)

        rejected = self.client.post(url, {
            'che': che.pk, 'asset': quay.pk, 'axis': crane_rail.pk,
            'service_lane_axis': foreign_lane.pk,
        }, format='json')
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertIn('service_lane_axis', rejected.data)

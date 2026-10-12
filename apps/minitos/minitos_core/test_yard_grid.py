from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from minitos_core.models import (
    AccessPolicy, Che, CheOperatingScope, ContainerVisit, EquipmentType, InterfacePosition,
    LayoutAsset, LayoutVersion, LinearAxis, Location, OperationalPosition,
    Pile, Position, RouteEdge, RouteNode, StorageGrid, TransferInterface,
)
from minitos_core.services import (
    geographic_to_local, local_to_geographic, offset_geographic, sync_yard_grid,
)


class LayoutTestCase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='layout-editor', password='test')
        self.client.force_authenticate(user=self.user)
        self.location = Location.objects.create(
            name='Layout Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3,
        )
        self.layout = LayoutVersion.objects.create(
            location=self.location, version=1, name='Working layout', created_by=self.user,
        )

    def create_grid(self, **overrides):
        values = {
            'code': 'A01', 'name': 'Block A01', 'asset_kind': LayoutAsset.YARD_BLOCK,
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[[-3, 40], [-2.9998, 40], [-2.9998, 40.0002], [-3, 40.0002], [-3, 40]]],
            },
        }
        asset = LayoutAsset.objects.create(layout_version=self.layout, **values)
        grid_values = {
            'name': asset.name, 'max_bays': 2, 'max_rows': 2, 'max_tier': 2,
            'latitude': 40, 'longitude': -3, 'bay_use_heading_axis': True,
            'bay_naming': StorageGrid.LETTERS, 'initial_bay': 'C',
        }
        grid_values.update(overrides)
        return StorageGrid.objects.create(layout_asset=asset, **grid_values)


class YardGridServiceTests(LayoutTestCase):
    def test_sync_creates_named_grid_positions_and_operational_positions(self):
        grid = self.create_grid()
        sync_yard_grid(grid)
        pile = Pile.objects.get(storage_grid=grid, bay=1, row=1)
        position = Position.objects.get(pile=pile, tier=1)

        self.assertEqual(grid.piles.count(), 4)
        self.assertEqual(Position.objects.filter(pile__storage_grid=grid).count(), 8)
        self.assertEqual(OperationalPosition.objects.filter(asset=grid.layout_asset).count(), 8)
        self.assertEqual(position.operational_position.logical_address['tier'], 1)
        self.assertEqual(pile.get_bay_name(), 'C')
        expected_lat, expected_lon = offset_geographic(
            grid.latitude, grid.longitude, grid.pile_width_m / 2, grid.pile_length_m / 2,
        )
        self.assertAlmostEqual(pile.latitude, expected_lat)
        self.assertAlmostEqual(pile.longitude, expected_lon)

        grid.heading_deg = 15
        grid.save(update_fields=['heading_deg'])
        self.assertTrue(Position.objects.filter(pk=position.pk).exists())

    def test_sync_preserves_existing_ids_and_rejects_reserved_shrink(self):
        grid = self.create_grid()
        sync_yard_grid(grid)
        preserved = Position.objects.get(pile__storage_grid=grid, pile__bay=1, pile__row=1, tier=1)
        protected = Position.objects.get(pile__storage_grid=grid, pile__bay=2, pile__row=2, tier=2)
        protected.is_reserved = True
        protected.save(update_fields=['is_reserved'])

        grid.max_bays = 3
        grid.heading_deg = 90
        sync_yard_grid(grid)
        self.assertEqual(
            Position.objects.get(pile__storage_grid=grid, pile__bay=1, pile__row=1, tier=1).pk,
            preserved.pk,
        )

        grid.max_bays = 1
        with self.assertRaises(ValidationError):
            sync_yard_grid(grid)
        self.assertTrue(Position.objects.filter(pk=protected.pk).exists())


class LayoutAssetApiTests(LayoutTestCase):
    def test_terminal_boundary_create_and_update_center_location_map_origin(self):
        url = reverse('minitos_core:layout-asset-list', args=[self.location.pk])
        response = self.client.post(
            url,
            {
                'code': 'TERMINAL', 'name': 'Terminal perimeter',
                'asset_kind': LayoutAsset.TERMINAL,
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[
                        [-3.004, 39.998], [-2.994, 39.998], [-2.994, 40.006],
                        [-3.004, 40.006], [-3.004, 39.998],
                    ]],
                },
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.location.refresh_from_db()
        self.assertAlmostEqual(self.location.map_origin_longitude, -2.999)
        self.assertAlmostEqual(self.location.map_origin_latitude, 40.002)

        update = self.client.patch(
            reverse(
                'minitos_core:layout-asset-detail',
                args=[self.location.pk, response.data['id']],
            ),
            {
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[
                        [-3.001, 40.001], [-2.993, 40.001], [-2.993, 40.005],
                        [-3.001, 40.005], [-3.001, 40.001],
                    ]],
                },
            },
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        self.location.refresh_from_db()
        self.assertAlmostEqual(self.location.map_origin_longitude, -2.997)
        self.assertAlmostEqual(self.location.map_origin_latitude, 40.003)
        self.assertEqual((self.location.longitude, self.location.latitude), (-3, 40))

    def test_yard_asset_create_and_update_synchronizes_grid(self):
        url = reverse('minitos_core:layout-asset-list', args=[self.location.pk])
        payload = {
            'code': 'N01', 'name': 'North Block', 'asset_kind': LayoutAsset.YARD_BLOCK,
            'geometry': {
                'type': 'Polygon',
                'coordinates': [[[-3, 40], [-2.9998, 40], [-2.9998, 40.0002], [-3, 40.0002], [-3, 40]]],
            },
            'heading_deg': 0,
            'storage_grid_settings': {
                'grid_kind': StorageGrid.AUTOMATED, 'max_bays': 3, 'max_rows': 2,
                'max_tier': 4, 'latitude': 40, 'longitude': -3,
                'heading_deg': 0, 'slot_heading_deg': 10,
                'pile_length_m': 6.058, 'pile_width_m': 2.438,
                'bay_spacing_m': 0.3, 'row_spacing_m': 0.3,
                'initial_bay': '1', 'initial_row': 'A',
                'bay_naming': StorageGrid.ODD_NUMBERS,
                'row_naming': StorageGrid.LETTERS,
                'bay_order': StorageGrid.FORWARD,
                'row_order': StorageGrid.FORWARD,
                'bay_use_heading_axis': True,
            },
        }
        response = self.client.post(url, payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        grid = StorageGrid.objects.get(layout_asset_id=response.data['id'])
        self.assertEqual(grid.piles.count(), 6)
        self.assertEqual(Position.objects.filter(pile__storage_grid=grid).count(), 24)
        preserved_id = Position.objects.get(
            pile__storage_grid=grid, pile__bay=1, pile__row=1, tier=1,
        ).pk

        update = self.client.patch(
            reverse('minitos_core:layout-asset-detail', args=[self.location.pk, response.data['id']]),
            {
                'heading_deg': 90,
                'storage_grid_settings': {
                    **payload['storage_grid_settings'], 'max_bays': 4, 'max_rows': 3,
                    'max_tier': 3, 'heading_deg': 90, 'latitude': 40.0004,
                    'longitude': -2.9996,
                },
            },
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        grid.refresh_from_db()
        self.assertEqual(grid.piles.count(), 12)
        self.assertEqual(Position.objects.filter(pile__storage_grid=grid).count(), 36)
        self.assertEqual(
            Position.objects.get(pile__storage_grid=grid, pile__bay=1, pile__row=1, tier=1).pk,
            preserved_id,
        )

    def test_asset_api_rejects_invalid_geojson(self):
        response = self.client.post(
            reverse('minitos_core:layout-asset-list', args=[self.location.pk]),
            {'code': 'BAD', 'name': 'Bad shape', 'asset_kind': LayoutAsset.LAND, 'geometry': {'type': 'Circle', 'coordinates': []}},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('geometry', response.data)

    def test_yard_transfer_interface_api_preserves_side_range_and_positions(self):
        grid = self.create_grid(max_bays=8, max_rows=6)
        sync_yard_grid(grid)

        response = self.client.post(
            reverse('minitos_core:transfer-interface-list', args=[self.location.pk]),
            {
                'asset': grid.layout_asset_id,
                'storage_grid': grid.pk,
                'code': 'A01.TP.LEFT',
                'name': 'Block A01 left transfer',
                'interface_kind': TransferInterface.VEHICLE_LANE,
                'side': TransferInterface.LEFT,
                'geometry': {
                    'type': 'LineString',
                    'coordinates': [[-3.00002, 40], [-3.00002, 40.0004]],
                },
                'bay_start': 1,
                'bay_end': 8,
                'row_start': 5,
                'row_end': 6,
                'capacity': 3,
                'requires_vehicle_present': True,
                'position_count': 3,
                'metadata': {'generated_by': 'asset_editor', 'editor_side': 'left'},
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        interface = TransferInterface.objects.get(pk=response.data['id'])
        self.assertEqual((interface.bay_start, interface.bay_end), (1, 8))
        self.assertEqual((interface.row_start, interface.row_end), (5, 6))
        self.assertEqual(interface.positions.count(), 3)
        self.assertEqual(
            OperationalPosition.objects.filter(interface_position__transfer_interface=interface).count(),
            3,
        )


class RoutingGraphApiTests(LayoutTestCase):
    def setUp(self):
        super().setUp()
        self.asset = LayoutAsset.objects.create(
            layout_version=self.layout, code='ROAD-1', name='Main spine',
            asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40], [-2.9995, 40.0005]]},
        )
        self.che_type = EquipmentType.objects.create(name='Terminal Tractor', equipment_type='TT')

    def test_axis_create_update_and_direction_compile_route_graph(self):
        response = self.client.post(
            reverse('minitos_core:linear-axis-list', args=[self.location.pk]),
            {
                'asset': self.asset.pk, 'code': 'ROAD-1.C', 'name': 'Centerline',
                'axis_kind': LinearAxis.ROAD_CENTERLINE,
                'points': [[-3, 40], [-2.9998, 40.0002], [-2.9995, 40.0005]],
                'direction': LinearAxis.FORWARD, 'lane_count': 2, 'capacity': 8,
            },
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        axis = LinearAxis.objects.get(pk=response.data['id'])
        self.assertEqual(RouteNode.objects.filter(metadata__axis_id=axis.id).count(), 3)
        self.assertEqual(RouteEdge.objects.filter(source_axis=axis).count(), 2)
        self.assertTrue(RouteEdge.objects.filter(source_axis=axis, movement_mode=RouteEdge.ROAD).exists())

        update = self.client.patch(
            reverse('minitos_core:linear-axis-detail', args=[self.location.pk, axis.pk]),
            {'direction': LinearAxis.BOTH}, format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        self.assertEqual(RouteEdge.objects.filter(source_axis=axis).count(), 4)

    def test_policy_interface_and_manual_handoff_edge(self):
        policy = self.client.post(
            reverse('minitos_core:access-policy-list', args=[self.location.pk]),
            {
                'code': 'TT-ONLY', 'name': 'Terminal tractors',
                'allowed_che_types': [self.che_type.pk], 'allow_loaded': True,
                'allow_empty': True,
            }, format='json',
        )
        self.assertEqual(policy.status_code, status.HTTP_201_CREATED, policy.data)
        self.assertTrue(AccessPolicy.objects.get(pk=policy.data['id']).allowed_che_types.filter(pk=self.che_type.pk).exists())

        parking = self.client.post(
            reverse('minitos_core:layout-asset-list', args=[self.location.pk]),
            {
                'code': 'PARK-TT', 'name': 'TT staging',
                'asset_kind': LayoutAsset.PARKING,
                'access_policy': policy.data['id'],
                'geometry': {
                    'type': 'Polygon',
                    'coordinates': [[
                        [-3, 40], [-2.9999, 40], [-2.9999, 40.0001],
                        [-3, 40.0001], [-3, 40],
                    ]],
                },
            }, format='json',
        )
        self.assertEqual(parking.status_code, status.HTTP_201_CREATED, parking.data)
        self.assertEqual(parking.data['access_policy'], policy.data['id'])

        interface = self.client.post(
            reverse('minitos_core:transfer-interface-list', args=[self.location.pk]),
            {
                'asset': self.asset.pk, 'access_policy': policy.data['id'],
                'code': 'ROAD-1.TP', 'name': 'Road handoff',
                'interface_kind': TransferInterface.GROUND_BUFFER,
                'side': TransferInterface.INTERNAL,
                'geometry': {'type': 'LineString', 'coordinates': [[-3, 40], [-2.9998, 40.0002]]},
                'capacity': 2, 'cargo_may_rest': True, 'position_count': 2,
            }, format='json',
        )
        self.assertEqual(interface.status_code, status.HTTP_201_CREATED, interface.data)
        transfer = TransferInterface.objects.get(pk=interface.data['id'])
        self.assertEqual(InterfacePosition.objects.filter(transfer_interface=transfer).count(), 2)
        positions = OperationalPosition.objects.filter(interface_position__transfer_interface=transfer)
        self.assertEqual(positions.count(), 2)
        nodes = list(RouteNode.objects.filter(operational_position__in=positions).order_by('code'))
        self.assertEqual(len(nodes), 2)

        edge = self.client.post(
            reverse('minitos_core:route-edge-list', args=[self.location.pk]),
            {
                'code': 'ROAD-1.HANDOFF', 'from_node': nodes[0].pk,
                'to_node': nodes[1].pk, 'edge_kind': RouteEdge.HANDOFF,
                'movement_mode': RouteEdge.STATIC, 'distance_m': 0,
                'capacity': 1, 'base_cost': 1, 'requirements': [],
            }, format='json',
        )
        self.assertEqual(edge.status_code, status.HTTP_201_CREATED, edge.data)
        self.assertFalse(edge.data['is_generated'])

    def test_cross_terminal_axis_is_rejected(self):
        other = Location.objects.create(name='Other', location_type='TL', latitude=41, longitude=-4)
        other_layout = LayoutVersion.objects.create(location=other, version=1)
        other_asset = LayoutAsset.objects.create(
            layout_version=other_layout, code='OTHER', name='Other road',
            asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-4, 41], [-3.99, 41.01]]},
        )
        response = self.client.post(
            reverse('minitos_core:linear-axis-list', args=[self.location.pk]),
            {'asset': other_asset.pk, 'code': 'WRONG', 'name': 'Wrong', 'axis_kind': LinearAxis.ROAD_CENTERLINE, 'points': [[-4, 41], [-3.99, 41.01]]},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_che_operating_scope_supports_fixed_axis_and_grid_ranges(self):
        che = Che.objects.create(
            name='TT-SCOPE', equipment_type=self.che_type, location=self.location,
        )
        axis = LinearAxis.objects.create(
            asset=self.asset, code='ROAD-1.SCOPE', name='Scope axis',
            axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=[[-3, 40], [-2.9995, 40.0005]],
        )
        response = self.client.post(
            reverse('minitos_core:che-operating-scope-list', args=[self.location.pk]),
            {
                'che': che.pk, 'equipment_type': None,
                'asset': self.asset.pk, 'axis': axis.pk,
                'min_chainage_m': 10, 'max_chainage_m': 50,
                'bay_start': 1, 'bay_end': 3,
                'can_reposition': False, 'is_enabled': True,
            }, format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        scope = CheOperatingScope.objects.get(pk=response.data['id'])
        self.assertEqual(scope.che, che)
        self.assertEqual(scope.axis, axis)

        invalid = self.client.patch(
            reverse(
                'minitos_core:che-operating-scope-detail',
                args=[self.location.pk, scope.pk],
            ),
            {'min_chainage_m': 60, 'max_chainage_m': 20},
            format='json',
        )
        self.assertEqual(invalid.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('max_chainage_m', invalid.data)

    def test_manual_route_node_and_edge_crud_recalculates_distance(self):
        node_url = reverse('minitos_core:route-node-list', args=[self.location.pk])
        first = self.client.post(
            node_url,
            {
                'code': 'MANUAL-A', 'name': 'Manual A', 'node_kind': RouteNode.JUNCTION,
                'point': {'type': 'Point', 'coordinates': [-3, 40]},
            }, format='json',
        )
        second = self.client.post(
            node_url,
            {
                'code': 'MANUAL-B', 'name': 'Manual B', 'node_kind': RouteNode.JUNCTION,
                'point': {'type': 'Point', 'coordinates': [-2.9999, 40.0001]},
            }, format='json',
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)
        self.assertEqual(second.status_code, status.HTTP_201_CREATED, second.data)
        edge = self.client.post(
            reverse('minitos_core:route-edge-list', args=[self.location.pk]),
            {
                'code': 'MANUAL-A-B', 'name': 'Manual A to B',
                'from_node': first.data['id'], 'to_node': second.data['id'],
                'edge_kind': RouteEdge.TRAVEL, 'movement_mode': RouteEdge.FREE_DRIVE,
                'distance_m': 1, 'capacity': 1, 'base_cost': 1,
            }, format='json',
        )
        self.assertEqual(edge.status_code, status.HTTP_201_CREATED, edge.data)

        update = self.client.patch(
            reverse('minitos_core:route-node-detail', args=[self.location.pk, second.data['id']]),
            {'point': {'type': 'Point', 'coordinates': [-2.999, 40.001]}},
            format='json',
        )
        self.assertEqual(update.status_code, status.HTTP_200_OK, update.data)
        route_edge = RouteEdge.objects.get(pk=edge.data['id'])
        self.assertGreater(route_edge.distance_m, 100)

        delete_edge = self.client.delete(
            reverse('minitos_core:route-edge-detail', args=[self.location.pk, edge.data['id']]),
        )
        delete_node = self.client.delete(
            reverse('minitos_core:route-node-detail', args=[self.location.pk, second.data['id']]),
        )
        self.assertEqual(delete_edge.status_code, status.HTTP_204_NO_CONTENT)
        self.assertEqual(delete_node.status_code, status.HTTP_204_NO_CONTENT)


class CoordinateProjectionTests(APITestCase):
    def test_location_rotation_round_trip(self):
        location = Location.objects.create(
            name='Rotated Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3, angle=32,
        )
        latitude, longitude = local_to_geographic(location, 250, 480)
        x_m, y_m = geographic_to_local(location, latitude, longitude)
        self.assertAlmostEqual(x_m, 250, places=5)
        self.assertAlmostEqual(y_m, 480, places=5)

    def test_movable_object_apis_accept_geographic_positions(self):
        user = User.objects.create_user(username='simulator', password='test')
        self.client.force_authenticate(user=user)
        location = Location.objects.create(
            name='Simulator Terminal', location_type='TL', latitude=40, longitude=-3,
            map_origin_latitude=40, map_origin_longitude=-3,
        )
        equipment_type = EquipmentType.objects.create(name='Test Tractor', equipment_type='TT')
        che = Che.objects.create(name='TT-GPS', equipment_type=equipment_type, location=location)
        visit = ContainerVisit.objects.create(location=location)
        che_response = self.client.patch(
            reverse('minitos_core:che-detail', args=[location.pk, che.pk]),
            {'latitude': 40.0002, 'longitude': -2.9998}, format='json',
        )
        visit_response = self.client.patch(
            reverse('minitos_core:container_visit-detail', args=[location.pk, visit.pk]),
            {'latitude': 40.0003, 'longitude': -2.9997}, format='json',
        )
        self.assertEqual(che_response.status_code, status.HTTP_200_OK, che_response.data)
        self.assertEqual(visit_response.status_code, status.HTTP_200_OK, visit_response.data)
        self.assertNotIn('x_position', che_response.data)
        self.assertNotIn('y_position', visit_response.data)

from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.urls import reverse

from minitos_core.models import (
    AccessPolicy, Che, EquipmentType, InterfacePosition, LayoutAsset, LinearAxis,
    Location, OperationalPosition, RouteEdge, RouteNode, TransferInterface,
)
from minitos_core.serializers import OperationalPositionSerializer, RouteNodeSerializer
from minitos_core.services.layout import (
    geographic_distance_m, point_along_axis, resolved_interface_point,
    sync_axis_graph, sync_interface_positions, validate_layout,
)
from minitos_core.services import offset_geographic, sync_yard_grid
from minitos_core.test_yard_grid import LayoutTestCase


class TransferLaneTests(LayoutTestCase):
    def interface(self, asset, code='TP', **overrides):
        lat, lon = offset_geographic(40, -3, 0, 100)
        payload = dict(asset=asset.pk, code=code, name=code,
                       interface_kind='vehicle_lane', side='left',
                       geometry={'type': 'LineString', 'coordinates': [[-3, 40], [lon, lat]]},
                       capacity=8, position_count=2, lane_count=4, lane_spacing_m=4)
        payload.update(overrides)
        response = self.client.post(reverse('minitos_core:transfer-interface-list', args=[self.location.pk]), payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return TransferInterface.objects.get(pk=response.data['id'])

    def test_multiple_lanes_generate_addressed_positions_on_every_side(self):
        grid = self.create_grid()
        for side in ('left', 'right', 'start', 'end'):
            interface = self.interface(grid.layout_asset, code=f'TP.{side}', side=side)
            self.assertEqual(interface.positions.count(), 8)
            first = interface.positions.get(lane_index=1, position_index=1)
            second = interface.positions.get(lane_index=2, position_index=1)
            self.assertAlmostEqual(geographic_distance_m([first.longitude, first.latitude], [second.longitude, second.latitude]), 4, delta=.01)
            self.assertEqual(first.operational_position.logical_address, {'interface': interface.code, 'lane': 1, 'position': 1})
            self.assertEqual(RouteNode.objects.filter(operational_position__interface_position__transfer_interface=interface).count(), 8)
            self.assertLess(second.longitude, first.longitude) if side in ('left', 'end') else self.assertGreater(second.longitude, first.longitude)

    def test_resizing_preserves_ids_and_connected_routes(self):
        interface = self.interface(self.create_grid().layout_asset)
        position = interface.positions.get(lane_index=2, position_index=1)
        node = position.operational_position.route_node
        other = RouteNode.objects.create(layout_version=self.layout, code='ENTRY', name='Entry', point={'type': 'Point', 'coordinates': [-3, 40]})
        edge = RouteEdge.objects.create(layout_version=self.layout, code='LINK', name='Link', from_node=other, to_node=node, movement_mode='road', base_cost=1)
        response = self.client.patch(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]), {'position_count': 3, 'lane_count': 5}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(InterfacePosition.objects.get(transfer_interface=interface, lane_index=2, position_index=1).pk, position.pk)
        self.assertEqual(RouteEdge.objects.get(pk=edge.pk).to_node_id, node.pk)
        self.assertEqual(interface.positions.count(), 15)
        response = self.client.patch(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]), {'lane_count': 1}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        interface.refresh_from_db()
        self.assertEqual(interface.lane_count, 5)
        self.assertEqual(interface.positions.count(), 15)

    def test_asset_move_and_rotation_carry_rail_grid_children_and_equipment(self):
        grid = self.create_grid()
        sync_yard_grid(grid)
        asset = grid.layout_asset
        rail = LinearAxis.objects.create(asset=asset, code='MANUAL.RAIL', name='Crane rail', axis_kind='crane_rail', points=[[-3, 40], [-3, 40.0005]])
        sync_axis_graph(rail)
        rail_node = RouteNode.objects.get(metadata__axis_id=rail.pk, metadata__axis_index=1)
        interface = self.interface(asset)
        position = interface.positions.first()
        edge = RouteEdge.objects.create(layout_version=self.layout, code='JOIN', name='Join', from_node=rail_node, to_node=position.operational_position.route_node, movement_mode='crane', base_cost=1)
        child = LayoutAsset.objects.create(layout_version=self.layout, parent=asset, code='BUFFER', name='Buffer', asset_kind='transfer_zone', geometry={'type': 'Point', 'coordinates': [-3, 40.0002]})
        equipment_type = EquipmentType.objects.create(name='Test ASC', equipment_type='RMG')
        che = Che.objects.create(name='ASC01', location=self.location, current_asset=child, equipment_type=equipment_type, latitude=40.0002, longitude=-3)
        old_lat, old_lon = position.latitude, position.longitude
        moved_lat, moved_lon = offset_geographic(40, -3, 50, 30)
        response = self.client.patch(reverse('minitos_core:layout-asset-detail', args=[self.location.pk, asset.pk]),
                                     {'geometry': {'type': 'Polygon', 'coordinates': [[[moved_lon, moved_lat], [moved_lon + .0001, moved_lat], [moved_lon, moved_lat + .0001], [moved_lon, moved_lat]]]}, 'heading_deg': 90}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        rail.refresh_from_db(); grid.refresh_from_db(); child.refresh_from_db(); che.refresh_from_db(); position.refresh_from_db()
        self.assertAlmostEqual(rail.points[0][0], moved_lon)
        self.assertAlmostEqual(rail.points[0][1], moved_lat)
        self.assertGreater(rail.points[1][0], rail.points[0][0])
        self.assertAlmostEqual(rail.points[1][1], rail.points[0][1])
        self.assertEqual(grid.heading_deg, 90)
        self.assertAlmostEqual(grid.longitude, moved_lon)
        self.assertNotEqual((position.latitude, position.longitude), (old_lat, old_lon))
        self.assertEqual(RouteEdge.objects.get(pk=edge.pk).from_node_id, rail_node.pk)
        self.assertAlmostEqual(che.rotation, 90)
        self.assertAlmostEqual(che.longitude, child.geometry['coordinates'][0])

    def test_quay_and_rail_interfaces_do_not_require_storage_grid(self):
        for kind in ('quay', 'rail_area'):
            asset = LayoutAsset.objects.create(layout_version=self.layout, code=kind, name=kind, asset_kind=kind, geometry={'type': 'Point', 'coordinates': [-3, 40]})
            interface = self.interface(asset, code=f'{kind}.TP', lane_count=2)
            self.assertIsNone(interface.storage_grid_id)
            self.assertEqual(interface.positions.count(), 4)

    def test_interface_compiles_one_aggregate_road_attachment_for_many_positions(self):
        block = self.create_grid().layout_asset
        road_asset = LayoutAsset.objects.create(
            layout_version=self.layout, code='ROAD01', name='Road 01',
            asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40], [-3, 40.001]]},
        )
        equipment_type = EquipmentType.objects.create(name='Terminal truck', equipment_type='TT')
        policy = AccessPolicy.objects.create(
            layout_version=self.layout, code='TRUCKS', name='Terminal trucks',
        )
        policy.allowed_che_types.add(equipment_type)
        road = LinearAxis.objects.create(
            asset=road_asset, code='ROAD01.CENTER', name='Road 01 centerline',
            axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=[[-3, 40], [-3, 40.001]], lane_count=2,
            direction=LinearAxis.BOTH, access_policy=policy,
        )
        sync_axis_graph(road)

        interface = self.interface(
            block, code='AB01.TP.LEFT', position_count=12, lane_count=2,
            road_axis=road.pk, access_policy=policy.pk, requires_vehicle_present=True,
        )
        interface.refresh_from_db()

        self.assertEqual(interface.road_axis_id, road.pk)
        self.assertEqual(interface.positions.count(), 24)
        self.assertEqual(interface.route_node.node_kind, RouteNode.INTERFACE)
        self.assertEqual(
            RouteNode.objects.filter(
                operational_position__interface_position__transfer_interface=interface,
                node_kind=RouteNode.OPERATIONAL,
            ).count(),
            24,
        )
        self.assertEqual(RouteEdge.objects.filter(metadata__connector_kind='interface_road', metadata__interface_id=interface.pk).count(), 2)
        self.assertEqual(RouteEdge.objects.filter(metadata__connector_kind='interface_position', metadata__interface_id=interface.pk).count(), 48)
        self.assertEqual(RouteEdge.objects.get(
            metadata__connector_kind='interface_road', metadata__interface_id=interface.pk,
            from_node=interface.route_node,
        ).access_policy_id, policy.pk)
        response = self.client.get(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['road_axis'], road.pk)
        self.assertNotIn('interface_without_road', {
            warning['code'] for warning in validate_layout(self.layout)['warnings']
            if warning['object'] == interface.code
        })
        response = self.client.patch(
            reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]),
            {'road_axis': None}, format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.assertFalse(RouteEdge.objects.filter(metadata__connector_kind='interface_road', metadata__interface_id=interface.pk).exists())
        self.assertIn('interface_without_road', {
            warning['code'] for warning in validate_layout(self.layout)['warnings']
            if warning['object'] == interface.code
        })

    def test_road_vertices_compile_junctions_and_preserve_ids_on_resync(self):
        first_asset = LayoutAsset.objects.create(
            layout_version=self.layout, code='R1', name='Road 1', asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40], [-3, 40.001]]},
        )
        second_asset = LayoutAsset.objects.create(
            layout_version=self.layout, code='R2', name='Road 2', asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40.001], [-2.999, 40.001]]},
        )
        first = LinearAxis.objects.create(
            asset=first_asset, code='R1', name='Road 1', axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=first_asset.geometry['coordinates'], direction=LinearAxis.BOTH,
        )
        second = LinearAxis.objects.create(
            asset=second_asset, code='R2', name='Road 2', axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=second_asset.geometry['coordinates'], direction=LinearAxis.BOTH,
        )
        sync_axis_graph(first)
        sync_axis_graph(second)
        junctions = RouteEdge.objects.filter(metadata__connector_kind='road_junction')
        self.assertEqual(junctions.count(), 2)
        edge_ids = set(junctions.values_list('pk', flat=True))
        sync_axis_graph(second)
        self.assertEqual(set(RouteEdge.objects.filter(metadata__connector_kind='road_junction').values_list('pk', flat=True)), edge_ids)

        start = RouteNode.objects.get(metadata__axis_id=first.pk, metadata__axis_index=1)
        finish = RouteNode.objects.get(metadata__axis_id=second.pk, metadata__axis_index=2)
        adjacency = {}
        for source, target in RouteEdge.objects.filter(layout_version=self.layout).values_list('from_node_id', 'to_node_id'):
            adjacency.setdefault(source, set()).add(target)
        pending, seen = [start.pk], set()
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            pending.extend(adjacency.get(node_id, set()) - seen)
        self.assertIn(finish.pk, seen)

    def test_crossing_road_segments_compile_a_stable_intersection(self):
        axes = []
        for code, points in [
            ('NS', [[-3, 40], [-3, 40.002]]),
            ('EW', [[-3.001, 40.001], [-2.999, 40.001]]),
        ]:
            asset = LayoutAsset.objects.create(
                layout_version=self.layout, code=code, name=code,
                asset_kind=LayoutAsset.ROAD,
                geometry={'type': 'LineString', 'coordinates': points},
            )
            axis = LinearAxis.objects.create(
                asset=asset, code=code, name=code,
                axis_kind=LinearAxis.ROAD_CENTERLINE,
                points=points, direction=LinearAxis.BOTH,
            )
            sync_axis_graph(axis)
            axes.append(axis)

        intersections = RouteEdge.objects.filter(
            metadata__connector_kind='road_intersection',
        )
        self.assertEqual(intersections.count(), 2)
        self.assertEqual(RouteNode.objects.filter(
            metadata__connector_kind='road_intersection_node',
        ).count(), 2)
        edge_ids = set(intersections.values_list('pk', flat=True))

        start = RouteNode.objects.get(metadata__axis_id=axes[0].pk, metadata__axis_index=1)
        finish = RouteNode.objects.get(metadata__axis_id=axes[1].pk, metadata__axis_index=2)
        adjacency = {}
        for source, target in RouteEdge.objects.filter(
            layout_version=self.layout,
        ).values_list('from_node_id', 'to_node_id'):
            adjacency.setdefault(source, set()).add(target)
        pending, seen = [start.pk], set()
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            pending.extend(adjacency.get(node_id, set()) - seen)
        self.assertIn(finish.pk, seen)

        sync_axis_graph(axes[1])
        self.assertEqual(
            set(RouteEdge.objects.filter(
                metadata__connector_kind='road_intersection',
            ).values_list('pk', flat=True)),
            edge_ids,
        )

        response = self.client.delete(reverse(
            'minitos_core:linear-axis-detail',
            args=[self.location.pk, axes[1].pk],
        ))
        self.assertEqual(response.status_code, 204, response.data)
        self.assertFalse(RouteEdge.objects.filter(
            metadata__connector_kind__in=[
                'road_intersection', 'road_intersection_access',
            ],
        ).exists())
        self.assertFalse(RouteNode.objects.filter(
            metadata__connector_kind='road_intersection_node',
        ).exists())

    def test_moving_shared_road_vertex_moves_every_connected_road(self):
        junction = [-3, 40.001]
        moved_junction = [-2.9998, 40.0012]
        assets = []
        axes = []
        for code, points in [
            ('WEST', [[-3.001, 40], junction]),
            ('EAST', [junction, [-2.999, 40.002]]),
        ]:
            asset = LayoutAsset.objects.create(
                layout_version=self.layout, code=code, name=code,
                asset_kind=LayoutAsset.ROAD,
                geometry={'type': 'LineString', 'coordinates': points},
            )
            axis = LinearAxis.objects.create(
                asset=asset, code=f'{code}.ROAD', name=code,
                axis_kind=LinearAxis.ROAD_CENTERLINE, points=points,
                metadata={'generated_by': 'asset_editor'},
            )
            sync_axis_graph(axis)
            assets.append(asset)
            axes.append(axis)
        self.assertEqual(RouteEdge.objects.filter(metadata__connector_kind='road_junction').count(), 2)

        response = self.client.patch(
            reverse('minitos_core:layout-asset-detail', args=[self.location.pk, assets[0].pk]),
            {'geometry': {'type': 'LineString', 'coordinates': [axes[0].points[0], moved_junction]}},
            format='json',
        )
        self.assertEqual(response.status_code, 200, response.data)

        for axis in axes:
            axis.refresh_from_db()
        assets[1].refresh_from_db()
        self.assertEqual(axes[0].points[-1], moved_junction)
        self.assertEqual(axes[1].points[0], moved_junction)
        self.assertEqual(assets[1].geometry['coordinates'][0], moved_junction)
        first_node = RouteNode.objects.get(metadata__axis_id=axes[0].pk, metadata__axis_index=2)
        second_node = RouteNode.objects.get(metadata__axis_id=axes[1].pk, metadata__axis_index=1)
        self.assertEqual(first_node.point['coordinates'], moved_junction)
        self.assertEqual(second_node.point['coordinates'], moved_junction)
        self.assertEqual(RouteEdge.objects.filter(metadata__connector_kind='road_junction').count(), 2)

    def test_validation_identifies_each_disconnected_routing_group(self):
        for code, points in [
            ('MAIN', [[-3, 40], [-3, 40.001], [-3, 40.002]]),
            ('ISLAND', [[-2.99, 40], [-2.99, 40.001]]),
        ]:
            asset = LayoutAsset.objects.create(
                layout_version=self.layout, code=code, name=code,
                asset_kind=LayoutAsset.ROAD,
                geometry={'type': 'LineString', 'coordinates': points},
            )
            axis = LinearAxis.objects.create(
                asset=asset, code=code, name=code,
                axis_kind=LinearAxis.ROAD_CENTERLINE, points=points,
            )
            sync_axis_graph(axis)

        report = validate_layout(self.layout)
        warning = next(item for item in report['warnings'] if item['code'] == 'graph_components')
        self.assertTrue(report['is_valid'])
        self.assertEqual(warning['main_component']['node_count'], 3)
        self.assertEqual(len(warning['disconnected_components']), 1)
        self.assertEqual(warning['disconnected_components'][0]['node_count'], 2)
        self.assertEqual(warning['disconnected_components'][0]['asset_codes'], ['ISLAND'])
        self.assertEqual(
            [node['code'] for node in warning['disconnected_components'][0]['nodes']],
            ['ISLAND.N001', 'ISLAND.N002'],
        )
        self.assertIn('Connect one road endpoint', warning['hint'])

    def test_two_blocks_route_through_interfaces_and_road_without_manual_position_edges(self):
        first_block = self.create_grid().layout_asset
        second_block = LayoutAsset.objects.create(
            layout_version=self.layout, code='RB01', name='RTG Block 01',
            asset_kind=LayoutAsset.YARD_BLOCK,
            geometry={'type': 'Polygon', 'coordinates': [[[-3, 40.001], [-3, 40.0012], [-2.9998, 40.0012], [-3, 40.001]]]},
        )
        road_asset = LayoutAsset.objects.create(
            layout_version=self.layout, code='SPINE', name='Road spine',
            asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40], [-3, 40.001]]},
        )
        road = LinearAxis.objects.create(
            asset=road_asset, code='SPINE', name='Road spine',
            axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=road_asset.geometry['coordinates'], direction=LinearAxis.BOTH,
        )
        sync_axis_graph(road)
        first_interface = self.interface(
            first_block, code='AB01.TP.LEFT', lane_count=1, position_count=8,
            geometry={'type': 'Point', 'coordinates': [-3, 40]}, road_axis=road.pk,
        )
        second_interface = self.interface(
            second_block, code='RB01.TP.TOP', lane_count=1, position_count=10,
            geometry={'type': 'Point', 'coordinates': [-3, 40.001]}, road_axis=road.pk,
        )
        start = RouteNode.objects.get(metadata__aggregate_kind='asset', asset=first_block)
        finish = RouteNode.objects.get(metadata__aggregate_kind='asset', asset=second_block)
        adjacency = {}
        for source, target in RouteEdge.objects.filter(layout_version=self.layout, is_enabled=True).values_list('from_node_id', 'to_node_id'):
            adjacency.setdefault(source, set()).add(target)
        pending, seen = [start.pk], set()
        while pending:
            node_id = pending.pop()
            if node_id in seen:
                continue
            seen.add(node_id)
            pending.extend(adjacency.get(node_id, set()) - seen)

        self.assertIn(finish.pk, seen)
        self.assertEqual(RouteEdge.objects.filter(layout_version=self.layout, is_generated=False).count(), 0)
        self.assertEqual(RouteEdge.objects.filter(metadata__connector_kind='interface_road').count(), 4)
        self.assertEqual(first_interface.route_node.metadata['aggregate_kind'], 'interface')
        self.assertEqual(second_interface.route_node.metadata['aggregate_kind'], 'interface')

    def test_interface_rejects_non_road_and_foreign_road_axes(self):
        block = self.create_grid().layout_asset
        rail = LinearAxis.objects.create(
            asset=block, code='RAIL', name='Rail', axis_kind=LinearAxis.CRANE_RAIL,
            points=[[-3, 40], [-3, 40.001]],
        )
        response = self.client.post(
            reverse('minitos_core:transfer-interface-list', args=[self.location.pk]),
            {
                'asset': block.pk, 'code': 'BAD', 'name': 'Bad road',
                'interface_kind': 'vehicle_lane', 'side': 'left',
                'geometry': {'type': 'Point', 'coordinates': [-3, 40]},
                'road_axis': rail.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('road_axis', response.data)

        other_location = Location.objects.create(name='Foreign', location_type='TL')
        from minitos_core.services.layout import current_layout
        other_layout = current_layout(other_location)
        other_asset = LayoutAsset.objects.create(
            layout_version=other_layout, code='ROAD', name='Foreign road', asset_kind=LayoutAsset.ROAD,
            geometry={'type': 'LineString', 'coordinates': [[-3, 40], [-3, 40.001]]},
        )
        foreign_road = LinearAxis.objects.create(
            asset=other_asset, code='ROAD', name='Foreign road', axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=other_asset.geometry['coordinates'],
        )
        response = self.client.post(
            reverse('minitos_core:transfer-interface-list', args=[self.location.pk]),
            {
                'asset': block.pk, 'code': 'FOREIGN', 'name': 'Foreign road',
                'interface_kind': 'vehicle_lane', 'side': 'left',
                'geometry': {'type': 'Point', 'coordinates': [-3, 40]},
                'road_axis': foreign_road.pk,
            },
            format='json',
        )
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('road_axis', response.data)

    def test_preset_crane_rail_resizes_and_spans_transfer_lanes(self):
        grid = self.create_grid(heading_deg=0)
        asset = grid.layout_asset
        rail = LinearAxis.objects.create(asset=asset, code='A01.RAIL', name='Rail', axis_kind='crane_rail', points=[[-3, 40], [-3, 40.0005]])
        sync_axis_graph(rail)
        ids = list(RouteNode.objects.filter(metadata__axis_id=rail.pk).values_list('pk', flat=True))
        response = self.client.patch(reverse('minitos_core:layout-asset-detail', args=[self.location.pk, asset.pk]),
                                     {'metadata': {'editor_transfers': {'sides': {'top': {'enabled': True, 'offset_m': 2, 'lane_count': 4, 'lane_spacing_m': 4}}}},
                                      'storage_grid_settings': {'max_bays': 5, 'max_rows': 2, 'max_tier': 2, 'latitude': 40, 'longitude': -3, 'heading_deg': 0, 'bay_use_heading_axis': True}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        rail.refresh_from_db()
        grid.refresh_from_db()
        self.assertAlmostEqual(geographic_distance_m(rail.points[0], rail.points[1]), grid.max_bays * grid.pile_length_m + (grid.max_bays - 1) * grid.bay_spacing_m + 14, delta=.01)
        self.assertEqual(list(RouteNode.objects.filter(metadata__axis_id=rail.pk).values_list('pk', flat=True)), ids)

    def test_equipment_relative_points_follow_chainage_without_database_writes(self):
        asset = self.create_grid().layout_asset
        axis = LinearAxis.objects.create(asset=asset, code='AXIS', name='Axis', axis_kind='crane_rail', points=[[-3, 40], [-3, 40.001]])
        equipment_type = EquipmentType.objects.create(name='QC type', equipment_type='QC')
        che = Che.objects.create(name='QC01', location=self.location, current_asset=asset, equipment_type=equipment_type, latitude=40.0002, longitude=-3)
        interface = self.interface(asset, lane_count=2, position_count=1, position_reference='che', reference_che=che.pk, axis=axis.pk, lateral_offset_m=5)
        position = interface.positions.get(lane_index=1)
        cached = [position.longitude, position.latitude]
        point = resolved_interface_point(position)
        self.assertAlmostEqual(geographic_distance_m([-3, che.latitude], point), 5, delta=.01)
        Che.objects.filter(pk=che.pk).update(latitude=40.0007)
        url = reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertAlmostEqual(response.data['positions'][0]['latitude'], 40.0007, places=6)
        position.refresh_from_db()
        self.assertEqual([position.longitude, position.latitude], cached)
        operational = OperationalPosition.objects.get(interface_position=position)
        self.assertAlmostEqual(OperationalPositionSerializer(operational).data['latitude'], 40.0007, places=6)
        node = RouteNode.objects.get(operational_position=operational)
        self.assertAlmostEqual(RouteNodeSerializer(node).data['point']['coordinates'][1], 40.0007, places=6)

    def test_reference_validation_missing_coordinates_and_axis_clamping(self):
        asset = self.create_grid().layout_asset
        equipment_type = EquipmentType.objects.create(name='TT type', equipment_type='TT')
        che = Che.objects.create(name='TT01', location=self.location, equipment_type=equipment_type)
        interface = self.interface(asset, lane_count=1, position_count=1, position_reference='che', reference_che=che.pk)
        self.assertIsNone(resolved_interface_point(interface.positions.first()))
        interface.reference_che = None
        with self.assertRaises(ValidationError):
            interface.full_clean()
        axis = [[-3, 40], [-3, 40.001]]
        self.assertEqual(point_along_axis(axis, 10000), axis[-1])
        for change in ({'lane_count': 0}, {'lane_spacing_m': -1}, {'position_reference': 'che', 'reference_che': None}):
            response = self.client.patch(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]), change, format='json')
            self.assertEqual(response.status_code, 400, response.data)
        other = Location.objects.create(name='Other terminal', location_type='TL')
        foreign_che = Che.objects.create(name='OTHER-TT', location=other, equipment_type=equipment_type)
        response = self.client.patch(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]), {'reference_che': foreign_che.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.client.delete(reverse('minitos_core:che-detail', args=[self.location.pk, che.pk]))
        self.assertEqual(response.status_code, 400, response.data)
        self.assertTrue(Che.objects.filter(pk=che.pk).exists())
        self.client.force_authenticate(user=None)
        response = self.client.get(reverse('minitos_core:transfer-interface-detail', args=[self.location.pk, interface.pk]))
        self.assertIn(response.status_code, (401, 403))


class TransferLaneMigrationTests(TransactionTestCase):
    def test_existing_positions_keep_ids_and_sequence_as_lane_address(self):
        executor = MigrationExecutor(connection)
        before = [('minitos_core', '0037_layout_asset_access_policy')]
        after = [('minitos_core', '0038_interfaceposition_lane_index_and_more')]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            location = apps.get_model('minitos_core', 'Location').objects.create(name='Migration terminal', location_type='TL')
            layout = apps.get_model('minitos_core', 'LayoutVersion').objects.create(location=location, version=1, name='Layout')
            asset = apps.get_model('minitos_core', 'LayoutAsset').objects.create(layout_version=layout, code='A', name='A', asset_kind='quay')
            interface = apps.get_model('minitos_core', 'TransferInterface').objects.create(asset=asset, code='TP', name='TP', interface_kind='vehicle_lane')
            Position = apps.get_model('minitos_core', 'InterfacePosition')
            ids = [Position.objects.create(transfer_interface=interface, sequence=i, code=f'TP.{i}', name=str(i), latitude=40, longitude=-3).pk for i in (1, 2, 3)]
            executor = MigrationExecutor(connection)
            executor.migrate(after)
            Position = executor.loader.project_state(after).apps.get_model('minitos_core', 'InterfacePosition')
            self.assertEqual(list(Position.objects.filter(pk__in=ids).values_list('pk', 'lane_index', 'position_index')), [(pk, 1, i) for i, pk in enumerate(ids, 1)])
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

    def test_existing_interfaces_gain_aggregate_hubs_and_generated_links(self):
        executor = MigrationExecutor(connection)
        before = [('minitos_core', '0038_interfaceposition_lane_index_and_more')]
        after = [('minitos_core', '0039_transfer_interface_road_graph')]
        try:
            executor.migrate(before)
            apps = executor.loader.project_state(before).apps
            location = apps.get_model('minitos_core', 'Location').objects.create(name='Routing migration', location_type='TL')
            layout = apps.get_model('minitos_core', 'LayoutVersion').objects.create(location=location, version=1, name='Layout')
            asset = apps.get_model('minitos_core', 'LayoutAsset').objects.create(
                layout_version=layout, code='AB01', name='Block', asset_kind='yard_block',
                geometry={'type': 'Polygon', 'coordinates': [[[-3, 40], [-3, 40.001], [-2.999, 40.001], [-3, 40]]]},
            )
            interface = apps.get_model('minitos_core', 'TransferInterface').objects.create(
                asset=asset, code='AB01.TP', name='Transfer', interface_kind='vehicle_lane',
                geometry={'type': 'Point', 'coordinates': [-3, 40]},
            )
            position = apps.get_model('minitos_core', 'InterfacePosition').objects.create(
                transfer_interface=interface, sequence=1, lane_index=1, position_index=1,
                code='AB01.TP.01', name='Position 1', latitude=40, longitude=-3,
            )
            operational = apps.get_model('minitos_core', 'OperationalPosition').objects.create(
                layout_version=layout, code='AB01.TP.01', name='Position 1',
                position_kind='transfer_position', asset=asset, interface_position=position,
                latitude=40, longitude=-3,
            )
            detailed_node = apps.get_model('minitos_core', 'RouteNode').objects.create(
                layout_version=layout, code='AB01.TP.01.NODE', name='Position 1',
                node_kind='interface', point={'type': 'Point', 'coordinates': [-3, 40]},
                asset=asset, operational_position=operational, is_generated=True,
            )

            executor = MigrationExecutor(connection)
            executor.migrate(after)
            apps = executor.loader.project_state(after).apps
            RouteNode = apps.get_model('minitos_core', 'RouteNode')
            RouteEdge = apps.get_model('minitos_core', 'RouteEdge')
            hub = RouteNode.objects.get(transfer_interface_id=interface.pk)
            self.assertEqual(hub.metadata['aggregate_kind'], 'interface')
            self.assertEqual(RouteNode.objects.get(pk=detailed_node.pk).node_kind, 'operational')
            self.assertEqual(RouteEdge.objects.filter(metadata__interface_id=interface.pk).count(), 4)
        finally:
            executor = MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())

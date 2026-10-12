from django.urls import reverse
from rest_framework import status

from minitos_core.models import (
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    ISOType,
    LayoutAsset,
    LinearAxis,
    OperationalPosition,
    Order,
    OrderLeg,
    OrderLegRouteEdge,
    OrderRoutePlan,
    RouteEdge,
    TransferInterface,
    WorkQueue,
)
from minitos_core.services import (
    sync_axis_graph,
    sync_interface_positions,
    sync_yard_grid,
)
from minitos_core.test_yard_grid import LayoutTestCase


class GateToYardOrderRoutingTests(LayoutTestCase):
    def test_order_creation_routes_gate_truck_to_transfer_point_and_asc_slot(self):
        grid = self.create_grid()
        sync_yard_grid(grid)
        gate = LayoutAsset.objects.create(
            layout_version=self.layout,
            code='GATE-IN',
            name='Gate In',
            asset_kind=LayoutAsset.GATE_AREA,
            geometry={
                'type': 'Polygon',
                'coordinates': [[
                    [-3.0001, 39.9995], [-2.9999, 39.9995],
                    [-2.9999, 40], [-3.0001, 40], [-3.0001, 39.9995],
                ]],
            },
        )
        gate_axis = LinearAxis.objects.create(
            asset=gate,
            code='GATE-IN.IN.01',
            name='Gate In lane 1',
            axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=[[-3, 39.9995], [-3, 40]],
            direction=LinearAxis.FORWARD,
            metadata={'generated_by': 'asset_editor', 'facility_role': 'gate_in'},
        )
        sync_axis_graph(gate_axis)
        gate_position = OperationalPosition.objects.get(
            axis=gate_axis,
            logical_address__generated_by='gate_axis_endpoint',
        )

        road_asset = LayoutAsset.objects.create(
            layout_version=self.layout,
            code='GATE-TO-A01',
            name='Gate to A01 road',
            asset_kind=LayoutAsset.ROAD,
            geometry={
                'type': 'LineString',
                'coordinates': [[-3, 40], [-3, 40.001]],
            },
        )
        road = LinearAxis.objects.create(
            asset=road_asset,
            code='GATE-TO-A01.ROAD',
            name='Gate to A01 road',
            axis_kind=LinearAxis.ROAD_CENTERLINE,
            points=road_asset.geometry['coordinates'],
            direction=LinearAxis.BOTH,
        )
        sync_axis_graph(road)

        transfer = TransferInterface.objects.create(
            asset=grid.layout_asset,
            storage_grid=grid,
            road_axis=road,
            code='A01.TP.LEFT',
            name='A01 left transfer point',
            interface_kind=TransferInterface.VEHICLE_LANE,
            side=TransferInterface.LEFT,
            geometry={'type': 'Point', 'coordinates': [-3, 40.001]},
            capacity=1,
            requires_vehicle_present=True,
        )
        sync_interface_positions(transfer, 1)
        transfer_position = OperationalPosition.objects.get(
            interface_position__transfer_interface=transfer,
        )
        yard_position = OperationalPosition.objects.get(
            stack_position__pile__storage_grid=grid,
            stack_position__pile__bay=1,
            stack_position__pile__row=1,
            stack_position__tier=1,
        )

        iso_type = ISOType.objects.create(iso_type='22G1')
        owner = ContainerOwner.objects.create(
            name='Routing Line', code='ROUT', is_shippingline=True,
        )
        container = ContainerUnit.objects.create(
            container_number='MSCU1234567',
            iso_type=iso_type,
            owner=owner,
        )
        visit = ContainerVisit.objects.create(
            location=self.location,
            container_number=container,
            category=ContainerVisit.IMPORT,
        )
        queue = WorkQueue.objects.create(
            name='GATE-IN-WQ',
            location=self.location,
            is_active=True,
        )

        response = self.client.post(
            reverse('minitos_core:order-list', args=[self.location.pk, queue.pk]),
            {
                'order_type': Order.MOVE_IN,
                'container_number': visit.pk,
                'from_position': gate_position.pk,
                'to_position': yard_position.pk,
            },
            format='json',
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        order = Order.objects.get(pk=response.data['id'])
        self.assertEqual(order.status, Order.PLANNED)
        self.assertEqual(response.data['status'], Order.PLANNED)
        self.assertEqual(order.from_position, gate_position)
        self.assertEqual(order.to_position, yard_position)
        plan = order.route_plans.get()
        self.assertEqual(plan.status, OrderRoutePlan.ACTIVE)
        self.assertEqual(plan.layout_version, self.layout)
        legs = list(plan.legs.prefetch_related('route_edges__route_edge'))
        self.assertEqual(len(legs), 2)
        self.assertEqual(
            (legs[0].sequence, legs[0].leg_kind, legs[0].start_position, legs[0].end_position),
            (1, OrderLeg.MOVE, gate_position, transfer_position),
        )
        self.assertEqual(
            (legs[1].sequence, legs[1].leg_kind, legs[1].start_position, legs[1].end_position),
            (2, OrderLeg.HANDLE, transfer_position, yard_position),
        )
        self.assertEqual(
            set(legs[0].route_edges.values_list('route_edge__movement_mode', flat=True)),
            {RouteEdge.ROAD},
        )
        self.assertEqual(
            set(legs[1].route_edges.values_list('route_edge__movement_mode', flat=True)),
            {RouteEdge.CRANE},
        )
        self.assertGreater(
            OrderLegRouteEdge.objects.filter(order_leg__order=order).count(),
            2,
        )

        self.assertEqual(response.data['from_position_code'], 'GATE-IN.IN.01.ENTRY')
        self.assertEqual(response.data['to_position_code'], yard_position.code)
        response_legs = response.data['route_plans'][0]['legs']
        self.assertEqual(
            [
                (leg['leg_kind'], leg['start_position_code'], leg['end_position_code'])
                for leg in response_legs
            ],
            [
                (OrderLeg.MOVE, 'GATE-IN.IN.01.ENTRY', transfer_position.code),
                (OrderLeg.HANDLE, transfer_position.code, yard_position.code),
            ],
        )
        nested = self.client.get(
            reverse(
                'minitos_core:order-leg-list',
                args=[self.location.pk, queue.pk, order.pk],
            )
        )
        self.assertEqual(nested.status_code, status.HTTP_200_OK, nested.data)
        self.assertEqual(len(nested.data), 2)
        self.assertTrue(nested.data[0]['route_edges'])

from datetime import date

from django.contrib.auth.models import User
from django.db.models.deletion import RestrictedError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from minitos_core.models import (
    CarrierService,
    CarrierVisit,
    Che,
    BusinessProcess,
    BusinessProcessExecution,
    ComponentType,
    EquipmentType,
    ISOType,
    Location,
    LayoutAsset,
    LayoutVersion,
    OperationalPosition,
    Order,
    OrderLeg,
    PointOfWork,
    ProcessHierarchy,
    ProcessVersion,
    RouteEdge,
    RouteNode,
    ServiceCargoRoute,
    VisitCargoRoute,
    WorkQueue,
)


class ModelAndApiRegressionTests(APITestCase):
    def setUp(self):
        user = User.objects.create_user(username="regression-user", password="test-password")
        self.client = APIClient()
        self.client.force_authenticate(user=user)
        self.location = self._location("Regression Terminal", 41.35, 2.17)
        self.other_location = self._location("Other Terminal", 40.41, -3.70)
        self.equipment_type = EquipmentType.objects.create(
            name="Regression SC",
            equipment_type=EquipmentType.SC,
        )

    def test_equipment_type_materializes_component_profiles_and_protects_catalog(self):
        roles = set(self.equipment_type.component_profiles.values_list('role', flat=True))
        self.assertEqual(
            roles,
            {'equipment', 'drive', 'hoist', 'spreader', 'engine', 'fuel_tank'},
        )
        drive_type = ComponentType.objects.get(code=ComponentType.DRIVE)
        with self.assertRaises(RestrictedError):
            drive_type.delete()

    def test_carrier_visit_creation_materializes_service_cargo_routes(self):
        inbound = self._carrier_service("INBOUND")
        outbound = self._carrier_service("OUTBOUND")
        iso_type = ISOType.objects.create(iso_type="45G1", length="40", height="HC")
        template = ServiceCargoRoute.objects.create(
            inbound_carrier_service=inbound,
            outbound_carrier_service=outbound,
            iso_type=iso_type,
            container_type=ServiceCargoRoute.FCL,
            is_reefer=True,
            container_count=24,
            expected_dwell_time_days=2.5,
        )

        visit = CarrierVisit.objects.create(
            name="VISIT-001",
            location=self.location,
            carrier_service=inbound,
        )

        route = VisitCargoRoute.objects.get(inbound_carrier_visit=visit)
        self.assertEqual(route.inbound_carrier_service, inbound)
        self.assertEqual(route.outbound_carrier_service, outbound)
        self.assertEqual(route.iso_type, iso_type)
        self.assertEqual(route.container_count, template.container_count)
        self.assertTrue(route.is_reefer)
        inbound.refresh_from_db()
        outbound.refresh_from_db()
        self.assertEqual(inbound.moves_in, 24)
        self.assertEqual(inbound.moves_out, 0)
        self.assertEqual(outbound.moves_in, 0)
        self.assertEqual(outbound.moves_out, 24)

        template.delete()
        inbound.refresh_from_db()
        outbound.refresh_from_db()
        self.assertEqual(inbound.moves_in, 0)
        self.assertEqual(outbound.moves_out, 0)

    def test_nested_cargo_route_api_recalculates_service_moves(self):
        inbound = self._carrier_service("ROUTE-IN")
        outbound = self._carrier_service("ROUTE-OUT")
        iso_type = ISOType.objects.create(iso_type="22G1", length="20", height="NC")
        list_url = reverse(
            "minitos_core:service-cargo-route-list",
            args=[self.location.id, inbound.id],
        )
        created = self.client.post(
            list_url,
            {
                "inbound_carrier_service": inbound.id,
                "outbound_carrier_service": outbound.id,
                "iso_type": iso_type.id,
                "container_type": ServiceCargoRoute.FCL,
                "container_count": 36,
                "expected_dwell_time_days": 3,
            },
            format="json",
        )
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        inbound.refresh_from_db()
        outbound.refresh_from_db()
        self.assertEqual(inbound.moves_in, 36)
        self.assertEqual(outbound.moves_out, 36)

        detail_url = reverse(
            "minitos_core:service-cargo-route-detail",
            args=[self.location.id, inbound.id, created.data["id"]],
        )
        updated = self.client.patch(detail_url, {"container_count": 48}, format="json")
        self.assertEqual(updated.status_code, status.HTTP_200_OK, updated.data)
        inbound.refresh_from_db()
        self.assertEqual(inbound.moves_in, 48)

        deleted = self.client.delete(detail_url)
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        inbound.refresh_from_db()
        outbound.refresh_from_db()
        self.assertEqual(inbound.moves_in, 0)
        self.assertEqual(outbound.moves_out, 0)

    def test_pow_api_exposes_che_assignments_and_topology_references(self):
        che = Che.objects.create(
            name="SC-POW-01",
            equipment_type=self.equipment_type,
            location=self.location,
            powered_on=True,
        )
        layout = LayoutVersion.objects.create(location=self.location, version=1)
        asset = LayoutAsset.objects.create(
            layout_version=layout, code='GATE', name='Gate',
            asset_kind=LayoutAsset.GATE_AREA,
            geometry={'type': 'Point', 'coordinates': [2.171, 41.351]},
        )
        position = OperationalPosition.objects.create(
            layout_version=layout, asset=asset, code='GI-01', name='Gate In',
            position_kind=OperationalPosition.WORK_POINT,
            latitude=41.351, longitude=2.171,
        )
        node = RouteNode.objects.create(
            layout_version=layout, asset=asset, operational_position=position,
            code='GI-01', name='Gate In', node_kind=RouteNode.OPERATIONAL,
            point={'type': 'Point', 'coordinates': [2.171, 41.351]},
        )
        source_response = self.client.post(
            reverse("minitos_core:point-of-work-list"),
            {
                "name": "Gate In",
                "code": "GI-01",
                "pow_type": PointOfWork.ENTRY_POINT,
                "location": self.location.id,
                "related_ches": [che.id],
                "latitude": 41.351,
                "longitude": 2.171,
                "operational_position": position.id,
                "route_node": node.id,
            },
            format="json",
        )
        self.assertEqual(source_response.status_code, status.HTTP_201_CREATED, source_response.data)
        self.assertEqual(source_response.data["related_ches_names"], [che.name])
        self.assertEqual(source_response.data['operational_position'], position.id)
        self.assertEqual(source_response.data['route_node'], node.id)

    def test_route_edge_api_enforces_layout_boundary(self):
        layout = LayoutVersion.objects.create(location=self.location, version=1)
        foreign_layout = LayoutVersion.objects.create(location=self.other_location, version=1)
        first = RouteNode.objects.create(
            layout_version=layout, code='A', name='A',
            point={'type': 'Point', 'coordinates': [2.17, 41.35]},
        )
        second = RouteNode.objects.create(
            layout_version=layout, code='B', name='B',
            point={'type': 'Point', 'coordinates': [2.18, 41.36]},
        )
        foreign = RouteNode.objects.create(
            layout_version=foreign_layout, code='X', name='X',
            point={'type': 'Point', 'coordinates': [-3.7, 40.4]},
        )
        url = reverse('minitos_core:route-edge-list', args=[self.location.id])
        valid = self.client.post(url, {
            'from_node': first.id, 'to_node': second.id,
            'edge_kind': RouteEdge.TRAVEL, 'movement_mode': RouteEdge.ROAD,
        }, format='json')
        self.assertEqual(valid.status_code, status.HTTP_201_CREATED, valid.data)
        self.assertEqual(valid.data['code'], 'A__TO__B')
        self.assertEqual(valid.data['name'], 'A to B')

        duplicate = self.client.post(url, {
            'from_node': first.id, 'to_node': second.id,
            'edge_kind': RouteEdge.TRAVEL, 'movement_mode': RouteEdge.ROAD,
        }, format='json')
        self.assertEqual(duplicate.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('to_node', duplicate.data)

        cross_terminal = self.client.post(url, {
            'from_node': first.id, 'to_node': foreign.id,
            'edge_kind': RouteEdge.TRAVEL, 'movement_mode': RouteEdge.ROAD,
        }, format='json')
        self.assertEqual(cross_terminal.status_code, status.HTTP_400_BAD_REQUEST)

    def test_che_assignment_is_limited_to_assets_in_its_terminal(self):
        layout = LayoutVersion.objects.create(location=self.location, version=1)
        foreign_layout = LayoutVersion.objects.create(location=self.other_location, version=1)
        local_asset = LayoutAsset.objects.create(
            layout_version=layout, code='PARK', name='Parking',
            asset_kind=LayoutAsset.PARKING,
            geometry={'type': 'Point', 'coordinates': [2.17, 41.35]},
        )
        foreign_asset = LayoutAsset.objects.create(
            layout_version=foreign_layout, code='FOREIGN', name='Foreign block',
            asset_kind=LayoutAsset.YARD,
            geometry={'type': 'Point', 'coordinates': [-3.7, 40.4]},
        )
        url = reverse('minitos_core:che-list', args=[self.location.id])
        rejected = self.client.post(url, {
            'name': 'SC-FOREIGN-ASSET',
            'equipment_type': self.equipment_type.id,
            'current_asset': foreign_asset.id,
        }, format='json')
        self.assertEqual(rejected.status_code, status.HTTP_400_BAD_REQUEST, rejected.data)
        self.assertIn('current_asset', rejected.data)

        created = self.client.post(url, {
            'name': 'SC-LOCAL-ASSET',
            'equipment_type': self.equipment_type.id,
            'current_asset': local_asset.id,
        }, format='json')
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)
        self.assertEqual(created.data['current_asset'], local_asset.id)

    def test_che_assignment_rejects_ineligible_layout_assets(self):
        layout = LayoutVersion.objects.create(location=self.location, version=1)
        building = LayoutAsset.objects.create(
            layout_version=layout, code='BLDG', name='Workshop building',
            asset_kind=LayoutAsset.BUILDING,
            geometry={'type': 'Point', 'coordinates': [2.17, 41.35]},
        )

        response = self.client.post(reverse('minitos_core:che-list', args=[self.location.id]), {
            'name': 'SC-BUILDING',
            'equipment_type': self.equipment_type.id,
            'current_asset': building.id,
        }, format='json')

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.data)
        self.assertEqual(
            response.data['current_asset'][0],
            'CHE may only be assigned to a quay, yard block, parking area, or rail area.',
        )

    def test_nested_order_leg_url_returns_order_legs_not_piles(self):
        queue = WorkQueue.objects.create(name="WQ-REGRESSION", location=self.location)
        order = Order.objects.create(work_queue=queue, order_type=Order.HOUSEKEEPING)
        leg = OrderLeg.objects.create(order=order, distance=120)

        order_list = self.client.get(
            reverse("minitos_core:order-list", args=[self.location.id, queue.id])
        )
        order_detail = self.client.get(
            reverse("minitos_core:order-detail", args=[self.location.id, queue.id, order.id])
        )
        response = self.client.get(
            reverse(
                "minitos_core:order-leg-list",
                args=[self.location.id, queue.id, order.id],
            )
        )
        detail = self.client.get(
            reverse(
                "minitos_core:order-leg-detail",
                args=[self.location.id, queue.id, order.id, leg.id],
            )
        )

        self.assertEqual(order_list.status_code, status.HTTP_200_OK, order_list.data)
        self.assertEqual(len(order_list.data), 1)
        self.assertEqual(order_detail.status_code, status.HTTP_200_OK, order_detail.data)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["order"], order.id)
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        self.assertEqual(detail.data["order"], order.id)
        self.assertEqual(OrderLeg.objects.get(pk=leg.pk), leg)

    def test_business_process_api_versions_and_executes_nodered_flow(self):
        hierarchy_response = self.client.post(
            reverse("minitos_core:process-hierarchy-list"),
            {
                "name": "Quay Operations",
                "code": "QUAY",
                "process_area": ProcessHierarchy.QUAY,
                "description": "Vessel-side operational processes",
            },
            format="json",
        )
        self.assertEqual(hierarchy_response.status_code, status.HTTP_201_CREATED, hierarchy_response.data)

        process_response = self.client.post(
            reverse("minitos_core:business-process-list"),
            {
                "name": "Vessel discharge exception handling",
                "code": "VESSEL_DISCHARGE_EXCEPTION",
                "hierarchy": hierarchy_response.data["id"],
                "location": self.location.id,
                "owner_role": "Quay Planner",
                "trigger_type": BusinessProcess.TRIGGER_MQTT,
                "input_schema": {"required": ["che_id", "container_visit_id"]},
                "output_schema": {"events": ["sop.updated"]},
            },
            format="json",
        )
        self.assertEqual(process_response.status_code, status.HTTP_201_CREATED, process_response.data)

        version_response = self.client.post(
            reverse("minitos_core:process-version-list"),
            {
                "business_process": process_response.data["id"],
                "version_label": "v1.0",
                "status": ProcessVersion.PUBLISHED,
                "is_current": True,
                "nodered_flow": [
                    {"id": "tab1", "type": "tab", "label": "Discharge exception"},
                    {"id": "mqtt-in", "type": "mqtt in", "topic": "minitos/che/+/ddat"},
                ],
                "input_signals": [{"type": "mqtt", "topic": "minitos/che/+/ddat"}],
                "output_signals": [{"type": "rest_api", "url": "/minitos_core/process-executions/"}],
                "sop_markdown": "1. Review the stopped CHE.\n2. Reassign the move if needed.",
                "mqtt_announcement_topic": "minitos/bpm/process-updated",
            },
            format="json",
        )
        self.assertEqual(version_response.status_code, status.HTTP_201_CREATED, version_response.data)
        self.assertEqual(version_response.data["version_number"], 1)
        self.assertEqual(version_response.data["created_by_username"], "regression-user")
        self.assertIsNotNone(version_response.data["published_at"])

        second_version = self.client.post(
            reverse("minitos_core:process-version-list"),
            {
                "business_process": process_response.data["id"],
                "version_label": "v1.1",
                "is_current": True,
                "nodered_flow": [],
            },
            format="json",
        )
        self.assertEqual(second_version.status_code, status.HTTP_201_CREATED, second_version.data)
        self.assertEqual(second_version.data["version_number"], 2)
        self.assertFalse(ProcessVersion.objects.get(pk=version_response.data["id"]).is_current)
        self.assertTrue(ProcessVersion.objects.get(pk=second_version.data["id"]).is_current)

        execute_response = self.client.post(
            reverse("minitos_core:process-version-execute", args=[second_version.data["id"]]),
            {
                "location": self.location.id,
                "trigger_source": "manual-test",
                "trigger_payload": {"che_id": "SC01"},
            },
            format="json",
        )
        self.assertEqual(execute_response.status_code, status.HTTP_201_CREATED, execute_response.data)
        self.assertEqual(execute_response.data["status"], BusinessProcessExecution.COMPLETED)
        self.assertEqual(execute_response.data["business_process_code"], "VESSEL_DISCHARGE_EXCEPTION")
        self.assertEqual(execute_response.data["trigger_payload"], {"che_id": "SC01"})
        self.assertIn("Node-RED runtime deployment is not enabled yet", execute_response.data["result_payload"]["message"])

    def test_process_version_api_rejects_invalid_flow_and_signal_shapes(self):
        process = BusinessProcess.objects.create(
            name="Gate appointment import",
            code="GATE_APPT_IMPORT",
            location=self.location,
        )

        invalid_flow = self.client.post(
            reverse("minitos_core:process-version-list"),
            {
                "business_process": process.id,
                "version_number": 1,
                "nodered_flow": "not-json-flow",
            },
            format="json",
        )
        self.assertEqual(invalid_flow.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("nodered_flow", invalid_flow.data)

        invalid_signals = self.client.post(
            reverse("minitos_core:process-version-list"),
            {
                "business_process": process.id,
                "version_number": 1,
                "nodered_flow": [],
                "input_signals": {"topic": "minitos/input"},
            },
            format="json",
        )
        self.assertEqual(invalid_signals.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("input_signals", invalid_signals.data)

    def _location(self, name, latitude, longitude):
        return Location.objects.create(
            name=name,
            location_type="TL",
            latitude=latitude,
            longitude=longitude,
            map_origin_latitude=latitude,
            map_origin_longitude=longitude,
        )

    def _carrier_service(self, name):
        return CarrierService.objects.create(
            name=name,
            location=self.location,
            start_date=date(2026, 1, 1),
        )

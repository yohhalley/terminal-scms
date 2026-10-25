import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    CommandResultReceipt,
    DispatchAssignment,
    EnterpriseProjection,
    EquipmentCommand,
    EquipmentState,
    ExecutionOperation,
    ExecutionOrder,
    InboxEvent,
    OutboxEvent,
    SimulationSession,
    SiteProjection,
)
from .mqtt import connect as connect_mqtt, create_client
from .services.dispatch import apply_command_event, dispatch_next_operation
from .services.ingestion import ingest_released_order
from .services.site_sync import (
    _snapshot_checksum,
    ingest_site_configuration_published,
    ingest_site_created,
)
from .services.simulations import (
    bootstrap_document,
    claim_next_session,
    start_simulation,
    stop_simulation,
)
from .services.sparkplug_runtime import process_sparkplug_message
from .sparkplug import decode_payload, encode_payload, metrics_by_name
from .uns import subscriptions


@override_settings(MQTT_HOST="broker", MQTT_PORT=1883)
class MQTTConnectionTests(SimpleTestCase):
    def test_connect_raises_immediately_when_retry_is_disabled(self):
        client = Mock()
        client.connect.side_effect = ConnectionRefusedError("broker down")

        with self.assertRaises(ConnectionRefusedError):
            connect_mqtt(client)

    def test_connect_retries_transient_socket_failure(self):
        client = Mock()
        client.connect.side_effect = [ConnectionRefusedError("broker down"), 0]

        with patch("execution.mqtt.time.sleep") as sleep:
            result = connect_mqtt(client, retry=True, retry_interval=0.25)

        self.assertEqual(result, 0)
        self.assertEqual(client.connect.call_count, 2)
        sleep.assert_called_once_with(0.25)

    @override_settings(MQTT_CLIENT_ID="oes", SPARKPLUG_HOST_ID="PRIMARY-OES")
    @patch("execution.mqtt.mqtt.Client")
    def test_host_application_has_retained_offline_will(self, client_class):
        client = client_class.return_value

        result = create_client("consumer", host_application=True)

        self.assertEqual(result, client)
        client.will_set.assert_called_once_with(
            "spBv1.0/STATE/PRIMARY-OES",
            payload="OFFLINE",
            qos=1,
            retain=True,
        )

    @override_settings(MQTT_CLIENT_ID="oes")
    @patch("execution.mqtt.mqtt.Client")
    def test_consumer_session_is_persistent_mqtt311_with_fixed_client_id(self, client_class):
        """TCL-028 / Q19: clean session false, fixed client id, MQTT 3.1.1."""
        from paho.mqtt import client as paho

        create_client("consumer", host_application=True, persistent_session=True)
        kwargs = client_class.call_args.kwargs
        self.assertEqual(kwargs["client_id"], "oes-consumer")
        self.assertEqual(kwargs["protocol"], paho.MQTTv311)
        self.assertIs(kwargs["clean_session"], False)

        create_client("outbox")
        self.assertIs(client_class.call_args.kwargs["clean_session"], True)

    @override_settings(SPARKPLUG_HOST_ID="PRIMARY-OES")
    @patch("execution.management.commands.run_mqtt_consumer.connect")
    @patch("execution.management.commands.run_mqtt_consumer.create_client")
    def test_consumer_publishes_retained_host_online_and_offline(self, create, connect):
        client = create.return_value
        publish_info = Mock()
        client.publish.return_value = publish_info
        reason_code = Mock(is_failure=False)

        def run_loop(**_kwargs):
            client.on_connect(client, None, None, reason_code, None)
            raise KeyboardInterrupt

        client.loop_forever.side_effect = run_loop
        call_command("run_mqtt_consumer", stdout=StringIO())

        create.assert_called_once_with(
            "consumer", host_application=True, persistent_session=True,
        )
        connect.assert_called_once_with(client, retry=True)
        online, offline = client.publish.call_args_list
        self.assertEqual(online.args, ("spBv1.0/STATE/PRIMARY-OES", "ONLINE"))
        self.assertEqual(online.kwargs, {"qos": 1, "retain": True})
        self.assertEqual(offline.args, ("spBv1.0/STATE/PRIMARY-OES", "OFFLINE"))
        self.assertEqual(offline.kwargs, {"qos": 1, "retain": True})
        publish_info.wait_for_publish.assert_called_once_with(timeout=5)
        client.disconnect.assert_called_once_with()


class SparkplugCodecTests(SimpleTestCase):
    def test_metric_payload_round_trip(self):
        payload = encode_payload([
            {"name": "State/PoweredOn", "type": "Boolean", "value": False},
            {"name": "Position/Latitude", "type": "Double", "value": 17.94},
            {"name": "Identity/Name", "type": "String", "value": "SC01"},
        ], sequence=19, timestamp_ms=123456)

        document = decode_payload(payload)

        self.assertEqual(document["timestamp"], 123456)
        self.assertEqual(document["seq"], 19)
        self.assertEqual(metrics_by_name(document)["State/PoweredOn"], False)
        self.assertAlmostEqual(metrics_by_name(document)["Position/Latitude"], 17.94)
        self.assertEqual(metrics_by_name(document)["Identity/Name"], "SC01")


class ExecutionServiceTests(TestCase):
    def released_order_payload(self, event_id=None):
        return {
            "schema": "scms.order.released.v1",
            "event_id": str(event_id or uuid.uuid4()),
            "occurred_at": timezone.now().isoformat(),
            "enterprise": {
                "id": str(self.enterprise.source_id),
                "code": self.enterprise.code,
                "name": self.enterprise.name,
            },
            "site": {
                "id": str(self.site.source_id),
                "code": self.site.code,
                "name": self.site.name,
                "source_location_id": self.site.source_location_id,
            },
            "terminal": {"id": 1, "code": "TEST"},
            "data": {
                "order": {
                    "id": 41,
                    "status": "released",
                    "priority": 20,
                    "container_number": 101,
                    "from_position": 11,
                    "to_position": 12,
                    "released_at": timezone.now().isoformat(),
                },
                "legs": [{
                    "id": 4101,
                    "sequence": 1,
                    "leg_kind": "move",
                    "status": "planned",
                    "equipment_type": 4,
                    "movement_mode": "road",
                    "start_position": 11,
                    "end_position": 12,
                    "source_inventory_position_id": 111,
                    "destination_inventory_position_id": 112,
                    "source_latitude": 17.9401,
                    "source_longitude": -102.1501,
                    "destination_latitude": 17.9408,
                    "destination_longitude": -102.1494,
                }],
            },
        }

    def equipment_payload(self):
        return {
            "schema": "scms.equipment.state.v1",
            "event_id": str(uuid.uuid4()),
            "enterprise": {
                "id": str(self.enterprise.source_id),
                "code": self.enterprise.code,
                "name": self.enterprise.name,
            },
            "site": {
                "id": str(self.site.source_id),
                "code": self.site.code,
                "name": self.site.name,
                "source_location_id": self.site.source_location_id,
            },
            "terminal": {"id": 1, "code": "TEST"},
            "data": {
                "che_id": 17,
                "name": "SC01",
                "equipment_type_id": 4,
                "equipment_type_code": "SC",
                "powered_on": True,
                "working": False,
                "is_available": True,
                "is_dispatched": False,
                "is_in_maintenance": False,
                "latitude": 17.94,
                "longitude": -102.15,
                "rotation": 90.0,
                "fuel_level": 80.0,
                "energy_level": 76.5,
                "speed_mps": 2.25,
                "timestamp": timezone.now().isoformat(),
            },
        }

    def setUp(self):
        self.enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="enterprise-a", name="Enterprise A",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=self.enterprise,
            source_location_id=1, code="site-a", name="Site A",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=1, snapshot_checksum="a" * 64,
        )
        self.order = ingest_released_order(
            "scms/v2/enterprise-a/site-a/tos/orders/41/released",
            self.released_order_payload(),
        )
        equipment_data = self.equipment_payload()["data"]
        self.equipment = EquipmentState.objects.create(
            site=self.site,
            tos_che_id=equipment_data["che_id"],
            name=equipment_data["name"],
            equipment_type_id=equipment_data["equipment_type_id"],
            equipment_type_code=equipment_data["equipment_type_code"],
            powered_on=True,
            is_available=True,
            latitude=equipment_data["latitude"],
            longitude=equipment_data["longitude"],
            rotation=equipment_data["rotation"],
            fuel_level=equipment_data["fuel_level"],
            energy_level=equipment_data["energy_level"],
            speed_mps=equipment_data["speed_mps"],
            source_timestamp=timezone.now(),
            last_seen_at=timezone.now(),
        )
        self.session = SimulationSession.objects.create(
            site=self.site,
            configuration_revision=1,
            configuration_checksum="a" * 64,
            sparkplug_group_id="ENTERPRISE_A",
            sparkplug_node_id="SCMS_ENTERPRISE_A_SITE_A",
            actual_state=SimulationSession.RUNNING,
            node_connected=True,
        )
        self.equipment.simulation_session = self.session
        self.equipment.sparkplug_device_id = "CHE_17"
        self.equipment.device_connected = True
        self.equipment.data_valid = True
        self.equipment.save(update_fields=[
            "simulation_session", "sparkplug_device_id", "device_connected", "data_valid",
        ])

    def test_released_order_ingestion_is_idempotent(self):
        event_id = uuid.uuid4()
        payload = self.released_order_payload(event_id)
        payload["data"]["order"]["id"] = 42
        payload["data"]["legs"][0]["id"] = 4201
        first = ingest_released_order("topic", payload)
        second = ingest_released_order("topic", payload)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ExecutionOrder.objects.filter(site=self.site, tos_order_id=42).count(), 1,
        )
        self.assertEqual(InboxEvent.objects.filter(event_id=event_id).count(), 1)

    def test_dispatch_creates_assignment_command_and_outbox(self):
        assignment = dispatch_next_operation(self.order.pk)

        self.assertEqual(assignment.equipment, self.equipment)
        self.assertEqual(assignment.operation.status, ExecutionOperation.DISPATCHED)
        self.order.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(self.order.status, ExecutionOrder.IN_PROGRESS)
        self.assertTrue(self.equipment.is_dispatched)
        self.assertFalse(self.equipment.is_available)
        command = assignment.commands.get()
        self.assertEqual(command.payload["data"]["destination_position_id"], 12)
        self.assertEqual(command.payload["data"]["destination_inventory_position_id"], 112)
        self.assertEqual(command.payload["data"]["destination_latitude"], 17.9408)
        self.assertEqual(command.payload["data"]["destination_longitude"], -102.1494)
        self.assertEqual(command.simulation_session, self.session)
        self.assertTrue(command.topic.endswith("/DCMD/SCMS_ENTERPRISE_A_SITE_A/CHE_17"))
        # TCL-028: expiry on the Sim/TimeMs clock, default 600 s of sim time.
        self.assertIsNone(command.expires_at)
        self.assertEqual(command.expires_at_sim_ms, 600_000)
        self.assertTrue(OutboxEvent.objects.get(event_id=command.command_id).payload_bytes)
        self.assertEqual(OutboxEvent.objects.filter(event_id=command.command_id).count(), 1)

    def test_execution_events_carry_terminal_id_for_minitos(self):
        """B3 (TCL-005): MiniTOS rejects an execution event without terminal.id."""
        dispatch_next_operation(self.order.pk)
        event = OutboxEvent.objects.get(schema="scms.execution.event.v1")
        self.assertEqual(
            event.payload["terminal"],
            {"id": self.site.source_location_id, "code": self.site.code},
        )

    def test_command_events_complete_operation_and_release_equipment(self):
        assignment = dispatch_next_operation(self.order.pk)
        command = assignment.commands.get()

        apply_command_event(command.command_id, "started")
        assignment.operation.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(assignment.operation.status, ExecutionOperation.WORKING)
        self.assertTrue(self.equipment.working)

        apply_command_event(command.command_id, "completed")
        assignment.operation.refresh_from_db()
        self.order.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(assignment.operation.status, ExecutionOperation.COMPLETE)
        self.assertEqual(self.order.status, ExecutionOrder.COMPLETE)
        self.assertFalse(self.equipment.working)
        self.assertFalse(self.equipment.is_dispatched)
        self.assertTrue(self.equipment.is_available)

    def test_stale_equipment_is_not_dispatched(self):
        self.equipment.last_seen_at = timezone.now() - timedelta(minutes=2)
        self.equipment.save(update_fields=["last_seen_at"])
        with self.assertRaisesMessage(ValidationError, "No fresh, available CHE"):
            dispatch_next_operation(self.order.pk)
        self.assertFalse(DispatchAssignment.objects.exists())

    def test_overlapping_local_ids_are_isolated_by_site_uuid(self):
        enterprise_b = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="enterprise-b", name="Enterprise B",
        )
        site_b = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=enterprise_b,
            source_location_id=1, code="site-b", name="Site B",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=1, snapshot_checksum="b" * 64,
        )
        payload = self.released_order_payload()
        payload["enterprise"] = {
            "id": str(enterprise_b.source_id), "code": enterprise_b.code,
            "name": enterprise_b.name,
        }
        payload["site"] = {
            "id": str(site_b.source_id), "code": site_b.code,
            "name": site_b.name, "source_location_id": 1,
        }

        other = ingest_released_order(
            "scms/v2/enterprise-b/site-b/tos/orders/41/released", payload,
        )

        self.assertNotEqual(other.pk, self.order.pk)
        self.assertEqual(ExecutionOrder.objects.filter(tos_order_id=41).count(), 2)
        self.assertEqual({self.order.site_id, other.site_id}, {self.site.pk, site_b.pk})


class EquipmentConsoleTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="dispatcher",
            password="secret",
        )
        enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="terminal-velocity", name="Terminal Velocity",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=enterprise,
            source_location_id=1, code="test-terminal", name="Test Terminal",
        )
        self.equipment = EquipmentState.objects.create(
            site=self.site,
            tos_che_id=17,
            name="SC01",
            equipment_type_code="SC",
            powered_on=True,
            is_available=True,
            latitude=17.94,
            longitude=-102.15,
            rotation=90.0,
            fuel_level=80.0,
            energy_level=75.0,
            source_timestamp=timezone.now(),
            last_seen_at=timezone.now(),
        )

    def test_console_requires_login(self):
        response = self.client.get(reverse("equipment-console-list"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('equipment-console-list')}",
        )

    def test_console_renders_equipment_map_and_telemetry(self):
        self.client.force_login(self.user)
        response = self.client.get(
            reverse("equipment-console-list"),
            {"equipment": self.equipment.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "SC01")
        self.assertContains(response, "equipment-map-data")
        self.assertContains(response, "75.0%")

    def test_console_manual_override_records_operator_and_source(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("equipment-console-list"), {
            "equipment_id": self.equipment.pk,
            "powered_on": "on",
            "is_available": "on",
            "latitude": "17.9500000",
            "longitude": "-102.1400000",
            "rotation": "135.0",
            "fuel_level": "72.0",
            "energy_level": "68.0",
            "speed_mps": "0.0",
        })

        self.assertRedirects(
            response,
            f"{reverse('equipment-console-list')}?equipment={self.equipment.pk}",
        )
        self.equipment.refresh_from_db()
        self.assertEqual(self.equipment.state_source, EquipmentState.MANUAL)
        self.assertEqual(self.equipment.manual_override_by, "dispatcher")
        self.assertEqual(self.equipment.latitude, 17.95)
        self.assertEqual(self.equipment.energy_level, 68.0)


@override_settings(OES_API_TOKEN="secret", DEBUG=False)
class APIAuthenticationTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_internal_api_requires_service_token(self):
        response = self.client.get(reverse("execution-order-list"))
        self.assertEqual(response.status_code, 403)
        response = self.client.get(
            reverse("execution-order-list"),
            HTTP_X_OES_TOKEN="secret",
        )
        self.assertEqual(response.status_code, 200)

    def test_health_is_public(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["service"], "terminal-oes")


class SiteSynchronizationTests(TestCase):
    def site_event(self, *, schema='scms.site.created.v1', event_id=None, revision=0):
        payload = {
            'schema': schema,
            'event_id': str(event_id or uuid.uuid4()),
            'occurred_at': timezone.now().isoformat(),
            'enterprise': {
                'id': '8bc8db6e-e9f3-4d66-a019-394fd605f9b6',
                'code': 'terminal-velocity',
                'name': 'Terminal Velocity',
            },
            'site': {
                'id': '90506bb9-065d-4231-8a16-7b90da9d83f4',
                'code': 'test-terminal',
                'name': 'Test Terminal',
                'source_location_id': 7,
                'location_type': 'TL',
            },
            'data': {
                'lifecycle_status': 'draft',
                'configuration_revision': revision,
                'simulation_enabled': True,
            },
        }
        return payload

    def snapshot(self, revision=1):
        return {
            'schema': 'scms.site.execution-snapshot.v1',
            'generated_at': timezone.now().isoformat(),
            'enterprise': {
                'id': '8bc8db6e-e9f3-4d66-a019-394fd605f9b6',
                'code': 'terminal-velocity',
                'name': 'Terminal Velocity',
            },
            'site': {
                'id': '90506bb9-065d-4231-8a16-7b90da9d83f4',
                'code': 'test-terminal',
                'name': 'Test Terminal',
                'source_location_id': 7,
                'lifecycle_status': 'published',
                'configuration_revision': revision,
                'simulation_enabled': True,
            },
            'location': {'id': 7, 'name': 'Test Terminal'},
            'layout': {'version': {'id': 11, 'version': 3}},
            'equipment': {
                'types': [{'id': 4, 'equipment_type': 'SC'}],
                'ches': [{
                    'id': 17,
                    'name': 'SC01',
                    'equipment_type_id': 4,
                    'powered_on': False,
                    'working': False,
                    'is_available': False,
                    'is_dispatched': False,
                    'is_in_maintenance': False,
                    'latitude': 17.94,
                    'longitude': -102.15,
                    'rotation': 90.0,
                }],
            },
        }

    def configuration_event(self, revision=1, *, event_id=None, checksum=None):
        snapshot = self.snapshot(revision)
        payload = self.site_event(
            schema='scms.site.configuration.published.v1',
            event_id=event_id,
            revision=revision,
        )
        payload['data'] = {
            'configuration_revision': revision,
            'layout_version_id': 11,
            'snapshot_schema': snapshot['schema'],
            'snapshot_checksum': checksum or _snapshot_checksum(snapshot),
            'snapshot_path': f'/minitos_core/sites/{payload["site"]["id"]}/execution-snapshot/?revision={revision}',
        }
        return payload, snapshot

    def test_site_created_is_idempotent(self):
        event_id = uuid.uuid4()
        payload = self.site_event(event_id=event_id)

        first = ingest_site_created('site/topic', payload)
        second = ingest_site_created('site/topic', payload)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EnterpriseProjection.objects.count(), 1)
        self.assertEqual(SiteProjection.objects.count(), 1)
        self.assertEqual(InboxEvent.objects.filter(event_id=event_id).count(), 1)

    def test_configuration_event_fetches_snapshot_and_marks_site_ready(self):
        payload, snapshot = self.configuration_event()
        loader = Mock(return_value={
            'site': payload['site']['id'],
            'revision': 1,
            'layout_version': 11,
            'schema': snapshot['schema'],
            'checksum': payload['data']['snapshot_checksum'],
            'payload': snapshot,
        })

        site = ingest_site_configuration_published(
            'configuration/topic', payload, snapshot_loader=loader,
        )

        self.assertEqual(site.projection_status, SiteProjection.READY)
        self.assertEqual(site.source_revision, 1)
        self.assertEqual(site.snapshot_payload, snapshot)
        equipment = EquipmentState.objects.get(site=site, tos_che_id=17)
        self.assertEqual(equipment.equipment_type_code, 'SC')
        self.assertEqual(equipment.state_source, EquipmentState.IMPORT)
        loader.assert_called_once_with(payload['data']['snapshot_path'])

    def test_stale_configuration_event_does_not_refetch_or_regress(self):
        payload, snapshot = self.configuration_event()
        document = {
            'revision': 1,
            'checksum': payload['data']['snapshot_checksum'],
            'payload': snapshot,
        }
        site = ingest_site_configuration_published(
            'configuration/topic', payload, snapshot_loader=Mock(return_value=document),
        )
        stale_payload, _ = self.configuration_event(revision=1)
        stale_loader = Mock(side_effect=AssertionError('stale event fetched a snapshot'))

        unchanged = ingest_site_configuration_published(
            'configuration/topic', stale_payload, snapshot_loader=stale_loader,
        )

        self.assertEqual(unchanged.pk, site.pk)
        self.assertEqual(unchanged.source_revision, 1)
        stale_loader.assert_not_called()

    def test_checksum_failure_keeps_projection_unready_and_records_failure(self):
        payload, snapshot = self.configuration_event(checksum='0' * 64)
        loader = Mock(return_value={
            'revision': 1,
            'checksum': '0' * 64,
            'payload': snapshot,
        })

        with self.assertRaisesMessage(ValidationError, 'checksum'):
            ingest_site_configuration_published(
                'configuration/topic', payload, snapshot_loader=loader,
            )

        site = SiteProjection.objects.get(source_id=payload['site']['id'])
        inbox = InboxEvent.objects.get(event_id=payload['event_id'])
        self.assertEqual(site.projection_status, SiteProjection.ERROR)
        self.assertEqual(site.source_revision, 0)
        self.assertEqual(inbox.status, InboxEvent.FAILED)

    def test_consumer_subscribes_to_v2_and_sparkplug_topics_only(self):
        topics = {topic for topic, _qos in subscriptions()}

        self.assertIn('scms/v2/+/+/tos/site/created', topics)
        self.assertIn('scms/v2/+/+/tos/configuration/published', topics)
        self.assertIn('scms/v2/+/+/tos/orders/+/released', topics)
        self.assertNotIn('scms/v1/+/tos/orders/+/released', topics)
        self.assertIn('spBv1.0/+/NBIRTH/+', topics)
        self.assertIn('spBv1.0/+/DDATA/+/+', topics)


@override_settings(
    OES_API_TOKEN="oes-secret",
    SIMULATOR_RUNNER_TOKEN="runner-secret",
    SIMULATOR_OES_URL="http://terminal-oes:8000",
    DEBUG=False,
)
class SimulationSessionTests(TestCase):
    def setUp(self):
        self.enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="terminal-velocity", name="Terminal Velocity",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=self.enterprise,
            source_location_id=1, code="site-a", name="Site A",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=3, snapshot_checksum="a" * 64,
            snapshot_payload={
                "schema": "scms.site.execution-snapshot.v1",
                "site": {
                    "id": "unused-by-session-service",
                    "configuration_revision": 3,
                },
                "equipment": {"ches": []},
            },
        )
        self.api = APIClient()

    def test_draft_or_stale_site_cannot_start(self):
        self.site.projection_status = SiteProjection.DRAFT
        self.site.save(update_fields=["projection_status"])
        with self.assertRaisesMessage(ValidationError, "ready Site"):
            start_simulation(self.site.pk)
        self.site.projection_status = SiteProjection.READY
        self.site.snapshot_payload["site"]["configuration_revision"] = 2
        self.site.save(update_fields=["projection_status", "snapshot_payload"])
        with self.assertRaisesMessage(ValidationError, "stale"):
            start_simulation(self.site.pk)

    def test_ready_site_creates_exactly_one_pending_session(self):
        first, created = start_simulation(self.site.pk, simulator_version="test", time_scale=2)
        second, created_again = start_simulation(self.site.pk)

        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.actual_state, SimulationSession.PENDING)
        self.assertEqual(first.configuration_revision, 3)
        self.assertEqual(SimulationSession.objects.count(), 1)

    def test_claim_issues_short_lived_token_and_bootstrap_is_frozen(self):
        EquipmentState.objects.create(
            site=self.site,
            tos_che_id=17, name="SC01", last_seen_at=timezone.now(),
        )
        session, _ = start_simulation(self.site.pk)
        claimed, token = claim_next_session("runner-a")

        self.assertEqual(claimed.pk, session.pk)
        self.assertEqual(claimed.actual_state, SimulationSession.CLAIMED)
        self.assertNotEqual(claimed.token_hash, token)
        self.assertNotIn(token, claimed.token_hash)
        document = bootstrap_document(claimed, token)
        self.assertEqual(document["session"]["id"], str(session.pk))
        self.assertEqual(document["session"]["configuration_revision"], 3)
        self.assertEqual(document["equipment_state"][0]["tos_che_id"], 17)

        self.site.source_revision = 4
        self.site.snapshot_payload = {"site": {"configuration_revision": 4}}
        self.site.save(update_fields=["source_revision", "snapshot_payload"])
        frozen = bootstrap_document(claimed, token)
        self.assertEqual(frozen["session"]["configuration_revision"], 3)

    def test_start_claim_bootstrap_and_stop_api_contract(self):
        start = self.api.post(
            reverse("site-projection-start-simulation", args=[self.site.pk]),
            {"simulator_version": "4.2.1", "time_scale": 5},
            format="json", HTTP_X_OES_TOKEN="oes-secret",
        )
        self.assertEqual(start.status_code, 201, start.data)
        session_id = start.data["id"]

        denied_claim = self.api.post(
            reverse("simulation-session-claim"),
            {"runner_identity": "runner-a"}, format="json",
        )
        self.assertEqual(denied_claim.status_code, 403)
        claim = self.api.post(
            reverse("simulation-session-claim"),
            {"runner_identity": "runner-a"}, format="json",
            HTTP_X_RUNNER_TOKEN="runner-secret",
        )
        self.assertEqual(claim.status_code, 200, claim.data)
        token = claim.data["environment"]["SIMULATOR_TOKEN"]
        self.assertNotIn("MINITOS_URL", claim.data["environment"])

        denied_bootstrap = self.api.get(
            reverse("simulation-session-bootstrap", args=[session_id]),
            HTTP_X_SIMULATION_TOKEN="wrong",
        )
        self.assertEqual(denied_bootstrap.status_code, 403)
        bootstrap = self.api.get(
            reverse("simulation-session-bootstrap", args=[session_id]),
            HTTP_X_SIMULATION_TOKEN=token,
        )
        self.assertEqual(bootstrap.status_code, 200, bootstrap.data)
        self.assertEqual(bootstrap.data["session"]["site_id"], str(self.site.source_id))

        stopped = self.api.post(
            reverse("simulation-session-stop", args=[session_id]), {},
            format="json", HTTP_X_OES_TOKEN="oes-secret",
        )
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.data["desired_state"], SimulationSession.STOPPED_DESIRED)

    def test_simulation_console_requires_login_and_shows_readiness(self):
        response = self.client.get(reverse("simulation-console"))
        self.assertRedirects(
            response, f"{reverse('login')}?next={reverse('simulation-console')}",
        )
        user = get_user_model().objects.create_user("sim-operator", password="secret")
        self.client.force_login(user)
        response = self.client.get(reverse("simulation-console"))
        self.assertContains(response, "Site A")
        self.assertContains(response, "ready")

    def test_pending_session_can_be_stopped_without_runner(self):
        session, _ = start_simulation(self.site.pk)
        stopped = stop_simulation(session.pk)
        self.assertEqual(stopped.actual_state, SimulationSession.STOPPED)
        self.assertEqual(stopped.runner_status, SimulationSession.RUNNER_STOPPED)


class SparkplugRuntimeTests(TestCase):
    def setUp(self):
        enterprise = EnterpriseProjection.objects.create(
            source_id=uuid.uuid4(), code="terminal-velocity", name="Terminal Velocity",
        )
        self.site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=enterprise,
            source_location_id=1, code="test-terminal", name="Test Terminal",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=7, snapshot_checksum="7" * 64,
        )
        self.session = SimulationSession.objects.create(
            site=self.site,
            configuration_revision=7,
            configuration_checksum="7" * 64,
            sparkplug_group_id="TERMINAL_VELOCITY",
            sparkplug_node_id="SCMS_TERMINAL_VELOCITY_TEST_TERMINAL",
            actual_state=SimulationSession.STARTING,
        )
        self.equipment = EquipmentState.objects.create(
            site=self.site,
            tos_che_id=17, name="SC01", equipment_type_id=4,
            equipment_type_code="SC", last_seen_at=timezone.now(),
        )
        self.order = ExecutionOrder.objects.create(
            site=self.site,
            tos_order_id=41, status=ExecutionOrder.RELEASED,
            released_at=timezone.now(),
        )
        ExecutionOperation.objects.create(
            order=self.order, tos_leg_id=4101, sequence=1,
            required_equipment_type_id=4,
            destination_latitude=17.941,
            destination_longitude=-102.149,
        )

    @property
    def node_topic(self):
        return "spBv1.0/TERMINAL_VELOCITY/{}/{}".format(
            "NBIRTH", self.session.sparkplug_node_id,
        )

    @property
    def device_topic_prefix(self):
        return "spBv1.0/TERMINAL_VELOCITY/{{}}/{}/CHE_17".format(
            self.session.sparkplug_node_id,
        )

    def _birth_node(self):
        process_sparkplug_message(self.node_topic, encode_payload([
            {"name": "bdSeq", "type": "Int64", "value": 3},
            {"name": "Session/Id", "type": "String", "value": str(self.session.pk)},
            {"name": "Site/Id", "type": "String", "value": str(self.site.source_id)},
            {"name": "Configuration/Revision", "type": "Int64", "value": 7},
            {"name": "Configuration/Checksum", "type": "String", "value": "7" * 64},
        ], sequence=0))

    def _birth_device(self, sequence=1):
        process_sparkplug_message(
            self.device_topic_prefix.format("DBIRTH"),
            encode_payload([
                {"name": "Identity/Id", "type": "Int64", "value": 17},
                {"name": "Identity/Name", "type": "String", "value": "SC01"},
                {"name": "State/PoweredOn", "type": "Boolean", "value": True},
                {"name": "State/Working", "type": "Boolean", "value": False},
                {"name": "State/IsAvailable", "type": "Boolean", "value": True},
                {"name": "State/IsDispatched", "type": "Boolean", "value": False},
                {"name": "State/InMaintenance", "type": "Boolean", "value": False},
            ], sequence=sequence),
        )

    def test_nbirth_and_dbirth_bind_site_session_and_device(self):
        self._birth_node()
        self._birth_device()

        self.session.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(self.session.actual_state, SimulationSession.RUNNING)
        self.assertTrue(self.session.node_connected)
        self.assertEqual(self.session.sparkplug_bdseq, 3)
        self.assertEqual(self.equipment.simulation_session, self.session)
        self.assertEqual(self.equipment.sparkplug_device_id, "CHE_17")
        self.assertTrue(self.equipment.device_connected)
        self.assertTrue(self.equipment.data_valid)

    def test_site_birth_does_not_activate_another_site_with_overlapping_ids(self):
        other_site = SiteProjection.objects.create(
            source_id=uuid.uuid4(), enterprise=self.site.enterprise,
            source_location_id=1, code="other-terminal", name="Other Terminal",
            lifecycle_status="published", projection_status=SiteProjection.READY,
            source_revision=7, snapshot_checksum="8" * 64,
        )
        other_session = SimulationSession.objects.create(
            site=other_site,
            configuration_revision=7,
            configuration_checksum="8" * 64,
            sparkplug_group_id="TERMINAL_VELOCITY",
            sparkplug_node_id="SCMS_TERMINAL_VELOCITY_OTHER_TERMINAL",
            actual_state=SimulationSession.STARTING,
        )
        other_equipment = EquipmentState.objects.create(
            site=other_site,
            tos_che_id=17,
            name="SC01-B",
            equipment_type_id=4,
            equipment_type_code="SC",
            last_seen_at=timezone.now(),
        )

        self._birth_node()
        self._birth_device()

        other_session.refresh_from_db()
        other_equipment.refresh_from_db()
        self.assertEqual(other_session.actual_state, SimulationSession.STARTING)
        self.assertFalse(other_session.node_connected)
        self.assertIsNone(other_equipment.simulation_session)
        self.assertFalse(other_equipment.device_connected)

    def test_power_off_is_valid_ddata_not_device_death(self):
        self._birth_node()
        self._birth_device()

        process_sparkplug_message(
            self.device_topic_prefix.format("DDATA"),
            encode_payload([
                {"name": "State/PoweredOn", "type": "Boolean", "value": False},
                {"name": "State/Working", "type": "Boolean", "value": False},
                {"name": "State/IsAvailable", "type": "Boolean", "value": False},
            ], sequence=2),
        )

        self.equipment.refresh_from_db()
        self.assertFalse(self.equipment.powered_on)
        self.assertTrue(self.equipment.device_connected)
        self.assertTrue(self.equipment.data_valid)
        self.assertIsNone(self.equipment.last_death_at)

    def test_duplicate_command_result_is_applied_once(self):
        self._birth_node()
        self._birth_device()
        assignment = dispatch_next_operation(self.order.pk)
        command = assignment.commands.get()
        result_id = uuid.uuid4()
        result_metrics = [
            {"name": "State/PoweredOn", "type": "Boolean", "value": True},
            {"name": "State/Working", "type": "Boolean", "value": False},
            {"name": "State/IsAvailable", "type": "Boolean", "value": True},
            {"name": "Command/Result/Id", "type": "String", "value": str(result_id)},
            {
                "name": "Command/Result/CommandId",
                "type": "String",
                "value": str(command.command_id),
            },
            {"name": "Command/Result/Event", "type": "String", "value": "completed"},
        ]
        topic = self.device_topic_prefix.format("DDATA")

        process_sparkplug_message(topic, encode_payload(result_metrics, sequence=2))
        process_sparkplug_message(topic, encode_payload(result_metrics, sequence=3))

        command.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(command.status, EquipmentCommand.COMPLETE)
        self.assertEqual(self.order.status, ExecutionOrder.COMPLETE)
        self.assertEqual(CommandResultReceipt.objects.filter(pk=result_id).count(), 1)

    def test_node_death_invalidates_equipment_and_blocks_dispatch(self):
        self._birth_node()
        self._birth_device()
        process_sparkplug_message(
            self.node_topic.replace("NBIRTH", "NDEATH"),
            encode_payload(
                [{"name": "bdSeq", "type": "Int64", "value": 3}],
                include_sequence=False,
            ),
        )

        self.session.refresh_from_db()
        self.equipment.refresh_from_db()
        self.assertEqual(self.session.actual_state, SimulationSession.STARTING)
        self.assertFalse(self.session.node_connected)
        self.assertFalse(self.equipment.device_connected)
        self.assertFalse(self.equipment.data_valid)
        with self.assertRaisesMessage(ValidationError, "no connected, running"):
            dispatch_next_operation(self.order.pk)

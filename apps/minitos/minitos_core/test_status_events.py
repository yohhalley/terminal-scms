"""TCL-003b: visit status-changed milestone events (D-024, D-026).

Each status change of a CarrierVisit or ContainerVisit writes one
scms.visit.status_changed.v1 outbox row in the same transaction as the change
(same-transaction outbox, as order.released). captureOnCommitCallbacks is kept
around the saves to prove that nothing is deferred to on_commit any more.
"""

import json
import uuid
from unittest import mock

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.test import APIClient

from minitos_core.models import (
    CarrierVisit,
    ContainerVisit,
    IntegrationOutbox,
    Location,
    Order,
)
from minitos_core.scenarios import reset_scenario
from minitos_core.services import integration_events, status_events
from minitos_core.services.status_events import SCHEMA, status_event_source

User = get_user_model()


def status_rows():
    return list(IntegrationOutbox.objects.filter(schema=SCHEMA).order_by('created_at', 'pk'))


class StatusEventTestBase(TestCase):
    def setUp(self):
        with self.captureOnCommitCallbacks(execute=True):
            reset_scenario('import_v1')
        self.visit = CarrierVisit.objects.get()
        self.container_visit = ContainerVisit.objects.get()
        self.site = self.visit.location.site
        IntegrationOutbox.objects.filter(schema=SCHEMA).delete()

    def save_status(self, instance, new_status, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            instance.status = new_status
            instance.save(**kwargs)

    def topic(self, kind, pk):
        return f'scms/v2/{self.site.enterprise.code}/{self.site.code}/tos/{kind}/{pk}/status-changed'


class VesselVisitEventTests(StatusEventTestBase):
    def test_transition_publishes_one_event_with_timestamp(self):
        before = timezone.now()
        self.save_status(self.visit, CarrierVisit.IN_OPERATION)

        [row] = status_rows()
        self.assertEqual(row.topic, self.topic('carrier-visits', self.visit.pk))
        self.assertEqual((row.qos, row.retain), (1, False))
        payload = row.payload
        self.assertEqual(payload['schema'], SCHEMA)
        self.assertEqual(str(row.event_id), payload['event_id'])
        self.assertEqual(payload['terminal']['id'], self.visit.location_id)
        self.assertEqual(payload['site']['code'], self.site.code)
        data = payload['data']
        self.assertEqual(
            {key: data[key] for key in ('entity_type', 'entity_id', 'entity_code', 'from_status', 'to_status')},
            {
                'entity_type': 'carrier_visit', 'entity_id': self.visit.pk, 'entity_code': 'V001',
                'from_status': 'Arrived', 'to_status': 'InOperation',
            },
        )
        self.assertEqual(data['carrier_visit']['visit_type'], 'Vessel')
        self.assertNotIn('container_visit', data)
        self.assertEqual(data['source'], 'minitos.system')
        self.assertIsNone(data['sim_time_ms'])
        self.assertNotIn('causation_id', data)
        self.assertEqual(payload['aggregate'], {
            'type': 'carrier_visit', 'id': self.visit.pk, 'revision': 2,
        })
        self.assertIsNone(payload['causation_id'])
        self.assertEqual(payload['correlation_id'], payload['event_id'])
        occurred_at = parse_datetime(payload['occurred_at'])
        self.assertIsNotNone(occurred_at.tzinfo)
        self.assertTrue(before <= occurred_at <= timezone.now())

    def test_each_milestone_is_one_event_in_order(self):
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            for new_status in ('InOperation', 'Complete', 'Departed'):
                self.visit.status = new_status
                self.visit.save()

        transitions = [(row.payload['data']['from_status'], row.payload['data']['to_status']) for row in status_rows()]
        self.assertEqual(transitions, [
            ('Arrived', 'InOperation'), ('InOperation', 'Complete'), ('Complete', 'Departed'),
        ])
        times = [parse_datetime(row.payload['occurred_at']) for row in status_rows()]
        self.assertEqual(times, sorted(times))
        self.assertEqual(len({row.event_id for row in status_rows()}), 3)
        self.assertEqual([row.payload['aggregate']['revision'] for row in status_rows()], [2, 3, 4])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status_revision, 4)

    def test_stale_instance_does_not_reuse_a_revision(self):
        stale = CarrierVisit.objects.get(pk=self.visit.pk)
        self.save_status(self.visit, CarrierVisit.IN_OPERATION)
        self.save_status(stale, CarrierVisit.COMPLETE)
        self.assertEqual([row.payload['aggregate']['revision'] for row in status_rows()], [2, 3])
        stale.vessel_name = 'RENAMED'
        stale.save()
        stale.refresh_from_db()
        self.assertEqual(stale.status_revision, 3)

    def test_transition_api_publishes_with_api_source(self):
        user = User.objects.create_superuser('tcl003b-api', 'api@terminal-scms.invalid', 'test-password')
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse('minitos_core:transition_visit_status', args=[self.visit.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(
                url, {'status': 'InOperation'}, format='json', HTTP_X_CORRELATION_ID='ui-click-42',
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        [row] = status_rows()
        self.assertEqual(row.payload['data']['source'], 'minitos.api')
        self.assertEqual(row.payload['data']['to_status'], 'InOperation')
        self.assertEqual(row.payload['correlation_id'], 'ui-click-42')
        self.assertIsNone(row.payload['causation_id'])

    def test_transition_api_is_one_transaction_with_its_outbox_row(self):
        user = User.objects.create_superuser('tcl003b-atomic', 'atomic@terminal-scms.invalid', 'test-password')
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse('minitos_core:transition_visit_status', args=[self.visit.pk])
        with (
            mock.patch.object(IntegrationOutbox.objects, 'create', side_effect=RuntimeError('outbox down')),
            self.assertLogs('django.request', level='ERROR'),
            self.assertRaises(RuntimeError),
        ):
            client.post(url, {'status': 'InOperation'}, format='json')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertEqual(self.visit.status_revision, 1)
        self.assertEqual(status_rows(), [])

    def test_oversized_correlation_header_is_ignored(self):
        user = User.objects.create_superuser('tcl003b-corr', 'corr@terminal-scms.invalid', 'test-password')
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse('minitos_core:transition_visit_status', args=[self.visit.pk])
        client.post(url, {'status': 'InOperation'}, format='json', HTTP_X_CORRELATION_ID='x' * 129)
        [row] = status_rows()
        self.assertEqual(row.payload['correlation_id'], row.payload['event_id'])

    def test_transition_api_same_status_publishes_nothing(self):
        user = User.objects.create_superuser('tcl003b-noop', 'noop@terminal-scms.invalid', 'test-password')
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse('minitos_core:transition_visit_status', args=[self.visit.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(url, {'status': 'Arrived'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(status_rows(), [])

    def test_admin_change_page_publishes_with_admin_source(self):
        user = User.objects.create_superuser('tcl003b-admin', 'admin@terminal-scms.invalid', 'test-password')
        self.client.force_login(user)
        model_admin = admin.site._registry[CarrierVisit]
        change_url = reverse('admin:minitos_core_carriervisit_change', args=[self.visit.pk])
        request = RequestFactory().get(change_url)
        request.user = user
        form = model_admin.get_form(request, self.visit)(instance=self.visit)
        data = {}
        for name, field in form.fields.items():
            value = form[name].value()
            if value is None or hasattr(field, 'fields'):
                continue
            data[name] = value
        data['status'] = 'InOperation'
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(change_url, data)
        self.assertEqual(response.status_code, 302, response.content[:2000])
        [row] = status_rows()
        self.assertEqual(row.payload['data']['source'], 'minitos.admin')
        self.assertEqual(row.payload['data']['to_status'], 'InOperation')


class ContainerVisitEventTests(StatusEventTestBase):
    def test_container_transition_publishes_d010_fields(self):
        self.save_status(self.container_visit, ContainerVisit.ARRIVED)

        [row] = status_rows()
        self.assertEqual(row.topic, self.topic('container-visits', self.container_visit.pk))
        data = row.payload['data']
        self.assertEqual(data['entity_type'], 'container_visit')
        self.assertEqual(data['entity_id'], self.container_visit.pk)
        self.assertEqual(data['entity_code'], self.container_visit.container_number_id)
        self.assertEqual((data['from_status'], data['to_status']), ('Preadvised', 'Arrived'))
        self.assertEqual(row.payload['aggregate'], {
            'type': 'container_visit', 'id': self.container_visit.pk, 'revision': 2,
        })
        self.assertEqual(data['container_visit'], {
            'container_number': self.container_visit.container_number_id,
            'category': 'I',
            'ib_carrier': {'id': self.visit.pk, 'name': 'V001', 'visit_type': 'Vessel'},
            'ob_carrier': None,
        })
        self.assertNotIn('carrier_visit', data)

    def test_update_fields_with_status_publishes(self):
        self.save_status(self.container_visit, ContainerVisit.AT_YARD, update_fields=['status', 'last_updated'])
        [row] = status_rows()
        self.assertEqual(row.payload['data']['to_status'], 'AtYard')

    def test_container_transition_api_publishes(self):
        user = User.objects.create_superuser('tcl003b-cv', 'cv@terminal-scms.invalid', 'test-password')
        client = APIClient()
        client.force_authenticate(user=user)
        url = reverse('minitos_core:transition_container_status', args=[self.container_visit.pk])
        with self.captureOnCommitCallbacks(execute=True):
            response = client.post(url, {'status': 'Arrived'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        [row] = status_rows()
        self.assertEqual(row.payload['data']['source'], 'minitos.api')

    def test_oes_execution_message_tags_source_causation_and_sim_time(self):
        order = Order.objects.get()
        event_id = uuid.uuid4()
        message = {
            'schema': 'scms.execution.event.v1',
            'event_id': str(event_id),
            'terminal': {'id': self.visit.location_id},
            'source_sim_time_ms': 125000,
            'correlation_id': 'discharge-V001-run-7',
            'data': {'order_id': order.pk, 'event': 'order_started', 'occurred_at': timezone.now().isoformat()},
        }

        def fake_apply(order_id, event, **kwargs):
            visit = ContainerVisit.objects.get(pk=self.container_visit.pk)
            visit.status = ContainerVisit.ON_EQUIPMENT
            visit.save(update_fields=['status'])
            return Order.objects.get(pk=order_id)

        with (
            mock.patch.object(integration_events, 'apply_execution_event', side_effect=fake_apply),
            self.captureOnCommitCallbacks(execute=True),
        ):
            integration_events.apply_execution_message('oes/topic', json.dumps(message))

        [row] = status_rows()
        data = row.payload['data']
        self.assertEqual(data['source'], 'oes.execution')
        self.assertEqual(row.payload['causation_id'], str(event_id))
        self.assertEqual(row.payload['correlation_id'], 'discharge-V001-run-7')
        self.assertEqual(data['sim_time_ms'], 125000)
        self.assertEqual(data['to_status'], 'OnEquipment')

    def test_oes_message_without_correlation_correlates_on_its_event_id(self):
        order = Order.objects.get()
        event_id = uuid.uuid4()
        message = {
            'schema': 'scms.execution.event.v1', 'event_id': str(event_id),
            'terminal': {'id': self.visit.location_id},
            'data': {'order_id': order.pk, 'event': 'order_started'},
        }

        def fake_apply(order_id, event, **kwargs):
            visit = ContainerVisit.objects.get(pk=self.container_visit.pk)
            visit.status = ContainerVisit.ON_EQUIPMENT
            visit.save(update_fields=['status'])
            return Order.objects.get(pk=order_id)

        with mock.patch.object(integration_events, 'apply_execution_event', side_effect=fake_apply):
            integration_events.apply_execution_message('oes/topic', json.dumps(message))
        [row] = status_rows()
        self.assertEqual(row.payload['causation_id'], str(event_id))
        self.assertEqual(row.payload['correlation_id'], str(event_id))
        self.assertIsNone(row.payload['data']['sim_time_ms'])

    def test_failed_execution_message_publishes_nothing(self):
        order = Order.objects.get()
        message = {
            'schema': 'scms.execution.event.v1', 'event_id': str(uuid.uuid4()),
            'terminal': {'id': self.visit.location_id},
            'data': {'order_id': order.pk, 'event': 'order_started'},
        }

        def failing_apply(order_id, event, **kwargs):
            visit = ContainerVisit.objects.get(pk=self.container_visit.pk)
            visit.status = ContainerVisit.ON_EQUIPMENT
            visit.save(update_fields=['status'])
            raise RuntimeError('consumer failed after the status change')

        with (
            mock.patch.object(integration_events, 'apply_execution_event', side_effect=failing_apply),
            self.captureOnCommitCallbacks(execute=True),
            self.assertRaises(RuntimeError),
        ):
            integration_events.apply_execution_message('oes/topic', json.dumps(message))

        self.assertEqual(status_rows(), [])
        self.container_visit.refresh_from_db()
        self.assertEqual(self.container_visit.status, ContainerVisit.PREADVISED)


class NoEventTests(StatusEventTestBase):
    def test_same_status_save_publishes_nothing(self):
        self.save_status(self.visit, CarrierVisit.ARRIVED)
        self.save_status(self.container_visit, ContainerVisit.PREADVISED)
        self.assertEqual(status_rows(), [])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status_revision, 1)

    def test_save_without_status_in_update_fields_publishes_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.visit.vessel_name = 'RENAMED'
            self.visit.save(update_fields=['vessel_name'])
        self.assertEqual(status_rows(), [])

    def test_other_field_change_publishes_nothing(self):
        with self.captureOnCommitCallbacks(execute=True):
            self.container_visit.weight = 12345
            self.container_visit.save()
        self.assertEqual(status_rows(), [])

    def test_rolled_back_change_publishes_nothing(self):
        with (
            self.captureOnCommitCallbacks(execute=True) as callbacks,
            self.assertRaises(RuntimeError),
            transaction.atomic(),
        ):
            self.visit.status = CarrierVisit.IN_OPERATION
            self.visit.save()
            self.container_visit.status = ContainerVisit.ARRIVED
            self.container_visit.save()
            raise RuntimeError('roll back')
        self.assertEqual(callbacks, [])
        self.assertEqual(status_rows(), [])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertEqual(self.visit.status_revision, 1)

    def test_rolled_back_savepoint_drops_only_its_event(self):
        with self.captureOnCommitCallbacks(execute=True), transaction.atomic():
            self.container_visit.status = ContainerVisit.ARRIVED
            self.container_visit.save()
            try:
                with transaction.atomic():
                    self.visit.status = CarrierVisit.IN_OPERATION
                    self.visit.save()
                    raise RuntimeError('inner roll back')
            except RuntimeError:
                pass
        [row] = status_rows()
        self.assertEqual(row.payload['data']['entity_type'], 'container_visit')
        self.visit.refresh_from_db()
        self.assertEqual((self.visit.status, self.visit.status_revision), (CarrierVisit.ARRIVED, 1))

    def test_outbox_row_is_written_in_the_same_transaction(self):
        with self.captureOnCommitCallbacks(execute=False) as callbacks, transaction.atomic():
            self.visit.status = CarrierVisit.IN_OPERATION
            self.visit.save()
            # Inside the still-open transaction, before any commit:
            self.assertTrue(transaction.get_connection().in_atomic_block)
            [row] = status_rows()
            self.assertEqual(row.payload['data']['to_status'], 'InOperation')
            self.assertEqual(
                CarrierVisit.objects.filter(pk=self.visit.pk).values_list('status', 'status_revision').get(),
                ('InOperation', row.payload['aggregate']['revision']),
            )
        self.assertEqual(callbacks, [])  # nothing deferred to on_commit

    def test_visit_at_a_location_without_site_publishes_nothing(self):
        location = Location.objects.create(name='No site', location_type=Location.TL)
        with self.captureOnCommitCallbacks(execute=True):
            visit = CarrierVisit.objects.create(name='V-NOSITE', location=location, status=CarrierVisit.ARRIVED)
            visit.status = CarrierVisit.IN_OPERATION
            visit.save()
        self.assertEqual(status_rows(), [])

    def test_outbox_write_failure_rolls_the_status_change_back(self):
        with (
            mock.patch.object(IntegrationOutbox.objects, 'create', side_effect=RuntimeError('db down')),
            self.assertRaises(RuntimeError),
            transaction.atomic(),
        ):
            self.visit.status = CarrierVisit.IN_OPERATION
            self.visit.save()
        self.visit.refresh_from_db()
        self.assertEqual((self.visit.status, self.visit.status_revision), (CarrierVisit.ARRIVED, 1))
        self.assertEqual(status_rows(), [])


class CreationEventTests(TestCase):
    def test_seed_publishes_the_first_status_of_each_visit(self):
        with self.captureOnCommitCallbacks(execute=True):
            reset_scenario('import_v1', containers=2)
        events = [
            (row.payload['data']['entity_type'], row.payload['data']['from_status'],
             row.payload['data']['to_status'], row.payload['data']['source'])
            for row in status_rows()
        ]
        self.assertEqual(events, [
            ('carrier_visit', None, 'Arrived', 'minitos.scenario'),
            ('container_visit', None, 'Preadvised', 'minitos.scenario'),
            ('container_visit', None, 'Preadvised', 'minitos.scenario'),
        ])
        self.assertEqual({row.payload['aggregate']['revision'] for row in status_rows()}, {1})


class SourceContextTests(TestCase):
    def test_unknown_source_is_refused(self):
        with self.assertRaises(ValueError), status_event_source('somewhere'):
            pass

    def test_context_is_restored_after_the_block(self):
        with status_event_source(status_events.SOURCE_OES_EXECUTION, sim_time_ms='42'):
            self.assertEqual(status_events.current_context()['sim_time_ms'], 42)
            with status_event_source(status_events.SOURCE_SCENARIO):
                self.assertEqual(status_events.current_context()['source'], 'minitos.scenario')
            self.assertEqual(status_events.current_context()['source'], 'oes.execution')
        self.assertEqual(status_events.current_context()['source'], 'minitos.system')

    def test_bad_sim_time_is_null(self):
        with status_event_source(status_events.SOURCE_OES_EXECUTION, sim_time_ms='soon'):
            self.assertIsNone(status_events.current_context()['sim_time_ms'])
        with status_event_source(status_events.SOURCE_OES_EXECUTION, sim_time_ms=True):
            self.assertIsNone(status_events.current_context()['sim_time_ms'])



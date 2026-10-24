import json
import uuid

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from rest_framework import status

from minitos_core.models import (
    Che,
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    EquipmentType,
    IntegrationInbox,
    IntegrationOutbox,
    ISOType,
    Order,
    OrderLeg,
    OrderRoutePlan,
    Position,
    Site,
    WorkQueue,
)
from minitos_core.services import (
    apply_execution_message,
    drop_container,
    pickup_container,
    sync_yard_grid,
)
from minitos_core.test_yard_grid import LayoutTestCase


@override_settings(MES_API_TOKEN='test-mes-token')
class OrderExecutionTests(LayoutTestCase):
    def setUp(self):
        super().setUp()
        self.grid = self.create_grid()
        sync_yard_grid(self.grid)
        self.source = Position.objects.get(
            pile__storage_grid=self.grid, pile__bay=1, pile__row=1, tier=1,
        )
        self.target = Position.objects.get(
            pile__storage_grid=self.grid, pile__bay=1, pile__row=2, tier=1,
        )
        self.iso_type = ISOType.objects.create(iso_type='22G1')
        self.owner = ContainerOwner.objects.create(
            name='Execution Line', code='EXEC', is_shippingline=True,
        )
        self.container = ContainerUnit.objects.create(
            container_number='MSCU1234567',
            iso_type=self.iso_type,
            owner=self.owner,
            is_in_terminal=True,
        )
        self.visit = ContainerVisit.objects.create(
            location=self.location,
            container_number=self.container,
            category=ContainerVisit.STORAGE,
            status=ContainerVisit.AT_YARD,
        )
        self.source.container_visit = self.visit
        self.source.save()
        self.queue = WorkQueue.objects.create(
            name='YARD-WQ', location=self.location, is_active=True,
        )
        self.order = Order.objects.create(
            order_type=Order.HOUSEKEEPING,
            status=Order.PLANNED,
            priority=10,
            container_number=self.visit,
            work_queue=self.queue,
            from_position=self.source.operational_position,
            to_position=self.target.operational_position,
        )
        self.plan = OrderRoutePlan.objects.create(
            order=self.order,
            layout_version=self.layout,
            version=1,
            status=OrderRoutePlan.ACTIVE,
        )
        self.leg = OrderLeg.objects.create(
            order=self.order,
            route_plan=self.plan,
            sequence=1,
            leg_kind=OrderLeg.HANDLE,
            start_position=self.source.operational_position,
            end_position=self.target.operational_position,
        )
        equipment_type = EquipmentType.objects.create(
            name='Test ASC', equipment_type=EquipmentType.ARMG,
            is_yard_equipment=True,
        )
        self.che = Che.objects.create(
            name='ASC01', equipment_type=equipment_type, location=self.location,
            powered_on=True, is_available=True,
            latitude=self.source.operational_position.latitude,
            longitude=self.source.operational_position.longitude,
        )

    def order_url(self, action=None):
        name = 'order-detail' if action is None else f'order-{action}'
        return reverse(
            f'minitos_core:{name}',
            args=[self.location.pk, self.queue.pk, self.order.pk],
        )

    def callback(self, event, **payload):
        return self.client.post(
            self.order_url('execution-callback'),
            {'event': event, **payload},
            format='json',
            HTTP_X_MES_TOKEN='test-mes-token',
        )

    def test_serializers_expose_order_lifecycle_and_container_execution_state(self):
        order_response = self.client.get(self.order_url())
        self.assertEqual(order_response.status_code, status.HTTP_200_OK)
        self.assertEqual(order_response.data['status'], Order.PLANNED)
        self.assertEqual(order_response.data['priority'], 10)
        self.assertIsNone(order_response.data['released_at'])
        visit_response = self.client.get(reverse(
            'minitos_core:container_visit-detail',
            args=[self.location.pk, self.visit.pk],
        ))
        self.assertEqual(visit_response.status_code, status.HTTP_200_OK)
        self.assertEqual(visit_response.data['status'], ContainerVisit.AT_YARD)
        self.assertIn('last_updated', visit_response.data)
        self.assertIsNone(visit_response.data['current_che'])

    @override_settings(UNS_DUAL_PUBLISH_LEGACY=False)
    def test_release_uses_v2_only_after_cutover(self):
        released = self.client.post(self.order_url('release'), {}, format='json')

        self.assertEqual(released.status_code, status.HTTP_200_OK, released.data)
        topics = list(IntegrationOutbox.objects.filter(
            schema='scms.order.released.v1',
        ).values_list('topic', flat=True))
        self.assertEqual(len(topics), 1)
        self.assertTrue(topics[0].startswith('scms/v2/'))

    @override_settings(UNS_DUAL_PUBLISH_LEGACY=True)
    def test_release_and_mes_callback_execute_pickup_drop_and_completion(self):
        released = self.client.post(self.order_url('release'), {}, format='json')
        self.assertEqual(released.status_code, status.HTTP_200_OK, released.data)
        self.assertEqual(released.data['status'], Order.RELEASED)
        self.assertIsNotNone(released.data['released_at'])
        outgoing = IntegrationOutbox.objects.get(
            schema='scms.order.released.v1', topic__startswith='scms/v2/',
        )
        legacy = IntegrationOutbox.objects.get(
            schema='scms.order.released.v1', topic__startswith='scms/v1/',
        )
        self.assertEqual(outgoing.payload['event_id'], legacy.payload['event_id'])
        site = Site.objects.get(location=self.location)
        self.assertEqual(outgoing.payload['site']['id'], str(site.pk))
        self.assertEqual(
            outgoing.topic,
            f'scms/v2/{site.enterprise.code}/{site.code}/tos/orders/{self.order.pk}/released',
        )
        self.assertEqual(outgoing.payload['data']['order']['id'], self.order.pk)
        self.assertEqual(
            outgoing.payload['data']['legs'][0]['destination_inventory_position_id'],
            self.target.pk,
        )
        self.assertEqual(
            outgoing.payload['data']['legs'][0]['source_latitude'],
            self.source.operational_position.latitude,
        )
        self.assertEqual(
            outgoing.payload['data']['legs'][0]['source_longitude'],
            self.source.operational_position.longitude,
        )
        self.assertEqual(
            outgoing.payload['data']['legs'][0]['destination_latitude'],
            self.target.operational_position.latitude,
        )
        self.assertEqual(
            outgoing.payload['data']['legs'][0]['destination_longitude'],
            self.target.operational_position.longitude,
        )

        dispatched = self.callback(
            'leg_dispatched', leg_id=self.leg.pk, che_id=self.che.pk,
        )
        self.assertEqual(dispatched.status_code, status.HTTP_200_OK, dispatched.data)
        started = self.callback('leg_started', leg_id=self.leg.pk, che_id=self.che.pk)
        self.assertEqual(started.status_code, status.HTTP_200_OK, started.data)
        self.assertEqual(started.data['status'], Order.IN_PROGRESS)

        picked = self.callback('container_picked_up', che_id=self.che.pk)
        self.assertEqual(picked.status_code, status.HTTP_200_OK, picked.data)
        self.visit.refresh_from_db()
        self.source.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.ON_EQUIPMENT)
        self.assertEqual(self.visit.current_che, self.che)
        self.assertIsNone(self.source.container_visit)

        duplicate_pickup = self.callback('container_picked_up', che_id=self.che.pk)
        self.assertEqual(duplicate_pickup.status_code, status.HTTP_200_OK)
        dropped = self.callback(
            'container_dropped', che_id=self.che.pk, position_id=self.target.pk,
        )
        self.assertEqual(dropped.status_code, status.HTTP_200_OK, dropped.data)
        self.visit.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.AT_YARD)
        self.assertIsNone(self.visit.current_che)
        self.assertEqual(self.target.container_visit, self.visit)

        completed = self.callback('leg_completed', leg_id=self.leg.pk)
        self.assertEqual(completed.status_code, status.HTTP_200_OK, completed.data)
        self.assertEqual(completed.data['status'], Order.COMPLETE)
        self.assertIsNotNone(completed.data['completed_at'])
        self.assertIsNotNone(completed.data['atc'])
        self.che.refresh_from_db()
        self.assertTrue(self.che.is_available)
        self.assertFalse(self.che.is_dispatched)
        self.assertFalse(self.che.working)

    def test_oes_execution_event_is_consumed_once(self):
        self.client.post(self.order_url('release'), {}, format='json')
        event_id = uuid.uuid4()
        payload = {
            'schema': 'scms.execution.event.v1',
            'event_id': str(event_id),
            'terminal': {'id': self.location.pk, 'code': f'terminal-{self.location.pk}'},
            'site': {
                'id': str(Site.objects.get(location=self.location).pk),
                'code': Site.objects.get(location=self.location).code,
            },
            'data': {
                'event': 'leg_dispatched',
                'order_id': self.order.pk,
                'leg_id': self.leg.pk,
                'che_id': self.che.pk,
                'occurred_at': '2026-10-12T10:00:00Z',
            },
        }
        site = Site.objects.select_related('enterprise').get(location=self.location)
        topic = (
            f'scms/v2/{site.enterprise.code}/{site.code}'
            f'/oes/orders/{self.order.pk}/execution'
        )
        apply_execution_message(topic, json.dumps(payload))
        apply_execution_message(topic, json.dumps(payload))

        self.leg.refresh_from_db()
        self.assertEqual(self.leg.status, OrderLeg.DISPATCHED)
        self.assertEqual(IntegrationInbox.objects.filter(event_id=event_id).count(), 1)

    def test_execution_callback_rejects_missing_mes_token(self):
        response = self.client.post(
            self.order_url('execution-callback'),
            {'event': 'order_started'},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_failed_order_records_terminal_timestamp(self):
        self.client.post(self.order_url('release'), {}, format='json')
        response = self.callback(
            'leg_failed', leg_id=self.leg.pk, che_id=self.che.pk,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data['status'], Order.FAILED)
        self.assertIsNotNone(response.data['completed_at'])

    def test_drop_is_atomic_when_target_is_occupied(self):
        pickup_container(self.visit.pk, self.che.pk)
        second_container = ContainerUnit.objects.create(
            container_number='MSCU7654321',
            iso_type=self.iso_type,
            owner=self.owner,
            is_in_terminal=True,
        )
        second_visit = ContainerVisit.objects.create(
            location=self.location,
            container_number=second_container,
            status=ContainerVisit.AT_YARD,
        )
        self.target.container_visit = second_visit
        self.target.save()

        with self.assertRaisesMessage(ValidationError, 'already occupied'):
            drop_container(self.visit.pk, self.che.pk, self.target.pk)

        self.visit.refresh_from_db()
        self.target.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.ON_EQUIPMENT)
        self.assertEqual(self.visit.current_che, self.che)
        self.assertEqual(self.target.container_visit, second_visit)

    def test_direct_status_patch_cannot_bypass_pickup_service(self):
        response = self.client.patch(
            reverse(
                'minitos_core:container_visit-detail',
                args=[self.location.pk, self.visit.pk],
            ),
            {'status': ContainerVisit.ON_EQUIPMENT},
            format='json',
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

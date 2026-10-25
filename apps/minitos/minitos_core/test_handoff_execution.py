"""TCL-003a: MiniTOS side of D-012 (appear on QC), T-006 (CHE→CHE transfer) and T-007/D-013.

Drives the real consumer ``apply_execution_event`` with the ``details`` an OES
``scms.execution.event.v1`` carries.
"""

from django.core.exceptions import ValidationError
from django.utils import timezone

from minitos_core.models import (
    Che,
    ContainerOwner,
    ContainerUnit,
    ContainerVisit,
    EquipmentType,
    IntegrationOutbox,
    ISOType,
    Order,
    OrderLeg,
    OrderRoutePlan,
    Position,
    WorkQueue,
)
from minitos_core.services import sync_yard_grid
from minitos_core.services.order_execution import _queue_released_order, apply_execution_event
from minitos_core.test_yard_grid import LayoutTestCase


class HandoffExecutionTests(LayoutTestCase):
    def setUp(self):
        super().setUp()
        self.block = self.create_grid(name='BLOCK01')
        sync_yard_grid(self.block)
        self.planned = Position.objects.get(
            pile__storage_grid=self.block, pile__bay=1, pile__row=2, tier=1,
        )
        iso_type = ISOType.objects.create(iso_type='22G1')
        owner = ContainerOwner.objects.create(name='Line', code='LINE', is_shippingline=True)
        unit = ContainerUnit.objects.create(
            container_number='MSCU1234565', iso_type=iso_type, owner=owner,
        )
        # Import on the vessel: no yard position, no CHE.
        self.visit = ContainerVisit.objects.create(
            location=self.location, container_number=unit,
            category=ContainerVisit.IMPORT, status=ContainerVisit.ON_VESSEL,
        )
        queue = WorkQueue.objects.create(name='QC01-WQ', location=self.location, is_active=True)
        self.order = Order.objects.create(
            order_type=Order.MOVE_IN,
            status=Order.IN_PROGRESS, priority=10, container_number=self.visit, work_queue=queue,
            to_position=self.planned.operational_position,
        )
        plan = OrderRoutePlan.objects.create(
            order=self.order, layout_version=self.layout, version=1, status=OrderRoutePlan.ACTIVE,
        )
        self.legs = [
            OrderLeg.objects.create(
                order=self.order, route_plan=plan, sequence=seq, leg_kind=OrderLeg.HANDLE,
            )
            for seq in (1, 2, 3)
        ]
        self.qc = self.make_che('QC01', EquipmentType.STS, 'STS')
        self.tt = self.make_che('TT01', EquipmentType.TT, 'TT')
        self.rtg = self.make_che('RTG01', EquipmentType.RTG, 'RTG')

    def make_che(self, name, kind, type_name):
        equipment_type, _ = EquipmentType.objects.get_or_create(
            name=type_name, defaults={'equipment_type': kind},
        )
        return Che.objects.create(
            name=name, equipment_type=equipment_type, location=self.location,
            powered_on=True, is_available=False, is_dispatched=True, latitude=40, longitude=-3,
        )

    def event(self, event, **details):
        leg = details.pop('leg', None)
        che = details.pop('che', None)
        return apply_execution_event(
            self.order.pk, event, leg_id=leg.pk if leg else None,
            che_id=che.pk if che else None, position_id=details.get('position_id'),
            occurred_at=timezone.now(), details=details,
        )

    def test_vessel_pick_places_the_container_on_the_qc(self):
        """B2/D-012 final: container_picked_up with source VESSEL → On Equipment / QC01."""
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.ON_EQUIPMENT)
        self.assertEqual(self.visit.current_che, self.qc)
        self.assertFalse(Position.objects.filter(container_visit=self.visit).exists())
        # Redelivery is idempotent.
        self.event('container_picked_up', che=self.qc, source='VESSEL')

    def test_vessel_pick_by_a_non_qc_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, 'quay crane'):
            self.event('container_picked_up', che=self.tt, source='VESSEL')

    def test_transfers_move_custody_che_to_che_once(self):
        """T-006: one container_transferred per hand-off; a repeat is a no-op."""
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        details = {'from_che_id': self.qc.pk, 'to_che_id': self.tt.pk}
        self.event('container_transferred', leg=self.legs[1], **details)
        self.event('container_transferred', leg=self.legs[1], **details)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.current_che, self.tt)
        self.event('container_transferred', leg=self.legs[2],
                   from_che_id=self.tt.pk, to_che_id=self.rtg.pk)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.current_che, self.rtg)
        with self.assertRaisesMessage(ValidationError, 'giving CHE'):
            self.event('container_transferred', leg=self.legs[1],
                       from_che_id=self.qc.pk, to_che_id=self.tt.pk)

    def test_no_show_exception_relocates_to_block01_flagged(self):
        """T-007/D-013: handoff_exception → BLOCK01 slot, exception flag, legs cancelled."""
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        details = {
            'reason': 'NO_TT_TIMEOUT', 'from_che_id': self.qc.pk, 'to_che_id': self.tt.pk,
            'destination_position_id': self.planned.pk,
        }
        self.event('handoff_exception', leg=self.legs[0], che=self.qc, **details)
        self.visit.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.visit.status, ContainerVisit.AT_YARD)
        self.assertIsNone(self.visit.current_che)
        self.assertEqual(self.visit.exception_reason, 'NO_TT_TIMEOUT')
        slot = Position.objects.get(container_visit=self.visit)
        self.assertEqual(slot.pile.storage_grid.name, 'BLOCK01')
        self.assertEqual(slot, self.planned)
        self.assertEqual(self.order.status, Order.CANCELLED)
        self.assertEqual(
            set(OrderLeg.objects.filter(order=self.order).values_list('status', flat=True)),
            {OrderLeg.CANCELLED},
        )
        for che in (self.qc, self.tt):
            che.refresh_from_db()
            self.assertTrue(che.is_available)
            self.assertFalse(che.is_dispatched)
        # Duplicate exception: no second move.
        self.event('handoff_exception', leg=self.legs[0], che=self.qc, **details)
        self.assertEqual(Position.objects.filter(container_visit=self.visit).count(), 1)

    def test_exception_uses_first_free_block01_slot_when_planned_is_taken(self):
        self.event('container_picked_up', che=self.qc, source='VESSEL')
        other = ContainerVisit.objects.create(
            location=self.location, category=ContainerVisit.STORAGE,
            status=ContainerVisit.AT_YARD,
            container_number=ContainerUnit.objects.create(
                container_number='TGHU0000001', iso_type=ISOType.objects.get(),
                owner=ContainerOwner.objects.get(),
            ),
        )
        self.planned.container_visit = other
        self.planned.save()
        self.event('handoff_exception', leg=self.legs[0], che=self.qc, reason='NO_TT_TIMEOUT',
                   destination_position_id=self.planned.pk)
        slot = Position.objects.get(container_visit=self.visit)
        self.assertNotEqual(slot, self.planned)
        self.assertEqual((slot.pile.storage_grid.name, slot.tier), ('BLOCK01', 1))

    def test_released_payload_carries_the_iso_container_number(self):
        """D-010: OES needs the ISO 6346 number, not only the visit pk."""
        self.order.status = Order.RELEASED
        self.order.released_at = timezone.now()
        self.order.save()
        _queue_released_order(self.order)
        # TL review S-1: filter by schema and order, not the newest outbox row.
        payload = IntegrationOutbox.objects.filter(
            schema='scms.order.released.v1',
        ).filter(payload__data__order__id=self.order.pk).latest('pk').payload
        self.assertEqual(payload['data']['order']['container_unit_number'], 'MSCU1234565')
        self.assertEqual(payload['data']['order']['container_number'], self.visit.pk)

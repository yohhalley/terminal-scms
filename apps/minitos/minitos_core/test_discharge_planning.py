"""TCL-010: discharge orders are created when a vessel visit goes InOperation.

The import_v1 seed still pre-creates its orders (TCL-010b removes that), so
the "creates" tests delete the seeded orders first, which is the state the
seed will leave once TCL-010b lands.
"""

import io
import random

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from minitos_core.models import (
    CarrierVisit,
    ContainerVisit,
    Order,
    OrderLeg,
    OrderRoutePlan,
    Position,
)
from minitos_core.scenarios import reset_scenario, run_step
from minitos_core.services import discharge_planning
from minitos_core.services.discharge_planning import (
    CODE_NO_FREE_SLOT,
    DischargePlanningError,
    plan_visit_discharge,
)

User = get_user_model()


def drop_seeded_orders():
    """The seed state after TCL-010b: container visits, no orders, slots free."""
    Order.objects.all().delete()
    Position.objects.filter(is_reserved=True).update(is_reserved=False)


def block_slots():
    return Position.objects.filter(pile__storage_grid__name='BLOCK01', tier=1)


class DischargePlanningTestBase(TestCase):
    containers = 3

    def setUp(self):
        reset_scenario('import_v1', containers=self.containers)
        self.visit = CarrierVisit.objects.get()
        self.containers_on_visit = list(
            ContainerVisit.objects.filter(ib_carrier=self.visit).order_by('pk')
        )

    def move_to_in_operation(self):
        self.visit.status = CarrierVisit.IN_OPERATION
        self.visit.save()
        self.visit.refresh_from_db()

    def discharge_orders(self):
        return Order.objects.filter(
            container_number__ib_carrier=self.visit, order_type=Order.MOVE_IN,
        ).order_by('pk')


class CreatesOrdersTests(DischargePlanningTestBase):
    def setUp(self):
        super().setUp()
        drop_seeded_orders()

    def test_in_operation_creates_one_planned_order_per_container_with_three_legs(self):
        self.move_to_in_operation()

        orders = list(self.discharge_orders())
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)
        self.assertEqual(
            [order.container_number_id for order in orders],
            [cv.pk for cv in self.containers_on_visit],
        )
        for order in orders:
            self.assertEqual(order.status, Order.PLANNED)
            self.assertEqual(order.work_queue.name, 'QC01-WQ')
            plan = OrderRoutePlan.objects.get(order=order)
            self.assertEqual(plan.status, OrderRoutePlan.ACTIVE)
            legs = list(order.order_legs.order_by('sequence'))
            self.assertEqual(
                [(leg.leg_kind, leg.equipment_type.equipment_type) for leg in legs],
                [(OrderLeg.HANDLE, 'STS'), (OrderLeg.MOVE, 'TT'), (OrderLeg.HANDLE, 'RTG')],
            )
            self.assertTrue(all(leg.route_plan_id == plan.pk for leg in legs))
            # Hand-off chain: each leg starts where the previous one ended.
            self.assertEqual(legs[0].start_position_id, order.from_position_id)
            self.assertEqual(legs[0].end_position_id, legs[1].start_position_id)
            self.assertEqual(legs[1].end_position_id, legs[2].start_position_id)
            # Legs 1-2 end with no yard destination (OES hands off to the next
            # CHE); only leg 3 ends on a yard slot.
            self.assertIsNone(legs[0].end_position.stack_position_id)
            self.assertIsNone(legs[1].end_position.stack_position_id)
            slot = legs[2].end_position.stack_position
            self.assertIsNotNone(slot)
            self.assertEqual(legs[2].end_position_id, order.to_position_id)
            self.assertEqual(slot.pile.storage_grid.name, 'BLOCK01')
            self.assertEqual(slot.tier, 1)
            self.assertTrue(slot.is_reserved)
            self.assertIsNone(slot.container_visit_id)
            # The truck leg has a road route; the crane legs have none.
            self.assertGreater(legs[1].route_edges.count(), 0)
            self.assertGreater(legs[1].distance, 0)
            self.assertEqual(legs[0].route_edges.count(), 0)
            self.assertEqual(legs[2].route_edges.count(), 0)
        destinations = [order.to_position_id for order in orders]
        self.assertEqual(len(set(destinations)), len(destinations))

    def test_running_twice_does_not_duplicate(self):
        self.move_to_in_operation()
        first = list(self.discharge_orders().values_list('pk', 'to_position_id'))
        legs = OrderLeg.objects.count()

        again = plan_visit_discharge(self.visit)
        self.visit.save()  # same status: not a transition, plans nothing
        run_step('import_v1', 'visit-in-operation')

        self.assertEqual(list(self.discharge_orders().values_list('pk', 'to_position_id')), first)
        self.assertEqual(OrderLeg.objects.count(), legs)
        self.assertEqual([item.created for item in again], [False] * len(first))

    def test_yard_slots_are_deterministic_across_reruns(self):
        self.move_to_in_operation()
        first = [order.to_position.code for order in self.discharge_orders()]

        reset_scenario('import_v1', containers=self.containers)
        drop_seeded_orders()
        self.visit = CarrierVisit.objects.get()
        self.move_to_in_operation()

        self.assertEqual([order.to_position.code for order in self.discharge_orders()], first)

    def test_rng_is_injectable_and_draws_from_the_free_slots(self):
        free = list(discharge_planning.free_yard_slots(block_slots().first().pile.storage_grid))
        expected = random.Random(42).sample(free, self.containers)

        planned = plan_visit_discharge(self.visit, rng=random.Random(42))

        self.assertEqual(
            [item.order.to_position_id for item in planned],
            [slot.operational_position.pk for slot in expected],
        )
        self.assertTrue(all(item.created for item in planned))

    def test_reserved_or_occupied_slots_are_not_used(self):
        grid = block_slots().first().pile.storage_grid
        free = list(discharge_planning.free_yard_slots(grid))
        keep = free[:self.containers]
        block_slots().exclude(pk__in=[slot.pk for slot in keep]).update(is_reserved=True)

        self.move_to_in_operation()

        self.assertEqual(
            sorted(order.to_position_id for order in self.discharge_orders()),
            sorted(slot.operational_position.pk for slot in keep),
        )

    def test_containers_already_off_the_vessel_get_no_order(self):
        landed = self.containers_on_visit[0]
        landed.status = ContainerVisit.AT_YARD
        landed.save()

        self.move_to_in_operation()

        self.assertEqual(
            [order.container_number_id for order in self.discharge_orders()],
            [cv.pk for cv in self.containers_on_visit[1:]],
        )

    def test_no_free_slot_refuses_in_operation_with_a_clear_error(self):
        block_slots().update(is_reserved=True)
        self.visit.status = CarrierVisit.IN_OPERATION

        with self.assertRaises(DischargePlanningError) as caught:
            self.visit.save()

        self.assertEqual(caught.exception.planning_code, CODE_NO_FREE_SLOT)
        self.assertIn('0 free ground slot', caught.exception.planning_message)
        self.assertIn('BLOCK01', caught.exception.planning_message)
        self.assertIn('V001', caught.exception.planning_message)
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)  # rolled back
        self.assertFalse(self.discharge_orders().exists())

    def test_fewer_free_slots_than_containers_plans_nothing(self):
        grid = block_slots().first().pile.storage_grid
        free = list(discharge_planning.free_yard_slots(grid))
        block_slots().exclude(pk__in=[slot.pk for slot in free[:self.containers - 1]]).update(is_reserved=True)

        with self.assertRaises(DischargePlanningError):
            plan_visit_discharge(self.visit)

        self.assertFalse(self.discharge_orders().exists())
        self.assertEqual(Position.objects.filter(is_reserved=False, pile__storage_grid=grid, tier=1).count(),
                         self.containers - 1)


class AdoptsExistingOrderTests(DischargePlanningTestBase):
    def test_seeded_orders_are_adopted_not_duplicated(self):
        seeded = list(self.discharge_orders().values_list('pk', 'to_position_id', 'status'))
        self.assertEqual(len(seeded), self.containers)
        legs = OrderLeg.objects.count()

        self.move_to_in_operation()

        self.assertEqual(list(self.discharge_orders().values_list('pk', 'to_position_id', 'status')), seeded)
        self.assertEqual(OrderLeg.objects.count(), legs)

    def test_only_containers_without_an_order_get_one(self):
        kept = self.discharge_orders().first()
        self.discharge_orders().exclude(pk=kept.pk).delete()

        planned = plan_visit_discharge(self.visit)

        self.assertEqual([item.created for item in planned], [False] + [True] * (self.containers - 1))
        self.assertEqual(planned[0].order.pk, kept.pk)
        self.assertEqual(self.discharge_orders().count(), self.containers)
        destinations = list(self.discharge_orders().values_list('to_position_id', flat=True))
        self.assertEqual(len(set(destinations)), len(destinations))

    def test_cancelled_order_is_not_adopted(self):
        cancelled = self.discharge_orders().first()
        cancelled.status = Order.CANCELLED
        cancelled.save()

        planned = plan_visit_discharge(self.visit)

        self.assertTrue(planned[0].created)
        self.assertNotEqual(planned[0].order.pk, cancelled.pk)

    def test_adopted_seed_with_no_free_slot_still_moves(self):
        block_slots().update(is_reserved=True)
        self.move_to_in_operation()
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)


class TriggerPathTests(DischargePlanningTestBase):
    """The API transition and the admin change page both plan (model save)."""

    def setUp(self):
        super().setUp()
        drop_seeded_orders()
        self.user = User.objects.create_superuser(
            username='tcl010-admin', email='admin@terminal-scms.invalid', password='test-password',
        )

    def transition(self, new_status):
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = reverse('minitos_core:transition_visit_status', args=[self.visit.pk])
        return client.post(url, {'status': new_status}, format='json')

    def admin_post(self, new_status):
        self.client.force_login(self.user)
        model_admin = admin.site._registry[CarrierVisit]
        change_url = reverse('admin:minitos_core_carriervisit_change', args=[self.visit.pk])
        request = RequestFactory().get(change_url)
        request.user = self.user
        form = model_admin.get_form(request, self.visit)(instance=self.visit)
        data = {}
        for name, field in form.fields.items():
            value = form[name].value()
            if value is None or hasattr(field, 'fields'):
                continue
            data[name] = value
        data['status'] = new_status
        return self.client.post(change_url, data)

    def test_transition_api_creates_orders(self):
        response = self.transition('InOperation')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.discharge_orders().count(), self.containers)

    def test_transition_api_no_free_slot_is_409_with_code(self):
        block_slots().update(is_reserved=True)
        response = self.transition('InOperation')
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT, response.content)
        self.assertEqual(response.data['code'], CODE_NO_FREE_SLOT)
        self.assertIn('BLOCK01', response.data['message'])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(self.discharge_orders().exists())

    def test_rest_patch_creates_orders_and_no_free_slot_is_400(self):
        client = APIClient()
        client.force_authenticate(user=self.user)
        url = reverse(
            'minitos_core:carrier-visit-detail',
            kwargs={'location_pk': self.visit.location_id, 'pk': self.visit.pk},
        )
        block_slots().update(is_reserved=True)
        response = client.patch(url, {'status': 'InOperation'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST, response.content)
        self.assertEqual(response.data['status'][0].code, CODE_NO_FREE_SLOT)
        self.assertFalse(self.discharge_orders().exists())

        block_slots().update(is_reserved=False)
        response = client.patch(url, {'status': 'InOperation'}, format='json')
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.discharge_orders().count(), self.containers)

    def test_admin_change_page_creates_orders(self):
        response = self.admin_post('InOperation')
        self.assertEqual(response.status_code, 302, response.content[:2000])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)
        self.assertEqual(self.discharge_orders().count(), self.containers)

    def test_admin_change_page_no_free_slot_shows_the_error_on_status(self):
        block_slots().update(is_reserved=True)
        response = self.admin_post('InOperation')
        self.assertEqual(response.status_code, 200)
        errors = response.context['adminform'].form.errors
        self.assertIn('status', errors)
        self.assertIn('BLOCK01', errors['status'][0])
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.ARRIVED)
        self.assertFalse(self.discharge_orders().exists())


class VisitInOperationStepTests(DischargePlanningTestBase):
    containers = 1

    def test_step_moves_the_seeded_visit_and_adopts_the_seeded_order(self):
        seeded = self.discharge_orders().get()
        result = run_step('import_v1', 'visit-in-operation')
        self.visit.refresh_from_db()
        self.assertEqual(self.visit.status, CarrierVisit.IN_OPERATION)
        self.assertEqual([item.order.pk for item in result.planned], [seeded.pk])
        self.assertEqual(self.discharge_orders().count(), 1)

    def test_step_then_release_discharge(self):
        drop_seeded_orders()
        run_step('import_v1', 'visit-in-operation')
        order = run_step('import_v1', 'release-discharge')
        self.assertEqual(order.status, Order.RELEASED)
        self.assertEqual(order.order_legs.count(), 3)

    def test_management_command_prints_a_summary_and_is_rerunnable(self):
        out = io.StringIO()
        call_command('scenario_step', 'import_v1', 'visit-in-operation', stdout=out)
        self.assertIn('visit V001 Arrived -> InOperation', out.getvalue())
        self.assertIn('0 created, 1 adopted', out.getvalue())
        out = io.StringIO()
        call_command('scenario_step', 'import_v1', 'visit-in-operation', stdout=out)
        self.assertIn('InOperation -> InOperation', out.getvalue())
        self.assertEqual(self.discharge_orders().count(), 1)

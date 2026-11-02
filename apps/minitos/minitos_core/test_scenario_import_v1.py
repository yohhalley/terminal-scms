"""TCL-006 acceptance tests: import_v1 seed, reset determinism and release step.

QA had no acceptance tests for TCL-006 when this was written (QA-first check
on 2026-10-27), so these tests are the acceptance contract for the ticket.
They run on SQLite (settings_test); MQTT is replaced by a fake client.
"""

import io
import json
import os
import random
import unittest
import uuid
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase

from minitos_core.models import (
    CarrierVisit, Che, ContainerVisit, Enterprise, EquipmentType, IntegrationOutbox,
    LayoutAsset, LayoutVersion, Order, OrderLeg, Position, Site, SiteConfigurationSnapshot,
)
from minitos_core.scenarios import import_v1, reset_scenario, run_step
from minitos_core.scenarios.base import (
    ALWAYS_MASKED_FIELDS, SCENARIO_NAMESPACE, VOLATILE, VOLATILE_FIELDS, _canonical_value,
    canonical_checksum, canonical_dump, container_number,
    iso6346_check_digit, scenario_uuid,
)
from minitos_core.scenarios.export import scenario_events
from minitos_core.scenarios.mqtt_reset import clear_retained

REPO_ROOT = Path(__file__).resolve().parents[3]
OES_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / 'terminal-oes' / 'execution' / 'fixtures' / 'scenario_import_v1_events.json'
)

RELEASED_SCHEMA = 'scms.order.released.v1'
PUBLISHED_SCHEMA = 'scms.site.configuration.published.v1'


class ImportV1ResetDeterminismTests(TestCase):
    def test_two_resets_produce_identical_canonical_state(self):
        reset_scenario('import_v1')
        first = canonical_dump()
        reset_scenario('import_v1')
        second = canonical_dump()

        self.assertEqual(sorted(first), sorted(second))
        for table in first:
            self.assertEqual(first[table], second[table], table)
        self.assertEqual(canonical_checksum(first), canonical_checksum(second))

    def test_reset_is_deterministic_with_five_containers_and_custom_seed(self):
        reset_scenario('import_v1', seed=7, containers=5)
        first = canonical_checksum(canonical_dump())
        reset_scenario('import_v1', seed=7, containers=5)
        self.assertEqual(first, canonical_checksum(canonical_dump()))

    def test_reset_after_release_returns_to_the_seeded_state(self):
        reset_scenario('import_v1')
        seeded = canonical_checksum(canonical_dump())
        run_step('import_v1', 'release-discharge')
        self.assertNotEqual(seeded, canonical_checksum(canonical_dump()))

        reset_scenario('import_v1')
        self.assertEqual(seeded, canonical_checksum(canonical_dump()))

    def test_outbox_delivery_bookkeeping_does_not_change_the_checksum(self):
        from django.utils import timezone
        from minitos_core.models import IntegrationOutbox

        reset_scenario('import_v1')
        unpublished = canonical_checksum(canonical_dump())
        self.assertTrue(IntegrationOutbox.objects.exists())
        IntegrationOutbox.objects.update(attempts=1, published_at=timezone.now())
        self.assertEqual(unpublished, canonical_checksum(canonical_dump()))

    def test_canonical_dump_detects_real_changes(self):
        reset_scenario('import_v1')
        before = canonical_checksum(canonical_dump())
        Che.objects.filter(name='TT02').update(name='TT99')
        self.assertNotEqual(before, canonical_checksum(canonical_dump()))

    def test_ids_are_stable_across_resets(self):
        first = reset_scenario('import_v1')
        first_ids = (
            first['site'].pk, first['site'].enterprise_id, first['layout'].pk,
            [order.pk for order in first['orders']],
            {name: che.pk for name, che in first['ches'].items()},
            first['orders'][0].container_number.container_number_id,
        )
        second = reset_scenario('import_v1')
        second_ids = (
            second['site'].pk, second['site'].enterprise_id, second['layout'].pk,
            [order.pk for order in second['orders']],
            {name: che.pk for name, che in second['ches'].items()},
            second['orders'][0].container_number.container_number_id,
        )
        self.assertEqual(first_ids, second_ids)
        self.assertEqual(first['orders'][0].pk, 1)
        self.assertEqual(first['layout'].pk, 1)

    def test_site_and_enterprise_use_uuid5_from_the_fixed_namespace(self):
        result = reset_scenario('import_v1')
        site = result['site']
        self.assertEqual(SCENARIO_NAMESPACE, uuid.UUID('7c0e5b1a-6d1e-4c55-9a2b-0000000000c6'))
        self.assertEqual(site.pk, uuid.uuid5(SCENARIO_NAMESPACE, 'site:terminal-velocity:test-terminal'))
        self.assertEqual(site.enterprise_id, scenario_uuid('enterprise', 'terminal-velocity'))
        self.assertEqual(site.pk.version, 5)

    def test_seed_changes_rng_choices_but_not_identities(self):
        default = reset_scenario('import_v1')
        default_order = default['orders'][0]
        default_number = default_order.container_number.container_number_id
        default_choices = (default_order.container_number.weight, default_order.to_position_id)
        default_site = default['site'].pk
        other = reset_scenario('import_v1', seed=12345)
        other_order = other['orders'][0]
        self.assertEqual(other['site'].pk, default_site)
        # The demo fixture number is fixed; weight and yard slot come from the RNG.
        self.assertEqual(other_order.container_number.container_number_id, default_number)
        self.assertNotEqual(
            (other_order.container_number.weight, other_order.to_position_id), default_choices,
        )

    def test_reset_keeps_auth_users(self):
        get_user_model().objects.create_user('planner', password='not-a-real-password')
        reset_scenario('import_v1')
        reset_scenario('import_v1')
        self.assertTrue(get_user_model().objects.filter(username='planner').exists())

    def test_failed_reset_rolls_back_and_keeps_previous_state(self):
        reset_scenario('import_v1')
        before = canonical_checksum(canonical_dump())
        with self.assertRaises(ValueError):
            reset_scenario('import_v1', containers=import_v1.MAX_CONTAINERS + 1)
        self.assertEqual(before, canonical_checksum(canonical_dump()))


def wall_clock_shifted(days):
    """Every timezone.now() (auto_now/auto_now_add included) runs ``days`` later."""
    from datetime import timedelta
    from django.utils import timezone

    real_now = timezone.now
    return mock.patch('django.utils.timezone.now', side_effect=lambda: real_now() + timedelta(days=days))


def first_differences(first, second, limit=10):
    found = []
    for table in sorted(set(first) | set(second)):
        rows_a, rows_b = first.get(table, []), second.get(table, [])
        if len(rows_a) != len(rows_b):
            found.append(f'{table}: {len(rows_a)} != {len(rows_b)} rows')
            continue
        for row_a, row_b in zip(rows_a, rows_b):
            for key in sorted(set(row_a) | set(row_b)):
                if row_a.get(key) != row_b.get(key):
                    found.append(f'{table}.{key}: {row_a.get(key)!r} != {row_b.get(key)!r}')
    return found[:limit]


class ScenarioDumpBusinessTimesTests(TestCase):
    """QA M1 (PR #24 review): only the explicit volatile allow-list is masked."""

    def assertSameDump(self, first, second):
        self.assertEqual(first_differences(first, second), [])
        self.assertEqual(canonical_checksum(first), canonical_checksum(second))

    def test_three_day_shift_of_business_times_changes_the_checksum(self):
        # QA probe PB: eta, ata and Order.etc moved by +3 days used to go unnoticed.
        from datetime import timedelta
        from minitos_core.models import CarrierVisit, Order

        reset_scenario('import_v1')
        seeded = canonical_checksum(canonical_dump())
        shift = timedelta(days=3)
        for field in ('eta', 'ata'):
            reset_scenario('import_v1')
            visit = CarrierVisit.objects.get(name='V001')
            CarrierVisit.objects.filter(pk=visit.pk).update(**{field: getattr(visit, field) + shift})
            self.assertNotEqual(seeded, canonical_checksum(canonical_dump()), field)
        reset_scenario('import_v1')
        order = Order.objects.order_by('pk').first()
        Order.objects.filter(pk=order.pk).update(etc=order.etc + shift)
        self.assertNotEqual(seeded, canonical_checksum(canonical_dump()), 'etc')
        reset_scenario('import_v1')
        visit = CarrierVisit.objects.get(name='V001')
        CarrierVisit.objects.filter(pk=visit.pk).update(etd=visit.etd + shift)
        self.assertNotEqual(seeded, canonical_checksum(canonical_dump()), 'etd')

    def test_business_times_are_dumped_literally(self):
        reset_scenario('import_v1')
        dump = canonical_dump()
        visit = next(row for row in dump['minitos_core_carriervisit'] if row['name'] == 'V001')
        self.assertEqual(visit['eta'], import_v1.SCENARIO_EPOCH.isoformat())
        self.assertEqual(visit['ata'], import_v1.SCENARIO_EPOCH.isoformat())
        self.assertNotIn(VOLATILE, (visit['eta'], visit['ata'], visit['etd']))
        self.assertTrue(all(row['etc'] not in (None, VOLATILE) for row in dump['minitos_core_order']))

    def test_volatile_allow_list_covers_every_wall_clock_field(self):
        """A reset three days later in wall-clock time gives the same dump.

        If this fails, a new field depends on the wall clock: add it to
        VOLATILE_FIELDS (or seed it from SCENARIO_EPOCH) and say why.
        """
        reset_scenario('import_v1')
        run_step('import_v1', 'release-discharge')
        first = canonical_dump()
        with wall_clock_shifted(3):
            reset_scenario('import_v1')
            run_step('import_v1', 'release-discharge')
        self.assertSameDump(first, canonical_dump())

    def test_allow_list_names_real_fields_only(self):
        from django.apps import apps

        models = {model._meta.model_name: model for model in apps.get_app_config('minitos_core').get_models()}
        for table, fields in list(VOLATILE_FIELDS.items()) + list(ALWAYS_MASKED_FIELDS.items()):
            self.assertIn(table, models)
            names = {field.attname for field in models[table]._meta.concrete_fields}
            self.assertLessEqual(fields, names, table)

    def test_released_at_set_or_unset_still_changes_the_checksum(self):
        from django.utils import timezone
        from minitos_core.models import Order

        reset_scenario('import_v1')
        before = canonical_checksum(canonical_dump())
        Order.objects.update(released_at=timezone.now())
        self.assertNotEqual(before, canonical_checksum(canonical_dump()))


class ImportV1SeedContentTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.result = reset_scenario('import_v1')

    def test_seeds_one_quay_block_road_and_the_che_fleet(self):
        layout = self.result['layout']
        self.assertEqual(LayoutVersion.objects.count(), 1)
        self.assertEqual(layout.status, LayoutVersion.PUBLISHED)
        self.assertTrue(layout.is_current)
        kinds = list(layout.assets.values_list('asset_kind', flat=True))
        self.assertEqual(kinds.count(LayoutAsset.QUAY), 1)
        self.assertEqual(kinds.count(LayoutAsset.YARD_BLOCK), 1)
        self.assertEqual(kinds.count(LayoutAsset.ROAD), 1)
        self.assertEqual(layout.assets.get(asset_kind=LayoutAsset.YARD_BLOCK).storage_grid.name, 'BLOCK01')

        fleet = {
            name: code for name, code in
            Che.objects.values_list('name', 'equipment_type__equipment_type')
        }
        self.assertEqual(fleet, {
            'QC01': EquipmentType.STS, 'TT01': EquipmentType.TT,
            'TT02': EquipmentType.TT, 'RTG01': EquipmentType.RTG,
        })

    def test_ches_start_powered_on_available_and_not_dispatched(self):
        # Spec K3 (QA Y2): QC01, TT01, TT02, RTG01 all available and not dispatched.
        states = {
            che.name: (che.powered_on, che.is_available, che.is_dispatched, che.working)
            for che in Che.objects.all()
        }
        self.assertEqual(states, {
            name: (True, True, False, False) for name in ('QC01', 'TT01', 'TT02', 'RTG01')
        })

    def test_seeds_one_vessel_visit_with_one_preadvised_import(self):
        visit = CarrierVisit.objects.get()
        self.assertEqual((visit.name, visit.visit_type), ('V001', CarrierVisit.VESSEL))
        self.assertEqual(visit.eta, import_v1.SCENARIO_EPOCH)
        # K7 (QA Y3): business times are on the simulator clock, ATA = sim time 0.
        self.assertEqual(visit.ata, import_v1.SCENARIO_EPOCH)
        container = ContainerVisit.objects.get()
        self.assertEqual(container.category, ContainerVisit.IMPORT)
        self.assertEqual(container.status, ContainerVisit.PREADVISED)
        self.assertEqual(container.ib_carrier, visit)
        number = container.container_number_id
        self.assertEqual(number, 'MSCU1234566')
        self.assertEqual(int(number[-1]), iso6346_check_digit(number[:10]))

        order = Order.objects.get()
        self.assertEqual((order.order_type, order.status), (Order.MOVE_IN, Order.PLANNED))
        self.assertEqual(order.from_position.logical_address['stow'], '0340682')
        self.assertEqual(order.to_position.stack_position.pile.storage_grid.name, 'BLOCK01')

    def test_block_has_room_for_five_imports(self):
        free = Position.objects.filter(
            pile__storage_grid__name='BLOCK01', tier=1, is_reserved=False, container_visit__isnull=True,
        ).count()
        self.assertGreaterEqual(free, 5)

    def test_order_legs_follow_the_handoff_chain(self):
        legs = list(OrderLeg.objects.order_by('sequence').select_related('equipment_type'))
        self.assertEqual(
            [(leg.leg_kind, leg.equipment_type.equipment_type) for leg in legs],
            [(OrderLeg.HANDLE, 'STS'), (OrderLeg.MOVE, 'TT'), (OrderLeg.HANDLE, 'RTG')],
        )
        self.assertEqual(legs[0].end_position, legs[1].start_position)
        self.assertEqual(legs[1].end_position, legs[2].start_position)
        self.assertTrue(legs[1].route_edges.exists())

    def test_site_is_published_through_the_outbox(self):
        site = Site.objects.get()
        self.assertEqual(site.lifecycle_status, Site.PUBLISHED)
        self.assertEqual(site.configuration_revision, 1)
        self.assertEqual(SiteConfigurationSnapshot.objects.count(), 1)
        schemas = list(IntegrationOutbox.objects.order_by('created_at').values_list('schema', 'topic'))
        self.assertEqual(schemas, [
            ('scms.site.created.v1', 'scms/v2/terminal-velocity/test-terminal/tos/site/created'),
            (PUBLISHED_SCHEMA, 'scms/v2/terminal-velocity/test-terminal/tos/configuration/published'),
        ])
        self.assertFalse(IntegrationOutbox.objects.filter(retain=True).exists())


class ImportV1ReleaseStepTests(TestCase):
    def setUp(self):
        reset_scenario('import_v1')

    def test_release_discharge_emits_order_released_with_handoff_legs(self):
        out = io.StringIO()
        call_command('scenario_step', 'import_v1', 'release-discharge', stdout=out)

        order = Order.objects.get()
        self.assertEqual(order.status, Order.RELEASED)
        event = IntegrationOutbox.objects.get(schema=RELEASED_SCHEMA)
        self.assertEqual(
            event.topic, f'scms/v2/terminal-velocity/test-terminal/tos/orders/{order.pk}/released',
        )
        legs = event.payload['data']['legs']
        types = dict(EquipmentType.objects.values_list('pk', 'equipment_type'))
        self.assertEqual([types[leg['equipment_type']] for leg in legs], ['STS', 'TT', 'RTG'])
        self.assertEqual(
            [leg['destination_inventory_position_id'] is None for leg in legs], [True, True, False],
        )
        # QC and RTG legs have no route edges (S2 will give them crane modes).
        self.assertEqual([leg['movement_mode'] for leg in legs], ['', 'road', ''])
        self.assertEqual(legs[1]['destination_position_code'], 'BLOCK01.TL.01')
        self.assertIn('order 1 is released', out.getvalue())

    def test_release_without_planned_orders_fails_clearly(self):
        run_step('import_v1', 'release-discharge')
        with self.assertRaises(CommandError):
            call_command('scenario_step', 'import_v1', 'release-discharge', stdout=io.StringIO())
        self.assertEqual(IntegrationOutbox.objects.filter(schema=RELEASED_SCHEMA).count(), 1)

    def test_release_of_an_explicit_order_is_idempotent(self):
        run_step('import_v1', 'release-discharge', order_id=1)
        run_step('import_v1', 'release-discharge', order_id=1)
        self.assertEqual(IntegrationOutbox.objects.filter(schema=RELEASED_SCHEMA).count(), 1)
        with self.assertRaises(ValidationError):
            run_step('import_v1', 'release-discharge', order_id=999)

    def test_unknown_step_is_rejected(self):
        with self.assertRaises(CommandError):
            call_command('scenario_step', 'import_v1', 'teleport', stdout=io.StringIO())


class ScenarioCommandTests(TestCase):
    def test_scenario_reset_command_prints_the_canonical_checksum(self):
        out = io.StringIO()
        call_command('scenario_reset', 'import_v1', '--noinput', stdout=out)
        checksum = canonical_checksum(canonical_dump())
        self.assertIn(f'canonical-sha256 {checksum}', out.getvalue())
        self.assertIn(str(scenario_uuid('site', 'terminal-velocity', 'test-terminal')), out.getvalue())

        dump_out = io.StringIO()
        call_command('scenario_dump', '--checksum', stdout=dump_out)
        self.assertEqual(dump_out.getvalue().strip(), checksum)

    def test_scenario_reset_rejects_bad_container_count(self):
        with self.assertRaises(CommandError):
            call_command('scenario_reset', 'import_v1', '--noinput', '--containers', '0', stdout=io.StringIO())

    def test_scenario_reset_requires_confirmation_when_interactive(self):
        with mock.patch('builtins.input', return_value='no'):
            with self.assertRaises(CommandError):
                call_command('scenario_reset', 'import_v1', stdout=io.StringIO())
        self.assertFalse(Site.objects.exists())
        self.assertFalse(Enterprise.objects.filter(pk=scenario_uuid('enterprise', 'terminal-velocity')).exists())


class ImportV1UnroutableSeedTests(TestCase):
    """BE review S-2: an unroutable TT leg fails the reset and rolls it back."""

    def setUp(self):
        reset_scenario('import_v1')
        self.before = canonical_checksum(canonical_dump())

    def test_unroutable_tt_leg_raises_and_keeps_the_previous_state(self):
        with mock.patch.object(
            import_v1, '_shortest_path', side_effect=ValidationError('No route found.'),
        ):
            with self.assertRaisesMessage(ValidationError, 'no road route from QC01.LANE'):
                reset_scenario('import_v1')
        self.assertEqual(canonical_checksum(canonical_dump()), self.before)

    def test_empty_tt_route_raises(self):
        with mock.patch.object(import_v1, '_shortest_path', return_value=[]):
            with self.assertRaises(ValidationError):
                reset_scenario('import_v1')
        self.assertEqual(canonical_checksum(canonical_dump()), self.before)

    def test_command_reports_the_rollback(self):
        with mock.patch.object(
            import_v1, '_shortest_path', side_effect=ValidationError('No route found.'),
        ):
            with self.assertRaisesMessage(CommandError, 'Reset rolled back'):
                call_command('scenario_reset', 'import_v1', '--noinput', stdout=io.StringIO())
        self.assertEqual(canonical_checksum(canonical_dump()), self.before)


class ImportV1SimulatorClockAndSeedTests(TestCase):
    """Spec K7 (QA Y3): one seed and one clock for the scenario and the simulator."""

    def test_defaults_match_k7(self):
        self.assertEqual(import_v1.DEFAULT_SEED, 1)
        self.assertEqual(import_v1.SIMULATOR_START_EPOCH_MS, 1792886400000)
        self.assertEqual(
            int(import_v1.SCENARIO_EPOCH.timestamp() * 1000), import_v1.SIMULATOR_START_EPOCH_MS,
        )
        self.assertEqual(import_v1.SCENARIO_EPOCH.isoformat(), '2026-10-25T00:00:00+00:00')

    def test_reset_reports_the_default_seed(self):
        self.assertEqual(reset_scenario('import_v1')['seed'], 1)

    @unittest.skipUnless((REPO_ROOT / 'compose.yaml').exists(), 'monorepo checkout required')
    def test_compose_and_makefile_use_the_same_seed_and_epoch(self):
        compose = (REPO_ROOT / 'compose.yaml').read_text()
        self.assertIn(f'SIMULATOR_SEED: ${{SIMULATOR_SEED:-{import_v1.DEFAULT_SEED}}}', compose)
        self.assertIn(
            f'SIMULATOR_START_EPOCH_MS: ${{SIMULATOR_START_EPOCH_MS:-{import_v1.SIMULATOR_START_EPOCH_MS}}}',
            compose,
        )
        makefile = (REPO_ROOT / 'Makefile').read_text()
        self.assertRegex(makefile, rf'(?m)^SEED \?= {import_v1.DEFAULT_SEED}$')
        target = re.search(r'(?ms)^scenario-reset:.*?(?=^\S)', makefile).group(0)
        self.assertIn('SIMULATOR_SEED=$(SEED)', target)
        self.assertIn('terminal-oes python manage.py scenario_reset $(SCENARIO) --noinput --seed $(SEED)', target)
        self.assertIn('--seed $(SEED) --containers $(CONTAINERS)', target)
        # QA M2: both services get the same reset epoch in one make run.
        self.assertRegex(makefile, r'(?m)^SCENARIO_RESET_EPOCH := ')
        self.assertEqual(target.count('--reset-epoch $(SCENARIO_RESET_EPOCH)'), 2)
        # TL 2026-11-02: each service's reset stops the target with a re-run hint.
        self.assertIn('|| $(call SCENARIO_RESET_FAILED,terminal-oes)', target)
        self.assertIn('|| $(call SCENARIO_RESET_FAILED,minitos)', target)


STUB_COMPOSE = '''#!/bin/sh
echo "compose $*"
case "$*" in *"$FAIL_SERVICE python manage.py scenario_reset"*) echo "reset blew up" >&2; exit 3;; esac
exit 0
'''


@unittest.skipUnless((REPO_ROOT / 'Makefile').exists(), 'monorepo checkout required')
@unittest.skipUnless(shutil.which('make'), 'make is not installed')
class ScenarioResetMakeTargetTests(SimpleTestCase):
    """TL 2026-11-02: make scenario-reset fails loudly when either service's reset fails.

    Runs the real target against a copy of the Makefile in a temp dir with a stub
    COMPOSE, so no containers are touched and no .env is created in the repo.
    """

    def make_scenario_reset(self, fail_service):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copy(REPO_ROOT / 'Makefile', tmp / 'Makefile')
            (tmp / '.env.example').write_text('')
            stub = tmp / 'compose-stub'
            stub.write_text(STUB_COMPOSE)
            stub.chmod(0o755)
            return subprocess.run(
                ['make', '--no-print-directory', '-C', str(tmp), 'scenario-reset', f'COMPOSE={stub}'],
                env={'PATH': os.environ.get('PATH', ''), 'FAIL_SERVICE': fail_service},
                capture_output=True, text=True, timeout=60,
            )

    def test_failed_reset_exits_non_zero_and_asks_for_a_re_run(self):
        for service in ('terminal-oes', 'minitos'):
            with self.subTest(service=service):
                result = self.make_scenario_reset(service)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(f'scenario-reset: {service} reset failed', result.stderr)
                self.assertIn('re-run scenario-reset', result.stderr)
                # Stops there: nothing waits for a half-reset system to be ready.
                self.assertNotIn('compose exec -T terminal-oes python manage.py scenario_wait_ready', result.stdout)

    def test_successful_reset_runs_through_to_wait_ready(self):
        result = self.make_scenario_reset('none')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('reset failed', result.stderr)  # make echoes the recipe on stdout
        self.assertIn('compose exec -T terminal-oes python manage.py scenario_wait_ready', result.stdout)


class ContainerNumberTests(TestCase):
    def test_iso6346_check_digit_matches_published_examples(self):
        # ISO 6346 / BIC worked example: CSQU 305438 3.
        self.assertEqual(iso6346_check_digit('CSQU305438'), 3)
        self.assertEqual(container_number('CSQU', 305438), 'CSQU3054383')
        self.assertEqual(iso6346_check_digit('TVLU218988'), 3)
        # Demo fixture: MSCU1234566 is valid, the old goldens' MSCU1234565 is not.
        self.assertEqual(container_number('MSCU', 123456), 'MSCU1234566')

    def test_seeded_rng_is_reproducible(self):
        self.assertEqual(
            random.Random(import_v1.DEFAULT_SEED).sample(range(10), 3),
            random.Random(import_v1.DEFAULT_SEED).sample(range(10), 3),
        )


class FakeMessage(SimpleNamespace):
    pass


class FakePublishResult:
    def wait_for_publish(self, timeout=None):
        return True


class FakeMqttClient:
    """Stands in for paho: SUBSCRIBE delivers the broker's retained messages."""

    def __init__(self, retained, live=()):
        self.retained = dict(retained)
        self.live = list(live)
        self.on_message = None
        self.subscriptions = []
        self.published = []
        self.disconnected = False

    def subscribe(self, topic_filter, qos=0):
        self.subscriptions.append(topic_filter)
        prefix = topic_filter.rstrip('#')
        for topic, payload in sorted(self.retained.items()):
            if topic.startswith(prefix):
                self.on_message(self, None, FakeMessage(topic=topic, payload=payload, retain=True))
        for topic, payload in self.live:
            if topic.startswith(prefix):
                self.on_message(self, None, FakeMessage(topic=topic, payload=payload, retain=False))

    def unsubscribe(self, topic_filter):
        pass

    def loop_start(self):
        pass

    def loop_stop(self):
        pass

    def publish(self, topic, payload, qos=0, retain=False):
        self.published.append((topic, payload, qos, retain))
        return FakePublishResult()

    def disconnect(self):
        self.disconnected = True


class ClearRetainedMqttTests(TestCase):
    RETAINED = {
        'scms/v2/terminal-velocity/test-terminal/godot/che/TT01/state': b'{"x":1}',
        'scms/v2/terminal-velocity/test-terminal/tos/configuration/published': b'{"rev":3}',
        'spBv1.0/STATE/terminal-oes': b'{"online":true}',
        'spBv1.0/TV/NDATA/edge': b'\x01',
        'other/app/topic': b'keep',
    }

    def test_clears_retained_scms_topics_and_preserves_sparkplug_host_state(self):
        client = FakeMqttClient(
            self.RETAINED, live=[('scms/v2/terminal-velocity/test-terminal/tos/orders/1/released', b'{}')],
        )
        cleared = clear_retained(client, settle_seconds=0, sleep=lambda _: None)

        self.assertEqual(cleared, [
            'scms/v2/terminal-velocity/test-terminal/godot/che/TT01/state',
            'scms/v2/terminal-velocity/test-terminal/tos/configuration/published',
            'spBv1.0/TV/NDATA/edge',
        ])
        self.assertEqual(client.published, [(topic, b'', 1, True) for topic in cleared])

    def test_dry_run_publishes_nothing(self):
        client = FakeMqttClient(self.RETAINED)
        cleared = clear_retained(client, dry_run=True, settle_seconds=0, sleep=lambda _: None)
        self.assertEqual(len(cleared), 3)
        self.assertEqual(client.published, [])

    def test_command_uses_the_injected_client(self):
        client = FakeMqttClient(self.RETAINED)
        out = io.StringIO()
        call_command(
            'clear_retained_mqtt', '--topic', 'scms/#', '--settle-seconds', '0',
            client=client, stdout=out,
        )
        self.assertEqual(client.subscriptions, ['scms/#'])
        self.assertEqual(len(client.published), 2)
        self.assertIn('Cleared 2 retained topic(s)', out.getvalue())
        self.assertFalse(client.disconnected)


class OesFixtureDriftTests(TestCase):
    """terminal-oes tests replay the exact import_v1 events; keep them in sync."""

    @staticmethod
    def canonical(document):
        document = json.loads(json.dumps(document, default=str))
        document.pop('_comment', None)
        document['snapshot_document'].pop('checksum', None)
        return _canonical_value(document)

    @unittest.skipUnless(OES_FIXTURE.exists(), 'monorepo checkout with terminal-oes required')
    def test_oes_fixture_matches_the_current_seed(self):
        expected = json.loads(OES_FIXTURE.read_text())
        self.assertEqual(
            self.canonical(scenario_events()), self.canonical(expected),
            'Seed changed: regenerate the fixture with `python manage.py scenario_export_events '
            '--settings=minitos_site.settings_test` (see the fixture _comment).',
        )

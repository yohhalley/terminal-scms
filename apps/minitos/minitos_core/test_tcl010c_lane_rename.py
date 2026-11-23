"""TCL-010c: import_v1 hand-off lane codes and the 0049 data migration."""

import importlib

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from minitos_core.models import (
    InterfacePosition,
    OperationalPosition,
    RouteEdge,
    RouteNode,
    TransferInterface,
)
from minitos_core.scenarios import reset_scenario
from minitos_core.scenarios.base import canonical_checksum, canonical_dump

MIGRATION = ('minitos_core', '0049_tcl010c_lane_code_rename')
migration = importlib.import_module(f'{MIGRATION[0]}.migrations.{MIGRATION[1]}')


def _historical_apps():
    """The app registry the migration sees when it runs (historical models)."""
    return MigrationLoader(connection).project_state(MIGRATION).apps


def _lane_codes():
    """Every code/name that carries a lane code, for both lanes."""
    rows = []
    for model, fields in [
        (TransferInterface, ['code']), (InterfacePosition, ['code']),
        (OperationalPosition, ['code']), (RouteNode, ['code', 'name']), (RouteEdge, ['code', 'name']),
    ]:
        for values in model.objects.values_list(*fields):
            rows.extend(values)
    return rows


class ImportV1LaneCodeTests(TestCase):
    def setUp(self):
        reset_scenario('import_v1')
        self.historical = _historical_apps()

    def test_seed_uses_the_agreed_lane_codes(self):
        self.assertEqual(
            sorted(TransferInterface.objects.values_list('code', flat=True)), ['BLOCK01-TL', 'QC01-L1'],
        )
        self.assertEqual(
            sorted(OperationalPosition.objects.filter(interface_position__isnull=False).values_list('code', flat=True)),
            ['BLOCK01-TL.01', 'BLOCK01-TL.02', 'QC01-L1.01'],
        )
        self.assertEqual(
            {row.logical_address['interface'] for row in OperationalPosition.objects.filter(interface_position__isnull=False)},
            {'BLOCK01-TL', 'QC01-L1'},
        )
        old = [value for value in _lane_codes() if 'QC01.LANE' in value or 'BLOCK01.TL' in value]
        self.assertEqual(old, [])

    def test_migration_renames_old_codes_and_round_trips(self):
        seeded = canonical_checksum(canonical_dump())
        migration.backwards(self.historical, None)
        values = _lane_codes()
        self.assertFalse([value for value in values if 'QC01-L1' in value or 'BLOCK01-TL' in value])
        self.assertIn('QC01.LANE.01', values)
        self.assertIn('BLOCK01.TL.02.NODE', values)
        self.assertIn('QC01.LANE.01.IN', values)
        self.assertEqual(
            OperationalPosition.objects.get(code='BLOCK01.TL.01').logical_address['interface'], 'BLOCK01.TL',
        )
        self.assertNotEqual(canonical_checksum(canonical_dump()), seeded)

        migration.forwards(self.historical, None)
        self.assertEqual(canonical_checksum(canonical_dump()), seeded)

    def test_migration_skips_when_the_target_code_already_exists(self):
        lane = TransferInterface.objects.get(code='QC01-L1')
        TransferInterface.objects.filter(pk=lane.pk).update(code='QC01.LANE')
        TransferInterface.objects.create(
            asset=lane.asset, code='QC01-L1', name='hand-built lane', axis=lane.axis,
            interface_kind=lane.interface_kind, side=lane.side, geometry=lane.geometry,
        )
        migration.forwards(self.historical, None)
        self.assertTrue(TransferInterface.objects.filter(pk=lane.pk, code='QC01.LANE').exists())

    def test_migration_is_a_no_op_without_the_lanes(self):
        TransferInterface.objects.all().delete()
        migration.forwards(self.historical, None)
        migration.backwards(self.historical, None)
        self.assertFalse(TransferInterface.objects.exists())

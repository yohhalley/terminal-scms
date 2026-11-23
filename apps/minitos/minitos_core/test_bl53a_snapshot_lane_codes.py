"""BL-53a: migration 0051 renames the TCL-010c lane codes in published site
snapshots, so an in-place migrate leaves no old code in what OES fetches."""

import copy
import importlib
import json

from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from minitos_core.models import IntegrationOutbox, SiteConfigurationSnapshot
from minitos_core.scenarios import reset_scenario
from minitos_core.services.site_sync import build_execution_snapshot, snapshot_checksum

RENAME = ('minitos_core', '0049_tcl010c_lane_code_rename')
SNAPSHOT = ('minitos_core', '0051_bl53a_snapshot_lane_codes')
rename_migration = importlib.import_module(f'{RENAME[0]}.migrations.{RENAME[1]}')
snapshot_migration = importlib.import_module(f'{SNAPSHOT[0]}.migrations.{SNAPSHOT[1]}')
OLD_CODES = ('QC01.LANE', 'BLOCK01.TL')
NEW_CODES = ('QC01-L1', 'BLOCK01-TL')
PUBLISHED = 'scms.site.configuration.published.v1'


def _historical_apps(target):
    return MigrationLoader(connection).project_state(target).apps


def _text(payload):
    return json.dumps(payload, sort_keys=True)


class SnapshotLaneCodeMigrationTests(TestCase):
    def setUp(self):
        reset_scenario('import_v1')
        self.snapshot = SiteConfigurationSnapshot.objects.order_by('-revision').first()
        self.site = self.snapshot.site
        self.layout = self.snapshot.layout_version
        self.rename_apps = _historical_apps(RENAME)
        self.snapshot_apps = _historical_apps(SNAPSHOT)

    def _publish_pre_rename_snapshot(self):
        """A database published before TCL-010c: old codes in the live rows
        and in the published snapshot, plus its undelivered published event."""
        rename_migration.backwards(self.rename_apps, None)
        payload = build_execution_snapshot(self.site, self.layout, self.snapshot.revision)
        payload['generated_at'] = self.snapshot.payload['generated_at']
        checksum = snapshot_checksum(payload)
        self.snapshot.payload = payload
        self.snapshot.checksum = checksum
        self.snapshot.save(update_fields=['payload', 'checksum'])
        events = IntegrationOutbox.objects.filter(schema=PUBLISHED)
        self.assertTrue(events.exists())
        for event in events:
            event.payload['data']['snapshot_checksum'] = checksum
            event.published_at = None
            event.save(update_fields=['payload', 'published_at'])
        return copy.deepcopy(payload), checksum

    def _migrate_in_place(self):
        rename_migration.forwards(self.rename_apps, None)
        snapshot_migration.forwards(self.snapshot_apps, None)
        self.snapshot.refresh_from_db()

    def _event_checksums(self):
        return {event.payload['data']['snapshot_checksum'] for event in IntegrationOutbox.objects.filter(schema=PUBLISHED)}

    def test_pre_rename_snapshot_gets_new_codes_and_a_valid_checksum(self):
        pre_payload, pre_checksum = self._publish_pre_rename_snapshot()
        for code in OLD_CODES:
            self.assertIn(code, _text(pre_payload))

        self._migrate_in_place()

        text = _text(self.snapshot.payload)
        for code in OLD_CODES:
            self.assertNotIn(code, text)
        for code in NEW_CODES:
            self.assertIn(code, text)
        self.assertNotEqual(self.snapshot.checksum, pre_checksum)
        self.assertEqual(self.snapshot.checksum, snapshot_checksum(self.snapshot.payload))
        # Same as what the publisher would build from the renamed live rows.
        fresh = build_execution_snapshot(self.site, self.layout, self.snapshot.revision)
        self.assertEqual(self.snapshot.payload['layout'], fresh['layout'])
        self.assertEqual(self.snapshot.payload['generated_at'], pre_payload['generated_at'])
        # The undelivered published event now points at the new checksum.
        self.assertEqual(self._event_checksums(), {self.snapshot.checksum})

    def test_reverse_restores_the_pre_rename_snapshot(self):
        pre_payload, pre_checksum = self._publish_pre_rename_snapshot()
        self._migrate_in_place()

        snapshot_migration.backwards(self.snapshot_apps, None)
        self.snapshot.refresh_from_db()

        self.assertEqual(self.snapshot.payload, pre_payload)
        self.assertEqual(self.snapshot.checksum, pre_checksum)
        self.assertEqual(self._event_checksums(), {pre_checksum})

    def test_delivered_events_are_left_alone(self):
        _, pre_checksum = self._publish_pre_rename_snapshot()
        IntegrationOutbox.objects.filter(schema=PUBLISHED).update(published_at=self.snapshot.created_at)

        self._migrate_in_place()

        self.assertEqual(self._event_checksums(), {pre_checksum})

    def test_snapshot_with_new_codes_is_unchanged(self):
        payload, checksum = copy.deepcopy(self.snapshot.payload), self.snapshot.checksum

        snapshot_migration.forwards(self.snapshot_apps, None)
        self.snapshot.refresh_from_db()

        self.assertEqual(self.snapshot.payload, payload)
        self.assertEqual(self.snapshot.checksum, checksum)

    def test_rename_payload_skips_when_the_target_code_already_exists(self):
        payload = copy.deepcopy(self.snapshot.payload)
        interfaces = payload['layout']['transfer_interfaces']
        lane = next(row for row in interfaces if row['code'] == 'QC01-L1')
        interfaces.append(dict(lane, id=lane['id'] + 1000, code='QC01.LANE'))

        self.assertFalse(snapshot_migration.rename_payload_lanes(payload, snapshot_migration.RENAMES))
        self.assertIn('QC01.LANE', [row['code'] for row in interfaces])

    def test_no_snapshots_is_a_no_op(self):
        SiteConfigurationSnapshot.objects.all().delete()
        snapshot_migration.forwards(self.snapshot_apps, None)
        self.assertFalse(SiteConfigurationSnapshot.objects.exists())

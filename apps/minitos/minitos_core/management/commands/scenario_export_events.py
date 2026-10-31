import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from minitos_core.scenarios.export import scenario_events


class Command(BaseCommand):
    help = (
        'TCL-006: regenerate the terminal-oes import_v1 event fixture. Runs a full '
        'reset, so it only runs against the in-memory test database.'
    )

    def handle(self, *args, **options):
        if not settings.SETTINGS_MODULE.endswith('settings_test'):
            raise CommandError('Run with --settings=minitos_site.settings_test (it resets the database).')
        from django.core.management import call_command

        call_command('migrate', verbosity=0, interactive=False)
        self.stdout.write(json.dumps(scenario_events(), indent=1, sort_keys=True, default=str))
        connection.close()

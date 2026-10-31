from django.core.management.base import BaseCommand

from minitos_core.mqtt import connect, create_client
from minitos_core.scenarios.mqtt_reset import DEFAULT_FILTERS, clear_retained


class Command(BaseCommand):
    help = (
        'TCL-006: delete retained MQTT messages under the SCMS topic trees '
        '(the Sparkplug host STATE topic is preserved).'
    )

    # Tests inject a fake paho client: call_command('clear_retained_mqtt', client=fake).
    stealth_options = ('client',)

    def add_arguments(self, parser):
        parser.add_argument(
            '--topic', action='append', dest='filters',
            help=f'Topic filter to clear (repeatable). Default: {", ".join(DEFAULT_FILTERS)}.',
        )
        parser.add_argument('--settle-seconds', type=float, default=1.0)
        parser.add_argument('--dry-run', action='store_true', help='List retained topics without clearing them.')

    def handle(self, *args, client=None, **options):
        filters = tuple(options['filters'] or DEFAULT_FILTERS)
        owns_client = client is None
        if owns_client:
            client = create_client('scenario-reset')
            connect(client)
        try:
            topics = clear_retained(
                client, filters, settle_seconds=options['settle_seconds'], dry_run=options['dry_run'],
            )
        finally:
            if owns_client:
                client.disconnect()
        verb = 'Found' if options['dry_run'] else 'Cleared'
        self.stdout.write(f'{verb} {len(topics)} retained topic(s) under {", ".join(filters)}.')
        for topic in topics:
            self.stdout.write(f'  {topic}')

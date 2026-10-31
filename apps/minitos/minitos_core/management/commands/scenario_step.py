from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from minitos_core.scenarios import SCENARIOS, run_step


class Command(BaseCommand):
    help = 'TCL-006: run one scripted scenario step (e.g. import_v1 release-discharge).'

    def add_arguments(self, parser):
        parser.add_argument('scenario', choices=sorted(SCENARIOS))
        parser.add_argument('step')
        parser.add_argument('--order', type=int, default=None, help='Order id (default: next planned order).')

    def handle(self, *args, **options):
        try:
            order = run_step(options['scenario'], options['step'], order_id=options['order'])
        except (LookupError, ValidationError) as exc:
            message = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            raise CommandError(message) from exc
        self.stdout.write(self.style.SUCCESS(
            f'{options["step"]}: order {order.pk} is {order.status}; '
            'scms.order.released.v1 queued in the outbox.'
        ))

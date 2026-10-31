from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from minitos_core.scenarios import SCENARIOS, reset_scenario
from minitos_core.scenarios.base import canonical_checksum, canonical_dump


class Command(BaseCommand):
    help = (
        'TCL-006: wipe every MiniTOS (minitos_core) table and seed a demo scenario. '
        'Auth users are kept. Publishes the Site through the normal outbox.'
    )

    def add_arguments(self, parser):
        parser.add_argument('scenario', choices=sorted(SCENARIOS))
        parser.add_argument(
            '--seed', type=int, default=None,
            help='Scenario RNG seed; make scenario-reset passes SEED (default 1, the K7 SIMULATOR_SEED).',
        )
        parser.add_argument('--containers', type=int, default=1, help='Import containers to pre-advise (1-5).')
        parser.add_argument(
            '--noinput', '--no-input', action='store_false', dest='interactive',
            help='Do not ask for confirmation (used by make scenario-reset).',
        )

    def handle(self, *args, **options):
        name = options['scenario']
        if options['interactive']:
            answer = input(
                f'This deletes ALL MiniTOS terminal data and seeds {name}. Type "yes" to continue: '
            )
            if answer.strip().lower() != 'yes':
                raise CommandError('Reset cancelled.')
        try:
            result = reset_scenario(name, seed=options['seed'], containers=options['containers'])
        except (LookupError, ValueError) as exc:
            raise CommandError(str(exc)) from exc
        except ValidationError as exc:
            # BE review S-2: e.g. an unroutable TT leg; the reset was rolled back.
            raise CommandError('Reset rolled back: ' + '; '.join(exc.messages)) from exc
        site = result['site']
        self.stdout.write(self.style.SUCCESS(
            f'{name}: wiped {result["tables_wiped"]} tables, seed {result["seed"]}; '
            f'site {site.enterprise.code}/{site.code} ({site.pk}) revision {site.configuration_revision}; '
            f'visit {result["visit"].name} with {len(result["orders"])} planned discharge order(s): '
            + ', '.join(str(order.pk) for order in result['orders'])
        ))
        self.stdout.write(f'canonical-sha256 {canonical_checksum(canonical_dump())}')

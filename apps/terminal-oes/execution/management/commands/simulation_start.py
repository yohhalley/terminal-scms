from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from execution.models import SiteProjection
from execution.services.simulations import start_simulation


class Command(BaseCommand):
    help = (
        "Runner (S2): start a simulation session for a READY Site, exactly as the OES "
        "Simulations page does. Idempotent: an already-active session is reused. "
        "The simulator-runner service claims it and launches headless Godot."
    )

    def add_arguments(self, parser):
        parser.add_argument("--site", default="test-terminal", help="Site code (default: test-terminal).")
        parser.add_argument("--time-scale", type=float, default=1.0)
        parser.add_argument("--simulator-version", default="local")

    def handle(self, *args, **options):
        site = SiteProjection.objects.filter(code=options["site"]).first()
        if site is None:
            raise CommandError(
                f"No Site projection {options['site']!r}; run `make scenario-reset` first."
            )
        try:
            session, created = start_simulation(
                site.pk,
                simulator_version=options["simulator_version"],
                time_scale=options["time_scale"],
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Simulation {session.pk} for Site {site.code} "
            f"{'created' if created else 'already active'} ({session.actual_state})."
        ))

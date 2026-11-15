from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from execution.models import SimulationSession, SiteProjection
from execution.services.simulations import start_simulation

# Same range as SimulationSession.time_scale (models.py validators) and
# StartSimulationSerializer. objects.create() does not run model validators, so
# the command checks it itself: 0 would hand Godot a frozen sim.
TIME_SCALE_MIN = 0.01
TIME_SCALE_MAX = 100.0


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
        time_scale = options["time_scale"]
        # Written so that NaN fails too.
        if not (TIME_SCALE_MIN <= time_scale <= TIME_SCALE_MAX):
            raise CommandError(
                f"--time-scale must be between {TIME_SCALE_MIN:g} and {TIME_SCALE_MAX:g} "
                f"(got {time_scale:g})."
            )
        site = SiteProjection.objects.filter(code=options["site"]).first()
        if site is None:
            raise CommandError(
                f"No Site projection {options['site']!r}; run `make scenario-reset` first."
            )
        try:
            session, created = start_simulation(
                site.pk,
                simulator_version=options["simulator_version"],
                time_scale=time_scale,
            )
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        if not created and session.actual_state == SimulationSession.STOPPING:
            # Nothing will run and no new session can be created until the
            # runner confirms the stop, so this is not a successful start.
            raise CommandError(
                f"Previous simulation {session.pk} for Site {site.code} is still stopping; "
                "run `make sim-stop` (simulation_stop --wait) and check the simulator-runner "
                "logs (`make logs-runner`), then start again."
            )
        self.stdout.write(self.style.SUCCESS(
            f"Simulation {session.pk} for Site {site.code} "
            f"{'created' if created else 'already active'} ({session.actual_state})."
        ))

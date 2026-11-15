import time

from django.core.management.base import BaseCommand, CommandError

from execution.scenarios import active_sessions
from execution.services.simulations import stop_simulation


class Command(BaseCommand):
    help = (
        "Runner (S2): request a stop for every active simulation session and wait until "
        "the simulator runner has confirmed it, so `scenario_reset` can run."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--wait", type=float, default=30.0,
            help="Seconds to wait for the runner to confirm (0 = do not wait).",
        )
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options):
        session_ids = list(active_sessions().values_list("pk", flat=True))
        for session_id in session_ids:
            stop_simulation(session_id)
        if not session_ids:
            self.stdout.write("No active simulation session.")
            return
        deadline = time.monotonic() + max(options["wait"], 0.0)
        while True:
            remaining = list(active_sessions().values_list("pk", flat=True))
            if not remaining:
                self.stdout.write(self.style.SUCCESS(
                    f"Stopped {len(session_ids)} simulation session(s)."
                ))
                return
            if time.monotonic() >= deadline:
                raise CommandError(
                    f"{len(remaining)} simulation session(s) still stopping after "
                    f"{options['wait']:.0f}s: {', '.join(str(pk) for pk in remaining)}. "
                    "Is the simulator-runner service running? (`docker compose --profile "
                    "simulation logs simulator-runner`). A stuck session can be cleared with "
                    "`scenario_reset --force`."
                )
            time.sleep(options["interval"])

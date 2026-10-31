from django.core.management.base import BaseCommand, CommandError

from execution.scenarios import wait_for_ready_site


class Command(BaseCommand):
    help = "TCL-006: wait until the Site projection is READY (after scenario-reset)."

    def add_arguments(self, parser):
        parser.add_argument("--site", default="test-terminal", help="Site code (default: test-terminal).")
        parser.add_argument("--min-revision", type=int, default=1)
        parser.add_argument("--timeout", type=float, default=60.0)
        parser.add_argument("--interval", type=float, default=1.0)

    def handle(self, *args, **options):
        try:
            site = wait_for_ready_site(
                options["site"], min_revision=options["min_revision"],
                timeout=options["timeout"], interval=options["interval"],
            )
        except TimeoutError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"Site {site} ({site.source_id}) READY at revision {site.source_revision} "
            f"with {site.equipment.count()} CHE(s)."
        ))

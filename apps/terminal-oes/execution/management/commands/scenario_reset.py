from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from execution.scenarios import SCENARIOS, reset_execution_tables


class Command(BaseCommand):
    help = (
        "TCL-006: wipe every terminal-oes (execution) table so the next MiniTOS "
        "Site publish is projected from scratch. Auth users are kept."
    )

    def add_arguments(self, parser):
        parser.add_argument("scenario", choices=SCENARIOS)
        parser.add_argument(
            "--noinput", "--no-input", action="store_false", dest="interactive",
            help="Do not ask for confirmation (used by make scenario-reset).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Reset even if a simulation session is still active.",
        )

    def handle(self, *args, **options):
        if options["interactive"]:
            answer = input("This deletes ALL terminal-oes execution data. Type \"yes\" to continue: ")
            if answer.strip().lower() != "yes":
                raise CommandError("Reset cancelled.")
        try:
            tables = reset_execution_tables(force=options["force"])
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"{options['scenario']}: wiped {tables} terminal-oes tables; "
            "waiting for MiniTOS to publish the Site."
        ))

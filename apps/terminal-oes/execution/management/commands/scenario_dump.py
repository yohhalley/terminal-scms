import json

from django.core.management.base import BaseCommand

from execution.scenarios import canonical_checksum, canonical_dump


class Command(BaseCommand):
    help = "TCL-006: print a canonical (volatile-free) dump of the terminal-oes tables, or its sha256."

    def add_arguments(self, parser):
        parser.add_argument("--checksum", action="store_true")

    def handle(self, *args, **options):
        dump = canonical_dump()
        if options["checksum"]:
            self.stdout.write(canonical_checksum(dump))
            return
        self.stdout.write(json.dumps(dump, sort_keys=True, indent=1, default=str))

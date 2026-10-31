from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from execution.scenarios import SCENARIOS, next_dispatchable_order
from execution.services.dispatch import dispatch_next_operation

STEPS = ("dispatch-next",)


class Command(BaseCommand):
    help = (
        "TCL-006: run one OES scenario step. dispatch-next dispatches the next step of "
        "the oldest released order (same service as POST /api/orders/{id}/dispatch-next/)."
    )

    def add_arguments(self, parser):
        parser.add_argument("scenario", choices=SCENARIOS)
        parser.add_argument("step", choices=STEPS)
        parser.add_argument("--order", type=int, default=None, help="MiniTOS (TOS) order id.")

    def handle(self, *args, **options):
        try:
            order = next_dispatchable_order(options["order"])
            assignment = dispatch_next_operation(order.pk)
        except ValidationError as exc:
            raise CommandError("; ".join(exc.messages)) from exc
        self.stdout.write(self.style.SUCCESS(
            f"dispatch-next: TOS order {order.tos_order_id} leg "
            f"{assignment.operation.sequence} -> {assignment.equipment.name}."
        ))

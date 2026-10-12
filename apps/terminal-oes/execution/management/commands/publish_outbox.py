import json
import time

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from execution.models import EquipmentCommand, OutboxEvent
from execution.mqtt import connect, create_client


class Command(BaseCommand):
    help = "Publish pending transactional-outbox records to the MQTT UNS."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-interval", type=float, default=0.5)

    def handle(self, *args, **options):
        client = create_client("outbox")
        connect(client)
        client.loop_start()
        try:
            while True:
                published = self._publish_batch(client)
                if options["once"]:
                    break
                if not published:
                    time.sleep(options["poll_interval"])
        except KeyboardInterrupt:
            self.stdout.write("Stopping OES outbox publisher.")
        finally:
            client.disconnect()
            client.loop_stop()

    def _publish_batch(self, client):
        event_ids = list(
            OutboxEvent.objects.filter(published_at__isnull=True)
            .values_list("event_id", flat=True)[:100]
        )
        count = 0
        for event_id in event_ids:
            with transaction.atomic():
                event = OutboxEvent.objects.select_for_update().get(pk=event_id)
                if event.published_at:
                    continue
                event.attempts += 1
                event.save(update_fields=["attempts"])
                try:
                    result = client.publish(
                        event.topic,
                        json.dumps(event.payload, separators=(",", ":")),
                        qos=event.qos,
                        retain=event.retain,
                    )
                    result.wait_for_publish(timeout=10)
                    if not result.is_published():
                        raise RuntimeError("MQTT publish acknowledgement timed out.")
                except Exception as exc:
                    event.last_error = str(exc)
                    event.save(update_fields=["last_error"])
                    continue
                now = timezone.now()
                event.published_at = now
                event.last_error = ""
                event.save(update_fields=["published_at", "last_error"])
                EquipmentCommand.objects.filter(
                    command_id=event.event_id,
                    status=EquipmentCommand.PENDING,
                ).update(status=EquipmentCommand.PUBLISHED, published_at=now)
                count += 1
        return count

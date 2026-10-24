import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from execution.mqtt import connect, create_client
from execution.services.messages import process_message
from execution.uns import subscriptions


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Consume released orders, equipment state and command results from the UNS."

    def handle(self, *args, **options):
        client = create_client("consumer", host_application=True)

        def on_connect(connected_client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                logger.error("MQTT connection failed: %s", reason_code)
                return
            connected_client.publish(
                f"spBv1.0/STATE/{settings.SPARKPLUG_HOST_ID}",
                "ONLINE",
                qos=1,
                retain=True,
            )
            for topic, qos in subscriptions():
                connected_client.subscribe(topic, qos=qos)
                self.stdout.write(f"Subscribed: {topic}")

        def on_message(connected_client, userdata, message):
            try:
                process_message(message.topic, message.payload)
            except Exception:
                logger.exception("Unable to process MQTT message on %s", message.topic)

        client.on_connect = on_connect
        client.on_message = on_message
        connect(client, retry=True)
        self.stdout.write(self.style.SUCCESS("OES MQTT consumer connected."))
        try:
            client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            self.stdout.write("Stopping OES MQTT consumer.")
        finally:
            client.publish(
                f"spBv1.0/STATE/{settings.SPARKPLUG_HOST_ID}",
                "OFFLINE",
                qos=1,
                retain=True,
            ).wait_for_publish(timeout=5)
            client.disconnect()

import logging

from django.conf import settings
from django.core.management.base import BaseCommand

from minitos_core.mqtt import connect, create_client
from minitos_core.services.integration_events import apply_execution_message


logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Consume Terminal OES execution events from the MQTT UNS.'

    def handle(self, *args, **options):
        client = create_client('consumer')

        def on_connect(connected_client, userdata, flags, reason_code, properties):
            if reason_code.is_failure:
                logger.error('MQTT connection failed: %s', reason_code)
                return
            topics = [
                f'{settings.UNS_ROOT}/+/oes/orders/+/execution',
                f'{settings.SITE_UNS_ROOT}/+/+/oes/orders/+/execution',
            ]
            for topic in topics:
                connected_client.subscribe(topic, qos=1)
                self.stdout.write(f'Subscribed: {topic}')

        def on_message(connected_client, userdata, message):
            try:
                apply_execution_message(message.topic, message.payload)
            except Exception:
                logger.exception('Unable to process OES event on %s', message.topic)

        client.on_connect = on_connect
        client.on_message = on_message
        connect(client, retry=True)
        self.stdout.write(self.style.SUCCESS('MiniTOS UNS consumer connected.'))
        try:
            client.loop_forever(retry_first_connection=True)
        except KeyboardInterrupt:
            self.stdout.write('Stopping MiniTOS UNS consumer.')
        finally:
            client.disconnect()

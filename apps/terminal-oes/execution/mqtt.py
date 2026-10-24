import logging
import time

from django.conf import settings
from paho.mqtt import client as mqtt


logger = logging.getLogger(__name__)


def create_client(suffix, *, host_application=False):
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f"{settings.MQTT_CLIENT_ID}-{suffix}",
        protocol=mqtt.MQTTv311,
    )
    if settings.MQTT_USERNAME:
        client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    if settings.MQTT_TLS:
        client.tls_set()
    if host_application:
        client.will_set(
            f"spBv1.0/STATE/{settings.SPARKPLUG_HOST_ID}",
            payload="OFFLINE",
            qos=1,
            retain=True,
        )
    return client


def connect(client, *, retry=False, retry_interval=5.0):
    while True:
        try:
            return client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)
        except OSError as exc:
            if not retry:
                raise
            logger.warning(
                "MQTT broker %s:%s is unavailable (%s); retrying in %.1f seconds.",
                settings.MQTT_HOST,
                settings.MQTT_PORT,
                exc,
                retry_interval,
            )
            time.sleep(retry_interval)

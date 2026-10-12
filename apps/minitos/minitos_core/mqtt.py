from django.conf import settings
from paho.mqtt import client as mqtt


def create_client(suffix):
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=f'{settings.MQTT_CLIENT_ID}-{suffix}',
        protocol=mqtt.MQTTv311,
    )
    if settings.MQTT_USERNAME:
        client.username_pw_set(settings.MQTT_USERNAME, settings.MQTT_PASSWORD)
    if settings.MQTT_TLS:
        client.tls_set()
    return client


def connect(client):
    client.connect(settings.MQTT_HOST, settings.MQTT_PORT, keepalive=60)

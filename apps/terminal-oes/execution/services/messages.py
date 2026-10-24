import json
from django.core.exceptions import ValidationError
from execution.services.ingestion import ingest_released_order
from execution.services.site_sync import (
    ingest_site_configuration_published,
    ingest_site_created,
)
from execution.services.sparkplug_runtime import process_sparkplug_message


def _decode(payload):
    try:
        value = json.loads(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("MQTT payload must be a UTF-8 JSON object.") from exc
    if not isinstance(value, dict):
        raise ValidationError("MQTT payload must be a JSON object.")
    return value


def process_message(topic, raw_payload):
    if topic.startswith("spBv1.0/"):
        return process_sparkplug_message(topic, raw_payload)
    payload = _decode(raw_payload)
    schema = payload.get("schema")
    if schema == "scms.order.released.v1":
        return ingest_released_order(topic, payload)
    if schema == "scms.site.created.v1":
        return ingest_site_created(topic, payload)
    if schema == "scms.site.configuration.published.v1":
        return ingest_site_configuration_published(topic, payload)
    raise ValidationError(f"Unsupported UNS message schema: {schema!r}.")

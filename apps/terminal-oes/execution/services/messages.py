import json
from django.core.exceptions import ValidationError
from execution.services.ingestion import ingest_released_order
from execution.services.site_sync import (
    ingest_site_configuration_published,
    ingest_site_created,
)
from execution.services.reset_epoch import ignore_if_stale
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
    # QA M2: after make scenario-reset the Site uuid5 and the ids are the same
    # again, so a MiniTOS envelope from the previous run (queued in the
    # persistent session, or published from an old outbox row) would be valid.
    # Ignore it before it reaches the inbox or a projection (logged; None).
    if ignore_if_stale(topic, payload):
        return None
    schema = payload.get("schema")
    if schema == "scms.order.released.v1":
        return ingest_released_order(topic, payload)
    if schema == "scms.site.created.v1":
        return ingest_site_created(topic, payload)
    if schema == "scms.site.configuration.published.v1":
        return ingest_site_configuration_published(topic, payload)
    raise ValidationError(f"Unsupported UNS message schema: {schema!r}.")

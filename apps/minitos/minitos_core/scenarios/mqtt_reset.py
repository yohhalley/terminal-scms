"""Clear retained MQTT messages before a scenario reset (TCL-006).

Today no scms/v2 business event is published retained (see the outbox
``retain`` flag), but Godot and future workers may retain state topics, and a
stale retained message would leak the previous run into the next one. Clearing
is done the MQTT way: publish an empty retained payload to every topic that
currently holds a retained message.

The Sparkplug host STATE topic (``spBv1.0/STATE/<host>``) is owned by the OES
host application and is never cleared: deleting it would make edge nodes think
the primary host went offline.
"""

import time

DEFAULT_FILTERS = ('scms/#', 'spBv1.0/#')
PROTECTED_PREFIXES = ('spBv1.0/STATE/',)


def _is_protected(topic, protected_prefixes):
    return any(topic.startswith(prefix) for prefix in protected_prefixes)


def collect_retained(client, filters=DEFAULT_FILTERS, *, settle_seconds=1.0,
                     protected_prefixes=PROTECTED_PREFIXES, sleep=time.sleep):
    """Subscribe to ``filters`` on a connected client and return retained topics.

    The broker delivers retained messages right after SUBSCRIBE; ``settle_seconds``
    is how long we wait for them. Live (non-retained) traffic is ignored.
    """
    retained = set()

    def on_message(_client, _userdata, message):
        if message.retain and message.payload and not _is_protected(message.topic, protected_prefixes):
            retained.add(message.topic)

    client.on_message = on_message
    for topic_filter in filters:
        client.subscribe(topic_filter, qos=1)
    client.loop_start()
    try:
        sleep(settle_seconds)
    finally:
        client.loop_stop()
        for topic_filter in filters:
            client.unsubscribe(topic_filter)
    return sorted(retained)


def clear_retained(client, filters=DEFAULT_FILTERS, *, settle_seconds=1.0,
                   protected_prefixes=PROTECTED_PREFIXES, dry_run=False, sleep=time.sleep):
    """Delete every retained message under ``filters``; return the cleared topics."""
    topics = collect_retained(
        client, filters, settle_seconds=settle_seconds,
        protected_prefixes=protected_prefixes, sleep=sleep,
    )
    if dry_run or not topics:
        return topics
    client.loop_start()
    try:
        for topic in topics:
            result = client.publish(topic, b'', qos=1, retain=True)
            result.wait_for_publish(timeout=10)
    finally:
        client.loop_stop()
    return topics

"""Minimal Sparkplug B payload codec for the S1 e2e runner.

Copied from apps/terminal-oes/execution/sparkplug.py, without Django, so the
runner can read the session NBIRTH (and, if needed, send a Rebirth NCMD)
without importing the OES service.
"""
from __future__ import annotations

import struct
import time

DATA_TYPES = {
    "Int32": 3,
    "Int64": 4,
    "Float": 9,
    "Double": 10,
    "Boolean": 11,
    "String": 12,
    "DateTime": 13,
}
DATA_TYPE_NAMES = {value: key for key, value in DATA_TYPES.items()}


def _varint(value: int) -> bytes:
    value = max(int(value), 0)
    encoded = bytearray()
    while value >= 0x80:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value & 0x7F)
    return bytes(encoded)


def _key(field_number: int, wire_type: int) -> bytes:
    return _varint((field_number << 3) | wire_type)


def _varint_field(field_number: int, value: int) -> bytes:
    return _key(field_number, 0) + _varint(value)


def _bytes_field(field_number: int, value: bytes) -> bytes:
    return _key(field_number, 2) + _varint(len(value)) + value


def _string_field(field_number: int, value: str) -> bytes:
    return _bytes_field(field_number, str(value).encode("utf-8"))


def encode_metric(metric: dict, timestamp_ms: int) -> bytes:
    name = str(metric.get("name", ""))
    data_type = str(metric.get("type", "String"))
    if not name:
        raise ValueError("A Sparkplug metric name is required.")
    if data_type not in DATA_TYPES:
        raise ValueError(f"Unsupported Sparkplug metric type: {data_type}")
    encoded = bytearray()
    encoded += _string_field(1, name)
    if metric.get("alias") is not None:
        encoded += _varint_field(2, metric["alias"])
    encoded += _varint_field(3, metric.get("timestamp", timestamp_ms))
    encoded += _varint_field(4, DATA_TYPES[data_type])
    value = metric.get("value")
    if data_type == "Int32":
        encoded += _varint_field(10, value)
    elif data_type in {"Int64", "DateTime"}:
        encoded += _varint_field(11, value)
    elif data_type == "Float":
        encoded += _key(12, 5) + struct.pack("<f", float(value))
    elif data_type == "Double":
        encoded += _key(13, 1) + struct.pack("<d", float(value))
    elif data_type == "Boolean":
        encoded += _varint_field(14, 1 if value else 0)
    else:
        encoded += _string_field(15, value)
    return bytes(encoded)


def encode_payload(metrics=(), *, sequence: int = 0, timestamp_ms=None, include_sequence: bool = True) -> bytes:
    timestamp_ms = int(timestamp_ms if timestamp_ms is not None else time.time() * 1000)
    encoded = bytearray(_varint_field(1, timestamp_ms))
    for metric in metrics:
        encoded += _bytes_field(2, encode_metric(metric, timestamp_ms))
    if include_sequence:
        encoded += _varint_field(3, sequence % 256)
    return bytes(encoded)


def encode_rebirth_ncmd() -> bytes:
    return encode_payload([{"name": "Node Control/Rebirth", "type": "Boolean", "value": True}])


def _read_varint(payload: bytes, offset: int):
    value = 0
    shift = 0
    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            break
    raise ValueError("Malformed Sparkplug protobuf varint.")


def _read_field(payload: bytes, offset: int):
    key, offset = _read_varint(payload, offset)
    field_number = key >> 3
    wire_type = key & 0x07
    if wire_type == 0:
        value, offset = _read_varint(payload, offset)
    elif wire_type == 1:
        end = offset + 8
        if end > len(payload):
            raise ValueError("Truncated Sparkplug fixed64 field.")
        value, offset = payload[offset:end], end
    elif wire_type == 2:
        length, offset = _read_varint(payload, offset)
        end = offset + length
        if end > len(payload):
            raise ValueError("Truncated Sparkplug bytes field.")
        value, offset = payload[offset:end], end
    elif wire_type == 5:
        end = offset + 4
        if end > len(payload):
            raise ValueError("Truncated Sparkplug fixed32 field.")
        value, offset = payload[offset:end], end
    else:
        raise ValueError(f"Unsupported Sparkplug protobuf wire type {wire_type}.")
    return field_number, wire_type, value, offset


def decode_metric(payload: bytes) -> dict:
    result = {"name": "", "alias": None, "timestamp": None, "datatype": 0, "value": None}
    offset = 0
    while offset < len(payload):
        field, wire, value, offset = _read_field(payload, offset)
        if field == 1 and wire == 2:
            result["name"] = value.decode("utf-8")
        elif field == 2 and wire == 0:
            result["alias"] = value
        elif field == 3 and wire == 0:
            result["timestamp"] = value
        elif field == 4 and wire == 0:
            result["datatype"] = value
        elif field == 7 and wire == 0:
            result["is_null"] = bool(value)
        elif field == 10 and wire == 0:
            result["value"] = value
        elif field == 11 and wire == 0:
            result["value"] = value
        elif field == 12 and wire == 5:
            result["value"] = struct.unpack("<f", value)[0]
        elif field == 13 and wire == 1:
            result["value"] = struct.unpack("<d", value)[0]
        elif field == 14 and wire == 0:
            result["value"] = bool(value)
        elif field == 15 and wire == 2:
            result["value"] = value.decode("utf-8")
        elif field == 16 and wire == 2:
            result["value"] = bytes(value)
    result["type"] = DATA_TYPE_NAMES.get(result["datatype"], "Unknown")
    return result


def decode_payload(payload) -> dict:
    if not isinstance(payload, (bytes, bytearray)):
        raise ValueError("Sparkplug payload must be bytes.")
    result = {"timestamp": None, "metrics": [], "seq": None}
    offset = 0
    while offset < len(payload):
        field, wire, value, offset = _read_field(payload, offset)
        if field == 1 and wire == 0:
            result["timestamp"] = value
        elif field == 2 and wire == 2:
            result["metrics"].append(decode_metric(value))
        elif field == 3 and wire == 0:
            result["seq"] = value
        elif field == 4 and wire == 2:
            result["uuid"] = value.decode("utf-8")
        elif field == 5 and wire == 2:
            result["body"] = bytes(value)
    return result


def metrics_by_name(document: dict) -> dict:
    return {
        metric["name"]: metric["value"]
        for metric in document.get("metrics", [])
        if metric.get("name")
    }


def parse_sparkplug_topic(topic: str) -> dict:
    parts = str(topic).split("/")
    if len(parts) not in {4, 5} or parts[0] != "spBv1.0":
        raise ValueError(f"Invalid Sparkplug topic: {topic}")
    message_type = parts[2]
    return {
        "group_id": parts[1],
        "message_type": message_type,
        "node_id": parts[3],
        "device_id": parts[4] if len(parts) == 5 else "",
    }

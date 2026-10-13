class_name SparkPlugMQTTManager
extends Node

signal broker_connected
signal broker_disconnected
signal broker_connection_failed(message: String)
signal publish_queued(message_type: String, topic: String, packet_id: int)
signal publish_failed(message_type: String, topic: String, message: String)
signal publish_acknowledged(packet_id: int)
signal message_received(topic: String, payload: PackedByteArray)

enum ConnectionState { DISCONNECTED, CONNECTING, CONNECTED }

const MQTT_SCRIPT = preload("res://addons/mqtt/mqtt.gd")
const BDSEQ_PATH = "user://sparkplug_session.cfg"
const DATA_TYPES = {
	"Int32": 3,
	"Int64": 4,
	"Float": 9,
	"Double": 10,
	"Boolean": 11,
	"String": 12,
	"DateTime": 13,
}

var broker_url := "tcp://127.0.0.1:1883"
var group_id := "MINI-TERMINAL"
var node_id := "GODOT-SANDBOX"
var client_id := "godot-terminal-sandbox"
var username := ""
var password := ""

var sequence_number := 0
var birth_death_sequence := 0
var mqtt = null

var _state := ConnectionState.DISCONNECTED
var _connection_attempted := false
var _intentional_disconnect := false


func _ready():
	_load_next_bdseq()
	_ensure_transport()


func _exit_tree():
	if _state == ConnectionState.CONNECTED:
		disconnect_from_broker()


func _ensure_transport():
	if mqtt == null or not is_instance_valid(mqtt):
		mqtt = get_node_or_null("MQTT")
	if mqtt == null:
		mqtt = MQTT_SCRIPT.new()
		mqtt.name = "MQTT"
		add_child(mqtt)
	mqtt.binarymessages = true
	mqtt.verbose_level = 1
	mqtt.pinginterval = 30
	_connect_once(mqtt.broker_connected, _on_transport_connected)
	_connect_once(mqtt.broker_disconnected, _on_transport_disconnected)
	_connect_once(mqtt.broker_connection_failed, _on_transport_connection_failed)
	_connect_once(mqtt.publish_acknowledge, _on_publish_acknowledged)
	if mqtt.has_signal("received_message"):
		_connect_once(mqtt.received_message, _on_message_received)


func _connect_once(source_signal: Signal, callback: Callable):
	if not source_signal.is_connected(callback):
		source_signal.connect(callback)


func is_ready_for_connection() -> bool:
	return mqtt != null and is_instance_valid(mqtt) and mqtt.is_node_ready()


func is_broker_connected() -> bool:
	return _state == ConnectionState.CONNECTED


func configure(
	p_broker_url: String,
	p_group_id: String,
	p_node_id: String,
	p_client_id: String,
	p_username := "",
	p_password := ""
):
	broker_url = p_broker_url
	group_id = p_group_id
	node_id = p_node_id
	client_id = p_client_id
	username = p_username
	password = p_password


func connect_to_broker() -> bool:
	_ensure_transport()
	if _state == ConnectionState.CONNECTED:
		return true
	if _state == ConnectionState.CONNECTING:
		return false
	if _connection_attempted:
		_advance_bdseq()
	_connection_attempted = true
	sequence_number = 0
	_intentional_disconnect = false
	mqtt.client_id = client_id
	if username.strip_edges().is_empty():
		mqtt.set_user_pass(null, null)
	else:
		mqtt.set_user_pass(username, password)
	mqtt.set_last_will(_topic("NDEATH"), _ndeath_payload(), false, 1)
	_state = ConnectionState.CONNECTING
	print("MQTT connecting: %s as %s" % [broker_url, client_id])
	if not mqtt.connect_to_broker(broker_url):
		_state = ConnectionState.DISCONNECTED
		broker_connection_failed.emit("MQTT transport rejected broker URL %s" % broker_url)
		return false
	return true


func disconnect_from_broker():
	if mqtt == null:
		return
	_intentional_disconnect = true
	if _state == ConnectionState.CONNECTED:
		publish_node_death()
	mqtt.disconnect_from_server()
	_state = ConnectionState.DISCONNECTED


func publish_device_birth(device_id: String, metrics: Array) -> bool:
	return _publish("DBIRTH", _topic("DBIRTH", device_id), _payload(metrics), 0)


func publish_device_data(device_id: String, metrics: Array) -> bool:
	return _publish("DDATA", _topic("DDATA", device_id), _payload(metrics), 0)


func publish_device_death(device_id: String) -> bool:
	return _publish("DDEATH", _topic("DDEATH", device_id), _payload(), 0)


func publish_node_birth() -> bool:
	var metrics = [
		metric("bdSeq", "Int64", birth_death_sequence),
		metric("Node Control/Rebirth", "Boolean", false),
	]
	return _publish("NBIRTH", _topic("NBIRTH"), _payload(metrics), 0)


func publish_node_death() -> bool:
	return _publish("NDEATH", _topic("NDEATH"), _ndeath_payload(), 1)


func publish_json(topic: String, payload: Dictionary, qos := 1, retain := false) -> bool:
	return _publish("JSON", topic, JSON.stringify(payload).to_utf8_buffer(), qos, retain)


func subscribe(topic: String, qos := 1) -> bool:
	if _state != ConnectionState.CONNECTED:
		return false
	return int(mqtt.subscribe(topic, qos)) >= 0


func unsubscribe(topic: String) -> bool:
	if mqtt == null:
		return false
	mqtt.unsubscribe(topic)
	return true


func _publish(message_type: String, topic: String, payload: PackedByteArray, qos: int, retain := false) -> bool:
	if _state != ConnectionState.CONNECTED:
		publish_failed.emit(message_type, topic, "MQTT broker is not connected")
		return false
	var packet_id = int(mqtt.publish(topic, payload, retain, qos))
	if packet_id < 0:
		publish_failed.emit(message_type, topic, "MQTT transport rejected the packet")
		return false
	print("%s queued: %s" % [message_type, topic])
	publish_queued.emit(message_type, topic, packet_id)
	return true


func _on_transport_connected():
	_state = ConnectionState.CONNECTED
	sequence_number = 0
	if not publish_node_birth():
		broker_connection_failed.emit("MQTT connected but NBIRTH could not be sent")
		disconnect_from_broker()
		return
	print("MQTT connected; Sparkplug NBIRTH sent")
	broker_connected.emit()


func _on_transport_disconnected():
	var expected = _intentional_disconnect
	_state = ConnectionState.DISCONNECTED
	_intentional_disconnect = false
	if not expected:
		print("MQTT connection lost; broker will publish the NDEATH will")
	broker_disconnected.emit()


func _on_transport_connection_failed():
	_state = ConnectionState.DISCONNECTED
	if mqtt != null:
		mqtt.cleanupsockets()
	broker_connection_failed.emit("MQTT broker connection failed")


func _on_publish_acknowledged(packet_id: int):
	publish_acknowledged.emit(packet_id)


func _on_message_received(topic, payload):
	var bytes: PackedByteArray
	if payload is PackedByteArray:
		bytes = payload
	else:
		bytes = str(payload).to_utf8_buffer()
	message_received.emit(str(topic), bytes)


func _topic(message_type: String, device_id := "") -> String:
	if device_id.is_empty():
		return "spBv1.0/%s/%s/%s" % [group_id, message_type, node_id]
	return "spBv1.0/%s/%s/%s/%s" % [group_id, message_type, node_id, device_id]


func _payload(metrics: Array = []) -> PackedByteArray:
	var payload = _encode_payload(_timestamp_ms(), sequence_number, metrics, true)
	sequence_number = (sequence_number + 1) % 256
	return payload


func _ndeath_payload() -> PackedByteArray:
	return _encode_payload(_timestamp_ms(), 0, [
		metric("bdSeq", "Int64", birth_death_sequence),
	], false)


func _load_next_bdseq():
	var config = ConfigFile.new()
	config.load(BDSEQ_PATH)
	var previous = int(config.get_value("sparkplug", "bdSeq", -1))
	birth_death_sequence = (previous + 1) % 256
	_save_bdseq()


func _advance_bdseq():
	birth_death_sequence = (birth_death_sequence + 1) % 256
	_save_bdseq()


func _save_bdseq():
	var config = ConfigFile.new()
	config.set_value("sparkplug", "bdSeq", birth_death_sequence)
	config.save(BDSEQ_PATH)


func _timestamp_ms() -> int:
	return int(Time.get_unix_time_from_system() * 1000.0)


static func metric(name: String, data_type: String, value) -> Dictionary:
	return {"name": name, "type": data_type, "value": value}


static func _encode_payload(timestamp_ms: int, sequence: int, metrics: Array, include_sequence: bool) -> PackedByteArray:
	var payload := PackedByteArray()
	_append_varint_field(payload, 1, timestamp_ms)
	for metric_data in metrics:
		_append_bytes_field(payload, 2, _encode_metric(metric_data, timestamp_ms))
	if include_sequence:
		_append_varint_field(payload, 3, sequence)
	return payload


static func _encode_metric(metric_data: Dictionary, timestamp_ms: int) -> PackedByteArray:
	var encoded := PackedByteArray()
	var data_type = str(metric_data.get("type", "String"))
	_append_string_field(encoded, 1, str(metric_data.get("name", "")))
	_append_varint_field(encoded, 3, int(metric_data.get("timestamp", timestamp_ms)))
	_append_varint_field(encoded, 4, int(DATA_TYPES.get(data_type, DATA_TYPES.String)))
	var value = metric_data.get("value")
	match data_type:
		"Int32":
			_append_varint_field(encoded, 10, int(value))
		"Int64", "DateTime":
			_append_varint_field(encoded, 11, int(value))
		"Float":
			_append_fixed32_field(encoded, 12, float(value))
		"Double":
			_append_fixed64_field(encoded, 13, float(value))
		"Boolean":
			_append_varint_field(encoded, 14, 1 if bool(value) else 0)
		_:
			_append_string_field(encoded, 15, str(value))
	return encoded


static func _append_varint_field(target: PackedByteArray, field_number: int, value: int):
	target.append_array(_varint((field_number << 3) | 0))
	target.append_array(_varint(value))


static func _append_bytes_field(target: PackedByteArray, field_number: int, value: PackedByteArray):
	target.append_array(_varint((field_number << 3) | 2))
	target.append_array(_varint(value.size()))
	target.append_array(value)


static func _append_string_field(target: PackedByteArray, field_number: int, value: String):
	_append_bytes_field(target, field_number, value.to_utf8_buffer())


static func _append_fixed32_field(target: PackedByteArray, field_number: int, value: float):
	var bytes := PackedByteArray()
	bytes.resize(4)
	bytes.encode_float(0, value)
	target.append_array(_varint((field_number << 3) | 5))
	target.append_array(bytes)


static func _append_fixed64_field(target: PackedByteArray, field_number: int, value: float):
	var bytes := PackedByteArray()
	bytes.resize(8)
	bytes.encode_double(0, value)
	target.append_array(_varint((field_number << 3) | 1))
	target.append_array(bytes)


static func _varint(value: int) -> PackedByteArray:
	var encoded := PackedByteArray()
	var remaining = max(value, 0)
	while remaining >= 0x80:
		encoded.append((remaining & 0x7f) | 0x80)
		remaining >>= 7
	encoded.append(remaining & 0x7f)
	return encoded

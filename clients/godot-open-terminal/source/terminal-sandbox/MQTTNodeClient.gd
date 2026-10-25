class_name MQTTNodeClient
extends Node

signal broker_checked(ok: bool, message: String)
signal message_published(message_type: String, device_id: String)
signal message_rejected(message_type: String, device_id: String, reason: String)
signal sparkplug_command_received(topic: String, message_type: String, device_id: String, metrics: Dictionary)

const SPARKPLUG_MANAGER = preload("res://source/sparkplugB/SparkPlugMQTTManager.gd")
const SIM_IDS = preload("res://source/headless/SimIds.gd")
const CONNECT_TIMEOUT_SECONDS := 8.0

var _manager = null
var _connect_timeout: Timer
var _pending_connection := {}
var _born_devices := {}
var _connected := false
var _disconnect_requested := false


func _ready():
	_manager = SPARKPLUG_MANAGER.new()
	_manager.name = "SparkPlugMQTTManager"
	add_child(_manager)
	_manager.broker_connected.connect(_on_connected)
	_manager.broker_disconnected.connect(_on_disconnected)
	_manager.broker_connection_failed.connect(_on_connection_failed)
	_manager.publish_failed.connect(_on_publish_failed)
	_manager.message_received.connect(_on_message_received)
	_connect_timeout = Timer.new()
	_connect_timeout.one_shot = true
	_connect_timeout.wait_time = CONNECT_TIMEOUT_SECONDS
	_connect_timeout.timeout.connect(_on_connection_timeout)
	add_child(_connect_timeout)


func connect_to_broker(
	host: String,
	port: int,
	username := "",
	password := "",
	protocol := "",
	group_id := "MINI-TERMINAL",
	node_id := "GODOT-SANDBOX",
	client_id := "godot-terminal-sandbox"
):
	if is_broker_connected():
		broker_checked.emit(true, "MQTT broker already connected; Sparkplug node is online")
		return
	_pending_connection = {
		"url": _format_broker_url(host, port, protocol),
		"username": username,
		"password": password,
		"group_id": group_id,
		"node_id": node_id,
		"client_id": client_id,
	}
	_disconnect_requested = false
	_connect_timeout.start()
	call_deferred("_start_pending_connection")


func set_node_birth_metrics(metrics: Array):
	if _manager != null:
		_manager.set_node_birth_metrics(metrics)


func _start_pending_connection():
	if _pending_connection.is_empty():
		return
	if _manager == null or not _manager.is_ready_for_connection():
		call_deferred("_start_pending_connection")
		return
	var request = _pending_connection
	_pending_connection = {}
	_manager.configure(
		str(request.url),
		str(request.group_id),
		str(request.node_id),
		str(request.client_id),
		str(request.username),
		str(request.password)
	)
	_manager.connect_to_broker()


func disconnect_from_broker():
	_pending_connection.clear()
	_disconnect_requested = true
	_connect_timeout.stop()
	if _manager != null:
		_manager.disconnect_from_broker()
	_connected = false
	_born_devices.clear()


func is_broker_connected() -> bool:
	return _connected and _manager != null and _manager.is_broker_connected()


func publish_powered_devices(ches: Array):
	if not is_broker_connected():
		return
	for che in ches:
		publish_che_birth(che)
	for che in ches:
		publish_che_data(che)


func subscribe_sparkplug_commands(group_id: String, node_id: String) -> bool:
	if not is_broker_connected():
		return false
	var node_ok = _manager.subscribe("spBv1.0/%s/NCMD/%s" % [group_id, node_id], 1)
	var device_ok = _manager.subscribe("spBv1.0/%s/DCMD/%s/+" % [group_id, node_id], 1)
	return node_ok and device_ok


func publish_rebirth() -> bool:
	_born_devices.clear()
	return _manager != null and _manager.publish_rebirth()


func publish_che_birth(che: Dictionary) -> bool:
	var device_id = _device_id(che)
	if not _can_publish("DBIRTH", device_id):
		return false
	if _born_devices.has(device_id):
		return false
	if not _manager.publish_device_birth(device_id, _che_metrics(che)):
		return false
	_born_devices[device_id] = true
	message_published.emit("DBIRTH", device_id)
	return true


func publish_che_data(che: Dictionary) -> bool:
	var device_id = _device_id(che)
	if not _can_publish("DDATA", device_id):
		return false
	if not _born_devices.has(device_id) and not publish_che_birth(che):
		return false
	if not _manager.publish_device_data(device_id, _che_metrics(che)):
		return false
	message_published.emit("DDATA", device_id)
	return true


func publish_command_result(che: Dictionary, metrics: Array) -> bool:
	var device_id = _device_id(che)
	if not _can_publish("DDATA", device_id):
		return false
	if not _born_devices.has(device_id) and not publish_che_birth(che):
		return false
	if not _manager.publish_device_data(device_id, metrics):
		return false
	message_published.emit("DDATA", device_id)
	return true


func publish_che_death(che: Dictionary) -> bool:
	var device_id = _device_id(che)
	if not _can_publish("DDEATH", device_id) or not _born_devices.has(device_id):
		return false
	if not _manager.publish_device_death(device_id):
		return false
	_born_devices.erase(device_id)
	message_published.emit("DDEATH", device_id)
	return true


func _can_publish(message_type: String, device_id: String) -> bool:
	if is_broker_connected():
		return true
	_reject(message_type, device_id, "MQTT broker is not connected")
	return false


func _reject(message_type: String, device_id: String, reason: String):
	push_warning("%s for CHE %s rejected: %s" % [message_type, device_id, reason])
	message_rejected.emit(message_type, device_id, reason)


func _on_connected():
	_connect_timeout.stop()
	_connected = true
	_born_devices.clear()
	broker_checked.emit(true, "MQTT broker connected; Sparkplug NBIRTH sent")


func _on_disconnected():
	_connect_timeout.stop()
	_connected = false
	_born_devices.clear()
	var message = "MQTT broker disconnected" if _disconnect_requested else "MQTT broker connection lost"
	_disconnect_requested = false
	broker_checked.emit(false, message)


func _on_connection_failed(message: String):
	_fail_connection(message)


func _on_connection_timeout():
	_pending_connection.clear()
	if _manager != null:
		_manager.disconnect_from_broker()
	_fail_connection("MQTT broker connection timed out")


func _fail_connection(message: String):
	_connect_timeout.stop()
	_connected = false
	_born_devices.clear()
	broker_checked.emit(false, message)


func _on_publish_failed(message_type: String, topic: String, message: String):
	_reject(message_type, topic.get_slice("/", topic.get_slice_count("/") - 1), message)


func _on_message_received(topic: String, payload: PackedByteArray):
	if topic.begins_with("spBv1.0/"):
		var parts = topic.split("/")
		if parts.size() not in [4, 5] or str(parts[2]) not in ["NCMD", "DCMD"]:
			return
		var document = SPARKPLUG_MANAGER.decode_payload(payload)
		if document.is_empty():
			_reject("SPARKPLUG", topic, "Message payload is not valid Sparkplug protobuf")
			return
		var device_id = str(parts[4]) if parts.size() == 5 else ""
		sparkplug_command_received.emit(
			topic,
			str(parts[2]),
			device_id,
			SPARKPLUG_MANAGER.metrics_by_name(document)
		)
		return


func _device_id(che: Dictionary) -> String:
	return SIM_IDS.device_id(che)


func _che_metrics(che: Dictionary) -> Array:
	var equipment: Dictionary = che.get("equipment_type_details", {})
	return [
		{"name": "Identity/Id", "type": "Int32", "value": int(che.get("pk", che.get("id", 0)))},
		{"name": "Identity/Name", "type": "String", "value": str(che.get("name", ""))},
		{"name": "Identity/EquipmentType", "type": "String", "value": str(che.get("equipment_type_code", "CHE"))},
		{"name": "State/PoweredOn", "type": "Boolean", "value": bool(che.get("powered_on", false))},
		{"name": "State/Working", "type": "Boolean", "value": bool(che.get("working", false))},
		{"name": "State/IsAvailable", "type": "Boolean", "value": bool(che.get("is_available", false))},
		{"name": "State/IsDispatched", "type": "Boolean", "value": bool(che.get("is_dispatched", false))},
		{"name": "State/InMaintenance", "type": "Boolean", "value": bool(che.get("is_in_maintenance", false))},
		{"name": "Position/Latitude", "type": "Double", "value": float(che.get("latitude", 0.0))},
		{"name": "Position/Longitude", "type": "Double", "value": float(che.get("longitude", 0.0))},
		{"name": "Position/HeadingTrueNorthDeg", "type": "Float", "value": float(che.get("rotation", 0.0))},
		{"name": "Fuel/LevelPercent", "type": "Float", "value": float(che.get("fuel_level", 100.0))},
		{"name": "Energy/LevelPercent", "type": "Float", "value": float(che.get("energy_level", che.get("battery_level", 0.0)))},
		{"name": "Motion/DrivingSpeedMps", "type": "Float", "value": float(che.get("speed_mps", 0.0))},
		{"name": "Fuel/ConsumptionRateLitersPerHour", "type": "Float", "value": float(che.get("fuel_consumption_rate_lph", 0.0))},
		{"name": "Fuel/ConsumedLitersSinceStart", "type": "Float", "value": float(che.get("fuel_consumed_liters", 0.0))},
		{"name": "Fuel/TankCapacityLiters", "type": "Float", "value": float(equipment.get("fuel_tank_capacity", 0.0))},
		{"name": "Equipment/EnergySource", "type": "String", "value": str(equipment.get("energy_source", ""))},
		{"name": "Equipment/DrivingSpeedEmptyKmh", "type": "Float", "value": float(equipment.get("driving_speed_empty_kmh", 0.0))},
		{"name": "Equipment/DrivingSpeedFullLoadKmh", "type": "Float", "value": float(equipment.get("driving_speed_full_load_kmh", 0.0))},
		{"name": "Equipment/LiftingSpeedEmptyMPerMin", "type": "Float", "value": float(equipment.get("lifting_speed_empty_m_per_min", 0.0))},
		{"name": "Equipment/LiftingSpeedFullLoadMPerMin", "type": "Float", "value": float(equipment.get("lifting_speed_full_load_m_per_min", 0.0))},
		{"name": "Equipment/TurningRadiusM", "type": "Float", "value": float(equipment.get("turning_radius_m", 0.0))},
		{"name": "State/TwistlockClosed", "type": "Boolean", "value": bool(che.get("twistlock_closed", false))},
		{"name": "Location/Id", "type": "Int32", "value": int(che.get("location", 0))},
		{"name": "Location/CurrentBlockId", "type": "Int32", "value": 0 if che.get("current_block") == null else int(che.get("current_block"))},
		{"name": "Maintenance/LastMaintenance", "type": "String", "value": str(che.get("last_maintenance", ""))},
	]


func _format_broker_url(host: String, port: int, protocol: String) -> String:
	var clean_host = host.strip_edges().trim_suffix("/")
	if clean_host.begins_with("tcp://") or clean_host.begins_with("ssl://") or clean_host.begins_with("ws://") or clean_host.begins_with("wss://"):
		if clean_host.get_slice_count(":") >= 3:
			return clean_host
		return "%s:%s" % [clean_host, port]
	var resolved_protocol = protocol
	if resolved_protocol.is_empty():
		resolved_protocol = "ssl://" if port == 8883 else "tcp://"
	return "%s%s:%s" % [resolved_protocol, clean_host, port]

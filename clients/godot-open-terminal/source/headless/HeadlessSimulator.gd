extends Node

const MinitosClient = preload("res://source/terminal-sandbox/MinitosClient.gd")
const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const METERS_PER_LATITUDE_DEGREE := 111319.49079327358

var _minitos := MinitosClient.new()
var _mqtt := MQTTNodeClient.new()
var _location_id := ""
var _terminal_code := ""
var _uns_root := "scms/v1"
var _mqtt_connected := false
var _configuration_loaded := false
var _minitos_retry_pending := false
var _mqtt_retry_pending := false
var _ches := {}
var _active_commands := {}
var _command_outcomes := {}
var _last_publish_ms := {}
var _default_speed_mps := 4.0
var _time_scale := 1.0


func _ready():
	randomize()
	_location_id = _env("SIMULATOR_LOCATION_ID", "1")
	_terminal_code = _env("SIMULATOR_TERMINAL_CODE", "terminal-%s" % _location_id)
	_uns_root = _env("UNS_ROOT", "scms/v1").trim_suffix("/")
	_default_speed_mps = max(float(_env("SIMULATOR_DEFAULT_SPEED_MPS", "4.0")), 0.1)
	_time_scale = max(float(_env("SIMULATOR_TIME_SCALE", "1.0")), 0.01)

	add_child(_minitos)
	add_child(_mqtt)
	_minitos.authenticated.connect(_on_authenticated)
	_minitos.terminal_layout_loaded.connect(_on_terminal_layout_loaded)
	_mqtt.broker_checked.connect(_on_broker_checked)
	_mqtt.business_message_received.connect(_on_business_message)
	_mqtt.message_rejected.connect(_on_message_rejected)

	_connect_minitos()
	_connect_mqtt()
	print("HEADLESS simulator starting for %s (location %s)" % [_terminal_code, _location_id])


func _process(delta: float):
	if not _configuration_loaded:
		return
	var scaled_delta = delta * _time_scale
	for che_id in _ches.keys():
		var che: Dictionary = _ches[che_id]
		if _active_commands.has(che_id):
			_step_command(int(che_id), che, _active_commands[che_id], scaled_delta)
		else:
			_consume_idle_energy(che, scaled_delta)
			_publish_when_due(che)


func _connect_minitos():
	var server_url = _env("MINITOS_URL", "http://minitos:8000")
	var username = _env("MINITOS_USERNAME", "")
	var password = _env("MINITOS_PASSWORD", "")
	_minitos.configure(server_url, username, password)
	_minitos.authenticate(username, password)


func _connect_mqtt():
	_mqtt.connect_to_broker(
		_env("MQTT_HOST", "hivemq"),
		int(_env("MQTT_PORT", "1883")),
		_env("MQTT_USERNAME", ""),
		_env("MQTT_PASSWORD", ""),
		_env("MQTT_PROTOCOL", ""),
		_env("SPARKPLUG_GROUP_ID", "MINI-TERMINAL"),
		_env("SPARKPLUG_NODE_ID", "GODOT-SANDBOX"),
		_env("MQTT_CLIENT_ID", "godot-headless-%s" % _location_id)
	)


func _on_authenticated(ok: bool, message: String):
	if not ok:
		push_error("MiniTOS authentication failed: %s" % message)
		_schedule_minitos_retry()
		return
	_minitos_retry_pending = false
	_minitos.refresh_terminal(_location_id)


func _on_terminal_layout_loaded(
	ok: bool,
	_location_data: Dictionary,
	_areas: Array,
	_roads: Array,
	ches: Array,
	message: String
):
	if not ok:
		push_error("MiniTOS terminal bootstrap failed: %s" % message)
		_schedule_minitos_retry()
		return
	_configuration_loaded = true
	_ches.clear()
	for source_che in ches:
		if not source_che is Dictionary:
			continue
		var che: Dictionary = source_che.duplicate(true)
		var che_id = int(che.get("pk", che.get("id", 0)))
		if che_id <= 0:
			continue
		che["pk"] = che_id
		che["speed_mps"] = 0.0
		che["fuel_consumption_rate_lph"] = 0.0
		che["fuel_consumed_liters"] = float(che.get("fuel_consumed_liters", 0.0))
		_ches[che_id] = che
	print("HEADLESS loaded %s CHE records for %s" % [_ches.size(), _terminal_code])
	if _mqtt_connected:
		_publish_birth_and_state()


func _on_broker_checked(ok: bool, message: String):
	_mqtt_connected = ok
	if not ok:
		push_warning(message)
		_schedule_mqtt_retry()
		return
	_mqtt_retry_pending = false
	if not _mqtt.subscribe_business_commands(_uns_root, _terminal_code):
		push_error("Could not subscribe to OES equipment commands")
		return
	print("HEADLESS subscribed to %s/%s/oes/che/+/command" % [_uns_root, _terminal_code])
	if _configuration_loaded:
		_publish_birth_and_state()


func _on_business_message(topic: String, payload: Dictionary):
	if payload.get("schema") != "scms.equipment.command.v1":
		return
	var data = payload.get("data", {})
	if not data is Dictionary:
		return
	var command_id = str(payload.get("command_id", ""))
	var che_id = int(data.get("che_id", 0))
	if command_id.is_empty() or not _ches.has(che_id):
		_publish_command_result(command_id, "failed", 0, "Unknown command or CHE")
		return
	if _command_outcomes.has(command_id):
		var outcome: Dictionary = _command_outcomes[command_id]
		_publish_command_result(command_id, str(outcome.event), int(outcome.get("position_id", 0)), str(outcome.get("reason", "")))
		return
	if _active_commands.has(che_id):
		_publish_command_result(command_id, "failed", 0, "CHE already has an active command")
		return
	var destination_latitude = data.get("destination_latitude")
	var destination_longitude = data.get("destination_longitude")
	if destination_latitude == null or destination_longitude == null:
		_publish_command_result(command_id, "failed", 0, "Command has no destination coordinates")
		return

	var che: Dictionary = _ches[che_id]
	if not bool(che.get("powered_on", false)) or bool(che.get("is_in_maintenance", false)):
		_publish_command_result(command_id, "failed", 0, "CHE is not executable")
		return
	_active_commands[che_id] = {
		"command_id": command_id,
		"destination_latitude": float(destination_latitude),
		"destination_longitude": float(destination_longitude),
		"source_inventory_position_id": _int_or_zero(data.get("source_inventory_position_id")),
		"destination_inventory_position_id": _int_or_zero(data.get("destination_inventory_position_id")),
		"picked_up": false,
	}
	che["working"] = true
	che["is_dispatched"] = true
	che["is_available"] = false
	_ches[che_id] = che
	_publish_command_result(command_id, "acknowledged")
	_publish_command_result(command_id, "started")
	_publish_equipment_state(che, true)
	print("HEADLESS accepted command %s for CHE %s from %s" % [command_id, che_id, topic])


func _step_command(che_id: int, che: Dictionary, command: Dictionary, delta: float):
	var latitude = float(che.get("latitude", 0.0))
	var longitude = float(che.get("longitude", 0.0))
	var target_latitude = float(command.destination_latitude)
	var target_longitude = float(command.destination_longitude)
	var latitude_radians = deg_to_rad(latitude)
	var east_m = deg_to_rad(target_longitude - longitude) * 6378137.0 * cos(latitude_radians)
	var north_m = deg_to_rad(target_latitude - latitude) * 6378137.0
	var distance_m = sqrt(east_m * east_m + north_m * north_m)
	if distance_m <= 0.5:
		_complete_command(che_id, che, command)
		return

	if not bool(command.picked_up) and int(command.source_inventory_position_id) > 0:
		command["picked_up"] = true
		_active_commands[che_id] = command
		_publish_command_result(str(command.command_id), "container_picked_up")

	var speed_mps = _che_speed_mps(che)
	var travel_m = min(speed_mps * delta, distance_m)
	var ratio = travel_m / distance_m
	che["latitude"] = latitude + (target_latitude - latitude) * ratio
	che["longitude"] = longitude + (target_longitude - longitude) * ratio
	che["rotation"] = fposmod(rad_to_deg(atan2(east_m, north_m)), 360.0)
	che["speed_mps"] = speed_mps
	che["fuel_consumption_rate_lph"] = 18.0
	_consume_fuel(che, 18.0, delta)
	_ches[che_id] = che
	_publish_when_due(che)


func _complete_command(che_id: int, che: Dictionary, command: Dictionary):
	che["latitude"] = float(command.destination_latitude)
	che["longitude"] = float(command.destination_longitude)
	che["working"] = false
	che["is_dispatched"] = false
	che["is_available"] = bool(che.get("powered_on", false)) and not bool(che.get("is_in_maintenance", false))
	che["speed_mps"] = 0.0
	che["fuel_consumption_rate_lph"] = 4.0 if bool(che.get("powered_on", false)) else 0.0
	_ches[che_id] = che
	_active_commands.erase(che_id)
	var command_id = str(command.command_id)
	var destination_position_id = int(command.destination_inventory_position_id)
	if destination_position_id > 0:
		_publish_command_result(command_id, "container_dropped", destination_position_id)
	_publish_command_result(command_id, "completed")
	_command_outcomes[command_id] = {"event": "completed", "position_id": destination_position_id}
	_publish_equipment_state(che, true)
	print("HEADLESS completed command %s for CHE %s" % [command_id, che_id])


func _publish_birth_and_state():
	var all_ches: Array = []
	for che in _ches.values():
		all_ches.append(che)
	_mqtt.publish_powered_devices(all_ches)
	for che in all_ches:
		_publish_equipment_state(che, true)


func _publish_when_due(che: Dictionary):
	if not _mqtt_connected or not bool(che.get("powered_on", false)):
		return
	var che_id = int(che.pk)
	var interval_ms = 2000 if bool(che.get("working", false)) else 20000
	var now_ms = Time.get_ticks_msec()
	if now_ms - int(_last_publish_ms.get(che_id, 0)) < interval_ms:
		return
	_publish_equipment_state(che)


func _publish_equipment_state(che: Dictionary, force := false):
	if not _mqtt_connected:
		return
	var che_id = int(che.pk)
	if bool(che.get("powered_on", false)):
		_mqtt.publish_che_data(che)
	elif force:
		_mqtt.publish_che_death(che)
	var occurred_at = _utc_timestamp()
	var equipment: Dictionary = che.get("equipment_type_details", {})
	var payload = {
		"schema": "scms.equipment.state.v1",
		"event_id": _event_id(),
		"occurred_at": occurred_at,
		"terminal": {"id": int(_location_id), "code": _terminal_code},
		"data": {
			"che_id": che_id,
			"name": str(che.get("name", "CHE_%s" % che_id)),
			"equipment_type_id": che.get("equipment_type"),
			"equipment_type_code": str(che.get("equipment_type_code", equipment.get("equipment_type", ""))),
			"powered_on": bool(che.get("powered_on", false)),
			"working": bool(che.get("working", false)),
			"is_available": bool(che.get("is_available", false)),
			"is_dispatched": bool(che.get("is_dispatched", false)),
			"is_in_maintenance": bool(che.get("is_in_maintenance", false)),
			"latitude": che.get("latitude"),
			"longitude": che.get("longitude"),
			"rotation": float(che.get("rotation", 0.0)),
			"fuel_level": che.get("fuel_level"),
			"energy_level": che.get("energy_level", che.get("battery_level")),
			"speed_mps": float(che.get("speed_mps", 0.0)),
			"fuel_consumption_rate_lph": float(che.get("fuel_consumption_rate_lph", 0.0)),
			"fuel_consumed_liters": float(che.get("fuel_consumed_liters", 0.0)),
			"timestamp": occurred_at,
		},
	}
	var topic = "%s/%s/simulator/che/%s/state" % [_uns_root, _terminal_code, che_id]
	if _mqtt.publish_business_event(topic, payload, true):
		_last_publish_ms[che_id] = Time.get_ticks_msec()


func _publish_command_result(command_id: String, event: String, position_id := 0, reason := ""):
	if command_id.is_empty() or not _mqtt_connected:
		return
	var data = {"command_id": command_id, "event": event}
	if position_id > 0:
		data["position_id"] = position_id
	if not reason.is_empty():
		data["reason"] = reason
	var payload = {
		"schema": "scms.command.result.v1",
		"event_id": _event_id(),
		"occurred_at": _utc_timestamp(),
		"terminal": {"id": int(_location_id), "code": _terminal_code},
		"data": data,
	}
	var topic = "%s/%s/simulator/commands/%s/result" % [_uns_root, _terminal_code, command_id]
	_mqtt.publish_business_event(topic, payload)


func _che_speed_mps(che: Dictionary) -> float:
	var equipment: Dictionary = che.get("equipment_type_details", {})
	var speed_kmh = float(equipment.get("driving_speed_empty_kmh", 0.0))
	return speed_kmh / 3.6 if speed_kmh > 0.0 else _default_speed_mps


func _consume_idle_energy(che: Dictionary, delta: float):
	if not bool(che.get("powered_on", false)):
		che["fuel_consumption_rate_lph"] = 0.0
		return
	che["fuel_consumption_rate_lph"] = 4.0
	_consume_fuel(che, 4.0, delta)


func _consume_fuel(che: Dictionary, rate_lph: float, delta: float):
	var consumed_liters = rate_lph * delta / 3600.0
	che["fuel_consumed_liters"] = float(che.get("fuel_consumed_liters", 0.0)) + consumed_liters
	var equipment: Dictionary = che.get("equipment_type_details", {})
	var capacity = float(equipment.get("fuel_tank_capacity", 0.0))
	if capacity > 0.0 and che.get("fuel_level") != null:
		che["fuel_level"] = max(float(che.fuel_level) - consumed_liters / capacity * 100.0, 0.0)


func _schedule_minitos_retry():
	if _minitos_retry_pending:
		return
	_minitos_retry_pending = true
	await get_tree().create_timer(5.0).timeout
	_minitos_retry_pending = false
	_connect_minitos()


func _schedule_mqtt_retry():
	if _mqtt_retry_pending:
		return
	_mqtt_retry_pending = true
	await get_tree().create_timer(5.0).timeout
	_mqtt_retry_pending = false
	_connect_mqtt()


func _on_message_rejected(message_type: String, device_id: String, reason: String):
	push_warning("%s %s rejected: %s" % [message_type, device_id, reason])


func _env(name: String, fallback: String) -> String:
	var value = OS.get_environment(name)
	return value if not value.is_empty() else fallback


func _utc_timestamp() -> String:
	return Time.get_datetime_string_from_unix_time(int(Time.get_unix_time_from_system()), false) + "Z"


func _int_or_zero(value) -> int:
	return 0 if value == null else int(value)


func _event_id() -> String:
	return "%08x-%04x-4%03x-%04x-%012x" % [
		randi(), randi() & 0xffff, randi() & 0xfff,
		(randi() & 0x3fff) | 0x8000,
		int(Time.get_ticks_usec()) & 0xffffffffffff,
	]

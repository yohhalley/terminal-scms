extends Node

const OESBootstrapClient = preload("res://source/headless/OESBootstrapClient.gd")
const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const SimIds = preload("res://source/headless/SimIds.gd")
const RUNTIME_STATE_PATH = "user://simulation_runtime_state.json"

var _bootstrap := OESBootstrapClient.new()
var _mqtt := MQTTNodeClient.new()
var _oes_url := ""
var _site_id := ""
var _session_id := ""
var _session_token := ""
var _configuration_revision := 0
var _configuration_checksum := ""
var _sparkplug_group_id := ""
var _sparkplug_node_id := ""
var _mqtt_connected := false
var _configuration_loaded := false
var _bootstrap_retry_pending := false
var _mqtt_retry_pending := false
var _ches := {}
var _active_commands := {}
var _command_outcomes := {}
var _persisted_runtime_state := {}
var _last_publish_ms := {}
var _default_speed_mps := 4.0
var _time_scale := 1.0


func _ready():
	randomize()
	_oes_url = OS.get_environment("OES_URL").strip_edges()
	_site_id = OS.get_environment("SIMULATOR_SITE_ID").strip_edges()
	_session_id = OS.get_environment("SIMULATOR_SESSION_ID").strip_edges()
	_session_token = OS.get_environment("SIMULATOR_TOKEN").strip_edges()
	if _oes_url.is_empty() or _site_id.is_empty() or _session_id.is_empty() or _session_token.is_empty():
		push_error("OES_URL, SIMULATOR_SITE_ID, SIMULATOR_SESSION_ID, and SIMULATOR_TOKEN are required")
		get_tree().quit(2)
		return
	_default_speed_mps = max(float(_env("SIMULATOR_DEFAULT_SPEED_MPS", "4.0")), 0.1)
	_time_scale = max(float(_env("SIMULATOR_TIME_SCALE", "1.0")), 0.01)
	_load_runtime_state()
	add_child(_bootstrap)
	add_child(_mqtt)
	_bootstrap.bootstrap_loaded.connect(_on_bootstrap_loaded)
	_mqtt.broker_checked.connect(_on_broker_checked)
	_mqtt.sparkplug_command_received.connect(_on_sparkplug_command)
	_mqtt.message_rejected.connect(_on_message_rejected)
	_load_bootstrap()
	print("HEADLESS simulator requesting OES session %s for Site %s" % [_session_id, _site_id])


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


func _load_bootstrap():
	_bootstrap.load_bootstrap(_oes_url, _session_id, _session_token)


func _on_bootstrap_loaded(ok: bool, document: Dictionary, message: String):
	if not ok:
		push_error(message)
		_schedule_bootstrap_retry()
		return
	var session: Dictionary = document.get("session", {})
	if str(session.get("id", "")) != _session_id or str(session.get("site_id", "")) != _site_id:
		_fail_bootstrap("OES bootstrap identity does not match this process")
		return
	_configuration_revision = int(session.get("configuration_revision", 0))
	_configuration_checksum = str(session.get("configuration_checksum", ""))
	if _configuration_revision != int(_env("SIMULATOR_CONFIG_REVISION", str(_configuration_revision))):
		_fail_bootstrap("OES bootstrap revision does not match the runner claim")
		return
	if _configuration_checksum != _env("SIMULATOR_CONFIG_CHECKSUM", _configuration_checksum):
		_fail_bootstrap("OES bootstrap checksum does not match the runner claim")
		return
	_sparkplug_group_id = str(session.get("sparkplug_group_id", ""))
	_sparkplug_node_id = str(session.get("sparkplug_node_id", ""))
	if _sparkplug_group_id.is_empty() or _sparkplug_node_id.is_empty():
		_fail_bootstrap("OES bootstrap is missing the Sparkplug identity")
		return
	_time_scale = max(float(session.get("time_scale", _time_scale)), 0.01)
	_load_equipment(document)
	_restore_runtime_state()
	_configuration_loaded = true
	_bootstrap_retry_pending = false
	_mqtt.set_node_birth_metrics([
		{"name": "Session/Id", "type": "String", "value": _session_id},
		{"name": "Site/Id", "type": "String", "value": _site_id},
		{"name": "Configuration/Revision", "type": "Int64", "value": _configuration_revision},
		{"name": "Configuration/Checksum", "type": "String", "value": _configuration_checksum},
	])
	_connect_mqtt()
	print("HEADLESS loaded %s CHE records from OES revision %s" % [_ches.size(), _configuration_revision])


func _fail_bootstrap(message: String):
	push_error(message)
	get_tree().quit(3)


func _load_equipment(document: Dictionary):
	_ches.clear()
	var projection: Dictionary = document.get("projection", {})
	var equipment: Dictionary = projection.get("equipment", {})
	var type_by_id := {}
	for item in equipment.get("types", []):
		if item is Dictionary:
			type_by_id[int(item.get("id", 0))] = item
	var state_by_id := {}
	for item in document.get("equipment_state", []):
		if item is Dictionary:
			state_by_id[int(item.get("tos_che_id", 0))] = item
	for source_che in equipment.get("ches", []):
		if not source_che is Dictionary:
			continue
		var che: Dictionary = source_che.duplicate(true)
		var che_id = int(che.get("id", 0))
		if che_id <= 0:
			continue
		for key in state_by_id.get(che_id, {}).keys():
			var value = state_by_id[che_id][key]
			if value != null:
				che[key] = value
		var equipment_type_id = int(che.get("equipment_type_id", 0))
		var equipment_type: Dictionary = type_by_id.get(equipment_type_id, {})
		che["pk"] = che_id
		che["equipment_type"] = equipment_type_id
		che["equipment_type_details"] = equipment_type
		che["equipment_type_code"] = str(equipment_type.get("equipment_type", "CHE"))
		che["speed_mps"] = 0.0
		che["fuel_consumption_rate_lph"] = 0.0
		che["fuel_consumed_liters"] = float(che.get("fuel_consumed_liters", 0.0))
		_ches[che_id] = che


func _connect_mqtt():
	_mqtt.connect_to_broker(
		_env("MQTT_HOST", "hivemq"), int(_env("MQTT_PORT", "1883")),
		_env("MQTT_USERNAME", ""), _env("MQTT_PASSWORD", ""),
		_env("MQTT_PROTOCOL", ""), _sparkplug_group_id, _sparkplug_node_id,
		_env("MQTT_CLIENT_ID", "godot-headless-%s" % _session_id)
	)


func _on_broker_checked(ok: bool, message: String):
	_mqtt_connected = ok
	if not ok:
		push_warning(message)
		_schedule_mqtt_retry()
		return
	_mqtt_retry_pending = false
	if not _mqtt.subscribe_sparkplug_commands(_sparkplug_group_id, _sparkplug_node_id):
		push_error("Could not subscribe to Sparkplug NCMD/DCMD topics")
		return
	_publish_birth_and_state()
	print("HEADLESS Sparkplug Edge Node is online and subscribed to commands")


func _on_sparkplug_command(topic: String, message_type: String, device_id: String, metrics: Dictionary):
	if message_type == "NCMD":
		if bool(metrics.get("Node Control/Rebirth", false)):
			if _mqtt.publish_rebirth():
				_publish_birth_and_state()
		return
	var command_id = str(metrics.get("Command/Id", ""))
	if command_id.is_empty():
		return
	if _command_outcomes.has(command_id):
		_replay_command_result(command_id)
		return
	var che_id = int(metrics.get("Command/CheId", 0))
	if not _ches.has(che_id) or device_id != SimIds.device_id(_ches[che_id]):
		_publish_command_result(che_id, command_id, "failed", 0, "Unknown command target")
		return
	if str(metrics.get("Command/SessionId", "")) != _session_id:
		_publish_command_result(che_id, command_id, "failed", 0, "Session mismatch")
		return
	if str(metrics.get("Command/SiteId", "")) != _site_id:
		_publish_command_result(che_id, command_id, "failed", 0, "Site mismatch")
		return
	if int(metrics.get("Command/ConfigurationRevision", -1)) != _configuration_revision:
		_publish_command_result(che_id, command_id, "failed", 0, "Configuration revision mismatch")
		return
	if int(metrics.get("Command/ExpiresAtMs", 0)) <= int(Time.get_unix_time_from_system() * 1000.0):
		_publish_command_result(che_id, command_id, "failed", 0, "Command expired")
		return
	if str(metrics.get("Command/AssignmentId", "")).is_empty():
		_publish_command_result(che_id, command_id, "failed", 0, "Assignment identity is missing")
		return
	if _active_commands.has(che_id):
		_publish_command_result(che_id, command_id, "failed", 0, "CHE already has an active command")
		return
	if not metrics.has("Command/DestinationLatitude") or not metrics.has("Command/DestinationLongitude"):
		_publish_command_result(che_id, command_id, "failed", 0, "Command has no destination coordinates")
		return
	var che: Dictionary = _ches[che_id]
	if not bool(che.get("powered_on", false)) or bool(che.get("is_in_maintenance", false)):
		_publish_command_result(che_id, command_id, "failed", 0, "CHE is not executable")
		return
	_active_commands[che_id] = {
		"command_id": command_id,
		"assignment_id": str(metrics.get("Command/AssignmentId", "")),
		"destination_latitude": float(metrics["Command/DestinationLatitude"]),
		"destination_longitude": float(metrics["Command/DestinationLongitude"]),
		"source_inventory_position_id": int(metrics.get("Command/SourceInventoryPositionId", 0)),
		"destination_inventory_position_id": int(metrics.get("Command/DestinationInventoryPositionId", 0)),
		"picked_up": false,
	}
	che["working"] = true
	che["is_dispatched"] = true
	che["is_available"] = false
	_ches[che_id] = che
	_save_runtime_state()
	_publish_command_result(che_id, command_id, "acknowledged")
	_publish_command_result(che_id, command_id, "started")
	_publish_equipment_state(che, true)
	print("HEADLESS accepted DCMD %s for CHE %s from %s" % [command_id, che_id, topic])


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
		_save_runtime_state()
		_publish_command_result(che_id, str(command.command_id), "container_picked_up")
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
	_save_runtime_state()
	var command_id = str(command.command_id)
	var destination_position_id = int(command.destination_inventory_position_id)
	if destination_position_id > 0:
		_publish_command_result(che_id, command_id, "container_dropped", destination_position_id)
	_publish_command_result(che_id, command_id, "completed", destination_position_id)
	_publish_equipment_state(che, true)
	print("HEADLESS completed command %s for CHE %s" % [command_id, che_id])


func _publish_birth_and_state():
	var all_ches: Array = []
	for che in _ches.values():
		all_ches.append(che)
	_mqtt.publish_powered_devices(all_ches)
	for che in all_ches:
		_last_publish_ms[int(che.pk)] = Time.get_ticks_msec()


func _publish_when_due(che: Dictionary):
	if not _mqtt_connected or not bool(che.get("powered_on", false)):
		return
	var che_id = int(che.pk)
	var interval_ms = 2000 if bool(che.get("working", false)) else 20000
	var now_ms = Time.get_ticks_msec()
	if now_ms - int(_last_publish_ms.get(che_id, 0)) < interval_ms:
		return
	_publish_equipment_state(che)


func _publish_equipment_state(che: Dictionary, _force := false):
	if _mqtt_connected and _mqtt.publish_che_data(che):
		_last_publish_ms[int(che.pk)] = Time.get_ticks_msec()
		_save_runtime_state()


func _publish_command_result(che_id: int, command_id: String, event: String, position_id := 0, reason := "", result_id := ""):
	if command_id.is_empty() or not _mqtt_connected:
		return
	if result_id.is_empty():
		result_id = _event_id()
	var metrics: Array = [
		{"name": "Command/Result/Id", "type": "String", "value": result_id},
		{"name": "Command/Result/CommandId", "type": "String", "value": command_id},
		{"name": "Command/Result/Event", "type": "String", "value": event},
	]
	if position_id > 0:
		metrics.append({"name": "Command/Result/PositionId", "type": "Int64", "value": position_id})
	if not reason.is_empty():
		metrics.append({"name": "Command/Result/Reason", "type": "String", "value": reason})
	var che: Dictionary = _ches.get(che_id, {"pk": che_id})
	if _mqtt.publish_command_result(che, metrics):
		_command_outcomes[command_id] = {
			"che_id": che_id, "event": event, "position_id": position_id,
			"reason": reason, "result_id": result_id,
		}
		_save_runtime_state()


func _replay_command_result(command_id: String):
	var outcome: Dictionary = _command_outcomes[command_id]
	_publish_command_result(
		int(outcome.get("che_id", 0)), command_id, str(outcome.get("event", "failed")),
		int(outcome.get("position_id", 0)), str(outcome.get("reason", "")),
		str(outcome.get("result_id", ""))
	)


func _load_runtime_state():
	if not FileAccess.file_exists(RUNTIME_STATE_PATH):
		return
	var file = FileAccess.open(RUNTIME_STATE_PATH, FileAccess.READ)
	if file == null:
		return
	var json = JSON.new()
	if json.parse(file.get_as_text()) == OK and json.get_data() is Dictionary:
		_persisted_runtime_state = json.get_data()


func _restore_runtime_state():
	if _persisted_runtime_state.is_empty():
		return
	if str(_persisted_runtime_state.get("session_id", "")) != _session_id:
		return
	if str(_persisted_runtime_state.get("site_id", "")) != _site_id:
		return
	if int(_persisted_runtime_state.get("configuration_revision", -1)) != _configuration_revision:
		return
	if str(_persisted_runtime_state.get("configuration_checksum", "")) != _configuration_checksum:
		return
	var saved_ches: Dictionary = _persisted_runtime_state.get("ches", {})
	for saved_id in saved_ches:
		var che_id = int(saved_id)
		if not _ches.has(che_id) or not saved_ches[saved_id] is Dictionary:
			continue
		var restored: Dictionary = _ches[che_id]
		for key in saved_ches[saved_id]:
			restored[key] = saved_ches[saved_id][key]
		_ches[che_id] = restored
	var saved_commands: Dictionary = _persisted_runtime_state.get("active_commands", {})
	for saved_id in saved_commands:
		var che_id = int(saved_id)
		if _ches.has(che_id) and saved_commands[saved_id] is Dictionary:
			_active_commands[che_id] = saved_commands[saved_id]
	var saved_outcomes = _persisted_runtime_state.get("command_outcomes", {})
	if saved_outcomes is Dictionary:
		_command_outcomes = saved_outcomes
	_persisted_runtime_state.clear()


func _save_runtime_state():
	if _session_id.is_empty() or _site_id.is_empty() or _configuration_revision <= 0:
		return
	var file = FileAccess.open(RUNTIME_STATE_PATH, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify({
			"session_id": _session_id,
			"site_id": _site_id,
			"configuration_revision": _configuration_revision,
			"configuration_checksum": _configuration_checksum,
			"ches": _ches,
			"active_commands": _active_commands,
			"command_outcomes": _command_outcomes,
		}))


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


func _schedule_bootstrap_retry():
	if _bootstrap_retry_pending:
		return
	_bootstrap_retry_pending = true
	await get_tree().create_timer(5.0).timeout
	_bootstrap_retry_pending = false
	_load_bootstrap()


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


func _event_id() -> String:
	return "%08x-%04x-4%03x-%04x-%012x" % [
		randi(), randi() & 0xffff, randi() & 0xfff,
		(randi() & 0x3fff) | 0x8000,
		int(Time.get_ticks_usec()) & 0xffffffffffff,
	]

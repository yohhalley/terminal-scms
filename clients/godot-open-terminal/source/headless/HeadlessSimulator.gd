extends Node

const OESBootstrapClient = preload("res://source/headless/OESBootstrapClient.gd")
const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const SimIds = preload("res://source/headless/SimIds.gd")
const SimClock = preload("res://source/headless/SimClock.gd")
const SimRandom = preload("res://source/headless/SimRandom.gd")
const QcDischarge = preload("res://source/headless/QcDischarge.gd")
const COMMAND_EXECUTE_ORDER_LEG := "execute_order_leg"
const COMMAND_QC_DISCHARGE := "qc_discharge"
const COMMAND_CANCEL := "cancel"
const SUPPORTED_COMMAND_TYPES := [COMMAND_EXECUTE_ORDER_LEG, COMMAND_QC_DISCHARGE, COMMAND_CANCEL]
## Command/Result/Reason machine codes on `failed` (contract §3.2: consumers branch
## on the code; the human-readable text goes in Command/Result/Detail).
const REASON_UNKNOWN_COMMAND_TARGET := "unknown_command_target"
const REASON_SESSION_MISMATCH := "session_mismatch"
const REASON_SITE_MISMATCH := "site_mismatch"
const REASON_CONFIGURATION_REVISION_MISMATCH := "configuration_revision_mismatch"
const REASON_COMMAND_EXPIRED := "command_expired"
const REASON_ASSIGNMENT_ID_MISSING := "assignment_id_missing"
const REASON_UNSUPPORTED_COMMAND_TYPE := "unsupported_command_type"
const REASON_CHE_BUSY := "che_busy"
const REASON_CONTAINER_NUMBER_INVALID := "container_number_invalid"
const REASON_DESTINATION_MISSING := "destination_missing"
const REASON_HANDOFF_ID_MISSING := "handoff_id_missing"
const REASON_CONTAINER_VISIT_ID_MISSING := "container_visit_id_missing"
const REASON_HANDOFF_ALREADY_ACTIVE := "handoff_already_active"
const REASON_CHE_NOT_EXECUTABLE := "che_not_executable"
const REASON_CANCEL_TARGET_MISSING := "cancel_target_missing"
const REASON_NO_ACTIVE_COMMAND := "no_active_command"
const REASON_CANCEL_TARGET_MISMATCH := "cancel_target_mismatch"
const TT_DRIVING_TO_SOURCE := "driving_to_source"
const TT_AT_SOURCE_WAITING := "at_source_waiting"
const TT_DRIVING_TO_DESTINATION := "driving_to_destination"
const DEFAULT_QC_WAIT_MIN_S := 10
const DEFAULT_QC_WAIT_MAX_S := 30
const DEFAULT_TT_NO_SHOW_TIMEOUT_S := 300
## Sim-time command TTL (seconds) applied when a DCMD carries no ExpiresAtMs.
const DEFAULT_COMMAND_TTL_S := 600
## Sim time is checkpointed at least this often, so a restart loses at most this much.
const SIM_TIME_CHECKPOINT_MS := 1000
const RUNTIME_STATE_PATH = "user://simulation_runtime_state.json"
## NBIRTH Contract/Version (TCL-002 §3.1, S2): "tcl-002.<minor>". OES refuses an
## unsupported major version.
const CONTRACT_VERSION := "tcl-002.1"

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
## Result journal (contract §5.2, B4): every result in Sim/EventSeq order, persisted.
## Entry: {seq, che_id, result_id, metrics, packet_id, acked}. An entry stays
## pending until the broker's PUBACK for its packet id arrives; packet_id 0 means
## "not sent on the current connection". After a disconnect or restart every
## pending entry is resent, in Sim/EventSeq order, with the same Result/Id.
## Acked entries are kept until session end/reset (Q20). Full-session replay
## after an OES rebirth request is S2 (Tech Lead review B15-2), not done here.
var _result_outbox: Array = []
var _persisted_runtime_state := {}
var _last_publish_ms := {}
var _default_speed_mps := 4.0
var _clock := SimClock.new()
var _random := SimRandom.new()
var _event_seq := 0
var _start_epoch_ms := 0
## NBIRTH Sim/ScenarioId: OES bootstrap session.scenario_id, else SIMULATOR_SCENARIO_ID.
var _scenario_id := ""
var _last_checkpoint_sim_ms := 0
## Whole seconds, inclusive, per QC wait step. D-012: only the hold wait exists.
var _qc_wait_s := {
	"hold": [DEFAULT_QC_WAIT_MIN_S, DEFAULT_QC_WAIT_MAX_S],
}
## D-013: whole seconds of sim time a QC waits for its TT before handoff_timed_out.
var _tt_no_show_timeout_s := DEFAULT_TT_NO_SHOW_TIMEOUT_S
var _command_ttl_s := DEFAULT_COMMAND_TTL_S


func _ready():
	_oes_url = OS.get_environment("OES_URL").strip_edges()
	_site_id = OS.get_environment("SIMULATOR_SITE_ID").strip_edges()
	_session_id = OS.get_environment("SIMULATOR_SESSION_ID").strip_edges()
	_session_token = OS.get_environment("SIMULATOR_TOKEN").strip_edges()
	if _oes_url.is_empty() or _site_id.is_empty() or _session_id.is_empty() or _session_token.is_empty():
		push_error("OES_URL, SIMULATOR_SITE_ID, SIMULATOR_SESSION_ID, and SIMULATOR_TOKEN are required")
		get_tree().quit(2)
		return
	_default_speed_mps = max(float(_env("SIMULATOR_DEFAULT_SPEED_MPS", "4.0")), 0.1)
	_configure_simulation()
	_load_runtime_state()
	add_child(_bootstrap)
	add_child(_mqtt)
	_bootstrap.bootstrap_loaded.connect(_on_bootstrap_loaded)
	_mqtt.broker_checked.connect(_on_broker_checked)
	_mqtt.sparkplug_command_received.connect(_on_sparkplug_command)
	_mqtt.message_rejected.connect(_on_message_rejected)
	_mqtt.publish_acknowledged.connect(_on_result_acknowledged)
	_load_bootstrap()
	print("HEADLESS simulator requesting OES session %s for Site %s" % [_session_id, _site_id])


func _configure_simulation():
	var seed_text = _env_any(["SIMULATOR_SEED", "SIM_SEED"], str(SimRandom.DEFAULT_SEED))
	var run_seed = SimRandom.parse_seed(seed_text, SimRandom.DEFAULT_SEED)
	if str(run_seed) != seed_text.strip_edges():
		push_warning("Invalid simulator seed %s; using %s" % [seed_text, run_seed])
	var run_id = _env_any(["SIMULATOR_RUN_ID", "SIM_RUN_ID"], _session_id)
	_random = SimRandom.new(run_seed, run_id)
	var step_ms = int(_env_any(["SIMULATOR_FIXED_STEP_MS", "SIM_FIXED_STEP_MS"], str(SimClock.DEFAULT_STEP_MS)))
	var time_scale = float(_env_any(["SIMULATOR_TIME_SCALE", "SIM_TIME_SCALE"], "1.0"))
	_clock = SimClock.new(step_ms, time_scale)
	_start_epoch_ms = int(_env_any(["SIMULATOR_START_EPOCH_MS", "SIM_START_EPOCH_MS"], "0"))
	_scenario_id = _env_any(["SIMULATOR_SCENARIO_ID", "SIM_SCENARIO_ID"], "")
	SimIds.use_che_name = _env_any(["SIMULATOR_DEVICE_ID_MODE", "SIM_DEVICE_ID_MODE"], "name") != "legacy"
	_mqtt.session_id = _session_id
	var default_min = _env_any(["SIMULATOR_QC_WAIT_MIN_S", "SIM_QC_WAIT_MIN_S"], str(DEFAULT_QC_WAIT_MIN_S))
	var default_max = _env_any(["SIMULATOR_QC_WAIT_MAX_S", "SIM_QC_WAIT_MAX_S"], str(DEFAULT_QC_WAIT_MAX_S))
	for step_name in _qc_wait_s.keys():
		var upper = step_name.to_upper()
		configure_qc_wait(
			step_name,
			_env_any(["SIMULATOR_QC_%s_WAIT_MIN_S" % upper, "SIM_QC_%s_WAIT_MIN_S" % upper], default_min),
			_env_any(["SIMULATOR_QC_%s_WAIT_MAX_S" % upper, "SIM_QC_%s_WAIT_MAX_S" % upper], default_max)
		)
	configure_tt_no_show_timeout(
		_env_any(
			["SIMULATOR_TT_NO_SHOW_TIMEOUT_S", "SIM_TT_NO_SHOW_TIMEOUT_S"], str(DEFAULT_TT_NO_SHOW_TIMEOUT_S)
		)
	)

	configure_command_ttl(
		_env_any(["SIMULATOR_COMMAND_TTL_S", "SIM_COMMAND_TTL_S"], str(DEFAULT_COMMAND_TTL_S))
	)


## Sets Sim/Timing/CommandTtlS (whole seconds, > 0). Invalid values keep the
## previous value. Returns true when applied.
func configure_command_ttl(text: String) -> bool:
	var value_text = text.strip_edges()
	if value_text.is_valid_int() and int(value_text) > 0:
		_command_ttl_s = int(value_text)
		return true
	push_warning("Invalid command TTL %s; keeping %s s" % [text, _command_ttl_s])
	return false


## Sim-time deadline for a DCMD. Command/ExpiresAtMs is in Sim/TimeMs units
## (OES sets it to Sim/TimeMs at dispatch + TTL). Without it, the deadline is
## receipt time + Sim/Timing/CommandTtlS.
func command_deadline_sim_ms(metrics: Dictionary) -> int:
	var expires_at = int(metrics.get("Command/ExpiresAtMs", 0))
	if expires_at > 0:
		return expires_at
	return _clock.now_ms + _command_ttl_s * 1000


func is_command_expired(metrics: Dictionary) -> bool:
	return command_deadline_sim_ms(metrics) <= _clock.now_ms


## Sets Sim/Timing/TtNoShowTimeoutS (whole seconds, > 0). Invalid values keep the
## previous value. Returns true when applied.
func configure_tt_no_show_timeout(text: String) -> bool:
	var value_text = text.strip_edges()
	if value_text.is_valid_int() and int(value_text) > 0:
		_tt_no_show_timeout_s = int(value_text)
		return true
	push_warning("Invalid TT no-show timeout %s; keeping %s s" % [text, _tt_no_show_timeout_s])
	return false


## Sets the inclusive whole-second range of one QC wait step. Invalid values keep
## the 10..30 s default. Returns true when the given range was applied.
func configure_qc_wait(step_name: String, min_text: String, max_text: String) -> bool:
	if not _qc_wait_s.has(step_name):
		push_warning("Unknown QC wait step %s" % step_name)
		return false
	var low_text = min_text.strip_edges()
	var high_text = max_text.strip_edges()
	if low_text.is_valid_int() and high_text.is_valid_int():
		var low = int(low_text)
		var high = int(high_text)
		if low >= 0 and high >= low:
			_qc_wait_s[step_name] = [low, high]
			return true
	push_warning("Invalid QC %s wait range %s..%s; keeping %s" % [step_name, min_text, max_text, _qc_wait_s[step_name]])
	return false


func _process(delta: float):
	if not _configuration_loaded:
		return
	var steps = _clock.steps_for_real_delta(delta)
	for _i in range(steps):
		_clock.step()
		_simulation_step(_clock.step_seconds())


## One fixed simulation step. Only sim time enters here, never frame delta.
## Movement runs first and QC handlers second, so a TT that reaches the transfer
## point in this step is seen by the QC in the same step, whatever the CHE order.
func _simulation_step(step_s: float):
	for che_id in _ches.keys():
		var che: Dictionary = _ches[che_id]
		if _active_commands.has(che_id):
			if not _is_qc_command(_active_commands[che_id]):
				_step_command(int(che_id), che, _active_commands[che_id], step_s)
		else:
			_consume_idle_energy(che, step_s)
			_publish_when_due(che)
	for che_id in _ches.keys():
		if _active_commands.has(che_id) and _is_qc_command(_active_commands[che_id]):
			_step_qc(int(che_id), _ches[che_id], _active_commands[che_id])
	if _clock.now_ms - _last_checkpoint_sim_ms >= SIM_TIME_CHECKPOINT_MS:
		_save_runtime_state()


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
	_clock.set_time_scale(float(session.get("time_scale", _clock.time_scale)))
	_mqtt.session_id = _session_id
	if _start_epoch_ms <= 0:
		_start_epoch_ms = int(session.get("start_epoch_ms", 0)) if session.get("start_epoch_ms") != null else 0
	var scenario_id = session.get("scenario_id")
	if scenario_id != null and not str(scenario_id).strip_edges().is_empty():
		_scenario_id = str(scenario_id).strip_edges()
	if session.has("seed") and session.get("seed") != null:
		_random.run_seed = SimRandom.parse_seed(str(session.get("seed")), _random.run_seed)
	_load_equipment(document)
	_restore_runtime_state()
	if _start_epoch_ms <= 0:
		# Wall clock only anchors Event/TimeMs; it never enters simulated state.
		_start_epoch_ms = int(Time.get_unix_time_from_system() * 1000.0)
	_configuration_loaded = true
	_bootstrap_retry_pending = false
	_mqtt.set_node_birth_metrics(node_birth_metrics())
	_connect_mqtt()
	print("HEADLESS loaded %s CHE records from OES revision %s" % [_ches.size(), _configuration_revision])


## NBIRTH metrics (contract §1, §2.1, §3.1, §5.1).
func node_birth_metrics() -> Array:
	return [
		{"name": "Contract/Version", "type": "String", "value": CONTRACT_VERSION},
		{"name": "Session/Id", "type": "String", "value": _session_id},
		{"name": "Site/Id", "type": "String", "value": _site_id},
		{"name": "Configuration/Revision", "type": "Int64", "value": _configuration_revision},
		{"name": "Configuration/Checksum", "type": "String", "value": _configuration_checksum},
		{"name": "Sim/Seed", "type": "Int64", "value": _random.run_seed},
		{"name": "Sim/RunId", "type": "String", "value": _random.run_id},
		{"name": "Sim/TimeScale", "type": "Double", "value": _clock.time_scale},
		{"name": "Sim/FixedStepMs", "type": "Int64", "value": _clock.step_ms},
		{"name": "Sim/ScenarioId", "type": "String", "value": _scenario_id},
		{"name": "Sim/StartEpochMs", "type": "Int64", "value": _start_epoch_ms},
		{"name": "Sim/Timing/QcHoldMinS", "type": "Int64", "value": int(_qc_wait_s.hold[0])},
		{"name": "Sim/Timing/QcHoldMaxS", "type": "Int64", "value": int(_qc_wait_s.hold[1])},
		{"name": "Sim/Timing/TtNoShowTimeoutS", "type": "Int64", "value": _tt_no_show_timeout_s},
		{"name": "Sim/Timing/CommandTtlS", "type": "Int64", "value": _command_ttl_s},
	]


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
		_on_broker_lost()
		push_warning(message)
		_schedule_mqtt_retry()
		return
	_mqtt_retry_pending = false
	if not _mqtt.subscribe_sparkplug_commands(_sparkplug_group_id, _sparkplug_node_id):
		push_error("Could not subscribe to Sparkplug NCMD/DCMD topics")
		return
	_publish_birth_and_state()
	_flush_result_outbox()
	print("HEADLESS Sparkplug Edge Node is online and subscribed to commands")


func _on_sparkplug_command(topic: String, message_type: String, device_id: String, metrics: Dictionary):
	if message_type == "NCMD":
		if bool(metrics.get("Node Control/Rebirth", false)):
			if _mqtt.publish_rebirth():
				_publish_birth_and_state()
				_flush_result_outbox()
		return
	var command_id = str(metrics.get("Command/Id", ""))
	if command_id.is_empty():
		return
	if _command_outcomes.has(command_id):
		_replay_command_result(command_id)
		return
	var che_id = int(metrics.get("Command/CheId", 0))
	if not _ches.has(che_id) or device_id != SimIds.device_id(_ches[che_id]):
		_fail(che_id, command_id, REASON_UNKNOWN_COMMAND_TARGET, "Unknown command target")
		return
	if str(metrics.get("Command/SessionId", "")) != _session_id:
		_fail(che_id, command_id, REASON_SESSION_MISMATCH, "Session mismatch")
		return
	if str(metrics.get("Command/SiteId", "")) != _site_id:
		_fail(che_id, command_id, REASON_SITE_MISMATCH, "Site mismatch")
		return
	if int(metrics.get("Command/ConfigurationRevision", -1)) != _configuration_revision:
		_fail(che_id, command_id, REASON_CONFIGURATION_REVISION_MISMATCH, "Configuration revision mismatch")
		return
	# ExpiresAtMs is a sim-time deadline (Sim/TimeMs units), checked on receipt
	# against the sim clock, never the wall clock.
	if is_command_expired(metrics):
		_fail(che_id, command_id, REASON_COMMAND_EXPIRED, "Command expired")
		return
	if str(metrics.get("Command/AssignmentId", "")).is_empty():
		_fail(che_id, command_id, REASON_ASSIGNMENT_ID_MISSING, "Assignment identity is missing")
		return
	var command_type = str(metrics.get("Command/Type", "")).strip_edges()
	if command_type.is_empty():
		command_type = COMMAND_EXECUTE_ORDER_LEG
	if not command_type in SUPPORTED_COMMAND_TYPES:
		_fail(che_id, command_id, REASON_UNSUPPORTED_COMMAND_TYPE, "Unsupported command type: %s" % command_type)
		return
	if command_type == COMMAND_CANCEL:
		_handle_cancel(che_id, command_id, metrics)
		return
	if _active_commands.has(che_id):
		# Contract §3.2: a hand-off assignment for a CHE that already holds one
		# is handoff_already_active; any other second command is che_busy.
		var holds_handoff = not str(_active_commands[che_id].get("handoff_id", "")).is_empty()
		var wants_handoff = not str(metrics.get("Command/HandoffId", "")).strip_edges().is_empty()
		_fail(
			che_id, command_id,
			REASON_HANDOFF_ALREADY_ACTIVE if holds_handoff and wants_handoff else REASON_CHE_BUSY,
			"CHE already has an active command"
		)
		return
	var container_number = str(metrics.get("Command/ContainerNumber", "")).strip_edges().to_upper()
	if not container_number.is_empty() and not is_container_number_format(container_number):
		_fail(che_id, command_id, REASON_CONTAINER_NUMBER_INVALID, "Container number format is invalid")
		return
	if command_type == COMMAND_EXECUTE_ORDER_LEG:
		if not metrics.has("Command/DestinationLatitude") or not metrics.has("Command/DestinationLongitude"):
			_fail(che_id, command_id, REASON_DESTINATION_MISSING, "Command has no destination coordinates")
			return
	elif command_type == COMMAND_QC_DISCHARGE:
		if str(metrics.get("Command/HandoffId", "")).strip_edges().is_empty():
			_fail(che_id, command_id, REASON_HANDOFF_ID_MISSING, "qc_discharge requires Command/HandoffId")
			return
		if int(metrics.get("Command/ContainerVisitId", 0)) <= 0:
			_fail(che_id, command_id, REASON_CONTAINER_VISIT_ID_MISSING, "qc_discharge requires Command/ContainerVisitId")
			return
	var handoff_id = str(metrics.get("Command/HandoffId", "")).strip_edges()
	if not handoff_id.is_empty() and _handoff_side_taken(handoff_id, command_type == COMMAND_QC_DISCHARGE):
		_fail(che_id, command_id, REASON_HANDOFF_ALREADY_ACTIVE, "HandoffId is already held by another active command")
		return
	var che: Dictionary = _ches[che_id]
	if not bool(che.get("powered_on", false)) or bool(che.get("is_in_maintenance", false)):
		_fail(che_id, command_id, REASON_CHE_NOT_EXECUTABLE, "CHE is not executable")
		return
	var command = _build_command(command_type, command_id, metrics, che)
	command["container_number"] = container_number
	che["working"] = true
	che["is_dispatched"] = true
	che["is_available"] = false
	_ches[che_id] = che
	_active_commands[che_id] = command
	if command_type == COMMAND_QC_DISCHARGE:
		var command_key = _command_seed_key(command)
		var hold_s = _draw_qc_wait_s(che, command_key, 0, "hold")
		var events = QcDischarge.start(command, _clock.now_ms, hold_s * 1000, _tt_no_show_timeout_s * 1000)
		_active_commands[che_id] = command
		_save_runtime_state()
		_publish_events(che_id, command, events)
	else:
		if not str(command.get("handoff_id", "")).is_empty() and command.has("source_latitude"):
			command["tt_phase"] = TT_DRIVING_TO_SOURCE
		_active_commands[che_id] = command
		_save_runtime_state()
		_publish_command_result(che_id, command_id, "acknowledged", 0, "", "", command)
		_publish_command_result(che_id, command_id, "started", 0, "", "", command)
	_publish_equipment_state(che, true)
	print("HEADLESS accepted %s %s for CHE %s from %s" % [command_type, command_id, che_id, topic])


## OES cancel DCMD (Command/Type = cancel). Command/CancelCommandId names the
## command to cancel and is required (OES always sets it; "cancel whatever is
## active" is unsafe after a re-dispatch).
## The active command stops, the CHE goes idle with no container, `cancelled`
## is published for the cancelled command, and the CHE accepts a new command.
func _handle_cancel(che_id: int, cancel_id: String, metrics: Dictionary):
	var target_id = str(metrics.get("Command/CancelCommandId", "")).strip_edges()
	if target_id.is_empty():
		_fail(che_id, cancel_id, REASON_CANCEL_TARGET_MISSING, "Cancel requires Command/CancelCommandId")
		return
	if not _active_commands.has(che_id):
		if (
			_command_outcomes.has(target_id)
			and str(_command_outcomes[target_id].get("event", "")) == "cancelled"
		):
			_command_outcomes[cancel_id] = _command_outcomes[target_id]
			_save_runtime_state()
			_replay_command_result(target_id)
			return
		_fail(che_id, cancel_id, REASON_NO_ACTIVE_COMMAND, "No active command to cancel")
		return
	var command: Dictionary = _active_commands[che_id]
	if target_id != str(command.command_id):
		_fail(che_id, cancel_id, REASON_CANCEL_TARGET_MISMATCH, "Cancel target is not the active command")
		return
	var events: Array
	if _is_qc_command(command):
		events = QcDischarge.cancel(command)
	else:
		command["load_container_visit_id"] = 0
		command["wait_until_ms"] = 0
		command["tt_phase"] = ""
		events = [{"event": "cancelled", "position_id": 0, "counterpart_che_id": 0, "work_state": "idle"}]
	var che: Dictionary = _ches[che_id]
	_mark_che_idle(che)
	_ches[che_id] = che
	_active_commands.erase(che_id)
	_publish_events(che_id, command, events)
	# A redelivered cancel replays the same cancelled result instead of
	# cancelling whatever command the CHE runs by then.
	_command_outcomes[cancel_id] = _command_outcomes[str(command.command_id)]
	_save_runtime_state()
	_publish_equipment_state(che, true)
	print("HEADLESS cancelled %s for CHE %s (cancel %s)" % [command.command_id, che_id, cancel_id])


func _build_command(command_type: String, command_id: String, metrics: Dictionary, che: Dictionary) -> Dictionary:
	var command := {
		"command_id": command_id,
		"command_type": command_type,
		"assignment_id": str(metrics.get("Command/AssignmentId", "")),
		"destination_latitude": float(metrics.get("Command/DestinationLatitude", che.get("latitude", 0.0))),
		"destination_longitude": float(metrics.get("Command/DestinationLongitude", che.get("longitude", 0.0))),
		"source_inventory_position_id": int(metrics.get("Command/SourceInventoryPositionId", 0)),
		"destination_inventory_position_id": int(metrics.get("Command/DestinationInventoryPositionId", 0)),
		"picked_up": false,
		"order_id": int(metrics.get("Command/OrderId", 0)),
		"leg_id": int(metrics.get("Command/LegId", 0)),
		"container_visit_id": int(metrics.get("Command/ContainerVisitId", 0)),
		"handoff_id": str(metrics.get("Command/HandoffId", "")).strip_edges(),
		"counterpart_che_id": int(metrics.get("Command/CounterpartCheId", 0)),
		"move_kind": str(metrics.get("Command/MoveKind", "")),
		"source_position_id": int(metrics.get("Command/SourcePositionId", 0)),
		"destination_position_id": int(metrics.get("Command/DestinationPositionId", 0)),
	}
	if metrics.has("Command/SourceLatitude") and metrics.has("Command/SourceLongitude"):
		command["source_latitude"] = float(metrics["Command/SourceLatitude"])
		command["source_longitude"] = float(metrics["Command/SourceLongitude"])
	# Hand-off lane: Command/TransferPositionId as sent by OES (S15-4). Only when
	# OES sends none does the QC fall back to its destination (the TT lane).
	var oes_transfer_id = int(metrics.get("Command/TransferPositionId", 0))
	if oes_transfer_id > 0:
		command["transfer_position_id"] = oes_transfer_id
	elif command_type == COMMAND_QC_DISCHARGE:
		var transfer_id = int(command.destination_inventory_position_id)
		command["transfer_position_id"] = transfer_id if transfer_id > 0 else int(command.destination_position_id)
	return command


## One HandoffId per transfer: each side (QC giver, TT receiver) can be held by
## one active command only. A CHE holds one active command, so a TT also holds
## at most one handoff.
func _handoff_side_taken(handoff_id: String, qc_side: bool) -> bool:
	for other in _active_commands.values():
		if str(other.get("handoff_id", "")) == handoff_id and _is_qc_command(other) == qc_side:
			return true
	return false


func _is_qc_command(command: Dictionary) -> bool:
	return str(command.get("command_type", COMMAND_EXECUTE_ORDER_LEG)) == COMMAND_QC_DISCHARGE


## Leg id when OES sends one (stable for a seeded MiniTOS fixture), else the command id.
func _command_seed_key(command: Dictionary) -> String:
	var leg_id = int(command.get("leg_id", 0))
	return "leg:%d" % leg_id if leg_id > 0 else "cmd:%s" % str(command.get("command_id", ""))


func _draw_qc_wait_s(che: Dictionary, command_key: String, wait_index: int, step_name: String) -> int:
	var bounds: Array = _qc_wait_s[step_name]
	return _random.draw_wait_s(SimIds.seed_key(che), command_key, wait_index, int(bounds[0]), int(bounds[1]))


## Light ISO 6346 shape check (owner code + category U/J/Z + 6 digits + check digit).
## The check digit itself is validated by MiniTOS.
static func is_container_number_format(container_number: String) -> bool:
	var pattern = RegEx.new()
	pattern.compile("^[A-Z]{3}[UJZ][0-9]{7}$")
	return pattern.search(container_number) != null


func _step_qc(che_id: int, che: Dictionary, command: Dictionary):
	var ready_id = _ready_counterpart_for(che_id, command)
	var events = QcDischarge.step(command, _clock.now_ms, ready_id)
	_active_commands[che_id] = command
	if events.is_empty():
		_publish_when_due(che)
		return
	if QcDischarge.is_finished(command):
		_mark_che_idle(che)
		_ches[che_id] = che
		_active_commands.erase(che_id)
	_save_runtime_state()
	_publish_events(che_id, command, events)
	if QcDischarge.is_finished(command):
		_complete_tt_handoff(int(command.get("counterpart_che_id_actual", 0)), che_id)
		print("HEADLESS completed qc_discharge %s for CHE %s" % [command.command_id, che_id])
	_publish_equipment_state(che, true)


## Godot-side readiness gate: a TT is ready for this QC when its active command
## carries the same HandoffId, it is waiting at its source (the transfer point)
## and, where either side names a counterpart, the names match. Pairing is by
## HandoffId only, never by container id.
func _ready_counterpart_for(qc_id: int, qc_command: Dictionary) -> int:
	var handoff_id = str(qc_command.get("handoff_id", ""))
	if handoff_id.is_empty():
		return 0
	var expected_id = int(qc_command.get("counterpart_che_id", 0))
	for other_key in _ches.keys():
		var other_id = int(other_key)
		if other_id == qc_id or (expected_id > 0 and other_id != expected_id):
			continue
		if not _active_commands.has(other_id):
			continue
		var other: Dictionary = _active_commands[other_id]
		if str(other.get("handoff_id", "")) != handoff_id:
			continue
		if str(other.get("tt_phase", "")) != TT_AT_SOURCE_WAITING:
			continue
		var named_qc = int(other.get("counterpart_che_id", 0))
		if named_qc > 0 and named_qc != qc_id:
			continue
		return other_id
	return 0


func _complete_tt_handoff(tt_id: int, qc_id: int):
	if tt_id <= 0 or not _active_commands.has(tt_id):
		return
	var command: Dictionary = _active_commands[tt_id]
	command["tt_phase"] = TT_DRIVING_TO_DESTINATION
	command["picked_up"] = true
	command["load_container_visit_id"] = int(command.get("container_visit_id", 0))
	command["counterpart_che_id_actual"] = qc_id
	_active_commands[tt_id] = command
	_save_runtime_state()
	var position_id = int(command.get("transfer_position_id", 0))
	if position_id <= 0:
		position_id = int(command.get("source_inventory_position_id", 0))
	if position_id <= 0:
		position_id = int(command.get("source_position_id", 0))
	# The receiver's pick is the hand-off itself: handoff_completed only.
	_publish_events(tt_id, command, [
		{"event": "handoff_completed", "position_id": position_id, "counterpart_che_id": qc_id, "handoff_role": "receiver"},
	])


func _publish_events(che_id: int, command: Dictionary, events: Array):
	for item in events:
		var context = command.duplicate()
		if int(item.get("counterpart_che_id", 0)) > 0:
			context["counterpart_che_id"] = int(item.counterpart_che_id)
		if item.has("work_state"):
			context["work_state"] = str(item.work_state)
		if item.has("handoff_role"):
			context["handoff_role"] = str(item.handoff_role)
		if item.has("source"):
			context["source"] = str(item.source)
		if str(item.event) == "handoff_completed":
			# Contract §3.3: both sides report the same FromCheId/ToCheId/TransferPositionId.
			var counterpart_id = int(item.get("counterpart_che_id", 0))
			var giver = str(item.get("handoff_role", "")) != "receiver"
			context["from_che_id"] = che_id if giver else counterpart_id
			context["to_che_id"] = counterpart_id if giver else che_id
			context["result_transfer_position_id"] = int(item.get("position_id", 0))
		_publish_command_result(
			che_id, str(command.command_id), str(item.event), int(item.get("position_id", 0)), "", "", context
		)


func _step_command(che_id: int, che: Dictionary, command: Dictionary, delta: float):
	var phase = str(command.get("tt_phase", ""))
	if phase == TT_DRIVING_TO_SOURCE:
		if _drive_towards(che_id, che, float(command.source_latitude), float(command.source_longitude), delta):
			command["tt_phase"] = TT_AT_SOURCE_WAITING
			_active_commands[che_id] = command
			_save_runtime_state()
			_publish_command_result(che_id, str(command.command_id), "arrived_at_source", 0, "", "", command)
			_publish_command_result(che_id, str(command.command_id), "waiting_for_handoff", 0, "", "", command)
			_publish_equipment_state(che, true)
		return
	if phase == TT_AT_SOURCE_WAITING:
		che["speed_mps"] = 0.0
		_consume_idle_energy(che, delta)
		_ches[che_id] = che
		_publish_when_due(che)
		return
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
		_publish_command_result(che_id, str(command.command_id), "container_picked_up", 0, "", "", command)
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


## Moves straight towards a point. Returns true (snapped, stopped) on arrival.
func _drive_towards(che_id: int, che: Dictionary, target_latitude: float, target_longitude: float, delta: float) -> bool:
	var latitude = float(che.get("latitude", 0.0))
	var longitude = float(che.get("longitude", 0.0))
	var east_m = deg_to_rad(target_longitude - longitude) * 6378137.0 * cos(deg_to_rad(latitude))
	var north_m = deg_to_rad(target_latitude - latitude) * 6378137.0
	var distance_m = sqrt(east_m * east_m + north_m * north_m)
	if distance_m <= 0.5:
		che["latitude"] = target_latitude
		che["longitude"] = target_longitude
		che["speed_mps"] = 0.0
		_ches[che_id] = che
		return true
	var speed_mps = _che_speed_mps(che)
	var ratio = min(speed_mps * delta, distance_m) / distance_m
	che["latitude"] = latitude + (target_latitude - latitude) * ratio
	che["longitude"] = longitude + (target_longitude - longitude) * ratio
	che["rotation"] = fposmod(rad_to_deg(atan2(east_m, north_m)), 360.0)
	che["speed_mps"] = speed_mps
	che["fuel_consumption_rate_lph"] = 18.0
	_consume_fuel(che, 18.0, delta)
	_ches[che_id] = che
	_publish_when_due(che)
	return false


func _mark_che_idle(che: Dictionary):
	che["working"] = false
	che["is_dispatched"] = false
	che["is_available"] = bool(che.get("powered_on", false)) and not bool(che.get("is_in_maintenance", false))
	che["speed_mps"] = 0.0
	che["fuel_consumption_rate_lph"] = 4.0 if bool(che.get("powered_on", false)) else 0.0


func _complete_command(che_id: int, che: Dictionary, command: Dictionary):
	che["latitude"] = float(command.destination_latitude)
	che["longitude"] = float(command.destination_longitude)
	_mark_che_idle(che)
	_ches[che_id] = che
	_active_commands.erase(che_id)
	_save_runtime_state()
	var command_id = str(command.command_id)
	var destination_position_id = int(command.destination_inventory_position_id)
	if destination_position_id > 0:
		_publish_command_result(che_id, command_id, "container_dropped", destination_position_id, "", "", command)
	_publish_command_result(che_id, command_id, "completed", destination_position_id, "", "", command)
	_publish_equipment_state(che, true)
	print("HEADLESS completed command %s for CHE %s" % [command_id, che_id])


func _publish_birth_and_state():
	var all_ches: Array = []
	for che in _ches.values():
		all_ches.append(che)
	_mqtt.publish_powered_devices(all_ches)
	for che in all_ches:
		_last_publish_ms[int(che.pk)] = _clock.now_ms


func _publish_when_due(che: Dictionary):
	if not _mqtt_connected or not bool(che.get("powered_on", false)):
		return
	var che_id = int(che.pk)
	var interval_ms = 2000 if bool(che.get("working", false)) else 20000
	var now_ms = _clock.now_ms
	if now_ms - int(_last_publish_ms.get(che_id, 0)) < interval_ms:
		return
	_publish_equipment_state(che)


func _publish_equipment_state(che: Dictionary, _force := false):
	_mqtt.sim_time_ms = _clock.now_ms
	if _mqtt_connected and _mqtt.publish_che_data(che):
		_last_publish_ms[int(che.pk)] = _clock.now_ms
		_save_runtime_state()


func _publish_command_result(
	che_id: int, command_id: String, event: String, position_id := 0, reason := "", result_id := "", context := {}
):
	if command_id.is_empty():
		return
	_event_seq += 1
	if result_id.is_empty():
		result_id = SimRandom.result_id(_session_id, _event_seq)
	var metrics: Array = [
		{"name": "Command/Result/Id", "type": "String", "value": result_id},
		{"name": "Command/Result/CommandId", "type": "String", "value": command_id},
		{"name": "Command/Result/Event", "type": "String", "value": event},
	]
	if position_id > 0:
		metrics.append({"name": "Command/Result/PositionId", "type": "Int64", "value": position_id})
	if not reason.is_empty():
		metrics.append({"name": "Command/Result/Reason", "type": "String", "value": reason})
	metrics.append_array(_result_context_metrics(context))
	metrics.append({"name": "Session/Id", "type": "String", "value": _session_id})
	metrics.append({"name": "Sim/TimeMs", "type": "Int64", "value": _clock.now_ms})
	metrics.append({"name": "Event/TimeMs", "type": "Int64", "value": _start_epoch_ms + _clock.now_ms})
	metrics.append({"name": "Sim/EventSeq", "type": "Int64", "value": _event_seq})
	_command_outcomes[command_id] = {
		"che_id": che_id, "event": event, "position_id": position_id,
		"reason": reason, "result_id": result_id, "metrics": metrics,
	}
	_result_outbox.append({
		"seq": _event_seq, "che_id": che_id, "result_id": result_id,
		"metrics": metrics, "packet_id": 0, "acked": false,
	})
	_save_runtime_state()
	_flush_result_outbox()


## `failed` result: Reason is a machine code, Detail the human-readable text.
func _fail(che_id: int, command_id: String, code: String, detail := ""):
	_publish_command_result(che_id, command_id, "failed", 0, code, "", {"detail": detail})


## Optional result metrics taken from the command (absent fields are omitted).
func _result_context_metrics(context: Dictionary) -> Array:
	var metrics: Array = []
	if context.is_empty():
		return metrics
	if not str(context.get("detail", "")).is_empty():
		metrics.append({"name": "Command/Result/Detail", "type": "String", "value": str(context.detail)})
	if int(context.get("container_visit_id", 0)) > 0:
		metrics.append({"name": "Command/Result/ContainerVisitId", "type": "Int64", "value": int(context.container_visit_id)})
	if not str(context.get("container_number", "")).is_empty():
		metrics.append({"name": "Command/Result/ContainerNumber", "type": "String", "value": str(context.container_number)})
	if not str(context.get("handoff_id", "")).is_empty():
		metrics.append({"name": "Command/Result/HandoffId", "type": "String", "value": str(context.handoff_id)})
	if int(context.get("counterpart_che_id", 0)) > 0:
		metrics.append({"name": "Command/Result/CounterpartCheId", "type": "Int64", "value": int(context.counterpart_che_id)})
	if not str(context.get("handoff_role", "")).is_empty():
		metrics.append({"name": "Command/Result/HandoffRole", "type": "String", "value": str(context.handoff_role)})
	if int(context.get("from_che_id", 0)) > 0:
		metrics.append({"name": "Command/Result/FromCheId", "type": "Int64", "value": int(context.from_che_id)})
	if int(context.get("to_che_id", 0)) > 0:
		metrics.append({"name": "Command/Result/ToCheId", "type": "Int64", "value": int(context.to_che_id)})
	if int(context.get("result_transfer_position_id", 0)) > 0:
		metrics.append({"name": "Command/Result/TransferPositionId", "type": "Int64", "value": int(context.result_transfer_position_id)})
	if not str(context.get("source", "")).is_empty():
		metrics.append({"name": "Command/Result/Source", "type": "String", "value": str(context.source)})
	if not str(context.get("move_kind", "")).is_empty():
		metrics.append({"name": "Command/Result/MoveKind", "type": "String", "value": str(context.move_kind)})
	var work_state = str(context.get("work_state", context.get("tt_phase", "")))
	if not work_state.is_empty():
		metrics.append({"name": "Work/State", "type": "String", "value": work_state})
		metrics.append({"name": "Work/WaitUntilSimMs", "type": "Int64", "value": int(context.get("wait_until_ms", 0))})
		metrics.append({"name": "Carrying/ContainerVisitId", "type": "Int64", "value": int(context.get("load_container_visit_id", 0))})
	return metrics


## Sends every pending journal entry not yet sent on this connection, in
## Sim/EventSeq order. Nothing is removed here: an entry stays pending until its
## PUBACK (_on_result_acknowledged). Stops at the first failed publish.
func _flush_result_outbox():
	if not _mqtt_connected:
		return
	_mqtt.sim_time_ms = _clock.now_ms
	var sent := 0
	for item in _pending_results_in_order():
		if int(item.get("packet_id", 0)) != 0:
			continue
		var che_id = int(item.get("che_id", 0))
		var che: Dictionary = _ches.get(che_id, {"pk": che_id})
		if not _mqtt.publish_command_result(che, item.get("metrics", [])):
			break
		var packet_id = int(_mqtt.last_result_packet_id)
		if packet_id <= 0:
			# Transport without PUBACK (QoS 0): nothing more to wait for.
			item["acked"] = true
		else:
			# A wrapped packet id still held by an older unacked entry: that
			# entry's PUBACK can no longer be told apart, so resend it later.
			for other in _result_outbox:
				if not is_same(other, item) and not bool(other.get("acked", false)) and int(other.get("packet_id", 0)) == packet_id:
					other["packet_id"] = 0
			item["packet_id"] = packet_id
		sent += 1
	if sent > 0:
		_save_runtime_state()


## Unacked journal entries sorted by Sim/EventSeq (stable for equal seq).
func _pending_results_in_order() -> Array:
	var pending: Array = []
	for item in _result_outbox:
		if not bool(item.get("acked", false)):
			pending.append(item)
	pending.sort_custom(func(a, b): return int(a.get("seq", 0)) < int(b.get("seq", 0)))
	return pending


func pending_result_count() -> int:
	return _pending_results_in_order().size()


## PUBACK from the broker: the matching in-flight entry is acked.
func _on_result_acknowledged(packet_id: int):
	if packet_id <= 0:
		return
	for item in _result_outbox:
		if not bool(item.get("acked", false)) and int(item.get("packet_id", 0)) == packet_id:
			item["acked"] = true
			_save_runtime_state()
			return


func _on_broker_lost():
	_mqtt_connected = false
	_requeue_unacked_results()


## Connection lost (or process restarted): in-flight entries never got a PUBACK
## on that connection, so they are resent after the next connect.
func _requeue_unacked_results():
	var changed := false
	for item in _result_outbox:
		if not bool(item.get("acked", false)) and int(item.get("packet_id", 0)) != 0:
			item["packet_id"] = 0
			changed = true
	if changed:
		_save_runtime_state()


## A redelivered DCMD replays the recorded result with the same Result/Id and
## Sim/EventSeq: its journal entry goes back to pending.
func _replay_command_result(command_id: String):
	var outcome: Dictionary = _command_outcomes[command_id]
	if outcome.get("metrics") is Array:
		var result_id = str(outcome.get("result_id", ""))
		var entry = null
		for item in _result_outbox:
			if str(item.get("result_id", "")) == result_id and not result_id.is_empty():
				entry = item
				break
		if entry == null:
			entry = {
				"seq": _metric_value(outcome.metrics, "Sim/EventSeq", 0), "che_id": int(outcome.get("che_id", 0)),
				"result_id": result_id, "metrics": outcome.metrics, "packet_id": 0, "acked": false,
			}
			_result_outbox.append(entry)
		entry["acked"] = false
		entry["packet_id"] = 0
		_save_runtime_state()
		_flush_result_outbox()
		return
	_publish_command_result(
		int(outcome.get("che_id", 0)), command_id, str(outcome.get("event", "failed")),
		int(outcome.get("position_id", 0)), str(outcome.get("reason", "")),
		str(outcome.get("result_id", ""))
	)


static func _metric_value(metrics: Array, name: String, fallback):
	for metric in metrics:
		if metric is Dictionary and str(metric.get("name", "")) == name:
			return metric.get("value", fallback)
	return fallback


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
	_clock.restore(int(_persisted_runtime_state.get("sim_time_ms", 0)))
	_event_seq = maxi(_event_seq, int(_persisted_runtime_state.get("event_seq", 0)))
	if int(_persisted_runtime_state.get("start_epoch_ms", 0)) > 0:
		_start_epoch_ms = int(_persisted_runtime_state.get("start_epoch_ms", 0))
	var saved_outcomes = _persisted_runtime_state.get("command_outcomes", {})
	if saved_outcomes is Dictionary:
		_command_outcomes = saved_outcomes
	var saved_outbox = _persisted_runtime_state.get("result_outbox", [])
	if saved_outbox is Array:
		_result_outbox = []
		for saved in saved_outbox:
			if not saved is Dictionary:
				continue
			var item: Dictionary = saved
			# JSON turns ints into floats; nothing was acked on the new connection.
			item["seq"] = int(item.get("seq", _metric_value(item.get("metrics", []), "Sim/EventSeq", 0)))
			item["che_id"] = int(item.get("che_id", 0))
			item["acked"] = bool(item.get("acked", false))
			item["packet_id"] = 0
			_result_outbox.append(item)
	_persisted_runtime_state.clear()


func _save_runtime_state():
	if _session_id.is_empty() or _site_id.is_empty() or _configuration_revision <= 0:
		return
	_last_checkpoint_sim_ms = _clock.now_ms
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
			"result_outbox": _result_outbox,
			"sim_time_ms": _clock.now_ms,
			"start_epoch_ms": _start_epoch_ms,
			"event_seq": _event_seq,
			"run_seed": _random.run_seed,
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


func _env_any(names: Array, fallback: String) -> String:
	for name in names:
		var value = OS.get_environment(str(name)).strip_edges()
		if not value.is_empty():
			return value
	return fallback

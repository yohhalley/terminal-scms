extends SceneTree

## TCL-014a: seed determinism, fixed-step sim clock, QC discharge state machine
## (incl. late TT), HandoffId pairing, command type routing, result outbox and
## MQTT packet-id wrap. TCL-014b: TT -> RTG at BLOCK01-TL, RTG stack to a dummy
## slot (full QC -> TT -> RTG chain). Runs without a broker or OES.
##
##   godot --headless --path . --script tests/unit/test_headless_sim.gd

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")
const SimClock = preload("res://source/headless/SimClock.gd")
const SimRandom = preload("res://source/headless/SimRandom.gd")
const SimIds = preload("res://source/headless/SimIds.gd")
const QcDischarge = preload("res://source/headless/QcDischarge.gd")
const RtgStack = preload("res://source/headless/RtgStack.gd")
const MQTTClient = preload("res://addons/mqtt/mqtt.gd")

const SESSION_ID := "11111111-1111-4111-8111-111111111111"
const SITE_ID := "22222222-2222-4222-8222-222222222222"
const QC_ID := 1
const TT_ID := 2
const QC_LAT := 41.35
const QC_LON := 2.17
const HANDOFF_ID := "55555555-5555-4555-8555-555555555555"
const RTG_ID := 3
## Block transfer lane BLOCK01-TL = the TT leg's destination.
const BLOCK_LAT := QC_LAT + 0.001
const HANDOFF_ID_2 := "66666666-6666-4666-8666-666666666666"
const HANDOFF_ID_WRONG := "77777777-7777-4777-8777-777777777777"
## Sim-time deadline (Sim/TimeMs units) far beyond every test run.
const COMMAND_EXPIRES_AT_SIM_MS := 86400000

var _failed := false
var _command_counter := 0


class FakeSparkplugManager:
	extends Node
	var calls: Array = []
	var connected := true

	func is_broker_connected():
		return connected

	func publish_device_birth(device_id: String, metrics: Array = []):
		calls.append(["DBIRTH", device_id, metrics])
		return true

	func publish_device_data(device_id: String, metrics: Array):
		calls.append(["DDATA", device_id, metrics])
		return true

	func publish_device_result(device_id: String, metrics: Array):
		calls.append(["RESULT", device_id, metrics])
		return true

	func publish_device_death(device_id: String):
		calls.append(["DDEATH", device_id, []])
		return true


func _initialize():
	_test_seed_determinism()
	_test_result_ids()
	_test_sim_clock()
	_test_qc_state_machine_tt_early()
	_test_qc_state_machine_tt_late()
	_test_qc_state_machine_no_show_and_cancel()
	_test_sim_qc_tt_late_end_to_end()
	_test_sim_qc_tt_early_end_to_end()
	_test_sim_replay_same_seed()
	_test_command_type_routing()
	_test_handoff_id_rules()
	_test_cancel_releases_che()
	_test_periodic_ddata_carries_sim_time()
	_test_command_expiry_uses_sim_time()
	_test_sim_tt_no_show_then_cancel()
	_test_configurable_qc_waits()
	_test_result_outbox_replays_in_order()
	_test_device_id_mapping()
	_test_mqtt_packet_id_wraps()
	_test_rtg_state_machine()
	_test_chain_happy_path_d016()
	_test_chain_late_tt_at_rtg()
	_test_chain_rtg_cancel()
	_test_chain_determinism()
	_test_chain_wrong_handoff_id()
	_test_rtg_rules_and_config()
	_cleanup_state_file()
	if not _failed:
		print("Headless simulation checks passed")
	quit(1 if _failed else 0)


# --- seed -------------------------------------------------------------------

func _test_seed_determinism():
	var a = SimRandom.new(42)
	var b = SimRandom.new(42)
	var c = SimRandom.new(43)
	var seq_a: Array = []
	var seq_b: Array = []
	var seq_c: Array = []
	for leg in range(20):
		for index in range(2):
			seq_a.append(a.draw_wait_s("QC01", "leg:%d" % leg, index, 10, 30))
			seq_b.append(b.draw_wait_s("QC01", "leg:%d" % leg, index, 10, 30))
			seq_c.append(c.draw_wait_s("QC01", "leg:%d" % leg, index, 10, 30))
	_assert(seq_a == seq_b, "Same seed must give the same wait sequence")
	_assert(seq_a != seq_c, "A different seed must change the wait sequence")
	for value in seq_a:
		_assert(typeof(value) == TYPE_INT and value >= 10 and value <= 30, "Waits must be whole seconds in 10..30")
	# Per-draw derivation: a draw does not depend on draws made before it.
	var fresh = SimRandom.new(42)
	_assert(fresh.draw_wait_s("QC01", "leg:7", 1, 10, 30) == seq_a[15], "A draw must not depend on earlier draws")
	_assert(fresh.draw_wait_s("QC01", "leg:7", 1, 10, 30) == seq_a[15], "Repeating a draw must give the same value")
	# Both bounds are reachable (uniform inclusive) over many keys.
	var seen := {}
	for leg in range(400):
		seen[a.draw_wait_s("CHE_9", "leg:%d" % leg, 0, 10, 30)] = true
	_assert(seen.has(10) and seen.has(30) and seen.size() == 21, "All 21 whole-second values 10..30 must be drawable")
	_assert(SimRandom.parse_seed("123") == 123, "Integer seed text must parse")
	_assert(SimRandom.parse_seed("abc", 7) == 7, "Invalid seed text must fall back")
	_assert(SimRandom.fnv1a32("a") == 0xe40c292c, "FNV-1a must match the reference value")


func _test_result_ids():
	var uuid = RegEx.new()
	uuid.compile("^[0-9a-f]{8}-[0-9a-f]{4}-8[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
	var a = SimRandom.new(1, "run-a")
	var id_a = a.result_id(SESSION_ID, "cmd", "completed", 3)
	_assert(uuid.search(id_a) != null, "Result ids must be UUID shaped (v8, RFC 4122 variant): %s" % id_a)
	_assert(id_a == SimRandom.new(1, "run-a").result_id(SESSION_ID, "cmd", "completed", 3), "Result ids must be deterministic")
	_assert(id_a != SimRandom.new(1, "run-b").result_id(SESSION_ID, "cmd", "completed", 3), "Result ids must include the run id")
	_assert(id_a != a.result_id(SESSION_ID, "cmd", "completed", 4), "Result ids must differ per event")


# --- clock ------------------------------------------------------------------

func _test_sim_clock():
	var clock = SimClock.new(100, 1.0)
	_assert(clock.steps_for_real_delta(0.05) == 0, "Half a step of real time must not run a step")
	_assert(clock.steps_for_real_delta(0.05) == 1, "Accumulated real time must run one step")
	_assert(clock.now_ms == 0, "steps_for_real_delta must not advance the clock itself")
	clock.step()
	_assert(clock.now_ms == 100, "A step must advance sim time by the fixed step")
	var fast = SimClock.new(100, 50.0)
	_assert(fast.steps_for_real_delta(1.0) == 500, "x50 must run 500 x 100 ms steps per real second")
	var odd_frames = SimClock.new(100, 1.0)
	var total := 0
	for frame in [0.016, 0.017, 0.033, 0.25, 0.684]:
		total += odd_frames.steps_for_real_delta(frame)
	_assert(total == 10, "Irregular frames must sum to whole steps of real time")
	var stalled = SimClock.new(100, 50.0)
	_assert(stalled.steps_for_real_delta(60.0) == SimClock.MAX_STEPS_PER_FRAME, "A stalled frame must be capped")
	_assert(stalled.steps_for_real_delta(0.0) == 0, "The dropped backlog must not run later")
	clock.restore(123400)
	_assert(clock.now_ms == 123400, "Restore must set sim time")
	_assert(is_equal_approx(SimClock.new(250, 1.0).step_seconds(), 0.25), "Step seconds must follow the fixed step")
	_assert(SimClock.new(100, 0.0).time_scale >= 0.01, "Time scale must stay positive")


# --- pure QC state machine ---------------------------------------------------

func _qc_command() -> Dictionary:
	return {"command_id": "c", "container_visit_id": 101, "transfer_position_id": 112, "handoff_id": HANDOFF_ID}


func _names(events: Array) -> Array:
	var names: Array = []
	for item in events:
		names.append(str(item.event))
	return names


func _test_qc_state_machine_tt_early():
	var command = _qc_command()
	var events = QcDischarge.start(command, 1000, 15000)
	_assert(_names(events) == ["acknowledged", "started", "container_picked_up"], "D-012: the container appears on receipt")
	_assert(str(events[0].work_state) == QcDischarge.COMMANDED, "Acknowledged is reported in commanded")
	_assert(command.work_state == QcDischarge.HOLDING and int(command.load_container_visit_id) == 101, "QC holds the container at once")
	_assert(int(command.wait_until_ms) == 16000, "Hold wait starts as soon as the QC has the container")
	_assert(QcDischarge.step(command, 15900, 7).is_empty(), "A ready TT must not shorten the hold wait")
	events = QcDischarge.step(command, 16000, 7)
	_assert(_names(events) == ["handoff_completed", "completed"], "Deposit at hold end when TT is ready (no giver drop)")
	_assert(int(events[0].position_id) == 112 and int(events[1].counterpart_che_id) == 7, "Deposit reports position and TT")
	_assert(str(events[0].get("handoff_role", "")) == "giver", "QC is the giver in discharge")
	_assert(QcDischarge.is_finished(command) and int(command.load_container_visit_id) == 0, "QC ends empty in done")
	_assert(not QcDischarge.new().has_method("picking"), "No picking phase exists")


func _test_qc_state_machine_tt_late():
	var command = _qc_command()
	QcDischarge.start(command, 0, 10000)
	_assert(QcDischarge.step(command, 9900, 0).is_empty(), "Nothing happens during the hold wait")
	var events = QcDischarge.step(command, 10000, 0)
	_assert(_names(events) == ["waiting_for_handoff"], "Hold end without a TT must wait for the TT")
	_assert(command.work_state == QcDischarge.WAITING_FOR_TT and int(command.wait_until_ms) == 0, "QC enters waiting_for_tt")
	_assert(QcDischarge.step(command, 45000, 0).is_empty(), "QC keeps waiting while no TT is ready")
	events = QcDischarge.step(command, 45100, 9)
	_assert(_names(events) == ["handoff_completed", "completed"], "Deposit as soon as the late TT is ready")
	_assert(int(events[2].counterpart_che_id) == 9, "Completion names the TT")


func _test_qc_state_machine_no_show_and_cancel():
	var command = _qc_command()
	QcDischarge.start(command, 0, 10000, 300000)
	_assert(_names(QcDischarge.step(command, 10000, 0)) == ["waiting_for_handoff"], "QC waits for the TT after the hold")
	_assert(QcDischarge.step(command, 309900, 0).is_empty(), "No time-out before 300 s of waiting")
	var events = QcDischarge.step(command, 310000, 0)
	_assert(_names(events) == ["handoff_timed_out"], "D-013: handoff_timed_out after 300 s sim time in waiting_for_tt")
	_assert(command.work_state == QcDischarge.WAITING_FOR_TT and int(command.load_container_visit_id) == 101, "QC keeps the container after the time-out")
	_assert(QcDischarge.step(command, 400000, 0).is_empty(), "handoff_timed_out is published once")
	_assert(QcDischarge.step(command, 410000, 9).is_empty(), "A TT arriving after the time-out gets no transfer")
	events = QcDischarge.cancel(command)
	_assert(_names(events) == ["cancelled"] and str(events[0].work_state) == QcDischarge.IDLE, "Cancel emits cancelled in idle")
	_assert(command.work_state == QcDischarge.IDLE and int(command.load_container_visit_id) == 0, "Cancel clears the container from the QC")
	var disabled = _qc_command()
	QcDischarge.start(disabled, 0, 1000, 60000)
	QcDischarge.step(disabled, 1000, 0)
	_assert(_names(QcDischarge.step(disabled, 61000, 0)) == ["handoff_timed_out"], "The no-show timeout is configurable")


# --- simulator end to end ------------------------------------------------------

func _new_simulator(run_seed: int, tt_lat: float) -> HeadlessSimulator:
	_cleanup_state_file()
	var simulator = HeadlessSimulator.new()
	var fake = FakeSparkplugManager.new()
	simulator._session_id = SESSION_ID
	simulator._site_id = SITE_ID
	simulator._configuration_revision = 7
	simulator._configuration_checksum = "checksum-7"
	simulator._mqtt._manager = fake
	simulator._mqtt._connected = true
	simulator._mqtt_connected = true
	simulator._random = SimRandom.new(run_seed, "test-run")
	simulator._clock = SimClock.new(100, 1.0)
	simulator._start_epoch_ms = 1764583200000
	simulator._ches = {
		QC_ID: _che(QC_ID, "QC01", QC_LAT, QC_LON),
		TT_ID: _che(TT_ID, "TT01", tt_lat, QC_LON),
	}
	return simulator


func _che(id: int, name: String, lat: float, lon: float) -> Dictionary:
	return {
		"pk": id, "name": name, "powered_on": true, "working": false, "is_available": true,
		"is_dispatched": false, "is_in_maintenance": false, "latitude": lat, "longitude": lon,
	}


func _free_simulator(simulator):
	var fake = simulator._mqtt._manager
	simulator._mqtt.free()
	simulator._bootstrap.free()
	simulator.free()
	fake.free()
	_cleanup_state_file()


func _cleanup_state_file():
	DirAccess.remove_absolute(ProjectSettings.globalize_path("user://simulation_runtime_state.json"))


func _command(che_id: int, extra: Dictionary) -> Dictionary:
	_command_counter += 1
	var metrics = {
		"Command/Id": "33333333-3333-4333-8333-%012d" % _command_counter,
		"Command/CheId": che_id,
		"Command/SessionId": SESSION_ID,
		"Command/SiteId": SITE_ID,
		"Command/ConfigurationRevision": 7,
		"Command/ExpiresAtMs": COMMAND_EXPIRES_AT_SIM_MS,
		"Command/AssignmentId": "44444444-4444-4444-8444-444444444444",
	}
	for key in extra:
		metrics[key] = extra[key]
	return metrics


func _send(simulator, che_id: int, metrics: Dictionary):
	var device_id = SimIds.device_id_for(che_id, simulator._ches)
	simulator._on_sparkplug_command("spBv1.0/G/DCMD/N/%s" % device_id, "DCMD", device_id, metrics)


func _qc_metrics() -> Dictionary:
	return {
		"Command/Type": "qc_discharge", "Command/OrderId": 41, "Command/LegId": 4101,
		"Command/ContainerVisitId": 101, "Command/ContainerNumber": "MSCU1234565",
		"Command/HandoffId": HANDOFF_ID, "Command/CounterpartCheId": TT_ID,
		"Command/DestinationInventoryPositionId": 112, "Command/MoveKind": "discharge",
	}


func _tt_metrics() -> Dictionary:
	return {
		"Command/Type": "execute_order_leg", "Command/OrderId": 41, "Command/LegId": 4102,
		"Command/ContainerVisitId": 101, "Command/HandoffId": HANDOFF_ID, "Command/CounterpartCheId": QC_ID,
		"Command/SourcePositionId": 112, "Command/SourceLatitude": QC_LAT, "Command/SourceLongitude": QC_LON,
		"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON,
		"Command/DestinationInventoryPositionId": 900,
	}


func _run_steps(simulator, max_steps: int):
	for _i in range(max_steps):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
		if simulator._active_commands.is_empty():
			return


## Decoded command results in publish order: [{device, metrics by name}].
func _results(simulator) -> Array:
	var out: Array = []
	for call in simulator._mqtt._manager.calls:
		if str(call[0]) != "RESULT":
			continue
		var by_name := {}
		for metric in call[2]:
			by_name[str(metric.name)] = metric.value
		out.append({"device": str(call[1]), "m": by_name})
	return out


func _events_of(results: Array, device: String) -> Array:
	var names: Array = []
	for item in results:
		if item.device == device:
			names.append(str(item.m["Command/Result/Event"]))
	return names


func _find(results: Array, device: String, event: String) -> Dictionary:
	for item in results:
		if item.device == device and str(item.m["Command/Result/Event"]) == event:
			return item.m
	return {}


func _test_sim_qc_tt_late_end_to_end():
	# TT starts 400 m away at 4 m/s: it reaches the QC after ~100 s, after the hold wait (<= 30 s).
	var simulator = _new_simulator(42, QC_LAT - 0.0036)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_assert(simulator._active_commands.size() == 2, "QC and TT commands must both be accepted")
	_run_steps(simulator, 3000)
	_assert(simulator._active_commands.keys() == [TT_ID], "QC finishes; the TT keeps its leg (%s)" % [simulator._active_commands.keys()])
	_assert(_tt_phase(simulator) == "at_destination_waiting", "TCL-014b: the loaded TT waits at the block lane for the RTG")
	var results = _results(simulator)
	var qc_events = _events_of(results, "QC01")
	_assert(qc_events == [
		"acknowledged", "started", "container_picked_up", "waiting_for_handoff",
		"handoff_completed", "completed",
	], "Late TT: QC event order: %s" % [qc_events])
	var tt_events = _events_of(results, "TT01")
	_assert(tt_events == [
		"acknowledged", "started", "arrived_at_source", "waiting_for_handoff",
		"handoff_completed", "arrived_at_destination",
	], "Late TT: TT event order: %s" % [tt_events])
	var picked = _find(results, "QC01", "container_picked_up")
	var waiting = _find(results, "QC01", "waiting_for_handoff")
	var arrived = _find(results, "TT01", "arrived_at_source")
	_assert(not _events_of(results, "QC01").has("container_dropped"), "A hand-off giver never publishes container_dropped")
	var dropped = _find(results, "QC01", "handoff_completed")
	var pick_ms = int(picked["Sim/TimeMs"])
	var hold_s = SimRandom.new(42).draw_wait_s("QC01", "leg:4101", 0, 10, 30)
	_assert(pick_ms == 0, "D-012: the container appears when the command arrives (%s ms)" % pick_ms)
	_assert(str(picked.get("Command/Result/Source", "")) == "VESSEL", "T-007: the container appears from source VESSEL")
	_assert(int(waiting["Sim/TimeMs"]) == pick_ms + hold_s * 1000, "QC waits for the TT exactly at the seeded hold-wait end")
	_assert(int(dropped["Sim/TimeMs"]) == int(arrived["Sim/TimeMs"]), "Deposit must happen when the late TT is ready")
	_assert(int(dropped["Command/Result/PositionId"]) == 112, "handoff_completed must carry the transfer PositionId")
	_assert(int(dropped["Command/Result/ContainerVisitId"]) == 101, "Results must carry ContainerVisitId")
	_assert(int(dropped["Command/Result/CounterpartCheId"]) == TT_ID, "QC deposit must name the TT")
	_assert(str(dropped["Command/Result/ContainerNumber"]) == "MSCU1234565", "Results echo the container number")
	_assert(str(dropped["Command/Result/MoveKind"]) == "discharge", "MoveKind is echoed back unchanged")
	_assert(str(dropped["Session/Id"]) == SESSION_ID, "Session/Id must be on every result")
	_assert(int(dropped["Event/TimeMs"]) == 1764583200000 + int(dropped["Sim/TimeMs"]), "Event/TimeMs = StartEpochMs + Sim/TimeMs")
	var qc_handoff = _find(results, "QC01", "handoff_completed")
	var tt_handoff = _find(results, "TT01", "handoff_completed")
	_assert(str(qc_handoff["Command/Result/HandoffId"]) == HANDOFF_ID and str(tt_handoff["Command/Result/HandoffId"]) == HANDOFF_ID, "Both sides report the HandoffId")
	_assert(str(qc_handoff["Command/Result/HandoffRole"]) == "giver", "QC handoff role is giver")
	_assert(str(tt_handoff["Command/Result/HandoffRole"]) == "receiver", "TT handoff role is receiver")
	_assert(int(tt_handoff["Command/Result/CounterpartCheId"]) == QC_ID, "TT handoff names the QC")
	_assert(str(_find(results, "QC01", "acknowledged")["Work/State"]) == "commanded", "Acknowledged is published in commanded")
	_assert(str(waiting["Work/State"]) == "waiting_for_tt", "QC Work/State waiting_for_tt")
	_assert(str(_find(results, "QC01", "completed")["Work/State"]) == "done", "QC Work/State done on completion")
	var seqs: Array = []
	for item in results:
		seqs.append(int(item.m["Sim/EventSeq"]))
	var sorted_seqs = seqs.duplicate()
	sorted_seqs.sort()
	_assert(seqs == sorted_seqs and seqs.size() == results.size(), "Sim/EventSeq must increase in publish order")
	_assert(not bool(simulator._ches[QC_ID].working) and bool(simulator._ches[QC_ID].is_available), "QC is idle again")
	_free_simulator(simulator)


func _test_sim_qc_tt_early_end_to_end():
	# TT starts at the transfer point: it is ready before the hold wait ends, so the QC never waits.
	var simulator = _new_simulator(7, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_steps(simulator, 3000)
	var results = _results(simulator)
	_assert(not _events_of(results, "QC01").has("waiting_for_handoff"), "Early TT: QC must not wait")
	var picked = _find(results, "QC01", "container_picked_up")
	var dropped = _find(results, "QC01", "handoff_completed")
	var hold_s = SimRandom.new(7).draw_wait_s("QC01", "leg:4101", 0, 10, 30)
	_assert(int(dropped["Sim/TimeMs"]) == int(picked["Sim/TimeMs"]) + hold_s * 1000, "Early TT: deposit at hold-wait end")
	_assert(not simulator._active_commands.has(QC_ID), "Early TT: the QC command finishes")
	_free_simulator(simulator)


func _trace(run_seed: int) -> Array:
	var simulator = _new_simulator(run_seed, QC_LAT - 0.0036)
	_command_counter = 100
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_steps(simulator, 3000)
	var trace: Array = []
	for item in _results(simulator):
		trace.append([item.device, item.m["Command/Result/Event"], int(item.m["Sim/TimeMs"]), item.m["Command/Result/Id"]])
	_free_simulator(simulator)
	return trace


func _test_sim_replay_same_seed():
	var first = _trace(1234)
	var second = _trace(1234)
	var other = _trace(4321)
	_assert(not first.is_empty() and first == second, "Same seed and inputs must replay the same events, times and ids")
	_assert(first != other, "A different seed must change the run")


# --- command routing and validation ---------------------------------------------

func _last_result(simulator) -> Dictionary:
	var results = _results(simulator)
	return results[-1].m if not results.is_empty() else {}


func _test_command_type_routing():
	var simulator = _new_simulator(1, QC_LAT)
	var legacy = _command(TT_ID, {"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON})
	_send(simulator, TT_ID, legacy)
	_assert(simulator._active_commands.has(TT_ID), "A DCMD without Command/Type stays execute_order_leg")
	_assert(str(simulator._active_commands[TT_ID].command_type) == "execute_order_leg", "Legacy type is execute_order_leg")
	_assert(not simulator._active_commands[TT_ID].has("tt_phase"), "Legacy commands keep legacy movement")
	_send(simulator, QC_ID, _command(QC_ID, {"Command/Type": "qc_load", "Command/HandoffId": "h"}))
	var last = _last_result(simulator)
	_assert(str(last["Command/Result/Event"]) == "failed" and str(last["Command/Result/Reason"]).begins_with("Unsupported command type"), "Unknown types are rejected")
	_assert(not simulator._active_commands.has(QC_ID), "A rejected type creates no action")
	var no_handoff = _qc_metrics()
	no_handoff.erase("Command/HandoffId")
	_send(simulator, QC_ID, _command(QC_ID, no_handoff))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]).contains("HandoffId"), "qc_discharge needs a HandoffId")
	var bad_number = _qc_metrics()
	bad_number["Command/ContainerNumber"] = "MSC-1234"
	_send(simulator, QC_ID, _command(QC_ID, bad_number))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]).contains("Container number"), "Malformed container numbers are rejected")
	_assert(HeadlessSimulator.is_container_number_format("MSCU1234565"), "A well-formed container number passes")
	for category in ["J", "Z"]:
		_assert(HeadlessSimulator.is_container_number_format("MSC%s1234565" % category), "ISO 6346 category %s passes" % category)
	_assert(not HeadlessSimulator.is_container_number_format("MSCR1234565"), "Category R is rejected (contract [UJZ])")
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_assert(simulator._active_commands.has(QC_ID), "A valid qc_discharge is accepted")
	_free_simulator(simulator)


func _test_handoff_id_rules():
	var simulator = _new_simulator(1, QC_LAT - 0.0036)
	simulator._ches[3] = _che(3, "TT02", QC_LAT - 0.0036, QC_LON)
	simulator._ches[4] = _che(4, "QC02", QC_LAT, QC_LON)
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	var second_tt = _tt_metrics()
	second_tt["Command/CounterpartCheId"] = QC_ID
	_send(simulator, 3, _command(3, second_tt))
	_assert(not simulator._active_commands.has(3), "A second receiver for the same HandoffId is rejected")
	_assert(str(_last_result(simulator)["Command/Result/Reason"]).contains("HandoffId"), "Rejection names the HandoffId")
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, 4, _command(4, _qc_metrics()))
	_assert(simulator._active_commands.has(QC_ID) and not simulator._active_commands.has(4), "A second giver for the same HandoffId is rejected")
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "CHE already has an active command", "A TT holds one handoff at a time")
	# Pairing is by HandoffId, not by container: a TT with the same container but another HandoffId is never used.
	var simulator2 = _new_simulator(1, QC_LAT)
	var other_tt = _tt_metrics()
	other_tt["Command/HandoffId"] = "66666666-6666-4666-8666-666666666666"
	var qc = _qc_metrics()
	qc.erase("Command/CounterpartCheId")
	_send(simulator2, QC_ID, _command(QC_ID, qc))
	_send(simulator2, TT_ID, _command(TT_ID, other_tt))
	_run_steps(simulator2, 1200)
	_assert(simulator2._active_commands.has(QC_ID), "QC must keep waiting when only a TT with another HandoffId is present")
	_assert(str(simulator2._active_commands[QC_ID].work_state) == "waiting_for_tt", "QC waits for its own HandoffId")
	_free_simulator(simulator2)
	_free_simulator(simulator)


func _cancel(che_id: int, target_id := "") -> Dictionary:
	var extra = {"Command/Type": "cancel"}
	if not target_id.is_empty():
		extra["Command/CancelCommandId"] = target_id
	return _command(che_id, extra)


func _test_cancel_releases_che():
	var simulator = _new_simulator(1, QC_LAT - 0.0036)
	var leg = _command(TT_ID, {"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON})
	_send(simulator, TT_ID, leg)
	_run_steps(simulator, 10)
	_send(simulator, TT_ID, _cancel(TT_ID, "99999999-9999-4999-8999-999999999999"))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Cancel target is not the active command", "A cancel for another command is rejected")
	_assert(simulator._active_commands.has(TT_ID), "A rejected cancel leaves the command running")
	var cancel = _cancel(TT_ID, str(leg["Command/Id"]))
	_send(simulator, TT_ID, cancel)
	var last = _last_result(simulator)
	_assert(str(last["Command/Result/Event"]) == "cancelled", "Cancel publishes cancelled")
	_assert(str(last["Command/Result/CommandId"]) == str(leg["Command/Id"]), "cancelled is reported for the cancelled command")
	_assert(str(last["Work/State"]) == "idle" and int(last["Load/ContainerVisitId"]) == 0, "Cancelled CHE is idle and empty")
	_assert(not simulator._active_commands.has(TT_ID), "Cancel clears the active command")
	_assert(not bool(simulator._ches[TT_ID].working) and bool(simulator._ches[TT_ID].is_available), "Cancelled CHE is available")
	var next_leg = _command(TT_ID, {"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON})
	_send(simulator, TT_ID, next_leg)
	_assert(simulator._active_commands.has(TT_ID) and str(simulator._active_commands[TT_ID].command_id) == str(next_leg["Command/Id"]), "The next command after a cancel is accepted")
	var before = _results(simulator).size()
	_send(simulator, TT_ID, cancel)
	_assert(str(_last_result(simulator)["Command/Result/Id"]) == str(last["Command/Result/Id"]), "A redelivered cancel replays the same cancelled result")
	_assert(_results(simulator).size() == before + 1 and simulator._active_commands.has(TT_ID), "A redelivered cancel does not cancel the next command")
	_send(simulator, TT_ID, _cancel(TT_ID))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Cancel requires Command/CancelCommandId", "A cancel without a target is rejected")
	_assert(simulator._active_commands.has(TT_ID), "A rejected cancel leaves the next command running")
	_send(simulator, TT_ID, _cancel(TT_ID, str(next_leg["Command/Id"])))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "cancelled" and not simulator._active_commands.has(TT_ID), "A cancel naming the active command cancels it")
	_send(simulator, TT_ID, _cancel(TT_ID, "99999999-9999-4999-8999-999999999999"))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "No active command to cancel", "Cancel on an idle CHE fails cleanly")
	# A TT that already holds the container is emptied by the cancel.
	var simulator2 = _new_simulator(3, QC_LAT)
	_send(simulator2, QC_ID, _command(QC_ID, _qc_metrics()))
	var tt = _command(TT_ID, _tt_metrics())
	_send(simulator2, TT_ID, tt)
	for _i in range(400):
		simulator2._clock.step()
		simulator2._simulation_step(simulator2._clock.step_seconds())
		if not simulator2._active_commands.has(QC_ID):
			break
	_assert(int(simulator2._active_commands[TT_ID].get("load_container_visit_id", 0)) == 101, "TT holds the container after the handoff")
	_send(simulator2, TT_ID, _cancel(TT_ID, str(tt["Command/Id"])))
	_assert(int(_last_result(simulator2)["Load/ContainerVisitId"]) == 0 and simulator2._active_commands.is_empty(), "Cancel clears the TT's container")
	_free_simulator(simulator2)
	_free_simulator(simulator)


func _leg(che_id: int, expires_at) -> Dictionary:
	var metrics = _command(che_id, {"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON})
	if expires_at == null:
		metrics.erase("Command/ExpiresAtMs")
	else:
		metrics["Command/ExpiresAtMs"] = expires_at
	return metrics


func _test_periodic_ddata_carries_sim_time():
	var simulator = _new_simulator(1, QC_LAT)
	simulator._mqtt.session_id = SESSION_ID
	simulator._clock.restore(40000)
	for _i in range(250):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
	var samples: Array = []
	for call in simulator._mqtt._manager.calls:
		if str(call[0]) != "DDATA":
			continue
		var by_name := {}
		for metric in call[2]:
			by_name[str(metric.name)] = metric.value
		samples.append(by_name)
	_assert(samples.size() >= 2, "Idle CHE publish periodic DDATA (%s)" % samples.size())
	var previous := -1
	for sample in samples:
		_assert(sample.has("Sim/TimeMs") and str(sample.get("Session/Id", "")) == SESSION_ID, "Periodic DDATA carries Sim/TimeMs and Session/Id")
		_assert(int(sample.get("Sim/TimeMs", -1)) >= 40000 and int(sample.get("Sim/TimeMs", -1)) >= previous, "Sim/TimeMs is the sim clock and never goes back")
		previous = int(sample.get("Sim/TimeMs", -1))
	_assert(int(samples[-1]["Sim/TimeMs"]) - int(samples[0]["Sim/TimeMs"]) == 20000, "Idle cadence is 20 s of sim time")
	_free_simulator(simulator)


func _test_command_expiry_uses_sim_time():
	var simulator = _new_simulator(1, QC_LAT)
	simulator._clock.restore(120000)
	_send(simulator, TT_ID, _leg(TT_ID, 120000))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Command expired", "ExpiresAtMs at the current Sim/TimeMs is expired")
	_send(simulator, TT_ID, _leg(TT_ID, 60000))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Command expired", "ExpiresAtMs in the sim past is expired")
	_assert(not simulator._active_commands.has(TT_ID), "Expired commands create no action")
	var wall_now = int(Time.get_unix_time_from_system() * 1000.0)
	_assert(not simulator.is_command_expired({"Command/ExpiresAtMs": 120001}), "A deadline 1 ms after Sim/TimeMs is valid even though it is long past in wall time")
	_assert(wall_now > 120001, "Sanity: the sim deadline is far behind the wall clock")
	_send(simulator, TT_ID, _leg(TT_ID, 120100))
	_assert(simulator._active_commands.has(TT_ID), "A command within its sim-time deadline is accepted")
	_assert(simulator._command_ttl_s == 600, "Default sim command TTL is 600 s")
	_assert(simulator.command_deadline_sim_ms({}) == 120000 + 600000, "Without ExpiresAtMs the deadline is receipt + Sim/Timing/CommandTtlS")
	_assert(simulator.configure_command_ttl("30") and simulator.command_deadline_sim_ms({}) == 150000, "Command TTL is configurable")
	_assert(not simulator.configure_command_ttl("0") and not simulator.configure_command_ttl("x") and simulator._command_ttl_s == 30, "Invalid TTLs are rejected")
	_send(simulator, QC_ID, _leg(QC_ID, null))
	_assert(simulator._active_commands.has(QC_ID), "A DCMD without ExpiresAtMs gets the default sim TTL and is accepted")
	_free_simulator(simulator)


func _test_sim_tt_no_show_then_cancel():
	var simulator = _new_simulator(11, QC_LAT - 0.0036)
	_assert(simulator.configure_tt_no_show_timeout("300"), "A whole-second timeout applies")
	_assert(not simulator.configure_tt_no_show_timeout("0") and not simulator.configure_tt_no_show_timeout("x"), "Invalid timeouts are rejected")
	_assert(simulator._tt_no_show_timeout_s == 300, "Default Sim/Timing/TtNoShowTimeoutS is 300")
	var qc = _command(QC_ID, _qc_metrics())
	_send(simulator, QC_ID, qc)
	var hold_s = SimRandom.new(11).draw_wait_s("QC01", "leg:4101", 0, 10, 30)
	for _i in range(3500):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
	var results = _results(simulator)
	_assert(_events_of(results, "QC01") == [
		"acknowledged", "started", "container_picked_up", "waiting_for_handoff", "handoff_timed_out",
	], "No-show: QC event order: %s" % [_events_of(results, "QC01")])
	var timed_out = _find(results, "QC01", "handoff_timed_out")
	_assert(int(timed_out["Sim/TimeMs"]) == hold_s * 1000 + 300000, "Time-out fires 300 s sim time after the hold end")
	_assert(str(timed_out["Command/Result/HandoffId"]) == HANDOFF_ID, "handoff_timed_out carries the HandoffId")
	_assert(str(timed_out["Work/State"]) == "waiting_for_tt" and int(timed_out["Load/ContainerVisitId"]) == 101, "QC still holds the container until OES cancels")
	# A TT that shows up after the time-out but before the cancel gets no transfer.
	simulator._ches[TT_ID].latitude = QC_LAT
	var tt = _command(TT_ID, _tt_metrics())
	_send(simulator, TT_ID, tt)
	for _i in range(50):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
	_assert(_find(_results(simulator), "QC01", "handoff_completed").is_empty(), "No transfer after handoff_timed_out")
	_send(simulator, QC_ID, _cancel(QC_ID, str(qc["Command/Id"])))
	_send(simulator, TT_ID, _cancel(TT_ID, str(tt["Command/Id"])))
	var qc_cancel = _find(_results(simulator), "QC01", "cancelled")
	_assert(str(qc_cancel.get("Work/State", "")) == "idle" and int(qc_cancel.get("Load/ContainerVisitId", -1)) == 0, "OES cancel clears the QC")
	_assert(simulator._active_commands.is_empty(), "Both legs are cancelled")
	var retry = _qc_metrics()
	retry["Command/HandoffId"] = "77777777-7777-4777-8777-777777777777"
	_send(simulator, QC_ID, _command(QC_ID, retry))
	_assert(simulator._active_commands.has(QC_ID), "The QC accepts a new command after the cancel")
	_free_simulator(simulator)


func _test_configurable_qc_waits():
	var simulator = _new_simulator(5, QC_LAT)
	_assert(simulator.configure_qc_wait("hold", "4", "4"), "A valid hold range must apply")
	_assert(not simulator.configure_qc_wait("hold", "9", "2"), "min > max must be rejected")
	_assert(not simulator.configure_qc_wait("hold", "x", "3"), "Non-integer bounds must be rejected")
	_assert(not simulator.configure_qc_wait("pick", "3", "3"), "D-012: there is no pick wait to configure")
	_assert(simulator._qc_wait_s.keys() == ["hold"] and simulator._qc_wait_s.hold == [4, 4], "Rejected ranges keep the last valid range")
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	var command = simulator._active_commands[QC_ID]
	_assert(int(command.hold_wait_ms) == 4000 and int(command.wait_until_ms) == 4000, "Configured hold wait applies from receipt")
	var defaults = HeadlessSimulator.new()
	_assert(defaults._qc_wait_s.hold == [10, 30], "Default hold wait is 10..30 s")
	defaults._mqtt.free()
	defaults._bootstrap.free()
	defaults.free()
	_free_simulator(simulator)


func _test_result_outbox_replays_in_order():
	var simulator = _new_simulator(2, QC_LAT)
	var fake = simulator._mqtt._manager
	simulator._mqtt_connected = false
	fake.connected = false
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_run_steps(simulator, 400)
	_assert(_results(simulator).is_empty(), "No results go out while disconnected")
	var buffered = simulator._result_outbox.size()
	_assert(buffered >= 3, "Results are buffered while disconnected (%s)" % buffered)
	var restarted = HeadlessSimulator.new()
	restarted._session_id = SESSION_ID
	restarted._site_id = SITE_ID
	restarted._configuration_revision = 7
	restarted._configuration_checksum = "checksum-7"
	restarted._ches = {QC_ID: _che(QC_ID, "QC01", QC_LAT, QC_LON)}
	restarted._load_runtime_state()
	restarted._restore_runtime_state()
	_assert(restarted._result_outbox.size() == buffered, "The outbox survives a same-session restart")
	var lost_ms = simulator._clock.now_ms - restarted._clock.now_ms
	_assert(lost_ms >= 0 and lost_ms <= HeadlessSimulator.SIM_TIME_CHECKPOINT_MS, "Sim time survives a restart within one checkpoint (%s ms lost)" % lost_ms)
	restarted._mqtt.free()
	restarted._bootstrap.free()
	restarted.free()
	fake.connected = true
	simulator._mqtt_connected = true
	simulator._flush_result_outbox()
	var events: Array = []
	var seqs: Array = []
	for item in _results(simulator):
		events.append(str(item.m["Command/Result/Event"]))
		seqs.append(int(item.m["Sim/EventSeq"]))
	_assert(events.size() == buffered and simulator._result_outbox.is_empty(), "Every pending result is replayed after reconnect")
	_assert(events.slice(0, 4) == ["acknowledged", "started", "container_picked_up", "waiting_for_handoff"], "Replay keeps publish order: %s" % [events])
	var sorted_seqs = seqs.duplicate()
	sorted_seqs.sort()
	_assert(seqs == sorted_seqs, "Replayed results keep their EventSeq order")
	_free_simulator(simulator)


func _test_device_id_mapping():
	var che = {"pk": 12, "name": "QC01"}
	_assert(SimIds.use_che_name, "Name mode (MiniTOS Che.name) is the default")
	_assert(SimIds.device_id(che) == "QC01", "Name mode uses MiniTOS Che.name")
	_assert(SimIds.device_id({"pk": 3, "name": ""}) == "CHE_3", "Name mode falls back when the name is empty")
	_assert(SimIds.device_id({"pk": 3, "name": "TT/1"}) == "TT_1", "Device ids are sanitised for Sparkplug")
	SimIds.use_che_name = false
	_assert(SimIds.device_id(che) == "CHE_12", "Legacy mode device id is CHE_{id}")
	_assert(SimIds.device_id({"pk": 3, "device_id": "RTG01"}) == "RTG01", "An explicit device_id wins")
	SimIds.use_che_name = true


func _test_mqtt_packet_id_wraps():
	var client = MQTTClient.new()
	_assert(client.next_packet_id() == 1, "First packet id is 1")
	client.pid = 65534
	_assert(client.next_packet_id() == 65535, "65535 is a valid packet id")
	_assert(client.next_packet_id() == 1, "Packet id wraps from 65535 to 1, never 0")
	client.pid = 0
	for _i in range(70000):
		var value = client.next_packet_id()
		if value < 1 or value > 65535:
			_assert(false, "Packet id out of range: %s" % value)
			break
	client.free()


# --- TCL-014b: TT -> RTG at the block ---------------------------------------------

func _rtg_command() -> Dictionary:
	return {
		"command_id": "rtg", "handoff_id": HANDOFF_ID_2, "container_visit_id": 101, "transfer_position_id": 900,
	}


func _test_rtg_state_machine():
	var slot = {"position_id": 0, "position_code": "BLOCK01-DUMMY-03"}
	# TT ready at once: commanded -> pick -> holding -> done.
	var command = _rtg_command()
	_assert(_names(RtgStack.start(command, 12000, slot)) == ["acknowledged", "started"], "RTG start publishes ack + started")
	var picked = RtgStack.step(command, 1000, TT_ID)
	_assert(_names(picked) == ["handoff_completed"] and RtgStack.picked_in(picked), "RTG picks from a ready TT at once")
	_assert(str(picked[0].handoff_role) == "receiver" and str(picked[0].work_state) == "picking_from_tt", "RTG pick is the receiver side")
	_assert(str(command.work_state) == "holding" and int(command.wait_until_ms) == 13000, "RTG holds for the seeded wait after the pick")
	_assert(RtgStack.step(command, 12999, 0).is_empty(), "No drop before the hold ends")
	var dropped = RtgStack.step(command, 13000, 0)
	_assert(_names(dropped) == ["container_dropped", "completed"], "RTG drops into the slot, then completes")
	_assert(str(dropped[0].position_code) == "BLOCK01-DUMMY-03" and RtgStack.is_finished(command), "Drop names the dummy slot")
	_assert(int(command.load_container_visit_id) == 0, "RTG is empty when done")
	# TT late: waiting_for_tt, then the pick.
	command = _rtg_command()
	RtgStack.start(command, 10000, slot)
	_assert(_names(RtgStack.step(command, 0, 0)) == ["waiting_for_handoff"], "RTG waits for a missing TT")
	_assert(str(command.work_state) == "waiting_for_tt", "RTG Work/State waiting_for_tt")
	_assert(_names(RtgStack.step(command, 50000, TT_ID)) == ["handoff_completed"], "Late TT: pick when it is ready")
	# No-show: one handoff_timed_out, the late TT gets nothing, cancel clears.
	command = _rtg_command()
	RtgStack.start(command, 10000, slot, 300000)
	RtgStack.step(command, 0, 0)
	_assert(RtgStack.step(command, 299999, 0).is_empty(), "No time-out before 300 s")
	_assert(_names(RtgStack.step(command, 300000, 0)) == ["handoff_timed_out"], "RTG times out after 300 s")
	_assert(RtgStack.step(command, 300100, TT_ID).is_empty(), "A TT after the time-out gets no transfer")
	_assert(_names(RtgStack.cancel(command)) == ["cancelled"] and str(command.work_state) == "idle", "Cancel clears the RTG")


func _new_chain_simulator(run_seed: int, tt_lat: float) -> HeadlessSimulator:
	var simulator = _new_simulator(run_seed, tt_lat)
	var rtg = _che(RTG_ID, "RTG01", BLOCK_LAT, QC_LON)
	rtg["equipment_type_code"] = "RTG"
	simulator._ches[RTG_ID] = rtg
	return simulator


func _rtg_metrics(handoff_id := HANDOFF_ID_2) -> Dictionary:
	return {
		"Command/Type": "execute_order_leg", "Command/OrderId": 41, "Command/LegId": 4103,
		"Command/ContainerVisitId": 101, "Command/ContainerNumber": "MSCU1234565",
		"Command/HandoffId": handoff_id, "Command/HandoffRole": "receiver", "Command/CounterpartCheId": TT_ID,
		"Command/TransferPositionCode": "BLOCK01-TL", "Command/SourcePositionId": 900,
		"Command/DestinationLatitude": BLOCK_LAT, "Command/DestinationLongitude": QC_LON,
		"Command/MoveKind": "discharge",
	}


## BE TCL-003a `queue_assign_handoff_dcmd` shape: base envelope (same
## AssignmentId as the TT leg) + LegId of the giver leg, visit, number,
## HandoffId, HandoffRole=giver, CounterpartCheId and TransferPositionId.
func _assign_metrics(handoff_id := HANDOFF_ID_2) -> Dictionary:
	return {
		"Command/Type": "assign_handoff", "Command/OrderId": 41, "Command/LegId": 4102,
		"Command/ContainerVisitId": 101, "Command/ContainerNumber": "MSCU1234565",
		"Command/HandoffId": handoff_id, "Command/HandoffRole": "giver",
		"Command/CounterpartCheId": RTG_ID, "Command/TransferPositionId": 900,
	}


func _tt_phase(simulator) -> String:
	return str(simulator._active_commands.get(TT_ID, {}).get("tt_phase", ""))


func _run_until(simulator, condition: Callable, max_steps: int) -> bool:
	for _i in range(max_steps):
		if condition.call():
			return true
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
	return condition.call()


## "DEVICE event Work/State" for every result, in publish order (D-016).
func _sequence(results: Array) -> Array:
	var out: Array = []
	for item in results:
		out.append("%s %s %s" % [item.device, item.m["Command/Result/Event"], item.m.get("Work/State", "-")])
	return out


## Drives the chain: QC + TT (H1), H2 to the TT once H1 is done, RTG command
## once the TT waits at BLOCK01-TL (or at once when `rtg_early`), run to the end.
func _run_chain(simulator, rtg_early: bool) -> Dictionary:
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_assert(_run_until(simulator, func(): return _tt_phase(simulator) == "driving_to_destination", 3000), "H1 completes")
	var assign = _command(TT_ID, _assign_metrics())
	_send(simulator, TT_ID, assign)
	if not rtg_early:
		_assert(_run_until(simulator, func(): return _tt_phase(simulator) == "at_destination_waiting", 3000), "TT reaches BLOCK01-TL")
	_send(simulator, RTG_ID, _command(RTG_ID, _rtg_metrics()))
	_run_until(simulator, func(): return simulator._active_commands.is_empty(), 3000)
	return assign


func _test_chain_happy_path_d016():
	var simulator = _new_chain_simulator(42, QC_LAT)
	_command_counter = 200
	var assign = _run_chain(simulator, false)
	_assert(simulator._active_commands.is_empty(), "QC, TT and RTG legs all finish")
	var results = _results(simulator)
	var sequence = _sequence(results)
	var expected = [
		"QC01 acknowledged commanded", "QC01 started commanded", "QC01 container_picked_up holding",
		"TT01 acknowledged driving_to_source", "TT01 started driving_to_source",
		"TT01 arrived_at_source at_source_waiting", "TT01 waiting_for_handoff at_source_waiting",
		"QC01 handoff_completed done", "QC01 completed done",
		"TT01 handoff_completed driving_to_destination",
		"TT01 arrived_at_destination at_destination_waiting", "TT01 waiting_for_handoff at_destination_waiting",
		"RTG01 acknowledged commanded", "RTG01 started commanded",
		"TT01 handoff_completed idle", "TT01 completed idle",
		"RTG01 handoff_completed picking_from_tt", "RTG01 container_dropped done", "RTG01 completed done",
	]
	_assert(sequence == expected, "D-016 ordered states/events:\n%s" % "\n".join(PackedStringArray(sequence)))
	var tt_give: Dictionary = {}
	for item in results:
		if item.device == "TT01" and str(item.m["Command/Result/Event"]) == "handoff_completed":
			tt_give = item.m
	var rtg_take = _find(results, "RTG01", "handoff_completed")
	var rtg_drop = _find(results, "RTG01", "container_dropped")
	_assert(str(tt_give["Command/Result/HandoffId"]) == HANDOFF_ID_2 and str(rtg_take["Command/Result/HandoffId"]) == HANDOFF_ID_2, "Both sides report H2")
	_assert(str(tt_give["Command/Result/HandoffRole"]) == "giver" and str(rtg_take["Command/Result/HandoffRole"]) == "receiver", "TT gives, RTG receives")
	_assert(int(tt_give["Command/Result/CounterpartCheId"]) == RTG_ID and int(rtg_take["Command/Result/CounterpartCheId"]) == TT_ID, "Each side names the other")
	_assert(int(tt_give["Event/TimeMs"]) == int(rtg_take["Event/TimeMs"]), "H2 is one instant on both sides")
	_assert(int(tt_give["Load/ContainerVisitId"]) == 0 and int(rtg_take["Load/ContainerVisitId"]) == 101, "Custody moves from TT to RTG")
	_assert(str(tt_give["Command/Result/CommandId"]) == str(assign["Command/Id"]), "BE: the TT giver's handoff_completed answers the assign_handoff command")
	_assert(int(tt_give["Command/Result/PositionId"]) == 900, "TT giver reports the assign TransferPositionId")
	var tt_done = _find(results, "TT01", "completed")
	_assert(str(tt_done["Command/Result/CommandId"]) == "33333333-3333-4333-8333-%012d" % 202, "TT completed closes the TT leg itself")
	for item in results:
		_assert(str(item.m["Command/Result/CommandId"]) != str(assign["Command/Id"]) or str(item.m["Command/Result/Event"]) == "handoff_completed", "Only handoff_completed is published on the assign id (no ack)")
	var hold_s = SimRandom.new(42).draw_wait_s("RTG01", "rtg:leg:4103", 0, 10, 30)
	_assert(int(rtg_drop["Sim/TimeMs"]) == int(rtg_take["Sim/TimeMs"]) + hold_s * 1000, "RTG drops at the seeded hold end (%s s)" % hold_s)
	var slot = SimRandom.new(42).draw_wait_s("RTG01", "slot:leg:4103", 0, 1, 20)
	_assert(str(rtg_drop.get("Command/Result/PositionCode", "")) == "BLOCK01-DUMMY-%02d" % slot, "D-009: dummy slot from the seed (%s)" % rtg_drop.get("Command/Result/PositionCode", ""))
	_assert(int(rtg_drop["Load/ContainerVisitId"]) == 0 and str(rtg_drop["Command/Result/ContainerNumber"]) == "MSCU1234565", "RTG drop echoes the container and is empty after")
	_assert(not _events_of(results, "RTG01").has("waiting_for_handoff"), "TT already at the lane: RTG never waits")
	for che_id in [QC_ID, TT_ID, RTG_ID]:
		var che: Dictionary = simulator._ches[che_id]
		_assert(not bool(che.working) and bool(che.is_available), "CHE %s is idle again" % che_id)
	_free_simulator(simulator)


func _test_chain_late_tt_at_rtg():
	# TT 400 m from the QC: late at the QC, then the RTG is commanded before the TT reaches the block.
	var simulator = _new_chain_simulator(42, QC_LAT - 0.0036)
	_run_chain(simulator, true)
	_assert(simulator._active_commands.is_empty(), "Late TT at RTG: all legs finish")
	var results = _results(simulator)
	var rtg_events = _events_of(results, "RTG01")
	_assert(rtg_events == [
		"acknowledged", "started", "waiting_for_handoff", "handoff_completed", "container_dropped", "completed",
	], "Late TT at RTG: RTG event order %s" % [rtg_events])
	_assert(str(_find(results, "RTG01", "waiting_for_handoff")["Work/State"]) == "waiting_for_tt", "RTG Work/State waiting_for_tt")
	var arrived = _find(results, "TT01", "arrived_at_destination")
	var take = _find(results, "RTG01", "handoff_completed")
	_assert(int(take["Sim/TimeMs"]) == int(arrived["Sim/TimeMs"]), "RTG picks in the step the late TT arrives")
	_assert(int(take["Sim/TimeMs"]) > int(_find(results, "RTG01", "acknowledged")["Sim/TimeMs"]), "RTG was commanded before the TT arrived")
	_free_simulator(simulator)


func _test_chain_rtg_cancel():
	# Cancel while waiting_for_tt: RTG idle, TT keeps the container and H2, a new RTG command completes the chain.
	var simulator = _new_chain_simulator(42, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_until(simulator, func(): return _tt_phase(simulator) == "driving_to_destination", 3000)
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics()))
	var rtg_cmd = _command(RTG_ID, _rtg_metrics())
	_send(simulator, RTG_ID, rtg_cmd)
	_run_steps(simulator, 3)
	_assert(str(simulator._active_commands[RTG_ID].work_state) == "waiting_for_tt", "RTG waits for the TT")
	_send(simulator, RTG_ID, _command(RTG_ID, _cancel(RTG_ID, str(rtg_cmd["Command/Id"]))))
	var cancelled = _last_result(simulator)
	_assert(str(cancelled["Command/Result/Event"]) == "cancelled" and str(cancelled["Work/State"]) == "idle", "RTG cancel publishes cancelled/idle")
	_assert(not simulator._active_commands.has(RTG_ID) and bool(simulator._ches[RTG_ID].is_available), "Cancel releases the RTG")
	_run_until(simulator, func(): return _tt_phase(simulator) == "at_destination_waiting", 3000)
	_run_steps(simulator, 20)
	_assert(_tt_phase(simulator) == "at_destination_waiting", "Without an RTG the TT keeps waiting at the lane")
	_assert(int(simulator._active_commands[TT_ID].load_container_visit_id) == 101, "TT still carries the container")
	_send(simulator, RTG_ID, _command(RTG_ID, _rtg_metrics()))
	_run_until(simulator, func(): return simulator._active_commands.is_empty(), 3000)
	_assert(simulator._active_commands.is_empty(), "A new RTG command with the same H2 completes the chain")
	_free_simulator(simulator)
	# Cancel while holding: the container is cleared and never dropped.
	simulator = _new_chain_simulator(42, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_until(simulator, func(): return _tt_phase(simulator) == "driving_to_destination", 3000)
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics()))
	_run_until(simulator, func(): return _tt_phase(simulator) == "at_destination_waiting", 3000)
	var holding_rtg = _command(RTG_ID, _rtg_metrics())
	_send(simulator, RTG_ID, holding_rtg)
	_run_steps(simulator, 2)
	_assert(str(simulator._active_commands[RTG_ID].work_state) == "holding", "RTG holds the container")
	_send(simulator, RTG_ID, _cancel(RTG_ID, str(holding_rtg["Command/Id"])))
	cancelled = _last_result(simulator)
	_assert(str(cancelled["Command/Result/Event"]) == "cancelled" and int(cancelled["Load/ContainerVisitId"]) == 0, "Cancel in holding clears the RTG load")
	_run_steps(simulator, 400)
	_assert(not _events_of(_results(simulator), "RTG01").has("container_dropped"), "A cancelled RTG never drops")
	_free_simulator(simulator)


func _chain_trace(run_seed: int) -> Array:
	var simulator = _new_chain_simulator(run_seed, QC_LAT - 0.0036)
	_command_counter = 300
	_run_chain(simulator, true)
	var trace: Array = []
	for item in _results(simulator):
		trace.append([
			item.device, item.m["Command/Result/Event"], item.m.get("Work/State", ""), int(item.m["Sim/TimeMs"]),
			item.m.get("Command/Result/PositionCode", ""),
		])
	_free_simulator(simulator)
	return trace


func _test_chain_determinism():
	var first = _chain_trace(2024)
	var second = _chain_trace(2024)
	_assert(not first.is_empty() and first == second, "D-016: same seed, same chain states, order and times")
	_assert(first != _chain_trace(77), "Another seed changes the chain")


func _test_chain_wrong_handoff_id():
	var simulator = _new_chain_simulator(42, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_steps(simulator, 2)
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics()))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "handoff_already_active", "H2 before H1 completes is rejected")
	_run_until(simulator, func(): return _tt_phase(simulator) == "driving_to_destination", 3000)
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics(HANDOFF_ID)))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "failed", "Re-using H1 as H2 is rejected")
	var mismatched = _command(TT_ID, _assign_metrics())
	mismatched["Command/AssignmentId"] = "55555555-5555-4555-8555-555555555555"
	_send(simulator, TT_ID, mismatched)
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Hand-off assignment does not match the active TT leg", "assign_handoff for another assignment is rejected")
	var wrong_leg = _command(TT_ID, _assign_metrics())
	wrong_leg["Command/LegId"] = 9999
	_send(simulator, TT_ID, wrong_leg)
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "Hand-off assignment LegId does not match the active TT leg", "assign_handoff for another leg is rejected")
	var no_role = _command(TT_ID, _assign_metrics())
	no_role.erase("Command/HandoffRole")
	_send(simulator, TT_ID, no_role)
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "failed", "assign_handoff requires HandoffRole=giver")
	var before = _results(simulator).size()
	var assign = _command(TT_ID, _assign_metrics())
	_send(simulator, TT_ID, assign)
	_assert(_results(simulator).size() == before, "H2 after H1 is accepted silently (no ack on the assign id)")
	_assert(str(simulator._active_commands[TT_ID].handoff_id) == HANDOFF_ID_2, "The TT leg now holds H2")
	_send(simulator, TT_ID, assign)
	_assert(_results(simulator).size() == before and str(simulator._active_commands[TT_ID].handoff_id) == HANDOFF_ID_2, "A redelivered assign_handoff is deduplicated without a reply")
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics(HANDOFF_ID_WRONG)))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "handoff_already_active", "A TT holds at most one active hand-off")
	_run_until(simulator, func(): return _tt_phase(simulator) == "at_destination_waiting", 3000)
	# RTG with a HandoffId the TT does not hold: never pairs, times out after 300 s sim.
	_send(simulator, RTG_ID, _command(RTG_ID, _rtg_metrics(HANDOFF_ID_WRONG)))
	_run_steps(simulator, 3100)
	var rtg_events = _events_of(_results(simulator), "RTG01")
	_assert(not rtg_events.has("handoff_completed"), "Wrong HandoffId: the RTG must not take the container (%s)" % [rtg_events])
	_assert(rtg_events.has("handoff_timed_out"), "Wrong HandoffId: the RTG times out (D-013 analogue)")
	_assert(_tt_phase(simulator) == "at_destination_waiting" and int(simulator._active_commands[TT_ID].load_container_visit_id) == 101, "The TT keeps the container")
	_free_simulator(simulator)


func _test_rtg_rules_and_config():
	var simulator = _new_chain_simulator(42, QC_LAT)
	var giver = _rtg_metrics()
	giver["Command/HandoffRole"] = "giver"
	_send(simulator, RTG_ID, _command(RTG_ID, giver))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "failed", "RTG must be the receiver in import")
	var no_visit = _rtg_metrics()
	no_visit.erase("Command/ContainerVisitId")
	_send(simulator, RTG_ID, _command(RTG_ID, no_visit))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "failed", "RTG hand-off leg needs a ContainerVisitId")
	simulator._ches[RTG_ID]["block_code"] = "BLOCK02"
	_send(simulator, RTG_ID, _command(RTG_ID, _rtg_metrics()))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "RTG does not serve block BLOCK01", "D-011: RTG rejects another block's lane")
	simulator._ches[RTG_ID].erase("block_code")
	_send(simulator, TT_ID, _command(TT_ID, _assign_metrics()))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "failed", "assign_handoff without an active TT leg fails")
	_free_simulator(simulator)
	simulator = _new_chain_simulator(42, QC_LAT)
	_assert(simulator.configure_rtg_wait("hold", "7", "7"), "RTG hold range is configurable")
	_assert(not simulator.configure_rtg_wait("hold", "9", "3"), "Inverted RTG range is refused")
	_run_chain(simulator, false)
	var results = _results(simulator)
	var gap = int(_find(results, "RTG01", "container_dropped")["Sim/TimeMs"]) - int(_find(results, "RTG01", "handoff_completed")["Sim/TimeMs"])
	_assert(gap == 7000, "Configured RTG hold of 7 s is used (%s ms)" % gap)
	_free_simulator(simulator)


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

extends SceneTree

## TCL-014a: seed determinism, fixed-step sim clock, QC discharge state machine
## (incl. late TT), HandoffId pairing, command type routing, result outbox and
## MQTT packet-id wrap. Runs without a broker or OES.
##
##   godot --headless --path . --script tests/unit/test_headless_sim.gd

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")
const SimClock = preload("res://source/headless/SimClock.gd")
const SimRandom = preload("res://source/headless/SimRandom.gd")
const SimIds = preload("res://source/headless/SimIds.gd")
const QcDischarge = preload("res://source/headless/QcDischarge.gd")
const MQTTClient = preload("res://addons/mqtt/mqtt.gd")

const SESSION_ID := "11111111-1111-4111-8111-111111111111"
const SITE_ID := "22222222-2222-4222-8222-222222222222"
const QC_ID := 1
const TT_ID := 2
const QC_LAT := 41.35
const QC_LON := 2.17
const HANDOFF_ID := "55555555-5555-4555-8555-555555555555"
## Sim-time deadline (Sim/TimeMs units) far beyond every test run.
const COMMAND_EXPIRES_AT_SIM_MS := 86400000

var _failed := false
var _command_counter := 0


class FakeSparkplugManager:
	extends Node
	var calls: Array = []
	var connected := true
	## QoS 1 packet ids as the real manager hands them out (PUBACK simulated by the test).
	var last_packet_id := 0
	var _next_packet_id := 0

	func is_broker_connected():
		return connected

	func publish_device_birth(device_id: String, metrics: Array = []):
		calls.append(["DBIRTH", device_id, metrics])
		return true

	func publish_device_data(device_id: String, metrics: Array):
		calls.append(["DDATA", device_id, metrics])
		return true

	func publish_device_result(device_id: String, metrics: Array):
		if not connected:
			return false
		_next_packet_id = _next_packet_id % 65535 + 1
		last_packet_id = _next_packet_id
		calls.append(["RESULT", device_id, metrics, last_packet_id])
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
	_test_results_pending_until_puback()
	_test_duplicate_dcmd_replays_same_result()
	_test_carrying_metric_name()
	_test_handoff_completed_fields()
	_test_node_birth_metrics()
	_test_failure_reason_codes()
	_test_device_id_mapping()
	_test_mqtt_packet_id_wraps()
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


## S15-1: Result/Id = uuid5(NS_TCL002, "{Session/Id}:{Sim/EventSeq}") (contract §5.2).
## Reference values from Python: uuid.uuid5(uuid.UUID(NS_TCL002), f"{session}:{seq}").
func _test_result_ids():
	var uuid = RegEx.new()
	uuid.compile("^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
	_assert(SimRandom.NS_TCL002 == SimRandom.uuid5("6ba7b811-9dad-11d1-80b4-00c04fd430c8", "urn:scms:contract:tcl-002:result-id"), "NS_TCL002 is uuid5(NAMESPACE_URL, urn:scms:contract:tcl-002:result-id)")
	var id_a = SimRandom.result_id(SESSION_ID, 3)
	_assert(uuid.search(id_a) != null, "Result ids are UUID v5 (RFC 4122 variant): %s" % id_a)
	_assert(id_a == "7d422a93-a77f-54c7-9a08-c1970096d39d", "Result/Id matches Python uuid5 for seq 3: %s" % id_a)
	_assert(SimRandom.result_id(SESSION_ID, 4) == "653ce639-35c3-5a62-bfd0-b42e91905699", "Result/Id matches Python uuid5 for seq 4")
	_assert(SimRandom.result_id("99999999-1111-4111-8111-111111111111", 3) != id_a, "A new Session/Id gives new ids")
	var simulator = _new_simulator(1, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	for item in _results(simulator):
		_assert(str(item.m["Command/Result/Id"]) == SimRandom.result_id(SESSION_ID, int(item.m["Sim/EventSeq"])), "Published Result/Id is uuid5(Session/Id:EventSeq)")
	_free_simulator(simulator)


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
	_assert(int(events[1].counterpart_che_id) == 9, "Completion names the TT")


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
	_assert(simulator._active_commands.is_empty(), "QC and TT must both finish")
	var results = _results(simulator)
	var qc_events = _events_of(results, "QC01")
	_assert(qc_events == [
		"acknowledged", "started", "container_picked_up", "waiting_for_handoff",
		"handoff_completed", "completed",
	], "Late TT: QC event order: %s" % [qc_events])
	var tt_events = _events_of(results, "TT01")
	_assert(tt_events == [
		"acknowledged", "started", "arrived_at_source", "waiting_for_handoff",
		"handoff_completed", "container_dropped", "completed",
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
	_assert(simulator._active_commands.is_empty(), "Early TT: both commands finish")
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
	_assert(str(last["Command/Result/Event"]) == "failed" and str(last["Command/Result/Reason"]) == "unsupported_command_type" and str(last["Command/Result/Detail"]) == "Unsupported command type: qc_load", "Unknown types are rejected with the contract code; text in Detail")
	_assert(not simulator._active_commands.has(QC_ID), "A rejected type creates no action")
	var no_handoff = _qc_metrics()
	no_handoff.erase("Command/HandoffId")
	_send(simulator, QC_ID, _command(QC_ID, no_handoff))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "handoff_id_missing", "qc_discharge needs a HandoffId")
	var bad_number = _qc_metrics()
	bad_number["Command/ContainerNumber"] = "MSC-1234"
	_send(simulator, QC_ID, _command(QC_ID, bad_number))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "container_number_invalid", "Malformed container numbers are rejected")
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
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "handoff_already_active" and str(_last_result(simulator)["Command/Result/Detail"]).contains("HandoffId"), "A held HandoffId is handoff_already_active (contract §3.2)")
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, 4, _command(4, _qc_metrics()))
	_assert(simulator._active_commands.has(QC_ID) and not simulator._active_commands.has(4), "A second giver for the same HandoffId is rejected")
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "handoff_already_active", "A TT holds one handoff at a time (handoff_already_active)")
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
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "cancel_target_mismatch", "A cancel for another command is rejected")
	_assert(simulator._active_commands.has(TT_ID), "A rejected cancel leaves the command running")
	var cancel = _cancel(TT_ID, str(leg["Command/Id"]))
	_send(simulator, TT_ID, cancel)
	var last = _last_result(simulator)
	_assert(str(last["Command/Result/Event"]) == "cancelled", "Cancel publishes cancelled")
	_assert(str(last["Command/Result/CommandId"]) == str(leg["Command/Id"]), "cancelled is reported for the cancelled command")
	_assert(str(last["Work/State"]) == "idle" and int(last["Carrying/ContainerVisitId"]) == 0, "Cancelled CHE is idle and empty")
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
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "cancel_target_missing", "A cancel without a target is rejected")
	_assert(simulator._active_commands.has(TT_ID), "A rejected cancel leaves the next command running")
	_send(simulator, TT_ID, _cancel(TT_ID, str(next_leg["Command/Id"])))
	_assert(str(_last_result(simulator)["Command/Result/Event"]) == "cancelled" and not simulator._active_commands.has(TT_ID), "A cancel naming the active command cancels it")
	_send(simulator, TT_ID, _cancel(TT_ID, "99999999-9999-4999-8999-999999999999"))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "no_active_command", "Cancel on an idle CHE fails cleanly")
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
	_assert(int(_last_result(simulator2)["Carrying/ContainerVisitId"]) == 0 and simulator2._active_commands.is_empty(), "Cancel clears the TT's container")
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
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "command_expired", "ExpiresAtMs at the current Sim/TimeMs is expired")
	_send(simulator, TT_ID, _leg(TT_ID, 60000))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "command_expired", "ExpiresAtMs in the sim past is expired")
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
	_assert(str(timed_out["Work/State"]) == "waiting_for_tt" and int(timed_out["Carrying/ContainerVisitId"]) == 101, "QC still holds the container until OES cancels")
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
	_assert(str(qc_cancel.get("Work/State", "")) == "idle" and int(qc_cancel.get("Carrying/ContainerVisitId", -1)) == 0, "OES cancel clears the QC")
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
	_assert(events.size() == buffered and simulator.pending_result_count() == buffered, "Every pending result is replayed after reconnect and stays pending until PUBACK")
	_ack_all(simulator)
	_assert(simulator.pending_result_count() == 0 and simulator._result_outbox.size() == buffered, "PUBACKs clear the pending set; the journal keeps acked entries")
	_assert(events.slice(0, 4) == ["acknowledged", "started", "container_picked_up", "waiting_for_handoff"], "Replay keeps publish order: %s" % [events])
	var sorted_seqs = seqs.duplicate()
	sorted_seqs.sort()
	_assert(seqs == sorted_seqs, "Replayed results keep their EventSeq order")
	_free_simulator(simulator)


## Test PUBACK: acks every in-flight journal entry.
func _ack_all(simulator):
	for item in simulator._result_outbox.duplicate():
		if not bool(item.get("acked", false)) and int(item.get("packet_id", 0)) > 0:
			simulator._on_result_acknowledged(int(item.packet_id))


## B15-2: a result written to the socket is not delivered until its PUBACK. A
## disconnect before the PUBACK resends it (same Result/Id) after reconnect, in
## Sim/EventSeq order, together with results produced while offline.
func _test_results_pending_until_puback():
	var simulator = _new_simulator(2, QC_LAT)
	var fake = simulator._mqtt._manager
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	var first = _results(simulator)
	_assert(first.size() == 3, "acknowledged, started, container_picked_up go out at once (%s)" % first.size())
	_assert(simulator.pending_result_count() == 3, "Results written to the socket stay pending until PUBACK")
	simulator._on_result_acknowledged(int(fake.calls[-2][3]))
	_assert(simulator.pending_result_count() == 2, "A PUBACK acks exactly its own entry")
	simulator._on_result_acknowledged(999)
	_assert(simulator.pending_result_count() == 2, "An unknown packet id acks nothing")
	var acked_ids := {}
	for item in simulator._result_outbox:
		if bool(item.acked):
			acked_ids[str(item.result_id)] = true
	# Connection drops before the other two PUBACKs arrive.
	fake.connected = false
	simulator._on_broker_lost()
	for item in simulator._result_outbox:
		if not bool(item.acked):
			_assert(int(item.packet_id) == 0, "Unacked entries are re-queued on disconnect")
	var before = _results(simulator).size()
	_run_steps(simulator, 400)
	_assert(_results(simulator).size() == before, "Nothing is sent while disconnected")
	var pending = simulator.pending_result_count()
	_assert(pending >= 3, "Offline results join the pending set (%s)" % pending)
	# Restart while offline: the journal and its pending state survive.
	var restarted = HeadlessSimulator.new()
	restarted._session_id = SESSION_ID
	restarted._site_id = SITE_ID
	restarted._configuration_revision = 7
	restarted._configuration_checksum = "checksum-7"
	restarted._ches = {QC_ID: _che(QC_ID, "QC01", QC_LAT, QC_LON)}
	restarted._load_runtime_state()
	restarted._restore_runtime_state()
	_assert(restarted.pending_result_count() == pending, "Pending results survive a restart")
	var all_unsent := true
	for item in restarted._result_outbox:
		if not bool(item.acked) and int(item.packet_id) != 0:
			all_unsent = false
	_assert(all_unsent, "After a restart every unacked entry is resent")
	restarted._mqtt.free()
	restarted._bootstrap.free()
	restarted.free()
	# Reconnect: everything unacked goes out again, in EventSeq order.
	fake.connected = true
	simulator._mqtt_connected = true
	simulator._flush_result_outbox()
	var resent = _results(simulator).slice(before)
	var seqs: Array = []
	var resent_ids := {}
	for item in resent:
		seqs.append(int(item.m["Sim/EventSeq"]))
		resent_ids[str(item.m["Command/Result/Id"])] = true
	var sorted_seqs = seqs.duplicate()
	sorted_seqs.sort()
	_assert(resent.size() == pending and seqs == sorted_seqs, "Every unacked result is resent in EventSeq order: %s" % [seqs])
	for result_id in acked_ids:
		_assert(not resent_ids.has(result_id), "An acked result is not resent")
	for item in first:
		var id = str(item.m["Command/Result/Id"])
		if not acked_ids.has(id):
			_assert(resent_ids.has(id), "An unacked result is resent with the same Result/Id")
	simulator._flush_result_outbox()
	_assert(_results(simulator).size() == before + pending, "In-flight entries are not sent twice on one connection")
	_ack_all(simulator)
	_assert(simulator.pending_result_count() == 0, "All PUBACKs clear the pending set")
	simulator._on_broker_lost()
	fake.connected = true
	simulator._mqtt_connected = true
	simulator._flush_result_outbox()
	_assert(_results(simulator).size() == before + pending, "Acked results are not resent after another reconnect")
	_free_simulator(simulator)


## N15-3: a DCMD redelivered after completion replays the recorded result with the same Result/Id
## and Sim/EventSeq, and it is tracked until PUBACK like any other.
func _test_duplicate_dcmd_replays_same_result():
	var simulator = _new_simulator(2, QC_LAT)
	var metrics = _command(QC_ID, _qc_metrics())
	_send(simulator, QC_ID, metrics)
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_steps(simulator, 3000)
	_assert(not simulator._active_commands.has(QC_ID), "The QC command completed before the redelivery")
	_ack_all(simulator)
	var last: Dictionary = {}
	for item in _results(simulator):
		if str(item.m["Command/Result/CommandId"]) == str(metrics["Command/Id"]):
			last = item.m
	_assert(str(last.get("Command/Result/Event", "")) == "completed", "The QC's last result is completed")
	var count = _results(simulator).size()
	var seq_before = simulator._event_seq
	_send(simulator, QC_ID, metrics)
	var replay = _last_result(simulator)
	_assert(_results(simulator).size() == count + 1, "A redelivered DCMD sends exactly one replay")
	_assert(str(replay["Command/Result/Id"]) == str(last["Command/Result/Id"]), "The replay keeps the Result/Id")
	_assert(int(replay["Sim/EventSeq"]) == int(last["Sim/EventSeq"]) and simulator._event_seq == seq_before, "The replay keeps its EventSeq and allocates none")
	_assert(simulator.pending_result_count() == 1, "The replay is pending until its PUBACK")
	_ack_all(simulator)
	_assert(simulator.pending_result_count() == 0, "The replay's PUBACK acks it")
	_free_simulator(simulator)



## S15-2: the custody key on result DDATA is Carrying/ContainerVisitId (contract §2), never Load/.
func _test_carrying_metric_name():
	var simulator = _new_simulator(2, QC_LAT)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	_send(simulator, TT_ID, _command(TT_ID, _tt_metrics()))
	_run_steps(simulator, 3000)
	var with_state := 0
	var carrying_values: Array = []
	for item in _results(simulator):
		for name in item.m.keys():
			_assert(not str(name).begins_with("Load/"), "No Load/ metric on results: %s" % name)
		if item.m.has("Work/State"):
			with_state += 1
			_assert(item.m.has("Carrying/ContainerVisitId"), "Results with Work/State carry Carrying/ContainerVisitId")
			carrying_values.append(int(item.m.get("Carrying/ContainerVisitId", -1)))
	_assert(with_state > 0 and carrying_values.has(101) and carrying_values.has(0), "Carrying/ContainerVisitId reports 101 while held and 0 when empty: %s" % [carrying_values])
	_free_simulator(simulator)


## S15-3/S15-4: both handoff_completed results carry FromCheId (QC), ToCheId (TT)
## and the TransferPositionId OES sent, not a lane derived from destination ids.
func _test_handoff_completed_fields():
	for oes_sends_lane in [true, false]:
		var simulator = _new_simulator(2, QC_LAT)
		var qc = _qc_metrics()
		var tt = _tt_metrics()
		if oes_sends_lane:
			qc["Command/TransferPositionId"] = 777
			tt["Command/TransferPositionId"] = 777
		_send(simulator, QC_ID, _command(QC_ID, qc))
		_send(simulator, TT_ID, _command(TT_ID, tt))
		_run_steps(simulator, 3000)
		var handoffs: Array = []
		for item in _results(simulator):
			if str(item.m["Command/Result/Event"]) == "handoff_completed":
				handoffs.append(item)
		_assert(handoffs.size() == 2, "Both sides report handoff_completed (%s)" % handoffs.size())
		var expected_lane = 777 if oes_sends_lane else 112
		for item in handoffs:
			var m: Dictionary = item.m
			_assert(int(m.get("Command/Result/FromCheId", 0)) == QC_ID and int(m.get("Command/Result/ToCheId", 0)) == TT_ID, "%s: FromCheId = QC, ToCheId = TT" % item.device)
			_assert(int(m.get("Command/Result/TransferPositionId", 0)) == expected_lane, "%s: TransferPositionId %s (got %s)" % [item.device, expected_lane, m.get("Command/Result/TransferPositionId")])
			_assert(int(m.get("Command/Result/PositionId", 0)) == expected_lane, "%s: PositionId is the transfer lane" % item.device)
		var other_events_clean := true
		for item in _results(simulator):
			if str(item.m["Command/Result/Event"]) != "handoff_completed" and item.m.has("Command/Result/FromCheId"):
				other_events_clean = false
		_assert(other_events_clean, "Only handoff_completed carries FromCheId/ToCheId")
		_free_simulator(simulator)


## S15-5: NBIRTH carries Contract/Version and Sim/ScenarioId; Sim/StartEpochMs and
## Event/TimeMs are Int64 epoch ms (contract §2, §2.1), not Sparkplug DateTime.
func _test_node_birth_metrics():
	var simulator = _new_simulator(2, QC_LAT)
	simulator._scenario_id = "import-discharge-ci"
	var by_name := {}
	for metric in simulator.node_birth_metrics():
		by_name[str(metric.name)] = metric
	_assert(by_name.has("Contract/Version") and str(by_name["Contract/Version"].value) == HeadlessSimulator.CONTRACT_VERSION, "NBIRTH has Contract/Version")
	_assert(str(by_name["Contract/Version"].value).begins_with("tcl-002."), "Contract/Version is tcl-002.<minor>")
	_assert(by_name.has("Sim/ScenarioId") and str(by_name["Sim/ScenarioId"].value) == "import-discharge-ci" and str(by_name["Sim/ScenarioId"].type) == "String", "NBIRTH has Sim/ScenarioId")
	_assert(str(by_name["Sim/StartEpochMs"].type) == "Int64" and int(by_name["Sim/StartEpochMs"].value) == 1764583200000, "Sim/StartEpochMs is Int64 epoch ms")
	for name in ["Session/Id", "Sim/Seed", "Sim/TimeScale", "Sim/Timing/TtNoShowTimeoutS"]:
		_assert(by_name.has(name), "NBIRTH keeps %s" % name)
	for metric in simulator.node_birth_metrics():
		_assert(str(metric.type) != "DateTime", "No DateTime metric on NBIRTH: %s" % metric.name)
	_send(simulator, QC_ID, _command(QC_ID, _qc_metrics()))
	for call in simulator._mqtt._manager.calls:
		if str(call[0]) != "RESULT":
			continue
		for metric in call[2]:
			if str(metric.name) == "Event/TimeMs":
				_assert(str(metric.type) == "Int64", "Event/TimeMs is Int64")
	_free_simulator(simulator)


## S15-6: every failed result carries a machine code in Reason (snake_case) and
## the human text in Detail; a second plain command is che_busy.
func _test_failure_reason_codes():
	var simulator = _new_simulator(1, QC_LAT)
	var plain = {"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON}
	_send(simulator, TT_ID, _command(TT_ID, plain))
	_send(simulator, TT_ID, _command(TT_ID, plain))
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "che_busy", "A second command without a hand-off is che_busy")
	var bad_session = _command(QC_ID, _qc_metrics())
	bad_session["Command/SessionId"] = "00000000-0000-4000-8000-000000000000"
	_send(simulator, QC_ID, bad_session)
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "session_mismatch", "Session mismatch code")
	var no_assignment = _command(QC_ID, _qc_metrics())
	no_assignment.erase("Command/AssignmentId")
	_send(simulator, QC_ID, no_assignment)
	_assert(str(_last_result(simulator)["Command/Result/Reason"]) == "assignment_id_missing", "Missing assignment code")
	var code = RegEx.new()
	code.compile("^[a-z]+(_[a-z]+)*$")
	for item in _results(simulator):
		if str(item.m["Command/Result/Event"]) == "failed":
			_assert(code.search(str(item.m["Command/Result/Reason"])) != null, "Reason is a code: %s" % item.m["Command/Result/Reason"])
			_assert(not str(item.m.get("Command/Result/Detail", "")).is_empty(), "Detail holds the text")
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


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

extends SceneTree
# QA acceptance tests for D-012 (QC discharge hold) and D-013 (TT no-show),
# vault '03 Decisions', confirmed 25 Oct. Written by QA for TCL-005.
#
# Needs the TCL-014a hooks (SimClock, SimRandom, qc_discharge). Verified at 014a cf21733.
# Not wired into make test-godot. B10 (cancel) lives in tests/unit/test_cancel_regression.gd.
# It does NOT compile on main. Oracle: everything is measured from published
# results (Sim/TimeMs), not from SimRandom, except where noted.
#   godot --headless --path clients/godot-open-terminal -s res://tests/requires-TCL-014a/test_acceptance_d012_d013.gd

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")
const SimClock = preload("res://source/headless/SimClock.gd")
const SimRandom = preload("res://source/headless/SimRandom.gd")
const SimIds = preload("res://source/headless/SimIds.gd")

const SESSION_ID := "11111111-1111-4111-8111-111111111111"
const SITE_ID := "22222222-2222-4222-8222-222222222222"
const QC_ID := 1
const TT_ID := 2
const QC_LAT := 41.35
const QC_LON := 2.17
const NO_SHOW_MS := 300000

var _failed := false
var _counter := 0
var _summary: Array = []


class FakeSparkplugManager:
	extends Node
	var calls: Array = []

	func is_broker_connected():
		return true

	func publish_device_birth(device_id: String, metrics: Array = []):
		calls.append(["DBIRTH", device_id, metrics])
		return true

	func publish_device_data(device_id: String, metrics: Array):
		calls.append(["DDATA", device_id, metrics])
		return true

	func publish_device_death(device_id: String):
		calls.append(["DDEATH", device_id, []])
		return true

	func publish_device_result(device_id: String, metrics: Array):
		calls.append(["RESULT", device_id, metrics])
		return true


func _initialize():
	_test_d012_appear_on_receipt_from_vessel()
	_test_d012a_hold_is_whole_seconds_in_10_30()
	_test_d012b_same_seed_same_holds_other_seed_differs()
	_test_d012c_late_tt_deposit_waits_no_drop_no_failure()
	_test_d012d_tt_waiting_deposit_at_appear_plus_hold()
	_test_d013_no_show_reports_exception_once()
	_test_d013_tt_just_before_timeout_gets_container()
	for line in _summary:
		print(line)
	if not _failed:
		print("D-012/D-013 acceptance checks passed")
	quit(1 if _failed else 0)


# --- harness (same internal hooks as tests/unit/test_headless_sim.gd) -----------

func _sim(run_seed: int, tt_lat: float):
	DirAccess.remove_absolute(ProjectSettings.globalize_path("user://simulation_runtime_state.json"))
	var simulator = HeadlessSimulator.new()
	simulator._session_id = SESSION_ID
	simulator._site_id = SITE_ID
	simulator._configuration_revision = 7
	simulator._configuration_checksum = "checksum-7"
	simulator._mqtt._manager = FakeSparkplugManager.new()
	simulator._mqtt._connected = true
	simulator._mqtt_connected = true
	simulator._random = SimRandom.new(run_seed, "qa-run")
	simulator._clock = SimClock.new(100, 1.0)
	simulator._start_epoch_ms = 1764583200000
	simulator._ches = {QC_ID: _che(QC_ID, "QC01", QC_LAT), TT_ID: _che(TT_ID, "TT01", tt_lat)}
	return simulator


func _che(id: int, name: String, lat: float) -> Dictionary:
	return {
		"pk": id, "name": name, "powered_on": true, "working": false, "is_available": true,
		"is_dispatched": false, "is_in_maintenance": false, "latitude": lat, "longitude": QC_LON,
	}


func _free(simulator):
	var fake = simulator._mqtt._manager
	simulator._mqtt.free()
	simulator._bootstrap.free()
	simulator.free()
	fake.free()
	DirAccess.remove_absolute(ProjectSettings.globalize_path("user://simulation_runtime_state.json"))


func _cmd(che_id: int, extra: Dictionary) -> Dictionary:
	_counter += 1
	var metrics = {
		"Command/Id": "33333333-3333-4333-8333-%012d" % _counter,
		"Command/CheId": che_id, "Command/SessionId": SESSION_ID, "Command/SiteId": SITE_ID,
		"Command/ConfigurationRevision": 7,
		"Command/ExpiresAtMs": int(Time.get_unix_time_from_system() * 1000.0) + 60000,
		"Command/AssignmentId": "44444444-4444-4444-8444-%012d" % _counter,
	}
	for key in extra:
		metrics[key] = extra[key]
	return metrics


func _handoff(leg: int) -> String:
	return "55555555-5555-4555-8555-%012d" % leg


func _qc(leg: int) -> Dictionary:
	return _cmd(QC_ID, {
		"Command/Type": "qc_discharge", "Command/OrderId": leg, "Command/LegId": leg,
		"Command/ContainerVisitId": 101, "Command/ContainerNumber": "MSCU1234565",
		"Command/HandoffId": _handoff(leg), "Command/CounterpartCheId": TT_ID,
		"Command/DestinationInventoryPositionId": 112, "Command/MoveKind": "discharge",
	})


func _tt(leg: int) -> Dictionary:
	return _cmd(TT_ID, {
		"Command/Type": "execute_order_leg", "Command/OrderId": leg, "Command/LegId": leg + 1,
		"Command/ContainerVisitId": 101, "Command/HandoffId": _handoff(leg),
		"Command/CounterpartCheId": QC_ID, "Command/SourcePositionId": 112,
		"Command/SourceLatitude": QC_LAT, "Command/SourceLongitude": QC_LON,
		"Command/DestinationLatitude": QC_LAT + 0.001, "Command/DestinationLongitude": QC_LON,
		"Command/DestinationInventoryPositionId": 900,
	})


func _send(simulator, che_id: int, metrics: Dictionary):
	var device = SimIds.device_id_for(che_id, simulator._ches)
	simulator._on_sparkplug_command("spBv1.0/G/DCMD/N/%s" % device, "DCMD", device, metrics)


func _steps(simulator, count: int, stop_when_idle := true):
	for _i in range(count):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
		if stop_when_idle and simulator._active_commands.is_empty():
			return


func _results(simulator) -> Array:
	var out: Array = []
	for call in simulator._mqtt._manager.calls:
		if str(call[0]) != "RESULT":
			continue
		var m := {}
		for metric in call[2]:
			m[str(metric.name)] = metric.value
		out.append({"device": str(call[1]), "m": m})
	return out


func _events(results: Array, device: String) -> Array:
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


## Runs one discharge with the TT already at the QC; returns hold in ms measured
## from results (deposit Sim/TimeMs - appear Sim/TimeMs).
func _measured_hold_ms(run_seed: int, leg: int) -> int:
	var simulator = _sim(run_seed, QC_LAT)
	_send(simulator, QC_ID, _qc(leg))
	_send(simulator, TT_ID, _tt(leg))
	_steps(simulator, 600)
	var results = _results(simulator)
	var appear = _find(results, "CHE_1", "container_picked_up")
	var dropped = _find(results, "CHE_1", "container_dropped")
	_free(simulator)
	if appear.is_empty() or dropped.is_empty():
		return -1
	return int(dropped["Sim/TimeMs"]) - int(appear["Sim/TimeMs"])


# --- D-012 ----------------------------------------------------------------------

func _test_d012_appear_on_receipt_from_vessel():
	var simulator = _sim(1, QC_LAT)
	_send(simulator, QC_ID, _qc(1))
	var results = _results(simulator)
	_assert(_events(results, "CHE_1") == ["acknowledged", "started", "container_picked_up"],
		"D-012: on receipt the container appears on the QC: %s" % [_events(results, "CHE_1")])
	var appear = _find(results, "CHE_1", "container_picked_up")
	_assert(int(appear.get("Sim/TimeMs", -1)) == 0, "D-012: appear at receipt time (no timed lift)")
	_assert(str(appear.get("Command/Result/Source", "")) == "VESSEL", "T-007: appear source VESSEL")
	_free(simulator)


func _test_d012a_hold_is_whole_seconds_in_10_30():
	var seen := {}
	var bad: Array = []
	var samples := 0
	for run_seed in range(1, 51):
		for leg in range(1, 6):
			var hold_ms = _measured_hold_ms(run_seed, leg * 1000)
			samples += 1
			if hold_ms < 0 or hold_ms % 1000 != 0 or hold_ms < 10000 or hold_ms > 30000:
				bad.append([run_seed, leg, hold_ms])
			else:
				seen[hold_ms / 1000] = true
	_assert(bad.is_empty(), "D-012a: holds outside whole seconds 10..30: %s" % [bad])
	_assert(seen.has(10) and seen.has(30), "D-012a: both bounds must occur over %d samples" % samples)
	var values = seen.keys()
	values.sort()
	_summary.append("D012a: %d samples (50 seeds x 5 containers), distinct holds %d, min %ds max %ds, out of range %d" % [samples, values.size(), values[0], values[-1], bad.size()])


func _hold_sequence(run_seed: int) -> Array:
	var holds: Array = []
	for leg in range(1, 6):
		holds.append(_measured_hold_ms(run_seed, leg * 1000))
	return holds


func _test_d012b_same_seed_same_holds_other_seed_differs():
	var pairs := 20
	var same_ok := 0
	var differ := 0
	for i in range(pairs):
		var seed_a = 1000 + i
		var seed_b = 5000 + i
		var first = _hold_sequence(seed_a)
		if first == _hold_sequence(seed_a):
			same_ok += 1
		if first != _hold_sequence(seed_b):
			differ += 1
	_assert(same_ok == pairs, "D-012b: same seed must repeat the hold sequence (%d/%d)" % [same_ok, pairs])
	# P(two independent 5-hold sequences match) = 21^-5 ~ 2.5e-7 per pair.
	_assert(differ == pairs, "D-012b: a different seed must change the sequence (%d/%d)" % [differ, pairs])
	_summary.append("D012b: same seed repeated %d/%d, different seed differed %d/%d (K=5)" % [same_ok, pairs, differ, pairs])


func _test_d012c_late_tt_deposit_waits_no_drop_no_failure():
	# TT about 1 km away: arrives roughly 250 s after receipt, after the hold and
	# before the 300 s no-show window ends.
	var simulator = _sim(42, QC_LAT - 0.009)
	_send(simulator, QC_ID, _qc(7000))
	_send(simulator, TT_ID, _tt(7000))
	var carried_until_tt := true
	var tt_arrived_ms := -1
	for _i in range(6000):
		simulator._clock.step()
		simulator._simulation_step(simulator._clock.step_seconds())
		var arrived = _find(_results(simulator), "CHE_2", "arrived_at_source")
		if tt_arrived_ms < 0 and not arrived.is_empty():
			tt_arrived_ms = int(arrived["Sim/TimeMs"])
		if tt_arrived_ms < 0:
			var qc = simulator._active_commands.get(QC_ID, {})
			if int(qc.get("load_container_visit_id", 0)) != 101:
				carried_until_tt = false
		if simulator._active_commands.is_empty():
			break
	var results = _results(simulator)
	var appear = _find(results, "CHE_1", "container_picked_up")
	var dropped = _find(results, "CHE_1", "container_dropped")
	var waiting = _find(results, "CHE_1", "waiting_for_handoff")
	_assert(tt_arrived_ms > 0 and not waiting.is_empty(), "D-012c precondition: the TT is late (QC waited)")
	_assert(carried_until_tt, "D-012c: the QC keeps the container until the TT arrives")
	_assert(not dropped.is_empty() and int(dropped["Sim/TimeMs"]) >= tt_arrived_ms, "D-012c: no drop before the TT arrives")
	_assert(int(dropped.get("Sim/TimeMs", -1)) == tt_arrived_ms, "D-012c: deposit when the TT arrives")
	var qc_events = _events(results, "CHE_1")
	_assert(not qc_events.has("failed") and not qc_events.has("handoff_timed_out"), "D-012c: no failure: %s" % [qc_events])
	_assert(_events(results, "CHE_1").count("container_dropped") == 1, "D-012c: exactly one deposit")
	_summary.append("D012c: appear %d ms, hold end %d ms, TT arrived %d ms, deposit %d ms, QC events %s" % [
		int(appear["Sim/TimeMs"]), int(waiting["Sim/TimeMs"]), tt_arrived_ms, int(dropped.get("Sim/TimeMs", -1)), qc_events])
	_free(simulator)


func _test_d012d_tt_waiting_deposit_at_appear_plus_hold():
	var exact := 0
	var runs := 10
	for run_seed in range(200, 200 + runs):
		var simulator = _sim(run_seed, QC_LAT)
		_send(simulator, TT_ID, _tt(9000))
		_steps(simulator, 20, false)  # TT reaches the QC and waits first
		_send(simulator, QC_ID, _qc(9000))
		_steps(simulator, 600)
		var results = _results(simulator)
		var appear = _find(results, "CHE_1", "container_picked_up")
		var dropped = _find(results, "CHE_1", "container_dropped")
		# The one place the oracle is SimRandom: the drawn hold for this run.
		var hold_s = SimRandom.new(run_seed).draw_wait_s("CHE_1", "leg:9000", 0, 10, 30)
		if not dropped.is_empty() and int(dropped["Sim/TimeMs"]) == int(appear["Sim/TimeMs"]) + hold_s * 1000 \
				and not _events(results, "CHE_1").has("waiting_for_handoff"):
			exact += 1
		_free(simulator)
	_assert(exact == runs, "D-012d: deposit at exactly appear+hold when the TT already waits (%d/%d)" % [exact, runs])
	_summary.append("D012d: deposit == appear+hold exactly in %d/%d runs" % [exact, runs])


# --- D-013 ----------------------------------------------------------------------

func _test_d013_no_show_reports_exception_once():
	var simulator = _sim(11, QC_LAT - 0.0036)
	_send(simulator, QC_ID, _qc(3000))  # no TT command: the TT never shows
	_steps(simulator, 4000, false)
	var results = _results(simulator)
	var appear = _find(results, "CHE_1", "container_picked_up")
	var hold_end = _find(results, "CHE_1", "waiting_for_handoff")
	var timed_out = _find(results, "CHE_1", "handoff_timed_out")
	var events = _events(results, "CHE_1")
	_assert(not timed_out.is_empty(), "D-013: a no-show is reported as an exception: %s" % [events])
	_assert(events.count("handoff_timed_out") == 1 and not events.has("failed"), "D-013: reported once, not as failed")
	var t = int(timed_out.get("Sim/TimeMs", -1))
	var from_appear = t - int(appear["Sim/TimeMs"])
	var from_hold_end = t - int(hold_end.get("Sim/TimeMs", 0))
	# "5 min after the QC is ready": the note doesn't say whether ready = appear
	# or = hold end. Accept either reading and report which one the code uses.
	_assert(from_appear == NO_SHOW_MS or from_hold_end == NO_SHOW_MS, "D-013: time-out at 300 s sim time (from appear %d ms, from hold end %d ms)" % [from_appear, from_hold_end])
	_assert(str(timed_out.get("Command/Result/HandoffId", "")) == _handoff(3000), "D-013: exception names the hand-off")
	_summary.append("D013-1: handoff_timed_out at %d ms = appear+%d ms = hold end+%d ms; QC keeps container %d; yard-block move is OES/MiniTOS (not in Godot)" % [
		t, from_appear, from_hold_end, int(timed_out.get("Load/ContainerVisitId", -1))])
	_free(simulator)


func _test_d013_tt_just_before_timeout_gets_container():
	var simulator = _sim(11, QC_LAT)
	_send(simulator, QC_ID, _qc(3100))
	_steps(simulator, 400, false)  # past the longest hold (30 s): QC is waiting
	var waiting = _find(_results(simulator), "CHE_1", "waiting_for_handoff")
	var target_ms = int(waiting.get("Sim/TimeMs", 0)) + NO_SHOW_MS - 1000
	while simulator._clock.now_ms < target_ms:
		_steps(simulator, 1, false)
	_send(simulator, TT_ID, _tt(3100))  # TT already at the QC, 299 s into the wait
	_steps(simulator, 600)
	var events = _events(_results(simulator), "CHE_1")
	_assert(not events.has("handoff_timed_out") and events.has("handoff_completed"), "D-013: a TT at 4:59 still gets the container: %s" % [events])
	_free(simulator)


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

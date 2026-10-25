extends SceneTree
# TCL-005 regression for B10 (QA survey, Godot review 2026-10-25).
#
# OES can cancel a command (POST /api/commands/{id}/event/ "cancelled"), marks the
# CHE available and dispatches the next order to it. The simulator must then free
# the CHE, or the next DCMD is rejected with "CHE already has an active command".
# The OES half (cancel must queue a DCMD) is execution/test_contract_execution_events.py.
#
# This is the Godot half. It delivers a cancel DCMD in the shape TCL-014a assumes
# (Command/Type = "cancel", Command/CancelCommandId); the shape is NOT agreed yet
# (BE review B4), so update it here when the contract is fixed.
#
# Known-bug convention (same as the Python known_bug marker):
# - the B10 checks fail today -> prints EXPECTED FAILURE and exits 0, so
#   `make test-godot` stays green on main;
# - the B10 checks pass       -> prints UNEXPECTED SUCCESS and exits 1: remove
#   KNOWN_BUG below in the change that fixes B10;
# - a precondition fails or the script errors -> exits 1 as usual.
# Set SCMS_CONTRACT_STRICT=1 to treat the known bug as a plain failure.

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")
const KNOWN_BUG := "B10: Godot keeps a cancelled command, so the CHE rejects its next command"

var _failed := false
var _known_bug_failures: Array = []


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

	func publish_device_result(device_id: String, metrics: Array):
		calls.append(["RESULT", device_id, metrics])
		return true

	func publish_device_death(device_id: String):
		calls.append(["DDEATH", device_id, []])
		return true


func _initialize():
	_test_next_command_after_cancel_is_accepted()
	quit(_exit_code())


func _exit_code() -> int:
	if _failed:
		return 1
	var strict = not OS.get_environment("SCMS_CONTRACT_STRICT").is_empty()
	if KNOWN_BUG.is_empty():
		if not _known_bug_failures.is_empty():
			return 1
		print("Cancel regression checks passed")
		return 0
	if _known_bug_failures.is_empty():
		push_error("UNEXPECTED SUCCESS: %s is fixed; remove KNOWN_BUG in test_cancel_regression.gd" % KNOWN_BUG)
		return 1
	if strict:
		return 1
	print("EXPECTED FAILURE (KNOWN BUG %s): %s" % [KNOWN_BUG, "; ".join(_known_bug_failures)])
	return 0


func _command(simulator, command_id: String, extra: Dictionary) -> Dictionary:
	var metrics = {
		"Command/Id": command_id,
		"Command/CheId": 17,
		"Command/SessionId": simulator._session_id,
		"Command/SiteId": simulator._site_id,
		"Command/ConfigurationRevision": 7,
		"Command/ExpiresAtMs": int(Time.get_unix_time_from_system() * 1000.0) + 60000,
		"Command/AssignmentId": "44444444-4444-4444-8444-%s" % command_id.right(12),
	}
	for key in extra:
		metrics[key] = extra[key]
	return metrics


func _leg() -> Dictionary:
	return {"Command/DestinationLatitude": 41.351, "Command/DestinationLongitude": 2.171}


func _test_next_command_after_cancel_is_accepted():
	var state_path = ProjectSettings.globalize_path("user://simulation_runtime_state.json")
	DirAccess.remove_absolute(state_path)
	var simulator = HeadlessSimulator.new()
	var fake = FakeSparkplugManager.new()
	simulator._session_id = "11111111-1111-4111-8111-111111111111"
	simulator._site_id = "22222222-2222-4222-8222-222222222222"
	simulator._configuration_revision = 7
	simulator._configuration_checksum = "checksum-7"
	simulator._mqtt._manager = fake
	simulator._mqtt._connected = true
	simulator._mqtt_connected = true
	simulator._ches = {17: {
		"pk": 17, "name": "SC01", "powered_on": true, "working": false,
		"is_available": true, "is_dispatched": false, "is_in_maintenance": false,
		"latitude": 41.35, "longitude": 2.17,
	}}
	var topic = "spBv1.0/GROUP/DCMD/NODE/CHE_17"

	var first_id = "33333333-3333-4333-8333-333333333333"
	simulator._on_sparkplug_command(topic, "DCMD", "CHE_17", _command(simulator, first_id, _leg()))
	_assert(simulator._active_commands.has(17), "Precondition: command A must become active on CHE 17")

	var cancel_id = "77777777-7777-4777-8777-777777777777"
	simulator._on_sparkplug_command(topic, "DCMD", "CHE_17", _command(simulator, cancel_id, {
		"Command/Type": "cancel", "Command/CancelCommandId": first_id,
	}))

	var second_id = "55555555-5555-4555-8555-555555555555"
	simulator._on_sparkplug_command(topic, "DCMD", "CHE_17", _command(simulator, second_id, _leg()))
	var outcome: Dictionary = simulator._command_outcomes.get(second_id, {})
	_known_bug_check(
		str(outcome.get("event", "")) != "failed",
		"next DCMD after a cancel is rejected: event=%s reason=%s" % [outcome.get("event", ""), outcome.get("reason", "")]
	)
	_known_bug_check(
		simulator._active_commands.has(17) and str(simulator._active_commands[17].command_id) == second_id,
		"CHE 17 still holds cancelled command %s" % first_id
	)

	DirAccess.remove_absolute(state_path)
	simulator._mqtt.free()
	simulator._bootstrap.free()
	simulator.free()
	fake.free()


func _known_bug_check(condition: bool, message: String):
	if not condition:
		_known_bug_failures.append(message)


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

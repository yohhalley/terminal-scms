extends SceneTree
# TCL-005 regression for B10 (QA survey, Godot review 2026-10-25).
#
# OES can cancel a command (POST /api/commands/{id}/event/ "cancelled"), marks the
# CHE available and dispatches the next order to it. Nothing tells the headless
# simulator, so its _active_commands entry for that CHE stays and the next DCMD is
# rejected with "CHE already has an active command".
#
# This script is a KNOWN-BUG test: it fails (exit 1) until a cancel/abort DCMD
# exists. The DCMD shape is not decided (BE review B4, Command/Type is TBD), so the
# test uses the simulator's only path today: OES cancels, nothing reaches Godot,
# then the next order's DCMD arrives. It is not wired into `make test-godot` so the
# existing gate stays green; run it with:
#   godot --headless --path clients/godot-open-terminal -s res://tests/unit/test_cancel_regression.gd

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")

var _failed := false


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


func _initialize():
	_test_next_command_after_oes_cancel_is_accepted()
	if not _failed:
		print("Cancel regression checks passed")
	quit(1 if _failed else 0)


func _command(simulator, command_id: String, assignment_id: String) -> Dictionary:
	return {
		"Command/Id": command_id,
		"Command/CheId": 17,
		"Command/SessionId": simulator._session_id,
		"Command/SiteId": simulator._site_id,
		"Command/ConfigurationRevision": 7,
		"Command/ExpiresAtMs": int(Time.get_unix_time_from_system() * 1000.0) + 60000,
		"Command/AssignmentId": assignment_id,
		"Command/DestinationLatitude": 41.351,
		"Command/DestinationLongitude": 2.171,
	}


func _test_next_command_after_oes_cancel_is_accepted():
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
	simulator._on_sparkplug_command(topic, "DCMD", "CHE_17", _command(simulator, first_id, "44444444-4444-4444-8444-444444444444"))
	_assert(simulator._active_commands.has(17), "Precondition: command A must become active on CHE 17")

	# OES cancels command A here. Today OES queues nothing for the CHE, so the
	# simulator receives nothing (B4/B10). When an abort DCMD is agreed, deliver it
	# here via simulator._on_sparkplug_command(...).

	var second_id = "55555555-5555-4555-8555-555555555555"
	simulator._on_sparkplug_command(topic, "DCMD", "CHE_17", _command(simulator, second_id, "66666666-6666-4666-8666-666666666666"))
	var outcome: Dictionary = simulator._command_outcomes.get(second_id, {})
	_assert(
		str(outcome.get("event", "")) != "failed",
		"KNOWN BUG B10: after an OES cancel the next DCMD to CHE 17 is rejected: event=%s reason=%s" % [outcome.get("event", ""), outcome.get("reason", "")]
	)
	_assert(
		simulator._active_commands.has(17) and str(simulator._active_commands[17].command_id) == second_id,
		"KNOWN BUG B10: CHE 17 still holds cancelled command %s" % first_id
	)

	DirAccess.remove_absolute(state_path)
	simulator._mqtt.free()
	simulator._bootstrap.free()
	simulator.free()
	fake.free()


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

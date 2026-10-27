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
# B10 is fixed by TCL-014a (#15, HeadlessSimulator._handle_cancel). The checks
# below are plain assertions: any B10 regression prints "B10 regression: ..." and
# exits 1.
#
# The DCMDs are addressed by the CHE name (SC01): #15 matches the topic's device
# id against SimIds.device_id() (name mode, the default). The old CHE_17 form was
# rejected with unknown_command_target before the cancel was ever exercised.

const HeadlessSimulator = preload("res://source/headless/HeadlessSimulator.gd")
const SimIds = preload("res://source/headless/SimIds.gd")
const CHE_ID := 17
const CHE_NAME := "SC01"

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
	print("Cancel regression checks passed")
	return 0


func _command(simulator, command_id: String, extra: Dictionary) -> Dictionary:
	var metrics = {
		"Command/Id": command_id,
		"Command/CheId": CHE_ID,
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
	simulator._ches = {CHE_ID: {
		"pk": CHE_ID, "name": CHE_NAME, "powered_on": true, "working": false,
		"is_available": true, "is_dispatched": false, "is_in_maintenance": false,
		"latitude": 41.35, "longitude": 2.17,
	}}
	# Name-based device id (#15 default), set explicitly so the test doesn't
	# depend on SIMULATOR_DEVICE_ID_MODE in the environment.
	var previous_name_mode = SimIds.use_che_name
	SimIds.use_che_name = true
	var device_id = SimIds.device_id_for(CHE_ID, simulator._ches)
	_assert(device_id == CHE_NAME, "Precondition: device id is the CHE name %s, got %s" % [CHE_NAME, device_id])
	var topic = "spBv1.0/GROUP/DCMD/NODE/%s" % device_id

	var first_id = "33333333-3333-4333-8333-333333333333"
	simulator._on_sparkplug_command(topic, "DCMD", device_id, _command(simulator, first_id, _leg()))
	_assert(simulator._active_commands.has(CHE_ID), "Precondition: command A must become active on %s" % CHE_NAME)

	var cancel_id = "77777777-7777-4777-8777-777777777777"
	simulator._on_sparkplug_command(topic, "DCMD", device_id, _command(simulator, cancel_id, {
		"Command/Type": "cancel", "Command/CancelCommandId": first_id,
	}))

	var cancelled: Dictionary = simulator._command_outcomes.get(first_id, {})
	_b10_check(
		str(cancelled.get("event", "")) == "cancelled" and not (
			simulator._active_commands.has(CHE_ID)
			and str(simulator._active_commands[CHE_ID].command_id) == first_id
		),
		"cancelled command %s was not dropped: event=%s" % [first_id, cancelled.get("event", "")]
	)

	var second_id = "55555555-5555-4555-8555-555555555555"
	simulator._on_sparkplug_command(topic, "DCMD", device_id, _command(simulator, second_id, _leg()))
	var outcome: Dictionary = simulator._command_outcomes.get(second_id, {})
	_b10_check(
		str(outcome.get("event", "")) != "failed",
		"next DCMD after a cancel is rejected: event=%s reason=%s" % [outcome.get("event", ""), outcome.get("reason", "")]
	)
	_b10_check(
		simulator._active_commands.has(CHE_ID) and str(simulator._active_commands[CHE_ID].command_id) == second_id,
		"%s still holds cancelled command %s" % [CHE_NAME, first_id]
	)

	SimIds.use_che_name = previous_name_mode
	DirAccess.remove_absolute(state_path)
	simulator._mqtt.free()
	simulator._bootstrap.free()
	simulator.free()
	fake.free()


func _b10_check(condition: bool, message: String):
	_assert(condition, "B10 regression: %s" % message)


func _assert(condition: bool, message: String):
	if condition:
		return
	push_error(message)
	_failed = true

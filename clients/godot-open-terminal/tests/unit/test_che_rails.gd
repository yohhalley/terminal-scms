extends SceneTree

const TerminalChe = preload("res://source/terminal-sandbox/che/TerminalChe.gd")
const RailPath = preload("res://source/terminal-sandbox/che/RailPath.gd")
const TerminalWorld = preload("res://source/terminal-sandbox/TerminalWorld3D.gd")
var _failed := false


func _initialize():
	_test_fixed_and_reverse()
	_test_sts_water_side_heading()
	_test_curved_rail()
	_test_rtg_exit_and_entry()
	_test_reposition_disabled_and_missing_rail()
	_test_geographic_map_rotation()
	_test_scope_endpoints_and_cancellation()
	if not _failed:
		print("CHE rail checks passed")
	quit(1 if _failed else 0)


func _rail(id: int, x: float = 0.0) -> Dictionary:
	return {"axis_id": id, "asset_id": id * 10, "points": [Vector3(x, 0, 0), Vector3(x, 0, -30)], "can_exit": true, "entry_tolerance_m": 1.0}


func _unit(mode: String, rails: Array, active: int, pose: Vector3):
	var unit = TerminalChe.new()
	root.add_child(unit)
	unit.position = pose
	unit.units_per_meter = 1.0
	unit.configure_rails({"mode": mode, "assigned_axis_id": active, "rails": rails})
	return unit


func _run(unit, target: Vector3, rail_x: float = INF):
	_assert(unit.move_to(target), "Move must be accepted")
	for frame in range(3000):
		if not unit.is_moving():
			break
		unit._physics_process(0.05)
		if rail_x != INF:
			_assert(absf(unit.position.x - rail_x) < 0.001, "Fixed CHE left its rail")
	_assert(not unit.is_moving(), "Rail action must finish at destination")
	_assert(unit.position.distance_to(unit.target_position()) <= 0.5, "Arrival exceeded 50cm tolerance")


func _test_fixed_and_reverse():
	var unit = _unit("fixed", [_rail(1)], 1, Vector3(4, 0, -5))
	_assert(absf(unit.position.x) < 0.001, "Initial placement must project onto rail")
	var heading: float = unit.rotation.y
	_run(unit, Vector3(12, 0, -20), 0)
	_assert(unit.target_position().is_equal_approx(Vector3(0, 0, -20)), "Fixed target must project to rail")
	_run(unit, Vector3(-12, 0, -2), 0)
	_assert(absf(unit.rotation.y - heading) < 0.001, "Reverse gantry travel must not rotate crane by 180 degrees")
	_run(unit, Vector3(0, 0, -80), 0)
	_assert(unit.position.is_equal_approx(Vector3(0, 0, -30)), "Target must clamp at rail end")
	unit.free()


func _test_sts_water_side_heading():
	var rail = _rail(1)
	rail.heading_offset_deg = -90.0
	var unit = _unit("fixed", [rail], 1, Vector3.ZERO)
	_assert((unit.transform.basis * Vector3.FORWARD).dot(Vector3.LEFT) > 0.99, "Left-water STS must face left and stay perpendicular to its rail")
	_run(unit, Vector3(0, 0, -20), 0)
	_assert((unit.transform.basis * Vector3.FORWARD).dot(Vector3.LEFT) > 0.99, "STS heading changed while gantrying")
	unit.free()
	rail.heading_offset_deg = 90.0
	unit = _unit("fixed", [rail], 1, Vector3.ZERO)
	_assert((unit.transform.basis * Vector3.FORWARD).dot(Vector3.RIGHT) > 0.99, "Right-water STS must face right and stay perpendicular to its rail")
	unit.free()


func _test_curved_rail():
	var rail = _rail(1)
	rail.points = [Vector3.ZERO, Vector3(0, 0, -10), Vector3(10, 0, -10)]
	var unit = _unit("fixed", [rail], 1, Vector3.ZERO)
	_assert(unit.move_to(Vector3(10, 0, -10)), "Curved rail move rejected")
	for frame in range(3000):
		if not unit.is_moving():
			break
		unit._physics_process(0.05)
		_assert(float(RailPath.project(rail.points, unit.position).distance) < 0.001, "Travel cut across a rail corner")
	_assert(not unit.is_moving(), "Curved rail action did not finish")
	_assert((unit.transform.basis * Vector3.FORWARD).dot(Vector3.RIGHT) > 0.99, "Heading must follow local rail tangent")
	unit.free()


func _test_rtg_exit_and_entry():
	var rails: Array = [_rail(1), _rail(2, 20)]
	var unit = _unit("repositionable", rails, 1, Vector3(0, 0, -15))
	_assert(unit.move_to(Vector3(20, 0, -15)), "RTG reposition rejected")
	var exited := false
	var entered := false
	var previous: Vector3 = unit.position
	var previous_mode := "rail"
	for frame in range(5000):
		if not unit.is_moving():
			break
		unit._physics_process(0.05)
		var state: Dictionary = unit.rail_state()
		if state.mode == "free" and previous_mode == "rail":
			_assert(minf(previous.distance_to(rails[0].points[0]), previous.distance_to(rails[0].points[-1])) < 0.01, "RTG exited through rail side instead of endpoint")
			exited = true
		if state.axis_id != null and int(state.axis_id) == 2:
			if not entered:
				_assert(minf(previous.distance_to(rails[1].points[0]), previous.distance_to(rails[1].points[-1])) <= 0.5, "RTG entered through rail side instead of endpoint")
			entered = true
			_assert(absf(unit.position.x - 20) < 0.001, "RTG left destination rail")
		previous = unit.position
		previous_mode = state.mode
	_assert(exited and entered and not unit.is_moving(), "RTG must complete rail/free/rail transition")
	_assert(unit.rail_state().asset_id == 20, "RTG assignment must follow destination asset")
	unit.free()


func _test_reposition_disabled_and_missing_rail():
	var rail = _rail(1)
	rail.can_exit = false
	var unit = _unit("repositionable", [rail], 1, Vector3(0, 0, -5))
	_run(unit, Vector3(10, 0, -20), 0)
	unit.free()
	unit = _unit("fixed", [], 0, Vector3.ZERO)
	_assert(not unit.move_to(Vector3(10, 0, 0)), "Fixed crane without a rail must not drive freely")
	unit.free()


func _test_geographic_map_rotation():
	var world = TerminalWorld.new()
	world.map_origin_latitude = 40.0
	world.map_origin_longitude = -3.0
	world.map_angle_deg = 71.4
	world.map_units_per_meter = 2.0
	var data := {"pk": 9, "current_asset": 10, "latitude": 40.0002, "longitude": -2.9999, "rotation": 90.0,
		"rail_motion": {"mode": "fixed", "assigned_axis_id": 1, "rails": [{"axis_id": 1, "asset_id": 10,
			"points": [[-3.0, 40.0], [-3.0, 40.001]], "geometry": {}, "can_exit": false}]}}
	var unit = TerminalChe.new()
	root.add_child(unit)
	unit.units_per_meter = 2.0
	unit.position = world._geographic_to_world(40.0002, -2.9999)
	unit.set_meta("terminal_data", data)
	unit.set_meta("terminal_movement", unit)
	world._configure_che_rails(unit, data)
	var pose: Dictionary = world._refresh_che_pose_metadata(unit)
	_assert(absf(float(pose.longitude) + 3.0) < 0.000001, "Initial WGS84 pose must align to rail despite rotated map")
	_assert(minf(float(pose.rotation), 360.0 - float(pose.rotation)) < 0.1, "World yaw must translate back to true north")
	var target: Vector3 = world._geographic_to_world(40.0006, -3.0)
	_run(unit, target)
	pose = world._refresh_che_pose_metadata(unit)
	_assert(absf(float(pose.latitude) - 40.0006) < 0.000001, "Rail movement must arrive at requested latitude")
	_assert(absf(float(pose.longitude) + 3.0) < 0.000001, "Rail movement must preserve longitude")
	_assert(int(pose.current_asset) == 10 and int(pose.rail_axis_id) == 1, "Rail assignment metadata must stay synchronized")
	unit.free()
	world.free()


func _test_scope_endpoints_and_cancellation():
	var rail = _rail(1)
	rail.entry_at_start = false
	rail.entry_at_end = false
	var unit = _unit("repositionable", [rail], 1, Vector3(0, 0, -5))
	_run(unit, Vector3(10, 0, -20), 0)
	_assert(unit.rail_state().mode == "rail", "Scope boundaries must not be usable as physical rail exits")
	unit.free()
	rail = _rail(1)
	rail.speed_limit_kph = 3.6
	unit = _unit("fixed", [rail], 1, Vector3.ZERO)
	_assert(unit.move_to(Vector3(0, 0, -30)), "Speed-limited rail move rejected")
	for frame in range(100):
		unit._physics_process(0.05)
		_assert(float(unit.debug_motion_state().current_speed_mps) <= 1.001, "Rail speed limit exceeded")
	var position: Vector3 = unit.position
	var cancelled := [false]
	unit.movement_cancelled.connect(func(): cancelled[0] = true)
	unit.cancel()
	for frame in range(10):
		unit._physics_process(0.05)
	_assert(cancelled[0] and not unit.is_moving() and unit.position.is_equal_approx(position), "Cancellation must halt motion and emit its lifecycle event")
	unit.free()


func _assert(condition: bool, message: String):
	if not condition:
		push_error(message)
		_failed = true

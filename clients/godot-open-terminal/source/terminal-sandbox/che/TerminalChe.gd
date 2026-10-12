extends Area3D

signal pose_changed
signal movement_finished
signal movement_cancelled
signal action_changed(new_action)

const DriveToAction = preload("res://source/terminal-sandbox/che/actions/DriveTo.gd")
const TravelRouteAction = preload("res://source/terminal-sandbox/che/actions/TravelRoute.gd")
const RailPath = preload("res://source/terminal-sandbox/che/RailPath.gd")

const ARRIVAL_TOLERANCE_M := 0.5
const MIN_SPEED_MPS := 0.5
const MIN_ACCELERATION_MPS2 := 0.25
const POSE_UPDATE_INTERVAL_S := 0.1

@export var equipment_code := "CHE"
@export var can_drive := true
@export var can_gantry := false

var action: Node = null
var units_per_meter := 1.0
var speed_mps := 4.0
var acceleration_mps2 := 1.2
var turning_radius_m := 0.0
var lifting_speed_empty_mps := 0.0
var lifting_speed_full_load_mps := 0.0
var loaded := false
var rail_motion_mode := "free"
var _rails: Array = []
var _active_rail_id := 0

var _target_position := Vector3.ZERO
var _pose_update_elapsed := 0.0
var _last_requested_travel_world := 0.0
var _last_travel_world := 0.0
var _last_translation := Vector3.ZERO
var _current_speed_mps := 0.0
var _action_locked := false
var _tearing_down_action := false

@onready var _geometry: Node3D = get_node_or_null("Geometry") as Node3D
@onready var _visual: MeshInstance3D = get_node_or_null("Geometry/Body") as MeshInstance3D
@onready var _model_root: Node3D = get_node_or_null("Geometry/Model") as Node3D
@onready var _label: Label3D = get_node_or_null("Label3D") as Label3D
@onready var _collision_shape: CollisionShape3D = get_node_or_null("CollisionShape3D") as CollisionShape3D


func configure_terminal_che(data: Dictionary, dimensions: Vector3, movement_profile: Dictionary, map_units_per_meter: float, visual_material: Material):
	_ensure_node_refs()
	name = "%s%s" % [equipment_code, str(data.get("name", data.get("pk", "Unit")))]
	units_per_meter = max(map_units_per_meter, 0.01)
	speed_mps = max(float(movement_profile.get("speed_mps", speed_mps)), MIN_SPEED_MPS)
	acceleration_mps2 = max(float(movement_profile.get("acceleration_mps2", acceleration_mps2)), MIN_ACCELERATION_MPS2)
	turning_radius_m = max(float(movement_profile.get("turning_radius_m", 0.0)), 0.0)
	lifting_speed_empty_mps = float(movement_profile.get("lifting_speed_empty_mps", 0.0))
	lifting_speed_full_load_mps = float(movement_profile.get("lifting_speed_full_load_mps", 0.0))
	loaded = bool(movement_profile.get("loaded", false))
	_apply_dimensions(dimensions)
	if _visual != null:
		_visual.material_override = visual_material
	if _label != null:
		_label.text = str(data.get("name", equipment_code))
		_label.position = Vector3.UP * (dimensions.y / 2.0 + 1.0 * units_per_meter)


func configure_rails(motion: Dictionary):
	rail_motion_mode = str(motion.get("mode", "free"))
	_rails = motion.get("rails", [])
	_active_rail_id = int(motion.get("assigned_axis_id", 0)) if motion.get("assigned_axis_id") != null else 0
	for rail in _rails:
		if int(rail.axis_id) == _active_rail_id:
			var projection := RailPath.project(rail.points, position)
			var containing := RailPath.rail_at([rail], position, units_per_meter)
			if rail_motion_mode == "fixed" or not containing.is_empty():
				position = _with_unit_y(projection.point)
				_align_to_rail(projection.heading)
			else:
				_active_rail_id = 0
			break


func set_active_rail(axis_id: int):
	if axis_id != _active_rail_id and axis_id != 0:
		for rail in _rails:
			if int(rail.axis_id) == axis_id:
				var projection := RailPath.project(rail.points, position)
				position = _with_unit_y(projection.point)
				break
	_active_rail_id = axis_id


func rail_state() -> Dictionary:
	for rail in _rails:
		if int(rail.axis_id) == _active_rail_id:
			return {"axis_id": _active_rail_id, "asset_id": rail.asset_id, "mode": "rail"}
	return {"axis_id": null, "asset_id": null, "mode": "free" if rail_motion_mode != "fixed" else "unassigned_rail"}


func move_to(target_position: Vector3) -> bool:
	if rail_motion_mode != "free":
		var route := RailPath.plan(_rails, rail_motion_mode, _active_rail_id, position, _with_unit_y(target_position), units_per_meter)
		if not bool(route.accepted):
			push_warning("%s requires an assigned logical crane rail." % equipment_code)
			return false
		_target_position = _with_unit_y(route.target)
		_set_action(TravelRouteAction.new(route.steps))
		return true
	if not can_drive:
		push_warning("%s cannot drive to arbitrary map locations." % equipment_code)
		return false
	_target_position = _with_unit_y(target_position)
	_set_action(DriveToAction.new(_target_position))
	return true


func cancel():
	if not is_moving():
		return
	_set_action(null)
	_reset_motion_debug()
	pose_changed.emit()
	movement_cancelled.emit()


func stop():
	_set_action(null)
	_reset_motion_debug()


func is_moving() -> bool:
	return action != null


func target_position() -> Vector3:
	return _target_position


func debug_motion_state() -> Dictionary:
	return {
		"speed_mps": speed_mps,
		"acceleration_mps2": acceleration_mps2,
		"current_speed_mps": _current_speed_mps,
		"last_requested_travel_world": _last_requested_travel_world,
		"last_requested_travel_m": _last_requested_travel_world / units_per_meter,
		"last_travel_world": _last_travel_world,
		"last_travel_m": _last_travel_world / units_per_meter,
		"last_translation": _last_translation,
	}


func estimated_lift_duration(height_m: float) -> float:
	var lift_speed = lifting_speed_full_load_mps if loaded else lifting_speed_empty_mps
	return height_m / lift_speed if lift_speed > 0.0 else INF


func _set_action(action_node: Node):
	if _action_locked:
		if action_node != null:
			action_node.queue_free()
		return
	_action_locked = true
	_teardown_current_action()
	action = action_node
	if action != null:
		action.tree_exited.connect(_on_action_node_tree_exited.bind(action))
		if action.has_method("configure"):
			action.call("configure", self)
		add_child(action)
		set_physics_process(true)
	else:
		set_physics_process(false)
	_action_locked = false
	action_changed.emit(action)


func _teardown_current_action():
	if action != null and is_instance_valid(action):
		_tearing_down_action = true
		if action.is_inside_tree():
			action.queue_free()
			remove_child(action)
		else:
			action.queue_free()
		_tearing_down_action = false
	action = null


func _on_action_node_tree_exited(action_node: Node):
	if action_node != action:
		return
	action = null
	set_physics_process(false)
	_reset_motion_debug()
	action_changed.emit(action)
	if not _tearing_down_action:
		pose_changed.emit()


func _finish_action(action_node: Node):
	if action_node != action:
		return
	if action_node.is_inside_tree():
		remove_child(action_node)
	action_node.queue_free()
	action = null
	set_physics_process(false)
	_reset_motion_debug()
	action_changed.emit(action)
	pose_changed.emit()
	movement_finished.emit()


func _physics_process(delta: float):
	if action == null or not is_instance_valid(action):
		return
	if action.has_method("tick"):
		action.call("tick", delta)
	_pose_update_elapsed += delta
	if _pose_update_elapsed >= POSE_UPDATE_INTERVAL_S:
		_pose_update_elapsed = 0.0
		pose_changed.emit()


func _drive_towards(target_position: Vector3, delta: float) -> bool:
	var target = _with_unit_y(target_position)
	var current = position
	var flat_current = Vector3(current.x, target.y, current.z)
	var displacement = (target - flat_current) * Vector3(1, 0, 1)
	var distance = displacement.length()
	var arrival_distance = max(ARRIVAL_TOLERANCE_M * units_per_meter, 0.02)
	if distance <= arrival_distance:
		position = Vector3(target.x, current.y, target.z)
		_reset_motion_debug()
		pose_changed.emit()
		return true
	var desired_speed = _desired_speed(distance, arrival_distance)
	_current_speed_mps = move_toward(_current_speed_mps, desired_speed, acceleration_mps2 * delta)
	var travel_world = min(distance, _current_speed_mps * units_per_meter * delta)
	_last_requested_travel_world = travel_world
	var direction = displacement.normalized()
	var next_position = flat_current + direction * travel_world
	next_position.y = current.y
	var previous_position = position
	position = next_position
	_rotate_towards(direction)
	_last_travel_world = previous_position.distance_to(position)
	_last_translation = position - previous_position
	return Vector2(position.x, position.z).distance_to(Vector2(target.x, target.z)) <= arrival_distance


func _desired_speed(distance_world: float, arrival_distance_world: float) -> float:
	var remaining_travel_m = max(0.0, (distance_world - arrival_distance_world) / units_per_meter)
	var stopping_speed = sqrt(max(0.0, 2.0 * acceleration_mps2 * remaining_travel_m))
	return min(speed_mps, stopping_speed)


func _drive_rail_towards(target: Vector3, heading: Vector3, delta: float) -> bool:
	var free_speed := speed_mps
	for rail in _rails:
		if int(rail.axis_id) == _active_rail_id and rail.get("speed_limit_kph") != null and float(rail.speed_limit_kph) > 0.0:
			speed_mps = minf(speed_mps, float(rail.speed_limit_kph) / 3.6)
			break
	_current_speed_mps = minf(_current_speed_mps, speed_mps)
	var arrived := _drive_towards(target, delta)
	speed_mps = free_speed
	if arrived:
		position = _with_unit_y(target)
	_align_to_rail(heading)
	return arrived


func _align_to_rail(heading: Vector3):
	if heading.length_squared() > 0.000001:
		var offset_deg := 0.0
		for rail in _rails:
			if int(rail.axis_id) == _active_rail_id:
				offset_deg = float(rail.get("heading_offset_deg", 0.0))
				break
		rotation.y = atan2(-heading.x, -heading.z) - deg_to_rad(offset_deg)


func _rotate_towards(direction: Vector3):
	if direction.length_squared() <= 0.000001:
		return
	var look_target = position + Vector3(direction.x, 0.0, direction.z)
	if look_target.is_equal_approx(position):
		return
	transform = transform.looking_at(look_target, Vector3.UP)


func _with_unit_y(target_position: Vector3) -> Vector3:
	target_position.y = position.y
	return target_position


func _apply_dimensions(dimensions: Vector3):
	_ensure_node_refs()
	if _visual != null:
		var mesh = _visual.mesh as BoxMesh
		if mesh != null:
			mesh.size = Vector3(dimensions.x, 0.12 * units_per_meter, dimensions.z)
		_visual.position = Vector3(0.0, -dimensions.y / 2.0 + 0.06 * units_per_meter, 0.0)
	if _collision_shape != null:
		var shape = _collision_shape.shape as BoxShape3D
		if shape != null:
			shape.size = dimensions
	if _model_root != null:
		_scale_model_to_dimensions(_model_root, dimensions)


func _ensure_node_refs():
	if _geometry == null:
		_geometry = get_node_or_null("Geometry") as Node3D
	if _visual == null:
		_visual = get_node_or_null("Geometry/Body") as MeshInstance3D
	if _model_root == null:
		_model_root = get_node_or_null("Geometry/Model") as Node3D
	if _label == null:
		_label = get_node_or_null("Label3D") as Label3D
	if _collision_shape == null:
		_collision_shape = get_node_or_null("CollisionShape3D") as CollisionShape3D


func primary_visual() -> GeometryInstance3D:
	var model_mesh = _first_mesh_instance(_model_root)
	if model_mesh != null:
		return model_mesh
	return _visual


func _scale_model_to_dimensions(model_root: Node3D, dimensions: Vector3):
	model_root.scale = Vector3.ONE
	var bounds_result = _visual_bounds(model_root, Transform3D.IDENTITY)
	if not bounds_result[0]:
		return
	var bounds: AABB = bounds_result[1]
	var safe_size = Vector3(max(bounds.size.x, 0.001), max(bounds.size.y, 0.001), max(bounds.size.z, 0.001))
	var target_size = dimensions * 0.92
	var model_scale = min(target_size.x / safe_size.x, target_size.y / safe_size.y, target_size.z / safe_size.z)
	model_root.scale = Vector3.ONE * model_scale
	var center = bounds.get_center()
	model_root.position = Vector3(-center.x * model_scale, -dimensions.y / 2.0 - bounds.position.y * model_scale, -center.z * model_scale)


func _first_mesh_instance(node: Node) -> MeshInstance3D:
	if node == null:
		return null
	if node is MeshInstance3D:
		return node
	for child in node.get_children():
		var found = _first_mesh_instance(child)
		if found != null:
			return found
	return null


func _visual_bounds(node: Node3D, relative_transform: Transform3D) -> Array:
	var found = false
	var bounds = AABB()
	if node is MeshInstance3D and node.mesh != null:
		bounds = relative_transform * node.get_aabb()
		found = true
	for child in node.get_children():
		if not child is Node3D:
			continue
		var child_result = _visual_bounds(child, relative_transform * child.transform)
		if not child_result[0]:
			continue
		if found:
			bounds = bounds.merge(child_result[1])
		else:
			bounds = child_result[1]
			found = true
	return [found, bounds]


func _reset_motion_debug():
	_current_speed_mps = 0.0
	_last_requested_travel_world = 0.0
	_last_travel_world = 0.0
	_last_translation = Vector3.ZERO

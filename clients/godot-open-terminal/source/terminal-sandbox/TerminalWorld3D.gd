extends SubViewportContainer

signal selection_changed(kind: String, data: Dictionary)
signal che_selection_changed(ches: Array)
signal che_move_requested(ches: Array)
signal che_poses_changed(ches: Array)
signal che_move_completed(ches: Array)
signal che_move_cancelled(ches: Array)

const MAP_Y := 0.0
const ROAD_Y := 0.42
const CHE_Y := 0.85
const EARTH_RADIUS_M := 6378137.0
const TEXTURE_TILE_M := 24.0
const BUILDING_HEIGHT_M := 5.0
const PARKING_FENCE_HEIGHT_M := 2.0
const LAND_TEXTURE_PATH := "res://source/terminal-sandbox/assets/terrain/terminal_land.png"
const CONCRETE_TEXTURE_PATH := "res://source/terminal-sandbox/assets/terrain/terminal_concrete.png"
const ASPHALT_TEXTURE_PATH := "res://source/terminal-sandbox/assets/terrain/terminal_asphalt.png"
const WATER_TEXTURE_PATH := "res://source/terminal-sandbox/assets/terrain/terminal_water.png"
const CHE_SCENE_PATHS := {
	"TT": "res://source/terminal-sandbox/che/TT.tscn",
	"SC": "res://source/terminal-sandbox/che/SC.tscn",
	"QC": "res://source/terminal-sandbox/che/QC.tscn",
	"STS": "res://source/terminal-sandbox/che/QC.tscn",
	"RTG": "res://source/terminal-sandbox/che/RTG.tscn",
	"RMG": "res://source/terminal-sandbox/che/RTG.tscn",
	"ARMG": "res://source/terminal-sandbox/che/RTG.tscn",
	"ASC": "res://source/terminal-sandbox/che/RTG.tscn",
	"RGC": "res://source/terminal-sandbox/che/RTG.tscn",
	"ARTG": "res://source/terminal-sandbox/che/RTG.tscn",
}
const DEBUG_CHE_TRANSFORMS := true

var map_width_m := 1000.0
var map_height_m := 1000.0
var map_units_per_meter := 1.0
var map_origin_latitude := 0.0
var map_origin_longitude := 0.0
var map_angle_deg := 0.0
var default_zoom := 15.0

var _viewport: SubViewport
var _world: Node3D
var _content: Node3D
var _camera: Camera3D
var _camera_target := Vector3.ZERO
var _camera_yaw := 0.0
var _camera_pitch := deg_to_rad(52.0)
var _camera_distance := 900.0
var _min_distance := 20.0
var _max_distance := 1600.0
var _rotating := false
var _mouse_inside := false
var _selected_collider: CollisionObject3D
var _selected_che_colliders: Array[CollisionObject3D] = []
var _selection_start := Vector2.ZERO
var _selection_current := Vector2.ZERO
var _selection_dragging := false
var _selection_additive := false
var _move_targeting := false
var _selection_material: StandardMaterial3D
var _selection_marker: MeshInstance3D
var _destination_indicators := {}
var _destination_material: StandardMaterial3D
var _last_che_debug_ms := {}
var _land_texture: Texture2D
var _concrete_texture: Texture2D
var _asphalt_texture: Texture2D
var _water_texture: Texture2D


func _ready():
	stretch = true
	mouse_filter = Control.MOUSE_FILTER_STOP
	_land_texture = _load_runtime_texture(LAND_TEXTURE_PATH)
	_concrete_texture = _load_runtime_texture(CONCRETE_TEXTURE_PATH)
	_asphalt_texture = _load_runtime_texture(ASPHALT_TEXTURE_PATH)
	_water_texture = _load_runtime_texture(WATER_TEXTURE_PATH)
	_build_world()
	set_process(true)


func _build_world():
	_viewport = SubViewport.new()
	_viewport.name = "TerminalViewport"
	_viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	_viewport.handle_input_locally = false
	_viewport.own_world_3d = true
	_viewport.msaa_3d = Viewport.MSAA_4X
	add_child(_viewport)

	_world = Node3D.new()
	_viewport.add_child(_world)
	_content = Node3D.new()
	_content.name = "TerminalContent"
	_world.add_child(_content)

	var environment = WorldEnvironment.new()
	var environment_resource = Environment.new()
	environment_resource.background_mode = Environment.BG_SKY
	var sky = Sky.new()
	var sky_material = ProceduralSkyMaterial.new()
	sky_material.sky_top_color = Color(0.20, 0.31, 0.43)
	sky_material.sky_horizon_color = Color(0.69, 0.77, 0.79)
	sky_material.ground_bottom_color = Color(0.06, 0.075, 0.08)
	sky_material.ground_horizon_color = Color(0.28, 0.33, 0.34)
	sky.sky_material = sky_material
	environment_resource.sky = sky
	environment_resource.ambient_light_source = Environment.AMBIENT_SOURCE_SKY
	environment_resource.ambient_light_energy = 0.7
	environment_resource.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.environment = environment_resource
	_world.add_child(environment)

	var sun = DirectionalLight3D.new()
	sun.rotation_degrees = Vector3(-52, -28, 0)
	sun.light_energy = 1.15
	sun.shadow_enabled = true
	_world.add_child(sun)

	_camera = Camera3D.new()
	_camera.current = true
	_camera.fov = 48.0
	_camera.near = 0.5
	_world.add_child(_camera)

	_selection_material = StandardMaterial3D.new()
	_selection_material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	_selection_material.albedo_color = Color(1.0, 0.84, 0.2, 0.42)
	_selection_material.emission_enabled = true
	_selection_material.emission = Color(1.0, 0.68, 0.08)
	_selection_material.emission_energy_multiplier = 1.8

	_selection_marker = MeshInstance3D.new()
	var marker_mesh = TorusMesh.new()
	marker_mesh.inner_radius = 0.44
	marker_mesh.outer_radius = 0.52
	marker_mesh.rings = 32
	marker_mesh.ring_segments = 8
	_selection_marker.mesh = marker_mesh
	_selection_marker.material_override = _material(Color(0.18, 0.92, 0.78), 0.35, 0.18)
	_selection_marker.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
	_selection_marker.visible = false
	_world.add_child(_selection_marker)
	_destination_material = StandardMaterial3D.new()
	_destination_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	_destination_material.albedo_color = Color(0.20, 0.95, 0.82)
	_destination_material.emission_enabled = true
	_destination_material.emission = Color(0.20, 0.95, 0.82)
	_destination_material.no_depth_test = true


func set_map_definition(location_data: Dictionary):
	map_width_m = max(float(location_data.get("map_width_m", 1000.0)), 1.0)
	map_height_m = max(float(location_data.get("map_height_m", 1000.0)), 1.0)
	map_units_per_meter = max(float(location_data.get("map_units_per_meter", 1.0)), 0.01)
	var origin_latitude = location_data.get("map_origin_latitude")
	var origin_longitude = location_data.get("map_origin_longitude")
	if origin_latitude == null:
		origin_latitude = location_data.get("latitude", 0.0)
	if origin_longitude == null:
		origin_longitude = location_data.get("longitude", 0.0)
	map_origin_latitude = float(origin_latitude)
	map_origin_longitude = float(origin_longitude)
	map_angle_deg = float(location_data.get("angle", 0.0))
	var location_zoom = location_data.get("default_zoom")
	default_zoom = float(location_zoom if location_zoom != null else 15.0)
	_configure_camera_bounds()
	_rebuild_ground()


func set_terminal_data(area_list: Array, road_list: Array, che_list: Array):
	_clear_content()
	_build_ground()
	for area in area_list:
		if bool(area.get("is_visible", true)):
			_build_area(area)
	for road in road_list:
		_build_road(road)
	for che in che_list:
		_build_che(che)


func set_areas(area_list: Array):
	set_terminal_data(area_list, [], [])


func set_design_enabled(_value: bool):
	pass


func set_tool(_value: String):
	pass


func add_saved_object(_object_data: Dictionary):
	pass


func reset_camera():
	_camera_target = _map_center_world()
	_camera_yaw = 0.0
	_camera_pitch = deg_to_rad(52.0)
	var zoom_factor = pow(2.0, clamp(15.0 - default_zoom, -3.0, 3.0))
	_camera_distance = clamp(max(map_width_m, map_height_m) * map_units_per_meter * 0.9 * zoom_factor, _min_distance, _max_distance)
	_update_camera()


func _configure_camera_bounds():
	var longest = max(map_width_m, map_height_m) * map_units_per_meter
	_min_distance = max(12.0, longest * 0.045)
	_max_distance = max(80.0, longest * 1.8)
	reset_camera()


func _clear_content():
	_clear_selection()
	_destination_indicators.clear()
	_last_che_debug_ms.clear()
	for child in _content.get_children():
		child.queue_free()


func _rebuild_ground():
	if _content == null:
		return
	_clear_content()
	_build_ground()


func _build_ground():
	var width = map_width_m * map_units_per_meter
	var height = map_height_m * map_units_per_meter
	var ground = MeshInstance3D.new()
	var plane = PlaneMesh.new()
	plane.size = Vector2(width, height)
	ground.mesh = plane
	ground.material_override = _material(Color(1, 1, 1, 0), 0.92, 0.0)
	ground.position = _map_center_world()
	_content.add_child(ground)
	_build_map_perimeter(width, height)


func _build_map_perimeter(width: float, height: float):
	var edge_y = MAP_Y + 0.025
	var edge_thickness = max(0.08, min(width, height) * 0.00025)
	var far_edge_color = Color(0.68, 0.75, 0.72, 0.55)
	_build_line_box(
		Vector3(width / 2.0, edge_y, -height),
		Vector3(width, 0.025, edge_thickness),
		far_edge_color
	)
	_build_line_box(
		Vector3(width, edge_y, -height / 2.0),
		Vector3(edge_thickness, 0.025, height),
		far_edge_color
	)
	# The origin edges double as terminal-axis markers: +X is red and +Y is blue.
	_build_line_box(
		Vector3(width / 2.0, edge_y, 0),
		Vector3(width, 0.035, edge_thickness),
		Color(0.82, 0.32, 0.26, 0.8)
	)
	_build_line_box(
		Vector3(0, edge_y, -height / 2.0),
		Vector3(edge_thickness, 0.035, height),
		Color(0.25, 0.64, 0.88, 0.8)
	)


func _build_line_box(position: Vector3, box_size: Vector3, color: Color):
	var line = MeshInstance3D.new()
	var mesh = BoxMesh.new()
	mesh.size = box_size
	line.mesh = mesh
	line.position = position
	line.material_override = _material(color, 1.0, 0.0)
	_content.add_child(line)


func _build_area(data: Dictionary):
	var points = _dictionary_points(data, "coordinates")
	if points.size() < 3:
		return
	var polygon_2d := PackedVector2Array()
	for point in points:
		polygon_2d.append(Vector2(point.x, point.z))
	var triangle_indices = Geometry2D.triangulate_polygon(polygon_2d)
	if triangle_indices.is_empty():
		return

	var vertices := PackedVector3Array()
	var uvs := PackedVector2Array()
	var indices := PackedInt32Array()
	var area_type = str(data.get("area_type", "terminal area"))
	var elevation = _area_elevation(area_type)
	for point in points:
		vertices.append(Vector3(point.x, elevation, point.z))
		uvs.append(Vector2(point.x, -point.z) / (TEXTURE_TILE_M * map_units_per_meter))
	for index in triangle_indices:
		indices.append(index)

	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = vertices
	arrays[Mesh.ARRAY_TEX_UV] = uvs
	arrays[Mesh.ARRAY_INDEX] = indices
	var mesh = ArrayMesh.new()
	mesh.add_surface_from_arrays(Mesh.PRIMITIVE_TRIANGLES, arrays)

	var visual = MeshInstance3D.new()
	visual.mesh = mesh
	visual.material_override = _area_material(area_type)
	_content.add_child(visual)
	match area_type.to_lower():
		"building":
			_build_building(points, elevation)
		"parking":
			_build_parking_fence(points, elevation)
		"yard block":
			_build_yard_grid(data, elevation)
	if not _is_selectable_area_type(area_type):
		return

	var collider = AnimatableBody3D.new()
	var shape_node = CollisionShape3D.new()
	var shape = ConcavePolygonShape3D.new()
	var faces := PackedVector3Array()
	for index in triangle_indices:
		faces.append(vertices[index])
	shape.set_faces(faces)
	shape_node.shape = shape
	collider.add_child(shape_node)
	_set_pick_metadata(collider, visual, "area", data)
	_content.add_child(collider)


func _is_selectable_area_type(area_type: String) -> bool:
	match area_type.to_lower():
		"water", "land", "terminal area", "road":
			return false
		_:
			return true


func _build_building(points: Array[Vector3], elevation: float):
	var bounds = _oriented_polygon_bounds(points)
	if bounds.is_empty():
		return
	var height = BUILDING_HEIGHT_M * map_units_per_meter
	var building = MeshInstance3D.new()
	building.name = "BuildingVolume"
	var mesh = BoxMesh.new()
	mesh.size = Vector3(bounds.length, height, bounds.width)
	building.mesh = mesh
	building.position = Vector3(bounds.center.x, elevation + height / 2.0, bounds.center.y)
	building.rotation.y = bounds.rotation
	building.material_override = _textured_material(_concrete_texture, Color(0.56, 0.61, 0.62), 0.82, 0.04)
	_content.add_child(building)


func _build_parking_fence(points: Array[Vector3], elevation: float):
	var height = PARKING_FENCE_HEIGHT_M * map_units_per_meter
	var post_size = max(0.55 * map_units_per_meter, 0.18)
	var rail_size = max(0.38 * map_units_per_meter, 0.14)
	var fence_material = _material(Color(0.72, 0.82, 0.78), 0.48, 0.42)
	fence_material.emission_enabled = true
	fence_material.emission = Color(0.18, 0.46, 0.43)
	fence_material.emission_energy_multiplier = 0.55
	for point_index in range(points.size()):
		var point: Vector3 = points[point_index]
		var post = MeshInstance3D.new()
		post.name = "ParkingFencePost%d" % point_index
		var post_mesh = BoxMesh.new()
		post_mesh.size = Vector3(post_size, height, post_size)
		post.mesh = post_mesh
		post.position = Vector3(point.x, elevation + height / 2.0, point.z)
		post.material_override = fence_material
		_content.add_child(post)
	for index in range(points.size()):
		var start: Vector3 = points[index]
		var finish: Vector3 = points[(index + 1) % points.size()]
		var delta = finish - start
		var length = Vector2(delta.x, delta.z).length()
		if length <= 0.01:
			continue
		var rail_heights = [height * 0.38, height * 0.82]
		for rail_index in range(rail_heights.size()):
			var rail_height: float = rail_heights[rail_index]
			var rail = MeshInstance3D.new()
			rail.name = "ParkingFenceRail%d_%d" % [index, rail_index]
			var rail_mesh = BoxMesh.new()
			rail_mesh.size = Vector3(rail_size, rail_size, length)
			rail.mesh = rail_mesh
			rail.position = (start + finish) / 2.0 + Vector3.UP * (elevation + rail_height)
			rail.rotation.y = atan2(delta.x, delta.z)
			rail.material_override = fence_material
			_content.add_child(rail)


func _build_yard_grid(data: Dictionary, elevation: float):
	var config = data.get("block_config", {})
	if not config is Dictionary or config.is_empty():
		return
	var piles = config.get("piles", [])
	if not piles is Array or piles.is_empty():
		return
	var pile_length = max(float(config.get("pile_length_m", 6.058)) * map_units_per_meter, 0.2)
	var pile_width = max(float(config.get("pile_width_m", 2.438)) * map_units_per_meter, 0.2)
	var relative_heading = deg_to_rad(float(config.get("heading_deg", 0.0)) + float(config.get("slot_heading_deg", 0.0)) - map_angle_deg)
	var length_axis = Vector2(sin(relative_heading), -cos(relative_heading))
	var width_axis = Vector2(cos(relative_heading), sin(relative_heading))
	var line_vertices := PackedVector3Array()
	var centers: Array[Dictionary] = []
	for pile in piles:
		if not pile is Dictionary or pile.get("latitude") == null or pile.get("longitude") == null:
			continue
		var center = _geographic_to_world(float(pile.latitude), float(pile.longitude), elevation + 0.055)
		var half_length = length_axis * (pile_length / 2.0)
		var half_width = width_axis * (pile_width / 2.0)
		var center_2d = Vector2(center.x, center.z)
		var corners = [
			center_2d - half_length - half_width,
			center_2d + half_length - half_width,
			center_2d + half_length + half_width,
			center_2d - half_length + half_width,
		]
		for corner_index in range(4):
			var first: Vector2 = corners[corner_index]
			var second: Vector2 = corners[(corner_index + 1) % 4]
			line_vertices.append(Vector3(first.x, center.y, first.y))
			line_vertices.append(Vector3(second.x, center.y, second.y))
		centers.append({"position": center, "pile": pile})
	if line_vertices.is_empty():
		return
	var arrays := []
	arrays.resize(Mesh.ARRAY_MAX)
	arrays[Mesh.ARRAY_VERTEX] = line_vertices
	var grid_mesh = ArrayMesh.new()
	grid_mesh.add_surface_from_arrays(Mesh.PRIMITIVE_LINES, arrays)
	var grid_visual = MeshInstance3D.new()
	grid_visual.name = "YardGrid"
	grid_visual.mesh = grid_mesh
	var grid_material = _material(Color(0.22, 0.94, 0.78), 0.55, 0.05)
	grid_material.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	grid_visual.material_override = grid_material
	_content.add_child(grid_visual)
	var show_all_labels = centers.size() <= 200
	for entry in centers:
		var pile: Dictionary = entry.pile
		if show_all_labels or int(pile.get("bay", 0)) == 1 or int(pile.get("row", 0)) == 1:
			_build_yard_grid_label(entry.position, pile, min(pile_length, pile_width))


func _build_yard_grid_label(position: Vector3, pile: Dictionary, _cell_width: float):
	var label = Label3D.new()
	label.name = "YardGridLabel%s_%s" % [pile.get("bay", "x"), pile.get("row", "x")]
	label.text = "%s/%s" % [pile.get("bay_name", pile.get("bay", "?")), pile.get("row_name", pile.get("row", "?"))]
	label.font_size = 14
	label.fixed_size = true
	label.pixel_size = 0.0018
	label.outline_size = 3
	label.no_depth_test = true
	label.visibility_range_end = max(120.0, min(map_width_m, map_height_m) * 0.55) * map_units_per_meter
	label.visibility_range_end_margin = label.visibility_range_end * 0.12
	label.modulate = Color(0.82, 1.0, 0.94)
	label.outline_modulate = Color(0.02, 0.10, 0.11, 0.92)
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	label.position = position + Vector3.UP * 0.08
	_content.add_child(label)


func _oriented_polygon_bounds(points: Array[Vector3]) -> Dictionary:
	if points.size() < 2:
		return {}
	var first = Vector2(points[0].x, points[0].z)
	var second = Vector2(points[1].x, points[1].z)
	var primary = (second - first).normalized()
	if primary.length_squared() <= 0.0001:
		return {}
	var secondary = Vector2(-primary.y, primary.x)
	var min_primary = INF
	var max_primary = -INF
	var min_secondary = INF
	var max_secondary = -INF
	for point in points:
		var flat = Vector2(point.x, point.z)
		var primary_value = flat.dot(primary)
		var secondary_value = flat.dot(secondary)
		min_primary = min(min_primary, primary_value)
		max_primary = max(max_primary, primary_value)
		min_secondary = min(min_secondary, secondary_value)
		max_secondary = max(max_secondary, secondary_value)
	var center = primary * ((min_primary + max_primary) / 2.0) + secondary * ((min_secondary + max_secondary) / 2.0)
	return {
		"center": center,
		"length": max(max_primary - min_primary, 0.2),
		"width": max(max_secondary - min_secondary, 0.2),
		"rotation": atan2(-primary.y, primary.x),
	}


func _build_road(data: Dictionary):
	var points = _dictionary_points(data, "points")
	if points.size() < 2:
		return
	var road_width = max(3.2, float(data.get("lane_count", 1)) * 3.2) * map_units_per_meter
	for index in range(points.size() - 1):
		var start: Vector3 = points[index]
		var finish: Vector3 = points[index + 1]
		var delta = finish - start
		var length = delta.length()
		if length <= 0.01:
			continue
		var visual = MeshInstance3D.new()
		var mesh = BoxMesh.new()
		mesh.size = Vector3(road_width, 0.16, length)
		visual.mesh = mesh
		visual.position = (start + finish) / 2.0 + Vector3.UP * ROAD_Y
		visual.rotation.y = atan2(delta.x, delta.z)
		var road_material = _textured_material(_asphalt_texture, Color(0.72, 0.76, 0.78), 0.92, 0.0)
		road_material.uv1_scale = Vector3(
			max(road_width / (TEXTURE_TILE_M * map_units_per_meter), 1.0),
			1.0,
			max(length / (TEXTURE_TILE_M * map_units_per_meter), 1.0)
		)
		visual.material_override = road_material
		_content.add_child(visual)


func _build_che(data: Dictionary):
	if data.get("latitude") == null or data.get("longitude") == null:
		return
	var code = str(data.get("equipment_type_code", "CHE")).to_upper()
	var dimensions = _che_dimensions(data) * map_units_per_meter
	var position = _geographic_to_world(float(data["latitude"]), float(data["longitude"]), CHE_Y)
	var heading_yaw = deg_to_rad(map_angle_deg - float(data.get("rotation", 0.0)))
	var scene = _load_che_scene(code)
	var collider: CollisionObject3D = (scene.instantiate() as CollisionObject3D) if scene != null else null
	if collider == null:
		push_warning("Could not instantiate CHE scene for code: %s" % code)
		return
	collider.name = "Che%s" % str(data.get("name", data.get("pk", "Unit")))
	collider.position = position + Vector3.UP * dimensions.y / 2.0
	collider.rotation.y = heading_yaw
	var visual = collider.get_node_or_null("Geometry/Body") as GeometryInstance3D
	var shape_node = collider.get_node_or_null("CollisionShape3D") as CollisionShape3D
	if collider.has_method("configure_terminal_che"):
		collider.call(
			"configure_terminal_che",
			data,
			dimensions,
			_che_movement_profile(data),
			map_units_per_meter,
			_material(_che_state_color(data, code), 0.58, 0.12)
		)
		if collider.has_method("primary_visual"):
			var model_visual = collider.call("primary_visual") as GeometryInstance3D
			if model_visual != null:
				visual = model_visual
	_set_pick_metadata(collider, visual, "che", data)
	collider.set_meta("terminal_model", visual)
	collider.set_meta("terminal_shape", shape_node)
	collider.set_meta("terminal_movement", collider)
	_content.add_child(collider)
	_configure_che_rails(collider, data)
	collider.connect("pose_changed", _on_che_pose_changed.bind(collider))
	collider.connect("movement_finished", _on_che_movement_finished.bind(collider))
	collider.connect("movement_cancelled", _on_che_movement_cancelled.bind(collider))


func _configure_che_rails(collider: CollisionObject3D, data: Dictionary):
	if not collider.has_method("configure_rails"):
		return
	var motion: Dictionary = data.get("rail_motion", {}).duplicate(true)
	for rail in motion.get("rails", []):
		var points: Array = []
		for coordinate in rail.get("points", []):
			points.append(_geographic_to_world(float(coordinate[1]), float(coordinate[0]), collider.position.y))
		rail["points"] = points
		var polygon := PackedVector2Array()
		var geometry: Dictionary = rail.get("geometry", {})
		if geometry.get("type") == "Polygon":
			for coordinate in geometry.get("coordinates", [[]])[0]:
				var point: Vector3 = _geographic_to_world(float(coordinate[1]), float(coordinate[0]), collider.position.y)
				polygon.append(Vector2(point.x, point.z))
		rail["polygon"] = polygon
	collider.call("configure_rails", motion)
	_refresh_che_pose_metadata(collider)


func _load_che_scene(code: String) -> PackedScene:
	var path = str(CHE_SCENE_PATHS.get(code, CHE_SCENE_PATHS.get("TT", "")))
	if path.is_empty() or not ResourceLoader.exists(path):
		return null
	return load(path) as PackedScene


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


func get_selected_che_data() -> Array:
	var selected: Array = []
	for collider in _selected_che_colliders:
		if is_instance_valid(collider):
			selected.append(collider.get_meta("terminal_data", {}).duplicate(true))
	return selected


func get_che_operating_data() -> Array:
	var ches: Array = []
	for child in _content.get_children():
		if not child is CollisionObject3D or child.get_meta("terminal_kind", "") != "che":
			continue
		var data: Dictionary = child.get_meta("terminal_data", {}).duplicate(true)
		var movement = child.get_meta("terminal_movement", null) as Node
		data["simulation_is_moving"] = movement != null and bool(movement.call("is_moving"))
		ches.append(data)
	return ches


func update_che_states(che_ids: Array, state_patch: Dictionary) -> Array:
	var requested_ids = che_ids.map(func(value): return int(value))
	var updated: Array = []
	for child in _content.get_children():
		if not child is CollisionObject3D or child.get_meta("terminal_kind", "") != "che":
			continue
		var data: Dictionary = child.get_meta("terminal_data", {}).duplicate(true)
		var che_id = int(data.get("pk", data.get("id", 0)))
		if che_id not in requested_ids:
			continue
		for key in state_patch:
			data[key] = state_patch[key]
		child.set_meta("terminal_data", data)
		var visual = child.get_meta("terminal_visual") as GeometryInstance3D
		if visual != null:
			visual.material_override = _material(_che_state_color(
				data, str(data.get("equipment_type_code", "CHE"))
			), 0.58, 0.12)
		updated.append(data)
	_emit_che_selection()
	return updated


func update_che_fuel(che_id: int, fuel_level: float, consumption_rate_lph: float, consumed_liters: float) -> Dictionary:
	for child in _content.get_children():
		if not child is CollisionObject3D or child.get_meta("terminal_kind", "") != "che":
			continue
		var data: Dictionary = child.get_meta("terminal_data", {}).duplicate(true)
		if int(data.get("pk", data.get("id", 0))) != che_id:
			continue
		data["fuel_level"] = fuel_level
		data["fuel_consumption_rate_lph"] = consumption_rate_lph
		data["fuel_consumed_liters"] = consumed_liters
		child.set_meta("terminal_data", data)
		if child in _selected_che_colliders:
			_emit_che_selection()
		return data
	return {}


func begin_che_move_targeting() -> bool:
	if _selected_che_colliders.is_empty():
		return false
	for collider in _selected_che_colliders:
		var data: Dictionary = collider.get_meta("terminal_data", {})
		if (
			bool(data.get("is_available", false))
			or not bool(data.get("powered_on", false))
			or bool(data.get("is_in_maintenance", false))
		):
			return false
	_move_targeting = true
	mouse_default_cursor_shape = Control.CURSOR_CROSS
	return true


func cancel_che_move_targeting():
	_move_targeting = false
	mouse_default_cursor_shape = Control.CURSOR_ARROW


func _move_selected_ches_to_screen(screen_position: Vector2):
	var viewport_position = _canvas_to_viewport_position(screen_position)
	var origin = _camera.project_ray_origin(viewport_position)
	var direction = _camera.project_ray_normal(viewport_position)
	var intersection = Plane(Vector3.UP, MAP_Y).intersects_ray(origin, direction)
	if intersection == null:
		return
	var target: Vector3 = intersection
	target.x = clamp(target.x, 0.0, map_width_m * map_units_per_meter)
	target.z = clamp(target.z, -map_height_m * map_units_per_meter, 0.0)
	var centroid := Vector3.ZERO
	for collider in _selected_che_colliders:
		centroid += Vector3(collider.position.x, CHE_Y, collider.position.z)
	centroid /= float(_selected_che_colliders.size())
	var group_delta = target - centroid
	group_delta.y = 0.0
	var min_delta_x := -INF
	var max_delta_x := INF
	var min_delta_z := -INF
	var max_delta_z := INF
	for collider in _selected_che_colliders:
		min_delta_x = max(min_delta_x, -collider.position.x)
		max_delta_x = min(max_delta_x, map_width_m * map_units_per_meter - collider.position.x)
		min_delta_z = max(min_delta_z, -map_height_m * map_units_per_meter - collider.position.z)
		max_delta_z = min(max_delta_z, -collider.position.z)
	group_delta.x = clamp(group_delta.x, min_delta_x, max_delta_x)
	group_delta.z = clamp(group_delta.z, min_delta_z, max_delta_z)
	var commanded: Array = []
	for collider in _selected_che_colliders:
		var destination = Vector3(collider.position.x, CHE_Y, collider.position.z) + group_delta
		var movement = collider.get_meta("terminal_movement", null) as Node
		if movement == null or not bool(movement.call("move_to", destination)):
			continue
		destination = movement.call("target_position")
		var destination_geographic = world_to_geographic(destination)
		var data: Dictionary = collider.get_meta("terminal_data", {}).duplicate(true)
		data["simulation_is_moving"] = true
		data["destination_latitude"] = destination_geographic.latitude
		data["destination_longitude"] = destination_geographic.longitude
		data["destination_world_x"] = destination.x
		data["destination_world_z"] = destination.z
		data["target_world_x"] = destination.x
		data["target_world_z"] = destination.z
		collider.set_meta("terminal_data", data)
		collider.set_meta("terminal_destination", destination)
		data = _refresh_che_pose_metadata(collider)
		_debug_che_transform(collider, "move-order")
		commanded.append(data)
	che_move_requested.emit(commanded)
	_emit_che_selection()
	queue_redraw()


func cancel_selected_che_moves() -> int:
	var cancelled := 0
	for collider in _selected_che_colliders:
		var movement = collider.get_meta("terminal_movement", null) as Node
		if movement != null and bool(movement.call("is_moving")):
			movement.call("cancel")
			cancelled += 1
	return cancelled


func _on_che_pose_changed(collider: CollisionObject3D):
	if not is_instance_valid(collider):
		return
	var data = _refresh_che_pose_metadata(collider)
	_debug_che_transform(collider, "pose")
	che_poses_changed.emit([data])
	if collider in _selected_che_colliders:
		_emit_che_selection()
		queue_redraw()


func _refresh_che_pose_metadata(collider: CollisionObject3D) -> Dictionary:
	var data: Dictionary = collider.get_meta("terminal_data", {}).duplicate(true)
	var geographic = world_to_geographic(Vector3(collider.position.x, CHE_Y, collider.position.z))
	data["latitude"] = geographic.latitude
	data["longitude"] = geographic.longitude
	data["rotation"] = fposmod(map_angle_deg - rad_to_deg(collider.rotation.y), 360.0)
	data["world_x"] = collider.position.x
	data["world_z"] = collider.position.z
	var movement = collider.get_meta("terminal_movement", null) as Node
	if movement != null and movement.has_method("rail_state"):
		var rail: Dictionary = movement.call("rail_state")
		data["rail_axis_id"] = rail.axis_id
		data["movement_mode"] = rail.mode
		if rail.mode == "rail" or movement.get("rail_motion_mode") == "repositionable":
			data["current_asset"] = rail.asset_id
	if movement != null and bool(movement.call("is_moving")):
		data["simulation_is_moving"] = true
		var target: Vector3 = movement.call("target_position")
		var target_geographic = world_to_geographic(target)
		data["target_latitude"] = target_geographic.latitude
		data["target_longitude"] = target_geographic.longitude
		data["target_world_x"] = target.x
		data["target_world_z"] = target.z
		data["destination_latitude"] = target_geographic.latitude
		data["destination_longitude"] = target_geographic.longitude
		data["destination_world_x"] = target.x
		data["destination_world_z"] = target.z
		data["target_distance_m"] = Vector2(collider.position.x, collider.position.z).distance_to(Vector2(target.x, target.z)) / map_units_per_meter
	elif collider.has_meta("terminal_destination"):
		var destination: Vector3 = collider.get_meta("terminal_destination")
		var destination_geographic = world_to_geographic(destination)
		data["target_latitude"] = destination_geographic.latitude
		data["target_longitude"] = destination_geographic.longitude
		data["target_world_x"] = destination.x
		data["target_world_z"] = destination.z
		data["target_distance_m"] = Vector2(collider.position.x, collider.position.z).distance_to(Vector2(destination.x, destination.z)) / map_units_per_meter
	collider.set_meta("terminal_data", data)
	return data


func _debug_che_transform(collider: CollisionObject3D, reason: String):
	if not DEBUG_CHE_TRANSFORMS or not is_instance_valid(collider):
		return
	var now_ms = Time.get_ticks_msec()
	var collider_id = collider.get_instance_id()
	if reason == "pose" and now_ms - int(_last_che_debug_ms.get(collider_id, 0)) < 500:
		return
	_last_che_debug_ms[collider_id] = now_ms
	var data: Dictionary = collider.get_meta("terminal_data", {})
	var visual = collider.get_meta("terminal_visual", null) as Node3D
	var model = collider.get_meta("terminal_model", null) as Node3D
	var shape = collider.get_meta("terminal_shape", null) as Node3D
	var visual_position = visual.global_position if is_instance_valid(visual) else Vector3.INF
	var model_position = model.global_position if is_instance_valid(model) else Vector3.INF
	var shape_position = shape.global_position if is_instance_valid(shape) else Vector3.INF
	var target_position = Vector3.INF
	if collider.has_meta("terminal_destination"):
		target_position = collider.get_meta("terminal_destination")
	var movement = collider.get_meta("terminal_movement", null) as Node
	if movement != null and bool(movement.call("is_moving")):
		target_position = movement.call("target_position")
	var motion_state := {}
	if movement != null:
		motion_state = movement.call("debug_motion_state")
	print(
			"[CHE DEBUG:%s] id=%s name=%s moving=%s root=%s visual=%s model=%s shape=%s target=%s latlon=(%.7f, %.7f) rot=%.2f target_dist_m=%.2f speed=%.2f current=%.2f accel=%.2f requested_m=%.3f last_travel_m=%.3f last_delta=%s" % [
			reason,
			str(data.get("pk", data.get("id", "?"))),
			str(data.get("name", "?")),
			str(movement != null and bool(movement.call("is_moving"))),
			str(collider.global_position),
			str(visual_position),
			str(model_position),
			str(shape_position),
			str(target_position),
			float(data.get("latitude", 0.0)),
			float(data.get("longitude", 0.0)),
			float(data.get("rotation", 0.0)),
			float(data.get("target_distance_m", -1.0)),
				float(motion_state.get("speed_mps", 0.0)),
				float(motion_state.get("current_speed_mps", 0.0)),
				float(motion_state.get("acceleration_mps2", 0.0)),
				float(motion_state.get("last_requested_travel_m", 0.0)),
				float(motion_state.get("last_travel_m", 0.0)),
				str(motion_state.get("last_translation", Vector3.ZERO)),
			]
	)


func _on_che_movement_finished(collider: CollisionObject3D):
	_set_che_movement_state(collider, false)
	che_move_completed.emit([collider.get_meta("terminal_data", {}).duplicate(true)])


func _on_che_movement_cancelled(collider: CollisionObject3D):
	_set_che_movement_state(collider, false)
	che_move_cancelled.emit([collider.get_meta("terminal_data", {}).duplicate(true)])


func _set_che_movement_state(collider: CollisionObject3D, moving: bool):
	if not is_instance_valid(collider):
		return
	var data: Dictionary = collider.get_meta("terminal_data", {}).duplicate(true)
	data["simulation_is_moving"] = moving
	if not moving:
		data.erase("destination_latitude")
		data.erase("destination_longitude")
		data.erase("destination_world_x")
		data.erase("destination_world_z")
		data.erase("target_latitude")
		data.erase("target_longitude")
		data.erase("target_world_x")
		data.erase("target_world_z")
		data.erase("target_distance_m")
		collider.remove_meta("terminal_destination")
	collider.set_meta("terminal_data", data)
	if collider in _selected_che_colliders:
		_emit_che_selection()
		queue_redraw()


func _che_state_color(data: Dictionary, equipment_code: String) -> Color:
	if bool(data.get("is_in_maintenance", false)):
		return Color(0.94, 0.72, 0.16)
	if not bool(data.get("powered_on", false)):
		return Color(0.32, 0.36, 0.38)
	if bool(data.get("working", false)):
		return Color(0.18, 0.55, 0.92)
	if bool(data.get("is_available", false)):
		return Color(0.23, 0.78, 0.42)
	return _che_color(equipment_code)


func _set_pick_metadata(collider: CollisionObject3D, visual: GeometryInstance3D, kind: String, data: Dictionary):
	collider.set_meta("terminal_kind", kind)
	collider.set_meta("terminal_data", data.duplicate(true))
	collider.set_meta("terminal_visual", visual)


func _dictionary_points(data: Dictionary, key: String) -> Array[Vector3]:
	var points: Array[Vector3] = []
	for point in data.get(key, []):
		if point is Array and point.size() >= 2:
			points.append(_geographic_to_world(float(point[1]), float(point[0]), 0.0))
	return points


func _geographic_to_local(latitude: float, longitude: float) -> Vector2:
	var north_m = deg_to_rad(latitude - map_origin_latitude) * EARTH_RADIUS_M
	var east_m = deg_to_rad(longitude - map_origin_longitude) * EARTH_RADIUS_M * cos(deg_to_rad(map_origin_latitude))
	var angle = deg_to_rad(map_angle_deg)
	return Vector2(
		east_m * cos(angle) - north_m * sin(angle),
		east_m * sin(angle) + north_m * cos(angle)
	)


func local_to_geographic(x: float, y: float) -> Dictionary:
	var angle = deg_to_rad(map_angle_deg)
	var east_m = x * cos(angle) + y * sin(angle)
	var north_m = -x * sin(angle) + y * cos(angle)
	var latitude = map_origin_latitude + rad_to_deg(north_m / EARTH_RADIUS_M)
	var longitude = map_origin_longitude + rad_to_deg(east_m / (EARTH_RADIUS_M * cos(deg_to_rad(map_origin_latitude))))
	return {"latitude": latitude, "longitude": longitude}


func world_to_geographic(position: Vector3) -> Dictionary:
	var x = position.x / map_units_per_meter
	var y = -position.z / map_units_per_meter
	return local_to_geographic(x, y)


func _geographic_to_world(latitude: float, longitude: float, elevation: float = 0.0) -> Vector3:
	var local = _geographic_to_local(latitude, longitude)
	return _local_to_world(local.x, local.y, elevation)


func _local_to_world(x: float, y: float, elevation: float = 0.0) -> Vector3:
	# The geographic map origin is Godot (0, 0, 0); terminal +Y points along Godot -Z.
	return Vector3(
		x * map_units_per_meter,
		elevation,
		-y * map_units_per_meter
	)


func _map_center_world() -> Vector3:
	return Vector3(
		map_width_m * map_units_per_meter / 2.0,
		MAP_Y,
		-map_height_m * map_units_per_meter / 2.0
	)


func _process(delta: float):
	if not is_visible_in_tree() or _camera == null:
		return
	_update_destination_indicators()
	var movement = Input.get_vector("move_map_left", "move_map_right", "move_map_up", "move_map_down")
	if _mouse_inside and not _rotating:
		var mouse = get_local_mouse_position()
		var edge = 10.0
		if mouse.x <= edge:
			movement.x = -1.0
		elif mouse.x >= size.x - edge:
			movement.x = 1.0
		if mouse.y <= edge:
			movement.y = -1.0
		elif mouse.y >= size.y - edge:
			movement.y = 1.0
	if movement.length_squared() > 0.0:
		var screen_down = Vector3(sin(_camera_yaw), 0, cos(_camera_yaw))
		var screen_right = Vector3(cos(_camera_yaw), 0, -sin(_camera_yaw))
		var speed = max(_camera_distance * 0.72, 18.0)
		_camera_target += (screen_right * movement.x + screen_down * movement.y).normalized() * speed * delta
		_clamp_camera_target()
		_update_camera()


func _gui_input(event: InputEvent):
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_WHEEL_UP and event.pressed:
			_zoom(0.86)
			accept_event()
		elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN and event.pressed:
			_zoom(1.16)
			accept_event()
		elif event.button_index in [MOUSE_BUTTON_MIDDLE, MOUSE_BUTTON_RIGHT]:
			_rotating = event.pressed
			accept_event()
		elif event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				_selection_start = event.position
				_selection_current = event.position
				_selection_dragging = true
				_selection_additive = event.ctrl_pressed or event.meta_pressed
			else:
				if _move_targeting:
					_selection_dragging = false
					cancel_che_move_targeting()
					_move_selected_ches_to_screen(event.position)
					queue_redraw()
					accept_event()
					return
				var distance = _selection_start.distance_to(event.position)
				_selection_dragging = false
				if distance >= 7.0:
					_select_ches_in_rect(_selection_rect(), _selection_additive)
				else:
					_pick(event.position, _selection_additive)
				queue_redraw()
			accept_event()
	elif event is InputEventMouseMotion:
		if _rotating:
			_camera_yaw -= event.relative.x * 0.007
			_camera_pitch = clamp(_camera_pitch - event.relative.y * 0.006, deg_to_rad(24.0), deg_to_rad(76.0))
			_update_camera()
			accept_event()
		elif _selection_dragging:
			_selection_current = event.position
			queue_redraw()
			accept_event()
	elif event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE and _move_targeting:
		cancel_che_move_targeting()
		accept_event()


func _draw():
	if _selection_dragging and _selection_start.distance_to(_selection_current) >= 4.0:
		var rect = _selection_rect()
		draw_rect(rect, Color(0.13, 0.76, 0.72, 0.13), true)
		draw_rect(rect, Color(0.22, 0.92, 0.82, 0.9), false, 2.0)


func _update_destination_indicators():
	var active_ids := {}
	for collider in _selected_che_colliders:
		if not is_instance_valid(collider):
			continue
		var movement = collider.get_meta("terminal_movement", null) as Node
		if movement == null or not bool(movement.call("is_moving")) or not collider.has_meta("terminal_destination"):
			continue
		var collider_id = collider.get_instance_id()
		active_ids[collider_id] = true
		var indicator = _destination_indicators.get(collider_id) as MeshInstance3D
		if not is_instance_valid(indicator):
			indicator = MeshInstance3D.new()
			indicator.name = "CheDestination%s" % collider_id
			indicator.cast_shadow = GeometryInstance3D.SHADOW_CASTING_SETTING_OFF
			_content.add_child(indicator)
			_destination_indicators[collider_id] = indicator
		var destination: Vector3 = collider.get_meta("terminal_destination")
		var start = collider.global_position + Vector3.UP * 0.25 * map_units_per_meter
		var finish = Vector3(destination.x, CHE_Y + 0.16 * map_units_per_meter, destination.z)
		var mesh = ImmediateMesh.new()
		mesh.surface_begin(Mesh.PRIMITIVE_LINES, _destination_material)
		mesh.surface_add_vertex(start)
		mesh.surface_add_vertex(finish)
		var radius = 2.4 * map_units_per_meter
		var segments = 32
		for index in range(segments):
			var angle_a = TAU * float(index) / float(segments)
			var angle_b = TAU * float(index + 1) / float(segments)
			mesh.surface_add_vertex(finish + Vector3(cos(angle_a) * radius, 0.0, sin(angle_a) * radius))
			mesh.surface_add_vertex(finish + Vector3(cos(angle_b) * radius, 0.0, sin(angle_b) * radius))
		mesh.surface_end()
		indicator.mesh = mesh
	for collider_id in _destination_indicators.keys():
		if active_ids.has(collider_id):
			continue
		var indicator = _destination_indicators[collider_id] as MeshInstance3D
		if is_instance_valid(indicator):
			indicator.queue_free()
		_destination_indicators.erase(collider_id)


func _canvas_to_viewport_position(canvas_position: Vector2) -> Vector2:
	var viewport_size = Vector2(_viewport.size)
	if size.x <= 0.0 or size.y <= 0.0 or viewport_size.x <= 0.0 or viewport_size.y <= 0.0:
		return canvas_position
	return canvas_position * viewport_size / size


func _viewport_to_canvas_position(viewport_position: Vector2) -> Vector2:
	var viewport_size = Vector2(_viewport.size)
	if viewport_size.x <= 0.0 or viewport_size.y <= 0.0:
		return viewport_position
	return viewport_position * size / viewport_size


func _selection_rect() -> Rect2:
	return Rect2(_selection_start, _selection_current - _selection_start).abs()


func _notification(what: int):
	if what == NOTIFICATION_MOUSE_ENTER:
		_mouse_inside = true
	elif what == NOTIFICATION_MOUSE_EXIT:
		_mouse_inside = false


func _zoom(factor: float):
	_camera_distance = clamp(_camera_distance * factor, _min_distance, _max_distance)
	_update_camera()


func _clamp_camera_target():
	var width = map_width_m * map_units_per_meter
	var height = map_height_m * map_units_per_meter
	_camera_target.x = clamp(_camera_target.x, 0.0, width)
	_camera_target.z = clamp(_camera_target.z, -height, 0.0)


func _update_camera():
	if _camera == null:
		return
	var horizontal = cos(_camera_pitch) * _camera_distance
	var offset = Vector3(
		sin(_camera_yaw) * horizontal,
		sin(_camera_pitch) * _camera_distance,
		cos(_camera_yaw) * horizontal
	)
	_camera.position = _camera_target + offset
	_camera.look_at(_camera_target, Vector3.UP)
	_camera.far = max(_max_distance * 3.0, 1000.0)


func _pick(screen_position: Vector2, additive := false):
	var viewport_position = _canvas_to_viewport_position(screen_position)
	var origin = _camera.project_ray_origin(viewport_position)
	var destination = origin + _camera.project_ray_normal(viewport_position) * _camera.far
	var query = PhysicsRayQueryParameters3D.create(origin, destination)
	query.collide_with_areas = true
	query.collide_with_bodies = true
	var hit = _world.get_world_3d().direct_space_state.intersect_ray(query)
	if hit.is_empty() or not hit.get("collider") is CollisionObject3D:
		_clear_selection()
		return
	var collider: CollisionObject3D = hit["collider"]
	if not collider.has_meta("terminal_data"):
		_clear_selection()
		return
	if collider.get_meta("terminal_kind", "") == "che":
		_toggle_che_selection(collider, additive)
	else:
		_select_collider(collider)


func _select_ches_in_rect(rect: Rect2, additive: bool):
	if not additive:
		_clear_selection(false)
	for child in _content.get_children():
		if not child is CollisionObject3D or child.get_meta("terminal_kind", "") != "che":
			continue
		var visual = child.get_meta("terminal_visual") as GeometryInstance3D
		if visual != null and not _camera.is_position_behind(visual.global_position):
			var screen_position = _camera.unproject_position(visual.global_position)
			if rect.has_point(screen_position) and child not in _selected_che_colliders:
				_selected_che_colliders.append(child)
				visual.material_overlay = _selection_material
	_emit_che_selection()


func _toggle_che_selection(collider: CollisionObject3D, additive: bool):
	if not additive:
		_clear_selection(false)
	if collider in _selected_che_colliders:
		var visual = collider.get_meta("terminal_visual") as GeometryInstance3D
		if visual != null:
			visual.material_overlay = null
		_selected_che_colliders.erase(collider)
	else:
		_selected_che_colliders.append(collider)
		var visual = collider.get_meta("terminal_visual") as GeometryInstance3D
		if visual != null:
			visual.material_overlay = _selection_material
	_emit_che_selection()


func _emit_che_selection():
	var selected = get_selected_che_data()
	che_selection_changed.emit(selected)
	if selected.size() == 1:
		selection_changed.emit("che", selected[0])
	elif selected.size() > 1:
		selection_changed.emit("che_group", {"count": selected.size(), "ches": selected})
	else:
		selection_changed.emit("", {})


func _select_collider(collider: CollisionObject3D):
	_clear_selection(false)
	_selected_collider = collider
	var visual = collider.get_meta("terminal_visual") as GeometryInstance3D
	if visual != null:
		visual.material_overlay = _selection_material
		if str(collider.get_meta("terminal_kind", "")) != "yard block":
			_show_selection_marker(visual)
	selection_changed.emit(str(collider.get_meta("terminal_kind")), collider.get_meta("terminal_data"))


func _show_selection_marker(visual: GeometryInstance3D):
	if _selection_marker == null or not visual is MeshInstance3D:
		return
	var bounds = visual.get_aabb()
	var center = visual.to_global(bounds.get_center())
	var bottom = visual.to_global(Vector3(bounds.get_center().x, bounds.position.y, bounds.get_center().z))
	var diameter = max(bounds.size.x, bounds.size.z) * 1.18
	_selection_marker.position = Vector3(center.x, bottom.y + 0.06, center.z)
	_selection_marker.scale = Vector3(diameter, diameter, diameter)
	_selection_marker.visible = true


func _clear_selection(emit_signal := true):
	for collider in _selected_che_colliders:
		if is_instance_valid(collider):
			var che_visual = collider.get_meta("terminal_visual") as GeometryInstance3D
			if che_visual != null:
				che_visual.material_overlay = null
	_selected_che_colliders.clear()
	if is_instance_valid(_selected_collider):
		var visual = _selected_collider.get_meta("terminal_visual") as GeometryInstance3D
		if visual != null:
			visual.material_overlay = null
	if is_instance_valid(_selection_marker):
		_selection_marker.visible = false
	_selected_collider = null
	if emit_signal:
		che_selection_changed.emit([])
		selection_changed.emit("", {})


func _material(color: Color, roughness: float, metallic: float) -> StandardMaterial3D:
	var material = StandardMaterial3D.new()
	material.albedo_color = color
	material.roughness = roughness
	material.metallic = metallic
	material.cull_mode = BaseMaterial3D.CULL_DISABLED
	if color.a < 1.0:
		material.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	return material


func _load_runtime_texture(path: String) -> Texture2D:
	if ResourceLoader.exists(path):
		var imported_texture = load(path) as Texture2D
		if imported_texture != null:
			return imported_texture
	var image = Image.load_from_file(path)
	if image == null or image.is_empty():
		push_warning("Could not load terminal texture: %s" % path)
		return null
	image.generate_mipmaps()
	return ImageTexture.create_from_image(image)


func _textured_material(texture: Texture2D, tint: Color, roughness: float, metallic: float) -> StandardMaterial3D:
	var material = _material(tint, roughness, metallic)
	if texture != null:
		material.albedo_texture = texture
		material.texture_filter = BaseMaterial3D.TEXTURE_FILTER_LINEAR_WITH_MIPMAPS_ANISOTROPIC
		material.texture_repeat = true
	return material


func _area_material(area_type: String) -> StandardMaterial3D:
	match area_type.to_lower():
		"water":
			return _textured_material(_water_texture, Color(0.72, 0.88, 0.94), 0.28, 0.08)
		"land":
			return _textured_material(_land_texture, Color(0.82, 0.82, 0.72), 0.96, 0.0)
		"road", "parking", "yard block":
			return _textured_material(_asphalt_texture, _area_color(area_type), 0.92, 0.0)
		_:
			return _textured_material(_concrete_texture, _area_color(area_type), 0.84, 0.02)


func _area_color(area_type: String) -> Color:
	match area_type.to_lower():
		"land": return Color(0.84, 0.86, 0.72)
		"yard": return Color(0.72, 0.79, 0.69)
		"yard block": return Color(0.70, 0.78, 0.52)
		"quay": return Color(0.70, 0.78, 0.84)
		"water": return Color(0.72, 0.88, 0.94)
		"terminal area": return Color(0.78, 0.72, 0.62)
		"building": return Color(0.72, 0.66, 0.58)
		"parking": return Color(0.60, 0.76, 0.70)
		_: return Color(0.76, 0.72, 0.66)


func _area_elevation(area_type: String) -> float:
	match area_type.to_lower():
		"water", "land": return 0.05
		"quay", "yard": return 0.14
		"yard block", "terminal area", "building", "parking": return 0.26
		_: return 0.20


func _che_color(code: String) -> Color:
	match code:
		"TT": return Color(0.95, 0.65, 0.16)
		"SC": return Color(0.18, 0.72, 0.65)
		"RTG", "ASC": return Color(0.45, 0.55, 0.92)
		"STS": return Color(0.86, 0.35, 0.42)
		_: return Color(0.82, 0.62, 0.24)


func _che_dimensions(data: Dictionary) -> Vector3:
	var code = str(data.get("equipment_type_code", "CHE")).to_upper()
	var details: Dictionary = data.get("equipment_type_details", {})
	var configured = Vector3(
		float(details.get("width_m", 0.0)),
		float(details.get("height_m", 0.0)),
		float(details.get("length_m", 0.0))
	)
	if configured.x > 0.0 and configured.y > 0.0 and configured.z > 0.0:
		return configured
	match code:
		"STS": return Vector3(14, 8, 7)
		"RTG", "ASC": return Vector3(8, 5, 12)
		"SC": return Vector3(5, 4, 10)
		_: return Vector3(3.2, 2.2, 6.5)


func _che_movement_profile(data: Dictionary) -> Dictionary:
	var code = str(data.get("equipment_type_code", "CHE")).to_upper()
	var details: Dictionary = data.get("equipment_type_details", {})
	var loaded = bool(data.get("twistlock_closed", false))
	var configured_speed_kmh = float(details.get(
		"driving_speed_full_load_kmh" if loaded else "driving_speed_empty_kmh", 0.0
	))
	var configured_speed_mps = configured_speed_kmh / 3.6
	var profile: Dictionary
	match code:
		"TT": profile = {"speed_mps": 7.0, "acceleration_mps2": 1.4, "turn_speed_deg_s": 55.0}
		"AVG": profile = {"speed_mps": 5.0, "acceleration_mps2": 1.0, "turn_speed_deg_s": 45.0}
		"SC", "AS": profile = {"speed_mps": 5.5, "acceleration_mps2": 0.8, "turn_speed_deg_s": 32.0}
		"RS", "TH", "EH", "FL": profile = {"speed_mps": 3.5, "acceleration_mps2": 0.7, "turn_speed_deg_s": 38.0}
		"RTG", "ARTG", "RGC", "RMG", "ARMG": profile = {"speed_mps": 1.5, "acceleration_mps2": 0.3, "turn_speed_deg_s": 12.0}
		"STS": profile = {"speed_mps": 0.75, "acceleration_mps2": 0.2, "turn_speed_deg_s": 8.0}
		_: profile = {"speed_mps": 4.0, "acceleration_mps2": 0.8, "turn_speed_deg_s": 35.0}
	if configured_speed_mps > 0.0:
		profile["speed_mps"] = configured_speed_mps
	profile["turning_radius_m"] = float(details.get("turning_radius_m", 0.0))
	profile["lifting_speed_empty_mps"] = float(details.get("lifting_speed_empty_m_per_min", 0.0)) / 60.0
	profile["lifting_speed_full_load_mps"] = float(details.get("lifting_speed_full_load_m_per_min", 0.0)) / 60.0
	profile["loaded"] = loaded
	return profile

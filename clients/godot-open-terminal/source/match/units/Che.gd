extends "res://source/match/units/Unit.gd"

# MiniTOS DB fields - Core Identity
var id: int = 0  # Primary key from Django
var che_name: String = ""
var equipment_type_id: int = 0
var equipment_type_name: String = ""
var equipment_type_code: String = ""  # TT, SC, RTG, ASC, STS

# TIC4.0 power/activity facts and MiniTOS operational assignment facts.
var powered_on: bool = false
var working: bool = false
var is_available: bool = false
var is_dispatched: bool = false
var is_in_maintenance: bool = false
var fuel_level: float = 100.0  # Percentage 0.0 - 100.0
var last_maintenance: String = "" # ISO8601 string or epoch ms

# Location and Position
var location_id: int = 0
var location_name: String = ""
var current_block_id: int = 0
var current_block_name: String = ""
var x_position: float = 0.0
var y_position: float = 0.0
var position_logical: String = "OUT OF BOUNDS"

# User and Assignment
var current_user_id: int = 0
var current_user_name: String = ""
var assigned_tasks = [] # Optional, for future

# Equipment State
var twistlock_closed: bool = false

# Resource management (inherited from Unit)
var resource_a = 0
var resource_b = 0
var resources_max = null

# Fuel management
var fuel_consumption_timer: Timer

# SparkPlugB constants
const DEVICE_ID_PREFIX = "CHE_"

var status_update_timer: Timer

func _ready():
	super._ready()
	
	# Connect the location detector Area3D to a handler function.
	# This assumes you have added an Area3D named "LocationDetector" to your CHE scene.
	if has_node("LocationDetector"):
		$LocationDetector.area_entered.connect(_on_location_detector_area_entered)
		$LocationDetector.area_exited.connect(_on_location_detector_area_exited)
	else:
		print("CHE is missing a 'LocationDetector' Area3D node.")

	# Initialize fuel consumption timer
	fuel_consumption_timer = Timer.new()
	fuel_consumption_timer.wait_time = 1.0
	fuel_consumption_timer.timeout.connect(_on_fuel_consumption_timer_timeout)
	add_child(fuel_consumption_timer)
	fuel_consumption_timer.start()
	
	_set_powered_on(false)
	
	#Create and connect timer for status updates
	_initialize_status_update_timer()

	# Update status when action changes
	action_changed.connect(func(new_action):
		_set_working(new_action != null)
		is_dispatched = new_action != null
		_update_visual_state()
	)

# Set action function for CHE unit
func _set_action(action_node):
	if new_action_allowed():
		if not is_inside_tree() or _action_locked:
			if action_node != null:
				action_node.queue_free()
			return
		_action_locked = true
		_teardown_current_action()
		action = action_node
		if action != null:
			var action_copy = action  # bind() performs copy itself, but lets force copy just in case
			action.tree_exited.connect(_on_action_node_tree_exited.bind(action_copy))
			add_child(action_node)
		_action_locked = false
		action_changed.emit(action)
		MatchSignals.unit_action_changed.emit(_get_mqtt_update_data())
	else:
		print("CHE %s cannot accept new assignments in its current state." % che_name)

# Validate if CHE can accept new assignments
func new_action_allowed() -> bool:
	return powered_on and is_available and not is_in_maintenance

# Update status update timer
func _update_status_update_timer():
	if not powered_on:
		_stop_status_update_timer()
	elif working:
		status_update_timer.wait_time = 1.0
		status_update_timer.start()
	else:
		status_update_timer.wait_time = 10.0
		status_update_timer.start()
		
func _initialize_status_update_timer():
	status_update_timer = Timer.new()
	status_update_timer.autostart = false
	status_update_timer.one_shot = false
	status_update_timer.timeout.connect(_on_status_update_timer_timeout)
	add_child(status_update_timer)

# Stop status update timer
func _stop_status_update_timer():
	if status_update_timer != null:
		status_update_timer.stop()
		
#	 Turn on the CHE
func _turn_on():
	_set_powered_on(true)
	_set_working(false)
	_emit_device_birth()
	_update_status_update_timer()

#	 Turn off the CHE
func _turn_off():
	_set_working(false)
	is_available = false
	_set_powered_on(false)
	_emit_device_death()
	_update_status_update_timer()

# Make the CHE available for assignments
func _make_available():
	if not powered_on or is_in_maintenance:
		print("Cannot make CHE available while Offline.")
		return
	else:
		is_available = true
		print("%s is now available for assignments." % che_name)

# Make the CHE unavailable for assignments
func _make_unavailable():
	if working:
		print("Cannot make CHE unavailable while Working.")
		return
	else:
		is_available = false
		print("%s is now available for assignments." % che_name)

func get_availability() -> bool:
	return is_available

func _set_powered_on(value: bool):
	if powered_on != value:
		powered_on = value
		_update_status_update_timer()
		_update_visual_state()


func _set_working(value: bool):
	if working != value:
		working = value
		_update_status_update_timer()
		_update_visual_state()


func get_status() -> String:
	if not powered_on:
		return "OF"
	return "WK" if working else "IL"


func _get_mqtt_client():
	# Adjust the path as needed for your project structure
	# This assumes MqttManager is a singleton or at /root/MqttManager with MQTT as a child
	if has_node("/root/MqttManager/MQTT"):
		return get_node("/root/MqttManager/MQTT")
	return null

func _emit_device_birth():
	MatchSignals.che_mqtt_birth.emit(che_name)

func _emit_device_data():
	var data = {
		"timestamp": Time.get_unix_time_from_system() * 1000,  # Convert to milliseconds
		"metrics": [
			{"name": "PoweredOn", "type": "Boolean", "value": powered_on},
			{"name": "Working", "type": "Boolean", "value": working},
			{"name": "IsAvailable", "type": "Boolean", "value": is_available},
			{"name": "IsDispatched", "type": "Boolean", "value": is_dispatched},
			{"name": "InMaintenance", "type": "Boolean", "value": is_in_maintenance},
			{
				"name": "FuelLevel",
				"type": "Float",
				"value": fuel_level
			},
			{
				"name": "CurrentBlockId",
				"type": "Int32",
				"value": current_block_id
			},
			{
				"name": "CurrentBlockName",
				"type": "String",
				"value": current_block_name
			},
			{
				"name": "CurrentUserId",
				"type": "Int32",
				"value": current_user_id
			},
			{
				"name": "CurrentUserName",
				"type": "String",
				"value": current_user_name
			},
			{
				"name": "PositionLogical",
				"type": "String",
				"value": position_logical
			},
			{
				"name": "Position",
				"type": "String",
				"value": JSON.stringify({
					"x": global_position.x,
					"y": global_position.y,
					"z": global_position.z
				})
			},
			{
				"name": "TwistlockClosed",
				"type": "Boolean",
				"value": twistlock_closed
			}
		]
	}
	
	if che_name != null:
		print("CHE %s update..." % che_name)
		MatchSignals.che_mqtt_data.emit(che_name, data)
	else:
		print("Error in CHE name: %s" % che_name)

func _emit_device_death():
	MatchSignals.che_mqtt_death.emit(che_name)

func _handle_unit_death():
	_emit_device_death()
	super._handle_unit_death()

func _on_fuel_consumption_timer_timeout():
	if powered_on and fuel_level > 0:
		if working:
			fuel_level -= 0.01
		else:
			fuel_level -= 0.001
		if fuel_level <= 0:
			_turn_off()

func _on_status_update_timer_timeout():
	_emit_device_data()

func initialize_from_data(che_data: Dictionary):
	# Set core identity properties
	id = che_data.get("pk", 0)
	che_name = che_data.get("name", "")
	equipment_type_id = che_data.get("equipment_type", 0)
	equipment_type_name = che_data.get("equipment_type_name", "")
	
	powered_on = bool(che_data.get("powered_on", false))
	working = bool(che_data.get("working", false))
	is_available = bool(che_data.get("is_available", false))
	is_dispatched = bool(che_data.get("is_dispatched", false))
	is_in_maintenance = bool(che_data.get("is_in_maintenance", false))
	fuel_level = che_data.get("fuel_level", 100)
	last_maintenance = "NA" #che_data.get("last_maintenance", "")
	
	# Set location and position
	location_id = che_data.get("location", 0)
	location_name = che_data.get("location_name", "")
	current_block_id = 0 #che_data.get("current_block", 0)
	current_block_name = "NA" #che_data.get("current_block_name", "")
	twistlock_closed = che_data.get("twistlock_closed", false)
	
	# Set user information
	current_user_id = 0 #che_data.get("current_user", 0)
	current_user_name = "NA" #che_data.get("current_user_name", "")
	
	# Set position if provided
	if "x_position" in che_data and "y_position" in che_data:
		x_position = che_data.x_position
		y_position = che_data.y_position
		global_position = Vector3(x_position, 0, y_position)
	
	_update_visual_state()
	if powered_on:
		_emit_device_birth()

func _update_visual_state():
	var status_color = Color.WHITE  # default
	if is_in_maintenance:
		status_color = Color.YELLOW
	elif not powered_on:
		status_color = Color.GRAY
	elif working:
		status_color = Color.BLUE
	elif is_available:
		status_color = Color.GREEN
	
	# Apply the color to the geometry
	var _color_material = StandardMaterial3D.new()
	_color_material.vertex_color_use_as_albedo = true
	_color_material.albedo_color = status_color
	_color_material.metallic = 1

	Utils.Match.traverse_node_tree_and_replace_materials_matching_albedo(
		find_child("Geometry"),
		MATERIAL_ALBEDO_TO_REPLACE,
		MATERIAL_ALBEDO_TO_REPLACE_EPSILON,
		_color_material
	)

func _on_location_detector_area_entered(area: Area3D):
	# Check if the area we entered is a Pile by seeing if it has the 'logical_name' property.
	if "logical_name" in area:
		self.position_logical = area.logical_name
		print(che_name + " entered pile: " + self.position_logical)

func _on_location_detector_area_exited(area: Area3D):
	# When we leave a pile, we can either clear the logical position
	# or just wait until we enter a new one.
	if "logical_name" in area and area.logical_name == self.position_logical:
		self.position_logical = "IN TRANSIT"
		print(che_name + " exited pile. Now in transit.")

func is_full():
	assert(resource_a + resource_b <= resources_max, "worker capacity was exceeded somehow")
	return resource_a + resource_b == resources_max

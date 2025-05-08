extends "res://source/match/units/Unit.gd"

# MiniTOS DB fields
var status: String = "OF"  # Default to Offline
var equipment_type_name: String = ""
var current_block_name: String = ""
var twistlock_closed: bool = false

# Resource management
var resource_a = 0
var resource_b = 0
var resources_max = null

# Fuel management
var fuel_level: int = 100
var fuel_consumption_timer: Timer

# SparkPlugB constants
const SPARKPLUG_NAMESPACE = "spBv1_0"
const GROUP_ID = "OPEN-TERMINAL-SIM"
const NODE_ID = "MQTTClient"
const DEVICE_ID_PREFIX = "CHE_"

func _ready():
	super._ready()
	# Initialize fuel consumption timer
	fuel_consumption_timer = Timer.new()
	fuel_consumption_timer.wait_time = 1.0
	fuel_consumption_timer.timeout.connect(_on_fuel_consumption_timer_timeout)
	add_child(fuel_consumption_timer)
	fuel_consumption_timer.start()
	
	# Set initial status to Idle
	_set_status("IL")
	
	# Update status when action changes
	action_changed.connect(func(new_action): 
		_set_status("WK" if new_action != null else "IL")
		_update_visual_state()
	)
	
	# Emit DBIRTH message
	_emit_device_birth()

func _set_status(new_status: String):
	if status != new_status:
		status = new_status
		_update_visual_state()
		_emit_device_data()

func _emit_device_birth():
	var birth_data = {
		"timestamp": Time.get_unix_time_from_system() * 1000,  # Convert to milliseconds
		"metrics": [
			{
				"name": "Status",
				"type": "String",
				"value": status
			},
			{
				"name": "FuelLevel",
				"type": "Int32",
				"value": fuel_level
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
	
	var topic = "%s/%s/%s/DBIRTH/%s" % [SPARKPLUG_NAMESPACE, GROUP_ID, NODE_ID, DEVICE_ID_PREFIX + name]
	MatchSignals.unit_action_changed.emit(topic, JSON.stringify(birth_data), true)

func _emit_device_data():
	var data = {
		"timestamp": Time.get_unix_time_from_system() * 1000,  # Convert to milliseconds
		"metrics": [
			{
				"name": "Status",
				"type": "String",
				"value": status
			},
			{
				"name": "FuelLevel",
				"type": "Int32",
				"value": fuel_level
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
	
	var topic = "%s/%s/%s/DDATA/%s" % [SPARKPLUG_NAMESPACE, GROUP_ID, NODE_ID, DEVICE_ID_PREFIX + name]
	MatchSignals.unit_action_changed.emit(topic, JSON.stringify(data), false)

func _emit_device_death():
	var death_data = {
		"timestamp": Time.get_unix_time_from_system() * 1000,  # Convert to milliseconds
	}
	
	var topic = "%s/%s/%s/DDEATH/%s" % [SPARKPLUG_NAMESPACE, GROUP_ID, NODE_ID, DEVICE_ID_PREFIX + name]
	MatchSignals.unit_action_changed.emit(topic, JSON.stringify(death_data), true)

func _handle_unit_death():
	_emit_device_death()
	super._handle_unit_death()

func _on_fuel_consumption_timer_timeout():
	#Fuel is consumed if the che is Working or Idle and the fuel is above 0.
	if fuel_level > 0:
		if status == "WK":
			fuel_level -= 2
		elif status == "IL":
			fuel_level -= 1
		if fuel_level <= 0:
			_set_status("OF")  # Set to Offline when out of fuel
			_update_visual_state()
			_emit_device_data()

func initialize_from_data(che_data: Dictionary):
	# Set basic properties
	name = che_data.get("name", "")
	status = che_data.get("status", "IL")  # Default to Idle instead of Offline
	equipment_type_name = che_data.get("equipment_type_name", "")
	twistlock_closed = che_data.get("twistlock_closed", false)
	
	# Set position if provided
	if "x_position" in che_data and "y_position" in che_data:
		global_position = Vector3(che_data.x_position, 0, che_data.y_position)
	
	# Update visual state based on status
	_update_visual_state()

func _update_visual_state():
	# Update the CHE's appearance based on its status
	match status:
		"IL":  # Idle
			# Set idle animation or state
			pass
		"WK":  # Working
			# Set working animation or state
			pass
		"OF":  # Offline
			# Set offline animation or state
			pass
		"BD":  # Breakdown
			# Set breakdown animation or state
			pass
		"MA":  # In Maintenance
			# Set maintenance animation or state
			pass

func is_full():
	assert(resource_a + resource_b <= resources_max, "worker capacity was exceeded somehow")
	return resource_a + resource_b == resources_max

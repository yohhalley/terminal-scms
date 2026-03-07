extends "res://source/match/units/Unit.gd"

var id: int = 0  # Primary key from Django
var category = ""
var category_display = ""
var container_number = ""
var container_number_name = ""
var ib_carrier = 0
var ib_carrier_name = ""
var ob_carrier = 0
var ob_carrier_name = ""
var location = 0
var location_name = ""
var eta = ""
var etd = ""
var weight = 0
var pod = 0
var pod_name = ""
var pod2 = 0
var pod2_name = ""
var requires_power = false
var is_imdg = false
var x_position = 0.0
var y_position = 0.0
var z_position = 0.0

func _ready():
	super._ready()

func initialize_from_data(container_visit_data: Dictionary):
	# Set core identity properties
	id = container_visit_data.get("pk", 0)
	category = container_visit_data.get("category", "")
	category_display = container_visit_data.get("category_display", "")
	container_number = container_visit_data.get("container_number", "")
	container_number_name = container_visit_data.get("container_number_name", "")
	ib_carrier = container_visit_data.get("ib_carrier", 0)
	ib_carrier_name = container_visit_data.get("ib_carrier_name", "")
	ob_carrier = container_visit_data.get("ob_carrier", 0)
	ob_carrier_name = container_visit_data.get("ob_carrier_name", "")
	location = container_visit_data.get("location", 0)
	location_name = container_visit_data.get("location_name", "")
	eta = container_visit_data.get("eta", "")
	etd = container_visit_data.get("etd", "")
	weight = container_visit_data.get("weight", 0)
	pod = container_visit_data.get("pod", 0)
	pod_name = container_visit_data.get("pod_name", "")
	pod2 = container_visit_data.get("pod2", 0)
	pod2_name = container_visit_data.get("pod2_name", "")
	requires_power = container_visit_data.get("requires_power", false)
	is_imdg = container_visit_data.get("is_imdg", false)
	
	# Set position if provided
	if "x_position" in container_visit_data and "y_position" and "z_position" in container_visit_data:
		x_position = container_visit_data.x_position
		y_position = container_visit_data.y_position
		global_position = Vector3(x_position, z_position, y_position)

	# Update visual state based on status
	#_update_visual_state()
	#if location == parent.get_current_location_id():
	#	pass # Additional logic if needed when at current location



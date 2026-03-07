extends Node

# A zoom level below this threshold is considered "zoomed in".
const PILE_VISIBILITY_ZOOM_THRESHOLD = 15.0

var _piles = []
var _camera = null

func _ready():
	# Wait a frame for the scene tree to be fully ready, then find the camera.
	call_deferred("_initialize")

func _initialize():
	# Find the camera in the main scene and connect to its signal.
	_camera = get_tree().get_root().find_child("IsometricCamera3D", true, false)
	if _camera and _camera.has_signal("zoom_level_changed"):
		_camera.zoom_level_changed.connect(_on_camera_zoom_changed)
		# Set initial visibility based on the camera's starting zoom.
		_on_camera_zoom_changed(_camera.size)
	else:
		printerr("VisibilityManager: Could not find IsometricCamera3D or its zoom_level_changed signal.")

# Called by YardManager for each pile it creates.
func register_pile(pile_node):
	_piles.append(pile_node)
	# Set initial visibility when the pile is registered.
	if _camera:
		pile_node.visible = _camera.size < PILE_VISIBILITY_ZOOM_THRESHOLD

func _on_camera_zoom_changed(new_zoom_level):
	var is_visible = new_zoom_level < PILE_VISIBILITY_ZOOM_THRESHOLD
	for pile in _piles:
		if is_instance_valid(pile):
			pile.visible = is_visible
		else:
			# The pile might have been destroyed, but we can ignore that.
			pass 
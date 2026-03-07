extends Node3D

const MatchSettings = preload("res://source/data-model/MatchSettings.gd")
const PlayerSettings = preload("res://source/data-model/PlayerSettings.gd")

const Unit = preload("res://source/match/units/Unit.gd")
const Structure = preload("res://source/match/units/Structure.gd")
const Player = preload("res://source/match/players/Player.gd")
const Human = preload("res://source/match/players/human/Human.gd")

const CommandCenter = preload("res://source/match/units/CommandCenter.tscn")
const ContainerUnit = preload("res://source/match/units/ContainerUnit.tscn")

const Drone = preload("res://source/match/units/Drone.tscn")
const Che = preload("res://source/match/units/Che.tscn")
const Worker = preload("res://source/match/units/Worker.tscn")

@export var settings: Resource = null

var map:
	set = _set_map,
	get = _get_map
var visible_player = null:
	set = _set_visible_player
var visible_players = null:
	set = _ignore,
	get = _get_visible_players

@onready var navigation = $Navigation
@onready var fog_of_war = $FogOfWar

@onready var _camera = $IsometricCamera3D
@onready var _players = $Players
@onready var _agents = $Players
@onready var _terrain = $Terrain
@onready var _mqtt_manager = $MqttManager
@onready var _http_request_manager = $HTTPRequestManager
@onready var _yard_manager = $YardManager


const SPARKPLUG_NAMESPACE = "spBv1.0"
const GROUP_ID = "LOCAL-GROUP"
const NODE_ID = "GODOT-SIM"

func _ready():
	assert(settings != null, "match cannot start without settings, see examples in tests/manual/")
	assert(map != null, "match cannot start without map, see examples in tests/manual/")
	assert(_mqtt_manager != null, "match cannot start without MQTT broker ready to connect")

	# Initialize MQTT connection with authentication
	print("Connecting to broker_connected signal")
	_mqtt_manager._connect_mqtt_signals()
	_mqtt_manager.connect_to_broker()
	# Connect to MQTT broker with local credentials
	#_mqtt_manager._connect_to_broker(
	#	"127.0.0.1",      # broker address
	#	1883,             # broker port
	#	GROUP_ID,         # group id
	#	NODE_ID,          # node id
	#	"godot_client",   # username
	#	"password"        # password
	#)
	MatchSignals.mqtt_spv1_broker_connected_init.connect(_continue_match_setup)
	
	#Connect signals for HTTP responses
	MatchSignals.che_list_received.connect(_on_che_list_received)
	MatchSignals.block_list_received.connect(_on_block_list_received)
	MatchSignals.container_visits_list_received.connect(_on_container_visit_received)
	MatchSignals.selected_location_id_update.connect(_request_tos_initialization_data)

	#HTTP requests for initialization.
	_setup_subsystems_dependent_on_map()

func _continue_match_setup():
	_setup_agents()
	#_setup_player_units()
	visible_player = get_tree().get_nodes_in_group("agents")
	#TODO: Replace the fixed value of 6 for a dynamically selected one...
	_request_tos_initialization_data("6")
	_move_camera_to_initial_position()
	
	if settings.visibility == settings.Visibility.FULL:
		fog_of_war.reveal()
	
func _request_tos_initialization_data(selected_location_id):
	_http_request_manager.RequestCheList(selected_location_id)
	_http_request_manager.RequestBlockList(selected_location_id)
	_http_request_manager.RequestContainerVisitList(selected_location_id)

func _on_che_list_received(che_data):
	print("Che data received from TOS.")
	# Parse the text data into JSON
	var json = JSON.new()
	var error = json.parse(che_data)
	if error == OK:
		var parsed_data = json.get_data()
		_setup_location_che(parsed_data)
	else:
		print("Error parsing CHE data: ", json.get_error_message())

func _setup_location_che(che_data):
	# Handle both direct array and results-wrapped responses
	var che_list: Array
	
	if che_data is Dictionary and che_data.has("results"):
		# API returned paginated response with results wrapper
		che_list = che_data["results"]
		if not che_list is Array:
			print("Invalid CHE list data in results field")
			return
	elif che_data is Array:
		# API returned direct array
		che_list = che_data
	elif che_data is Dictionary:
		# Single CHE object, wrap in array
		che_list = [che_data]
	else:
		print("Invalid CHE data format received")
		return
	
	# Ensure we have valid data
	if che_list.is_empty():
		print("No CHE available in Selected Location.")
		return
	
	# Create a container for CHE units if it doesn't exist
	var human_agent = find_child("Human")
	if not human_agent:
		human_agent = Node3D.new()
		human_agent.name = "Human"
	
	add_child(human_agent)
	
	# Get the human player for CHE ownership
	var human_player = _get_human_player()
	if not human_player:
		print("No human player found for CHE ownership")
		return
	human_agent.add_child(human_player)
	
	# Create CHE instances for each CHE in the list
	for che in che_list:
		# Create a new CHE instance
		var che_instance = Che.instantiate()

		# Set up the CHE's transform based on its position
		var che_transform = Transform3D()
		if "x_position" in che and "y_position" in che:
			che_transform = che_transform.translated(Vector3(che.x_position, 0, che.y_position))
		else:
			# If no position data, place at a default position
			che_transform = che_transform.translated(Vector3(0, 0, 0))
		
		# Set up the CHE with the transform and player
		_setup_and_spawn_unit(che_instance, che_transform, human_player, false)
		
		# Initialize the CHE with miniTOS data
		che_instance.initialize_from_data(che)
		# Add the CHE to the container
		human_player.add_child(che_instance)
		# Emit signal for CHE creation
		MatchSignals.unit_spawned.emit(che_instance)
		print("Created CHE: ", che_instance.che_name, " at position: ", che_instance.global_position)
		
	#che_container.add_child(human_player)

func _on_block_list_received(block_data):
	# Parse the text data into a Godot Dictionary/Array
	var json = JSON.new()
	var error = json.parse(block_data)
	if error != OK:
		print("Error parsing Block list data: ", json.get_error_message())
		return
	
	var parsed_data = json.get_data()
	
	# Handle both direct array and results-wrapped responses
	var block_list: Array
	
	if parsed_data is Dictionary and parsed_data.has("results"):
		# API returned paginated response with results wrapper
		block_list = parsed_data["results"]
		if not block_list is Array:
			printerr("Block data 'results' field is not an array.")
			return
	elif parsed_data is Array:
		# API returned direct array
		block_list = parsed_data
	elif parsed_data is Dictionary:
		# Single block object, wrap in array
		block_list = [parsed_data]
	else:
		printerr("Received block data is not in a recognized format.")
		return

	# Create a container node for the yard visuals if it doesn't exist
	var yard_container = find_child("Yard")
	if not yard_container:
		yard_container = Node3D.new()
		yard_container.name = "Yard"
		add_child(yard_container)
	
	# Call the YardManager to build the yard layout
	_yard_manager.build_yard(block_list, yard_container)

func _on_container_visit_received(container_data):
	var json = JSON.new()
	var error = json.parse(container_data)
	if error != OK:
		printerr("Error parsing Container Visit data: ", json.get_error_message())
		return
	
	var parsed_data = json.get_data()
	
	# Handle both direct array and results-wrapped responses
	var visit_list: Array
	
	if parsed_data is Dictionary and parsed_data.has("results"):
		# API returned paginated response with results wrapper
		visit_list = parsed_data["results"]
		if not visit_list is Array:
			printerr("Container Visit 'results' field is not an array.")
			return
	elif parsed_data is Array:
		# API returned direct array
		visit_list = parsed_data
	elif parsed_data is Dictionary:
		# Single container visit object, wrap in array
		visit_list = [parsed_data]
	else:
		printerr("Container visit data is not in a recognized format.")
		return
		
	# Create a container node for the container objects if it doesn't exist
	var container_parent = find_child("Containers")
	if not container_parent:
		container_parent = Node3D.new()
		container_parent.name = "Containers"
		add_child(container_parent)
	
	# Get the current location name from settings
	var current_location_name = "Terminal Velocity" #settings.get("location_name", "Terminal Velocity")
	if not current_location_name:
		printerr("Match.gd: 'location_name' not found in settings.")
		return

	var filtered_visits = []
	for visit in visit_list:
		if visit.get("location_name") == current_location_name:
			filtered_visits.append(visit)
		else:
			print("Container '%s' skipped: location '%s' does not match current location '%s'." % [
				visit.get("container_number_name", "Unknown"),
				visit.get("location_name", "N/A"),
				current_location_name
			])
	
	# Call the manager to build the container objects
	_yard_manager.build_container_inventory(filtered_visits, container_parent)

func _set_map(a_map):
	assert(get_node_or_null("Map") == null, "map already set")
	a_map.name = "Map"
	add_child(a_map)
	a_map.owner = self


func _ignore(_value):
	pass


func _get_map():
	return get_node_or_null("Map")


func _set_visible_player(player):
	_conceal_player_units(visible_player)
	_reveal_player_units(player)
	visible_player = player
	# visible_players.add_child(visible_player)


func _get_visible_players():
	if settings.visibility == settings.Visibility.PER_PLAYER:
		return [visible_player]
	return get_tree().get_nodes_in_group("agents")[settings.human_agent]


func _setup_subsystems_dependent_on_map():
	_terrain.update_shape(map.find_child("Terrain").mesh)
	fog_of_war.resize(map.size)
	_recalculate_camera_bounding_planes(map.size)
	navigation.setup(map)


func _recalculate_camera_bounding_planes(map_size: Vector2):
	_camera.bounding_planes[1] = Plane(-1, 0, 0, -map_size.x)
	_camera.bounding_planes[3] = Plane(0, 0, -1, -map_size.y)
	

func _setup_agents():
	assert(
		_agents.get_children().is_empty() or settings.agents.is_empty(),
		"Agents can be defined either in settings or in scene tree, not in both"
	)
	if _agents.get_children().is_empty():
		# _create_players_from_settings()
		_create_agents_from_settings()

	for node in _agents.get_children():
		if node is Player:
			node.add_to_group("agents")


func _create_agents_from_settings():
	for agent_settings in settings.agents:
		var agent_scene = Constants.Match.Player.CONTROLLER_SCENES[agent_settings.controller]
		var agent = agent_scene.instantiate()
		agent.color = agent_settings.color
		if agent_settings.spawn_index_offset > 0:
			for _i in range(agent_settings.spawn_index_offset):
				_agents.add_child(Node.new())
		_agents.add_child(agent)

func _setup_player_units():
	for player in _players.get_children():
		if not player is Player:
			continue
		var player_index = player.get_index()
		var predefined_units = player.get_children().filter(func(child): return child is Unit)
		if not predefined_units.is_empty():
			predefined_units.map(func(unit): _setup_unit_groups(unit, unit.player))
		else:
			_spawn_player_units(
				player, map.find_child("SpawnPoints").get_child(player_index).global_transform
			)
	
func _spawn_player_units(player, spawn_transform):
	#_setup_and_spawn_unit(CommandCenter.instantiate(), spawn_transform, player, false)
	#_setup_and_spawn_unit(
	#	Che.instantiate(), spawn_transform.translated(Vector3(-2, 0, 3)), player
	#)
	#_setup_and_spawn_unit(
	#	Che.instantiate(), spawn_transform.translated(Vector3(-3, 0, 3)), player
	#)
	#_setup_and_spawn_unit(
	#	Che.instantiate(), spawn_transform.translated(Vector3(3, 0, 3)), player
	#)
	pass


func _setup_and_spawn_unit(unit, a_transform, player, mark_structure_under_construction = true):
	unit.global_transform = a_transform
	if unit is Structure and mark_structure_under_construction:
		unit.mark_as_under_construction()
	_setup_unit_groups(unit, player)
	player.add_child(unit)
	MatchSignals.unit_spawned.emit(unit)


func _setup_unit_groups(unit, player):
	unit.add_to_group("units")
	if player == _get_human_player():
		unit.add_to_group("controlled_units")
	else:
		unit.add_to_group("adversary_units")
	if player == _get_visible_players():
		unit.add_to_group("revealed_units")


func _get_human_player():
	var tmp = get_tree()
	var human_players = get_tree().get_nodes_in_group("agents").filter(
		func(player): return player is Human
	)
	assert(human_players.size() <= 1, "more than one human player is not allowed")
	if not human_players.is_empty():
		return human_players[0]
	return null

func _move_camera_to_initial_position():
	var human_player = _get_human_player()
	if human_player != null:
		_move_camera_to_player_units_crowd_pivot(human_player)
	else:
		_move_camera_to_player_units_crowd_pivot(get_tree().get_nodes_in_group("agents")[settings.human_agent])


func _move_camera_to_player_units_crowd_pivot(player):
	var player_units = get_tree().get_nodes_in_group("units").filter(
		func(unit): return unit.player == player
	)
	
	if player_units.is_empty():
		# If no units, center the camera on the map
		var center_position = Vector3(100 / 2.0, 0, 100 / 2.0)
		_camera.set_position_safely(center_position)
	else:
		# If units exist, center on their crowd pivot
		var crowd_pivot = Utils.Match.Unit.Movement.calculate_aabb_crowd_pivot_yless(player_units)
		_camera.set_position_safely(crowd_pivot)


func _reveal_player_units(player):
	if player == null:
		return
	for unit in get_tree().get_nodes_in_group("units").filter(
		func(a_unit): return a_unit.player == player
	):
		unit.add_to_group("revealed_units")


func _conceal_player_units(player):
	if player == null:
		return
	for unit in get_tree().get_nodes_in_group("units").filter(
		func(a_unit): return a_unit.player == player
	):
		unit.remove_from_group("revealed_units")

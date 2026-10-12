extends Control

const MatchSettings = preload("res://source/data-model/MatchSettings.gd")
const PlayerSettings = preload("res://source/data-model/PlayerSettings.gd")
const LoadingScene = preload("res://source/main-menu/Loading.tscn")
const mqttexample = preload("res://mqttexample.tscn")

func _ready():
	call_deferred("quick_start")
	#_logos.tree_exited.connect(
		#get_tree().change_scene_to_file.bind("res://source/main-menu/Main.tscn")
	#)
	
func quick_start():
	#var new_scene = mqttexample.instantiate()
	var new_scene = LoadingScene.instantiate()
	new_scene.match_settings = _create_match_settings()
	new_scene.map_path = _get_selected_map_path()
	get_parent().add_child(new_scene)
	get_tree().current_scene = new_scene
	queue_free()

func _get_selected_map_path():
	return "res://source/match/maps/TerminalVelocityMap.tscn"

func _create_match_settings():
	var match_settings = MatchSettings.new()
	var spawn_index_offset = 0
	var agent_settings = PlayerSettings.new()
	agent_settings.controller = Constants.PlayerType.HUMAN
	agent_settings.color = Constants.Player.COLORS[0]
	agent_settings.spawn_index_offset = spawn_index_offset
	match_settings.agents.append(agent_settings)
	spawn_index_offset = 0
	match_settings.visible_player = 0
	match_settings.visibility = match_settings.Visibility.FULL
	return match_settings

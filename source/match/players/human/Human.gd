extends "res://source/match/players/Player.gd"

func _ready():
	add_to_group("human_player")
	if not MatchSignals.is_connected("terrain_targeted", Callable(self, "on_terrain_targeted")):
		MatchSignals.terrain_targeted.connect(on_terrain_targeted)
	print("Human.gd: Ready and connected to terrain_targeted.")

#func handle_unit_selection(unit, is_shift_select):
#	if not is_shift_select:
#		clear_selection()
#	if unit.is_in_group("selected_units"):
#		unit.remove_from_group("selected_units")
#		MatchSignals.unit_deselected.emit(unit)
#	else:
#		unit.add_to_group("selected_units")
#		MatchSignals.unit_selected.emit(unit)

#func clear_selection():
#	for unit_in_group in get_tree().get_nodes_in_group("selected_units"):
#		unit_in_group.remove_from_group("selected_units")
#		MatchSignals.unit_deselected.emit(unit_in_group)

func on_terrain_targeted(_position):
	print("on_terrain_targeted called. Shift held:", Input.is_key_pressed(KEY_SHIFT))
	if not Input.is_key_pressed(KEY_SHIFT):
		#clear_selection()
		pass

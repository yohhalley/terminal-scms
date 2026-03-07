extends PanelContainer

const VehicleFactory = preload("res://source/match/units/VehicleFactory.gd")
const AircraftFactory = preload("res://source/match/units/AircraftFactory.gd")
const CommandCenter = preload("res://source/match/units/CommandCenter.gd")
const Worker = preload("res://source/match/units/Worker.gd")
const Che = preload("res://source/match/units/Che.gd")
const ContainerUnit = preload("res://source/match/units/ContainerUnit.gd")

@onready var _generic_menu = find_child("GenericMenu")
@onready var _command_center_menu = find_child("CommandCenterMenu")
@onready var _vehicle_factory_menu = find_child("VehicleFactoryMenu")
@onready var _aircraft_factory_menu = find_child("AircraftFactoryMenu")
@onready var _worker_menu = find_child("WorkerMenu")
@onready var _che_menu = find_child("CheMenu")
@onready var _container_unit_menu = find_child("ContainerUnitMenu")
@onready var _selection_menu = get_parent().get_parent().get_parent().find_child("MarginContainer4").find_child("SelectionMenu")

@onready var che_units = []
@onready var container_units = []

func _ready():
	_reset_menus()
	MatchSignals.unit_selected.connect(func(_unit): _reset_menus())
	MatchSignals.unit_deselected.connect(func(_unit): _reset_menus())
	MatchSignals.unit_died.connect(func(_unit): _reset_menus())


func _reset_menus():
	_hide_all_menus()
	if _try_showing_any_menu():
		show()
	else:
		hide()

func _hide_all_menus():
	_generic_menu.hide()
	_command_center_menu.hide()
	_vehicle_factory_menu.hide()
	_aircraft_factory_menu.hide()
	_worker_menu.hide()
	_che_menu.hide()
	_container_unit_menu.hide()
	_selection_menu.hide()


func _try_showing_any_menu():
	var selected_controlled_units = get_tree().get_nodes_in_group("selected_units").filter(
		func(unit): return unit.is_in_group("controlled_units")
	)

	var has_che_in_selection = false
	var has_container_in_selection = false

	che_units = []
	container_units = []
	for unit in selected_controlled_units:
		if unit is Che:
			has_che_in_selection = true
			print("Che selected: ", unit.name)
			che_units.append(unit)
		elif unit is ContainerUnit:
			has_container_in_selection = true
			print("Container unit selected: ", unit.name)
			container_units.append(unit)

	if has_che_in_selection:
		_che_menu.show()
		_selection_menu.show()
		_selection_menu._update_display(che_units)
		return true

	if has_container_in_selection:
		_container_unit_menu.show()
		return true

	# If no CHEs are selected, fall back to single-selection logic for other units.
	if selected_controlled_units.size() == 1:
		var unit = selected_controlled_units[0]
		if unit is Worker:
			_worker_menu.show()
			return true
		elif unit is CommandCenter:
			_command_center_menu.show()
			return true
		elif unit is VehicleFactory:
			_vehicle_factory_menu.show()
			return true
		elif unit is AircraftFactory:
			_aircraft_factory_menu.show()
			return true
		else:
			_generic_menu.show()
			return true

	return false

func get_che_units():
	return che_units

func get_container_units():
	return container_units

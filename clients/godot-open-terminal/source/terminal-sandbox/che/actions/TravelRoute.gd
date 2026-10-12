extends Node

var _steps: Array = []
var _index := 0
var _unit: Node


func _init(steps: Array):
	_steps = steps


func configure(unit: Node):
	_unit = unit


func tick(delta: float):
	if _unit == null or not is_instance_valid(_unit):
		queue_free()
		return
	if _index >= _steps.size():
		_unit.call("_finish_action", self)
		return
	var step: Dictionary = _steps[_index]
	_unit.call("set_active_rail", int(step.axis_id))
	var arrived: bool
	if int(step.axis_id) != 0:
		arrived = bool(_unit.call("_drive_rail_towards", step.position, step.heading, delta))
	else:
		arrived = bool(_unit.call("_drive_towards", step.position, delta))
	if arrived:
		_index += 1
		if _index >= _steps.size():
			_unit.call("_finish_action", self)

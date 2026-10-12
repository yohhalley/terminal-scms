extends Node

var _target_position := Vector3.ZERO
var _unit: Node = null


func _init(target_position: Vector3):
	_target_position = target_position


func configure(unit: Node):
	_unit = unit


func tick(delta: float):
	if _unit == null or not is_instance_valid(_unit):
		queue_free()
		return
	if bool(_unit.call("_drive_towards", _target_position, delta)):
		_unit.call("_finish_action", self)


func target_position() -> Vector3:
	return _target_position

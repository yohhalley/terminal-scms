extends GridContainer

const Che = preload("res://source/match/units/Che.gd")

var units = []

func _ready():
	if columns == 0:
		columns = 3

func _update_display(che_units):
	units = che_units
	if units.size() == 0:
		visible = false
		return
	visible = true
	_populate_che_grid()

func _populate_che_grid():
	# Remove all previous cards
	for child in get_children():
		child.queue_free()
	# Add a card for each CHE
	for che in units:
		var card = Button.new()
		card.text = "%s\nStatus: %s\nFuel: %d%%" % [che.che_name, _get_status_display_name(che.get_status()), che.fuel_level]
		card.size_flags_horizontal = SIZE_EXPAND_FILL
		card.size_flags_vertical = SIZE_EXPAND_FILL
		add_child(card)

func _get_status_display_name(status: String) -> String:
	match status:
		"IL": return "Idle"
		"WK": return "Working"
		"OF": return "Offline"
		"BD": return "Breakdown"
		"MA": return "In Maintenance"
		_: return status

func _on_cancel_action_button_pressed():
	for unit in units:
		unit.action = null 

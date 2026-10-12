extends GridContainer

@onready var _on_che_toggle_on_off = find_child("ToggleOnOffButton")
@onready var _on_che_toggle_available_unavailable = find_child("ToggleAvailableButton")
@onready var _cancel_order = find_child("CancelOrderButton")
@onready var che_units = []

func _ready():
	_on_che_toggle_on_off.tooltip_text = tr("TOGGLE_CHE_ON_OFF_TOOLTIP")
	_on_che_toggle_available_unavailable.tooltip_text = tr("TOGGLE_CHE_AVAILABLE_UNAVAILABLE_TOOLTIP")
	_cancel_order.tooltip_text = tr("CANCEL_CHE_ORDERS_TOOLTIP")
	che_units = get_parent().get_parent().get_che_units()
	

func _on_cancel_order_button_pressed():
	pass # Replace with function body.

func _on_toggle_available_button_pressed():
	che_units = get_parent().get_parent().get_che_units()
	for che_unit in che_units:
		if che_unit.get_availability():
			che_unit._make_unavailable()
		else:
			che_unit._make_available()

	#update button text if availability was changed
	for che_unit in che_units:
		if che_unit.get_availability():
			_on_che_toggle_available_unavailable.text = tr("Make Unavailable")
		else:
			_on_che_toggle_available_unavailable.text = tr("Make Available")

func _on_toggle_on_off_button_pressed():
	che_units = get_parent().get_parent().get_che_units()
	for che_unit in che_units:
		if che_unit.get_status() == "OF":
			che_unit._turn_on()
		else:
			if che_unit.get_status() == "WK":
				print("Cannot turn off CHE while it is Working.")
			else:
				che_unit._turn_off()

	# update button text if turn on/off was successful
	for che_unit in che_units:
		if che_unit.get_status() == "OF":
			_on_che_toggle_on_off.text = tr("Turn On")
		else:
			_on_che_toggle_on_off.text = tr("Turn Off")

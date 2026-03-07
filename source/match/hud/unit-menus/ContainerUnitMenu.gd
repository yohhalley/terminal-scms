extends GridContainer

@onready var _create_new_order_button = find_child("CreateNewOrderButton")
@onready var _bypass_existing_order_button = find_child("BypassExistingOrderButton")
@onready var _cancel_existing_order_button = find_child("CancelExistingOrderButton")
@onready var container_units = []

func _ready():
	_create_new_order_button.tooltip_text = tr("CREATE_NEW_ORDER_TOOLTIP")
	_bypass_existing_order_button.tooltip_text = tr("BYPASS_EXISTING_ORDER_TOOLTIP")
	_cancel_existing_order_button.tooltip_text = tr("CANCEL_EXISTING_ORDER_TOOLTIP")
	
	# Connect button signals
	_create_new_order_button.pressed.connect(_on_create_new_order_button_pressed)
	_bypass_existing_order_button.pressed.connect(_on_bypass_existing_order_button_pressed)
	_cancel_existing_order_button.pressed.connect(_on_cancel_existing_order_button_pressed)
	
	container_units = get_parent().get_parent().get_container_units()


func _on_create_new_order_button_pressed():
	"""Create a new order for the selected container(s)"""
	container_units = get_parent().get_parent().get_container_units()
	
	for container_unit in container_units:
		# TODO: Implement order creation logic
		# This could open a dialog to create a new order or emit a signal
		print("Creating new order for container: ", container_unit.name)
		# Example: container_unit.create_new_order()


func _on_bypass_existing_order_button_pressed():
	"""Bypass the existing order on the selected container(s)"""
	container_units = get_parent().get_parent().get_container_units()
	
	for container_unit in container_units:
		# TODO: Implement order bypass logic
		# This should temporarily skip the current order
		print("Bypassing existing order for container: ", container_unit.name)
		# Example: container_unit.bypass_current_order()


func _on_cancel_existing_order_button_pressed():
	"""Cancel the existing order on the selected container(s)"""
	container_units = get_parent().get_parent().get_container_units()
	
	for container_unit in container_units:
		# TODO: Implement order cancellation logic
		# This should remove the current order entirely
		print("Cancelling existing order for container: ", container_unit.name)
		# Example: container_unit.cancel_current_order()

extends Control

signal retry_connection

func _ready():
	$Panel/VBox/Button.pressed.connect(_on_retry_button_pressed)

func set_error_message(message):
	$Panel/VBox/Label2.text = message

func _on_retry_button_pressed():
	retry_connection.emit() 

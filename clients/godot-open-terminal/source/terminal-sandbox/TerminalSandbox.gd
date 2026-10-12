extends Control

const MinitosClient = preload("res://source/terminal-sandbox/MinitosClient.gd")
const MQTTNodeClient = preload("res://source/terminal-sandbox/MQTTNodeClient.gd")
const TerminalWorld3D = preload("res://source/terminal-sandbox/TerminalWorld3D.gd")
const CheIntegrationPolicy = preload("res://source/terminal-sandbox/CheIntegrationPolicy.gd")
const CREDENTIALS_PATH = "user://terminal_sandbox.cfg"
const SC_FUEL_IDLE_LPH := 4.0
const SC_FUEL_DRIVING_EMPTY_LPH := 18.0
const SC_FUEL_DRIVING_LOADED_LPH := 24.0

var _minitos := MinitosClient.new()
var _mqtt_node := MQTTNodeClient.new()
var _locations := []
var _selected_location := {}
var _draft_object := {}

var _wizard: VBoxContainer
var _wizard_scroll: ScrollContainer
var _workspace: VBoxContainer
var _server_host: LineEdit
var _server_port: SpinBox
var _server_status: Label
var _username: LineEdit
var _password: LineEdit
var _auth_status: Label
var _remember_credentials: CheckBox
var _mqtt_host: LineEdit
var _mqtt_port: SpinBox
var _mqtt_username: LineEdit
var _mqtt_password: LineEdit
var _mqtt_status: Label
var _mqtt_connect_button: Button
var _locations_list: ItemList
var _locations_button: Button
var _locations_status: Label
var _mode_button: OptionButton
var _tool_button: OptionButton
var _object_name: LineEdit
var _bays: SpinBox
var _rows: SpinBox
var _tiers: SpinBox
var _save_status: Label
var _canvas
var _location_title: Label
var _location_clock: Label
var _location_timezone := "UTC"
var _location_utc_offset_minutes := 0
var _last_clock_second := -1
var _refresh_button: Button
var _selection_badge: Label
var _selection_kind: Label
var _selection_title: Label
var _selection_details: Label
var _che_commands: Control
var _che_powered_command: CheckBox
var _che_working_command: CheckBox
var _che_available_command: CheckBox
var _che_dispatched_command: CheckBox
var _che_maintenance_command: CheckBox
var _che_move_button: Button
var _che_cancel_move_button: Button
var _che_command_dirty := false
var _che_command_selection_key := ""
var _selected_ches: Array = []
var _terminal_ches: Array = []
var _last_che_mqtt_publish_ms := {}
var _telemetry_timer: Timer
var _minitos_connected := false
var _mqtt_connected := false


func _ready():
	var app_theme = Theme.new()
	app_theme.default_font_size = 24
	theme = app_theme
	add_child(_minitos)
	add_child(_mqtt_node)
	_connect_client_signals()
	_build_ui()
	_telemetry_timer = Timer.new()
	_telemetry_timer.wait_time = 2.0
	_telemetry_timer.timeout.connect(_publish_che_telemetry)
	add_child(_telemetry_timer)
	_telemetry_timer.start()


func _process(_delta):
	if _location_clock == null or not _workspace.visible:
		return
	var system_second := int(Time.get_unix_time_from_system())
	if system_second == _last_clock_second:
		return
	_last_clock_second = system_second
	var local_time := Time.get_datetime_dict_from_unix_time(system_second + _location_utc_offset_minutes * 60)
	_location_clock.text = "%04d-%02d-%02d  %02d:%02d:%02d  %s" % [
		local_time.year, local_time.month, local_time.day,
		local_time.hour, local_time.minute, local_time.second,
		_location_timezone,
	]


func _connect_client_signals():
	_minitos.server_checked.connect(_on_server_checked)
	_minitos.authenticated.connect(_on_authenticated)
	_minitos.locations_loaded.connect(_on_locations_loaded)
	_minitos.areas_loaded.connect(_on_areas_loaded)
	_minitos.terminal_layout_loaded.connect(_on_terminal_layout_loaded)
	_minitos.object_saved.connect(_on_object_saved)
	_mqtt_node.broker_checked.connect(_on_broker_checked)
	_mqtt_node.message_rejected.connect(_on_mqtt_message_rejected)


func _build_ui():
	var background = ColorRect.new()
	background.color = Color(0.04, 0.05, 0.06)
	background.set_anchors_preset(Control.PRESET_FULL_RECT)
	add_child(background)

	var root = MarginContainer.new()
	root.set_anchors_preset(Control.PRESET_FULL_RECT)
	root.add_theme_constant_override("margin_left", 28)
	root.add_theme_constant_override("margin_top", 24)
	root.add_theme_constant_override("margin_right", 28)
	root.add_theme_constant_override("margin_bottom", 24)
	add_child(root)

	var main = VBoxContainer.new()
	main.add_theme_constant_override("separation", 16)
	root.add_child(main)

	var title = Label.new()
	title.text = "Minitos Terminal Sandbox"
	title.add_theme_font_size_override("font_size", 45)
	main.add_child(title)

	_wizard = VBoxContainer.new()
	_wizard.add_theme_constant_override("separation", 12)
	_wizard.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_wizard_scroll = ScrollContainer.new()
	_wizard_scroll.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_wizard_scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_wizard_scroll.add_child(_wizard)
	main.add_child(_wizard_scroll)
	_build_wizard()

	_workspace = VBoxContainer.new()
	_workspace.visible = false
	_workspace.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_workspace.add_theme_constant_override("separation", 12)
	main.add_child(_workspace)
	_build_workspace()


func _build_wizard():
	_server_host = _line_edit("http://localhost", false)
	_server_port = _spin_box(1, 65535, 8000)
	_server_status = _status_label()
	_wizard.add_child(_minitos_server_panel())

	_username = _line_edit("", false)
	_password = _line_edit("", true)
	_auth_status = _status_label()
	_remember_credentials = CheckBox.new()
	_remember_credentials.text = "Remember credentials on this device"
	_remember_credentials.toggled.connect(_on_remember_credentials_toggled)
	_wizard.add_child(_credentials_panel())

	_mqtt_host = _line_edit("127.0.0.1", false)
	_mqtt_port = _spin_box(1, 65535, 1883)
	_mqtt_username = _line_edit("", false)
	_mqtt_password = _line_edit("", true)
	_mqtt_status = _status_label()
	_wizard.add_child(_mqtt_panel())

	_locations_list = ItemList.new()
	_locations_list.custom_minimum_size = Vector2(520, 180)
	_locations_list.item_selected.connect(_on_location_selected)
	_locations_status = _status_label()
	_wizard.add_child(_locations_panel())
	_load_saved_credentials()
	_update_location_gate()


func _minitos_server_panel() -> Control:
	var panel = _panel("1. Minitos server")
	var grid = GridContainer.new()
	grid.columns = 2
	panel.add_child(grid)
	grid.add_child(_label("URL"))
	grid.add_child(_server_host)
	grid.add_child(_label("Port"))
	grid.add_child(_server_port)
	var button = Button.new()
	button.text = "Check server"
	button.pressed.connect(_on_check_server_pressed)
	panel.add_child(button)
	panel.add_child(_server_status)
	return panel


func _credentials_panel() -> Control:
	var panel = _panel("2. Minitos credentials")
	var grid = GridContainer.new()
	grid.columns = 2
	panel.add_child(grid)
	grid.add_child(_label("Username"))
	grid.add_child(_username)
	grid.add_child(_label("Password"))
	grid.add_child(_password)
	panel.add_child(_remember_credentials)
	var connect_button = Button.new()
	connect_button.text = "Connect to Minitos"
	connect_button.pressed.connect(_on_authenticate_pressed)
	panel.add_child(connect_button)
	panel.add_child(_auth_status)
	return panel


func _mqtt_panel() -> Control:
	var panel = _panel("3. MQTT broker")
	var grid = GridContainer.new()
	grid.columns = 2
	panel.add_child(grid)
	grid.add_child(_label("Host"))
	grid.add_child(_mqtt_host)
	grid.add_child(_label("Port"))
	grid.add_child(_mqtt_port)
	grid.add_child(_label("Username"))
	grid.add_child(_mqtt_username)
	grid.add_child(_label("Password"))
	grid.add_child(_mqtt_password)
	_mqtt_connect_button = Button.new()
	_mqtt_connect_button.text = "Connect to MQTT broker"
	_mqtt_connect_button.pressed.connect(_on_connect_mqtt_pressed)
	panel.add_child(_mqtt_connect_button)
	panel.add_child(_mqtt_status)
	return panel


func _locations_panel() -> Control:

	var panel = _panel("4. Select terminal")
	_locations_button = Button.new()
	_locations_button.text = "Load terminals from /minitos_core/locations/"
	_locations_button.pressed.connect(_on_load_locations_pressed)
	panel.add_child(_locations_button)
	panel.add_child(_locations_list)
	panel.add_child(_locations_status)
	return panel


func _build_workspace():
	var title_row = HBoxContainer.new()
	title_row.add_theme_constant_override("separation", 16)
	_workspace.add_child(title_row)
	_location_title = Label.new()
	_location_title.add_theme_font_size_override("font_size", 33)
	_location_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	title_row.add_child(_location_title)
	_location_clock = Label.new()
	_location_clock.add_theme_font_size_override("font_size", 22)
	_location_clock.add_theme_color_override("font_color", Color(0.42, 0.82, 0.78))
	_location_clock.horizontal_alignment = HORIZONTAL_ALIGNMENT_RIGHT
	title_row.add_child(_location_clock)

	var toolbar = HBoxContainer.new()
	toolbar.add_theme_constant_override("separation", 12)
	_workspace.add_child(toolbar)
	_refresh_button = Button.new()
	_refresh_button.text = "Refresh Terminal Layout"
	_refresh_button.pressed.connect(_on_refresh_terminal_pressed)
	toolbar.add_child(_refresh_button)
	var reset_camera_button = Button.new()
	reset_camera_button.text = "Reset Camera"
	reset_camera_button.pressed.connect(_on_reset_camera_pressed)
	toolbar.add_child(reset_camera_button)
	_save_status = _status_label()
	_save_status.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toolbar.add_child(_save_status)

	_canvas = TerminalWorld3D.new()
	_canvas.custom_minimum_size = Vector2(980, 660)
	_canvas.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_canvas.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_canvas.set_design_enabled(false)
	_canvas.selection_changed.connect(_on_terminal_selection_changed)
	_canvas.che_selection_changed.connect(_on_che_selection_changed)
	_canvas.che_move_requested.connect(_on_che_move_requested)
	_canvas.che_poses_changed.connect(_on_che_poses_changed)
	_canvas.che_move_completed.connect(_on_che_move_completed)
	_canvas.che_move_cancelled.connect(_on_che_move_cancelled)
	_workspace.add_child(_canvas)
	_workspace.add_child(_selection_panel())


func _selection_panel() -> Control:
	var panel = PanelContainer.new()
	panel.custom_minimum_size = Vector2(0, 168)
	panel.add_theme_stylebox_override("panel", _hud_style(Color(0.035, 0.055, 0.065, 0.98), Color(0.24, 0.43, 0.48)))
	var margin = MarginContainer.new()
	margin.add_theme_constant_override("margin_left", 14)
	margin.add_theme_constant_override("margin_top", 14)
	margin.add_theme_constant_override("margin_right", 14)
	margin.add_theme_constant_override("margin_bottom", 14)
	panel.add_child(margin)
	var content = HBoxContainer.new()
	content.add_theme_constant_override("separation", 16)
	margin.add_child(content)

	var badge_frame = PanelContainer.new()
	badge_frame.custom_minimum_size = Vector2(124, 124)
	badge_frame.add_theme_stylebox_override("panel", _hud_style(Color(0.07, 0.11, 0.12), Color(0.22, 0.78, 0.78), 2))
	_selection_badge = Label.new()
	_selection_badge.text = "--"
	_selection_badge.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_selection_badge.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_selection_badge.add_theme_font_size_override("font_size", 38)
	_selection_badge.add_theme_color_override("font_color", Color(0.42, 0.92, 0.85))
	badge_frame.add_child(_selection_badge)
	content.add_child(badge_frame)

	var identity = VBoxContainer.new()
	identity.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	identity.add_theme_constant_override("separation", 4)
	content.add_child(identity)
	_selection_kind = Label.new()
	_selection_kind.text = "TERMINAL CONTROL"
	_selection_kind.add_theme_font_size_override("font_size", 17)
	_selection_kind.add_theme_color_override("font_color", Color(0.32, 0.78, 0.76))
	identity.add_child(_selection_kind)
	_selection_title = Label.new()
	_selection_title.text = "No terminal object selected"
	_selection_title.add_theme_font_size_override("font_size", 29)
	_selection_title.add_theme_color_override("font_color", Color(0.90, 0.93, 0.88))
	identity.add_child(_selection_title)
	_selection_details = Label.new()
	_selection_details.text = "Select a terminal area or equipment unit"
	_selection_details.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_selection_details.add_theme_color_override("font_color", Color(0.67, 0.75, 0.73))
	identity.add_child(_selection_details)

	_che_commands = _build_che_commands()
	content.add_child(_che_commands)

	var telemetry = GridContainer.new()
	telemetry.columns = 2
	telemetry.custom_minimum_size = Vector2(300, 0)
	telemetry.add_theme_constant_override("h_separation", 18)
	telemetry.add_theme_constant_override("v_separation", 6)
	telemetry.add_child(_hud_stat("LINK", "MINITOS"))
	telemetry.add_child(_hud_stat("VIEW", "TACTICAL"))
	telemetry.add_child(_hud_stat("MAP", "LIVE"))
	telemetry.add_child(_hud_stat("MODE", "OPS"))
	content.add_child(telemetry)
	return panel


func _build_che_commands() -> Control:
	var commands = VBoxContainer.new()
	commands.custom_minimum_size = Vector2(300, 0)
	commands.add_theme_constant_override("separation", 6)
	var caption = Label.new()
	caption.text = "CHE COMMANDS"
	caption.add_theme_font_size_override("font_size", 14)
	caption.add_theme_color_override("font_color", Color(0.34, 0.60, 0.61))
	commands.add_child(caption)
	var row = GridContainer.new()
	row.columns = 2
	row.add_theme_constant_override("h_separation", 12)
	row.add_theme_constant_override("v_separation", 4)
	commands.add_child(row)
	_che_powered_command = _che_command_checkbox("Powered on")
	_che_working_command = _che_command_checkbox("Working")
	_che_available_command = _che_command_checkbox("Available")
	_che_dispatched_command = _che_command_checkbox("Dispatched")
	_che_maintenance_command = _che_command_checkbox("Maintenance")
	row.add_child(_che_powered_command)
	row.add_child(_che_working_command)
	row.add_child(_che_available_command)
	row.add_child(_che_dispatched_command)
	row.add_child(_che_maintenance_command)
	var apply_button = Button.new()
	apply_button.text = "Apply"
	apply_button.tooltip_text = "Apply power and availability to selected CHE"
	apply_button.pressed.connect(_on_apply_che_commands)
	commands.add_child(apply_button)
	_che_move_button = Button.new()
	_che_move_button.text = "Move selected"
	_che_move_button.tooltip_text = "Choose a destination for unavailable CHE"
	_che_move_button.pressed.connect(_on_move_selected_ches)
	commands.add_child(_che_move_button)
	_che_cancel_move_button = Button.new()
	_che_cancel_move_button.text = "Cancel move"
	_che_cancel_move_button.tooltip_text = "Stop the selected CHE movement order"
	_che_cancel_move_button.pressed.connect(_on_cancel_selected_che_moves)
	commands.add_child(_che_cancel_move_button)
	commands.visible = false
	return commands


func _che_command_checkbox(label: String) -> CheckBox:
	var checkbox = CheckBox.new()
	checkbox.text = label
	checkbox.toggled.connect(func(_pressed): _che_command_dirty = true)
	return checkbox


func _hud_stat(caption: String, value: String) -> Control:
	var cell = VBoxContainer.new()
	cell.add_theme_constant_override("separation", 0)
	var caption_label = Label.new()
	caption_label.text = caption
	caption_label.add_theme_font_size_override("font_size", 14)
	caption_label.add_theme_color_override("font_color", Color(0.34, 0.60, 0.61))
	cell.add_child(caption_label)
	var value_label = Label.new()
	value_label.text = value
	value_label.add_theme_font_size_override("font_size", 20)
	value_label.add_theme_color_override("font_color", Color(0.78, 0.86, 0.82))
	cell.add_child(value_label)
	return cell


func _hud_style(background: Color, border: Color, border_width := 1) -> StyleBoxFlat:
	var style = StyleBoxFlat.new()
	style.bg_color = background
	style.border_color = border
	style.set_border_width_all(border_width)
	style.corner_radius_top_left = 3
	style.corner_radius_top_right = 3
	style.corner_radius_bottom_left = 3
	style.corner_radius_bottom_right = 3
	return style


func _inspector_panel() -> Control:
	var panel = _panel("Object")
	panel.custom_minimum_size = Vector2(320, 0)
	_object_name = _line_edit("Block A", false)
	_bays = _spin_box(1, 100, 6)
	_rows = _spin_box(1, 100, 4)
	_tiers = _spin_box(1, 20, 5)
	_save_status = _status_label()

	var grid = GridContainer.new()
	grid.columns = 2
	panel.add_child(grid)
	grid.add_child(_label("Name"))
	grid.add_child(_object_name)
	grid.add_child(_label("Bays"))
	grid.add_child(_bays)
	grid.add_child(_label("Rows"))
	grid.add_child(_rows)
	grid.add_child(_label("Tiers"))
	grid.add_child(_tiers)

	var save_button = Button.new()
	save_button.text = "Save object to Minitos"
	save_button.pressed.connect(_on_save_object_pressed)
	panel.add_child(save_button)
	panel.add_child(_save_status)
	return panel


func _connection_row(title: String, field: Control, button_text: String, callback: Callable, status: Label) -> Control:
	var panel = _panel(title)
	var row = HBoxContainer.new()
	row.add_theme_constant_override("separation", 8)
	panel.add_child(row)
	field.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(field)
	var button = Button.new()
	button.text = button_text
	button.pressed.connect(callback)
	row.add_child(button)
	panel.add_child(status)
	return panel


func _panel(title: String) -> VBoxContainer:
	var panel = VBoxContainer.new()
	panel.add_theme_constant_override("separation", 8)
	var label = Label.new()
	label.text = title
	label.add_theme_font_size_override("font_size", 27)
	panel.add_child(label)
	return panel


func _label(text: String) -> Label:
	var label = Label.new()
	label.text = text
	label.custom_minimum_size = Vector2(110, 0)
	return label


func _line_edit(text: String, secret: bool) -> LineEdit:
	var line_edit = LineEdit.new()
	line_edit.text = text
	line_edit.secret = secret
	line_edit.custom_minimum_size = Vector2(320, 0)
	return line_edit


func _spin_box(min_value: int, max_value: int, value: int) -> SpinBox:
	var spin_box = SpinBox.new()
	spin_box.min_value = min_value
	spin_box.max_value = max_value
	spin_box.value = value
	return spin_box


func _status_label() -> Label:
	var label = Label.new()
	label.text = "Waiting"
	label.modulate = Color(0.75, 0.78, 0.82)
	return label


func _on_check_server_pressed():
	_set_status(_server_status, "Checking server...", null)
	_minitos.check_server(_server_base_url())


func _on_authenticate_pressed():
	_set_status(_auth_status, "Checking credentials...", null)
	_minitos.configure(_server_base_url(), _username.text, _password.text)
	_minitos.authenticate(_username.text, _password.text)


func _on_connect_mqtt_pressed():
	_mqtt_connect_button.disabled = true
	_set_status(_mqtt_status, "Connecting to broker...", null)
	_mqtt_node.connect_to_broker(_mqtt_host.text, int(_mqtt_port.value), _mqtt_username.text, _mqtt_password.text)


func _on_load_locations_pressed():
	_set_status(_locations_status, "Loading terminals...", null)
	_minitos.load_locations()


func _on_location_selected(index: int):
	if index < 0 or index >= _locations.size():
		return
	_selected_location = _locations[index]
	_wizard.visible = false
	_wizard_scroll.visible = false
	_workspace.visible = true
	_location_title.text = "Terminal: %s" % _selected_location.get("name", "Unknown")
	_apply_location_time(_selected_location)
	_canvas.set_map_definition(_selected_location)
	_refresh_terminal_layout()


func _on_refresh_terminal_pressed():
	_refresh_terminal_layout()


func update_object_world_position(kind: String, object_id: String, world_position: Vector3):
	if _selected_location.is_empty():
		return
	var geographic = _canvas.world_to_geographic(world_position)
	var location_id = str(_selected_location.get("pk", _selected_location.get("id", "")))
	if kind == "container_visit":
		_minitos.update_container_visit_position(location_id, object_id, geographic.latitude, geographic.longitude)


func _on_reset_camera_pressed():
	_canvas.reset_camera()


func _on_terminal_selection_changed(kind: String, data: Dictionary):
	if data.is_empty():
		_selection_badge.text = "--"
		_selection_kind.text = "TERMINAL CONTROL"
		_selection_title.text = "No terminal object selected"
		_selection_details.text = "Select a terminal area or equipment unit"
		_che_commands.visible = false
		_che_command_selection_key = ""
		_che_command_dirty = false
		return
	var name = str(data.get("name", data.get("code", "Unnamed")))
	var object_id = data.get("pk", data.get("id", "?"))
	if kind == "che" or kind == "che_group":
		var selected = data.get("ches", [data])
		var first_che: Dictionary = selected[0] if not selected.is_empty() else data
		var equipment_code = str(data.get("equipment_type_code", "CHE"))
		if kind == "che_group":
			equipment_code = "x%s" % selected.size()
		_selection_badge.text = equipment_code.left(3).to_upper()
		_selection_kind.text = "MOBILE EQUIPMENT"
		_selection_title.text = "%s CHE selected" % selected.size() if kind == "che_group" else "%s  |  CHE #%s" % [name, object_id]
		var power_state = "ON" if bool(first_che.get("powered_on", false)) else "OFF"
		var activity_state = "WORKING" if bool(first_che.get("working", false)) else ("IDLE" if bool(first_che.get("powered_on", false)) else "N/A")
		_selection_details.text = "TYPE  %s     FUEL  %.1f%%\nPOWER  %s     ACTIVITY  %s\nAVAILABLE  %s     DISPATCHED  %s     MAINTENANCE  %s" % [
			first_che.get("equipment_type_name", first_che.get("equipment_type_code", "CHE")),
			float(first_che.get("fuel_level", 0.0)),
			power_state,
			activity_state,
			"YES" if bool(first_che.get("is_available", false)) else "NO",
			"YES" if bool(first_che.get("is_dispatched", false)) else "NO",
			"YES" if bool(first_che.get("is_in_maintenance", false)) else "NO",
		]
		if first_che.get("latitude") != null and first_che.get("longitude") != null:
			_selection_details.text += "\nPOSITION  %.7f, %.7f     ROT  %.1f deg" % [
				float(first_che.get("latitude")),
				float(first_che.get("longitude")),
				float(first_che.get("rotation", 0.0)),
			]
		if first_che.has("world_x") and first_che.has("world_z"):
			_selection_details.text += "\nWORLD X/Z  %.2f, %.2f" % [
				float(first_che.get("world_x")),
				float(first_che.get("world_z")),
			]
		if first_che.get("destination_latitude") != null and first_che.get("destination_longitude") != null:
			_selection_details.text += "\nTARGET  %.7f, %.7f" % [
				float(first_che.get("destination_latitude")),
				float(first_che.get("destination_longitude")),
			]
		if first_che.has("target_world_x") and first_che.has("target_world_z"):
			_selection_details.text += "\nTARGET X/Z  %.2f, %.2f     DIST  %.2f m" % [
				float(first_che.get("target_world_x")),
				float(first_che.get("target_world_z")),
				float(first_che.get("target_distance_m", 0.0)),
			]
		if (
			first_che.has("destination_world_x")
			and first_che.has("destination_world_z")
			and (
				abs(float(first_che.get("destination_world_x")) - float(first_che.get("target_world_x", first_che.get("destination_world_x")))) > 0.01
				or abs(float(first_che.get("destination_world_z")) - float(first_che.get("target_world_z", first_che.get("destination_world_z")))) > 0.01
			)
		):
			_selection_details.text += "\nCOMMAND X/Z  %.2f, %.2f" % [
				float(first_che.get("destination_world_x")),
				float(first_che.get("destination_world_z")),
			]
		_che_commands.visible = true
		if not _che_command_dirty:
			_sync_che_command_controls(first_che)
	else:
		_che_commands.visible = false
		var area_type = str(data.get("area_type", "Terminal area"))
		_selection_badge.text = _area_badge(area_type)
		_selection_kind.text = "TERMINAL ZONE"
		_selection_title.text = "%s  |  Area #%s" % [name, object_id]
		_selection_details.text = "TYPE  %s\nSURFACE  %.1f m2     VISIBLE  %s" % [
			area_type,
			float(data.get("area", 0.0)),
			"Yes" if bool(data.get("is_visible", true)) else "No"
		]


func _on_che_selection_changed(ches: Array):
	_selected_ches = ches
	var selection_key = _che_selection_key(ches)
	if selection_key != _che_command_selection_key:
		_che_command_selection_key = selection_key
		_che_command_dirty = false
	if _che_move_button != null:
		_che_move_button.disabled = ches.is_empty() or ches.any(func(che): return (
			bool(che.get("is_available", false))
			or not bool(che.get("powered_on", false))
			or bool(che.get("is_in_maintenance", false))
		))
	if _che_cancel_move_button != null:
		_che_cancel_move_button.disabled = ches.is_empty() or not ches.any(func(che): return bool(che.get("simulation_is_moving", false)))


func _che_selection_key(ches: Array) -> String:
	var ids: Array = ches.map(func(che): return int(che.get("pk", che.get("id", 0))))
	ids.sort()
	return ",".join(ids.map(func(value): return str(value)))


func _sync_che_command_controls(che: Dictionary):
	_che_powered_command.button_pressed = bool(che.get("powered_on", false))
	_che_working_command.button_pressed = bool(che.get("working", false))
	_che_available_command.button_pressed = bool(che.get("is_available", false))
	_che_dispatched_command.button_pressed = bool(che.get("is_dispatched", false))
	_che_maintenance_command.button_pressed = bool(che.get("is_in_maintenance", false))
	_che_command_dirty = false


func _on_move_selected_ches():
	if not _canvas.begin_che_move_targeting():
		_set_status(_save_status, "Only unavailable CHE can receive manual move orders.", false)
		return
	_set_status(_save_status, "Click the map destination. Press Escape to cancel.", null)


func _on_che_move_requested(ches: Array):
	_commit_che_transition(
		ches,
		CheIntegrationPolicy.move_started_state(),
		"%s CHE move order(s) started." % ches.size()
	)


func _on_cancel_selected_che_moves():
	var cancelled = _canvas.cancel_selected_che_moves()
	if cancelled == 0:
		_set_status(_save_status, "The selected CHE have no active move order.", false)


func _on_che_move_completed(ches: Array):
	_commit_che_transition(
		ches,
		CheIntegrationPolicy.move_completed_state(),
		"%s CHE move order(s) completed." % ches.size()
	)


func _on_che_move_cancelled(ches: Array):
	_commit_che_transition(
		ches,
		CheIntegrationPolicy.move_completed_state(),
		"%s CHE move order(s) cancelled." % ches.size()
	)


func _on_che_poses_changed(ches: Array):
	for che in ches:
		_update_terminal_che(che)


func _commit_che_transition(ches: Array, state_patch: Dictionary, message: String):
	if ches.is_empty():
		return
	var ids: Array = ches.map(func(che): return int(che.get("pk", che.get("id", 0))))
	var previous_by_id := {}
	for che in ches:
		previous_by_id[int(che.get("pk", che.get("id", 0)))] = bool(che.get("powered_on", false))
	var updated = _canvas.update_che_states(ids, state_patch)
	var location_id = str(_selected_location.get("pk", _selected_location.get("id", "")))
	for che in updated:
		var che_id = int(che.get("pk", che.get("id", 0)))
		_update_terminal_che(che)
		_minitos.update_che_snapshot(location_id, che)
		_publish_che_transition(che, bool(previous_by_id.get(che_id, false)))
	_selected_ches = updated
	_set_status(_save_status, message, true)


func _on_apply_che_commands():
	if _selected_ches.is_empty():
		return
	var state_patch = {
		"powered_on": _che_powered_command.button_pressed,
		"working": _che_working_command.button_pressed,
		"is_available": _che_available_command.button_pressed,
		"is_dispatched": _che_dispatched_command.button_pressed,
		"is_in_maintenance": _che_maintenance_command.button_pressed,
	}
	if not _validate_che_command(state_patch):
		return
	if not bool(state_patch.powered_on) or bool(state_patch.is_in_maintenance):
		_canvas.cancel_selected_che_moves()
	var ids: Array = _selected_ches.map(func(che): return int(che.get("pk", che.get("id", 0))))
	var previous_by_id := {}
	for che in _selected_ches:
		previous_by_id[int(che.get("pk", che.get("id", 0)))] = bool(che.get("powered_on", false))
	var updated = _canvas.update_che_states(ids, state_patch)
	var location_id = str(_selected_location.get("pk", _selected_location.get("id", "")))
	for che in updated:
		var che_id = int(che.get("pk", che.get("id", 0)))
		var previous_powered_on = bool(previous_by_id.get(che_id, false))
		_update_terminal_che(che)
		_minitos.update_che_snapshot(location_id, che)
		_publish_che_transition(che, previous_powered_on)
	_selected_ches = updated
	_che_command_dirty = false
	if not updated.is_empty():
		_sync_che_command_controls(updated[0])


func _validate_che_command(state: Dictionary) -> bool:
	if bool(state.working) and not bool(state.powered_on):
		_set_status(_save_status, "Working equipment must be powered on.", false)
		return false
	if bool(state.is_available) and not bool(state.powered_on):
		_set_status(_save_status, "Available equipment must be powered on.", false)
		return false
	if bool(state.is_in_maintenance) and (bool(state.working) or bool(state.is_available)):
		_set_status(_save_status, "Equipment in maintenance cannot be working or available.", false)
		return false
	return true


func _publish_che_transition(che: Dictionary, previous_powered_on: bool):
	var che_id = int(che.get("pk", che.get("id", 0)))
	var powered_on = bool(che.get("powered_on", false))
	for event in CheIntegrationPolicy.transition_events(previous_powered_on, powered_on):
		match event:
			"DBIRTH":
				_mqtt_node.publish_che_birth(che)
			"DDATA":
				if _mqtt_node.publish_che_data(che):
					_last_che_mqtt_publish_ms[che_id] = Time.get_ticks_msec()
			"DDEATH":
				_mqtt_node.publish_che_death(che)
				_last_che_mqtt_publish_ms.erase(che_id)


func _update_terminal_che(updated: Dictionary):
	var updated_id = int(updated.get("pk", updated.get("id", 0)))
	for index in range(_terminal_ches.size()):
		var current: Dictionary = _terminal_ches[index]
		if int(current.get("pk", current.get("id", 0))) == updated_id:
			_terminal_ches[index] = updated.duplicate(true)
			return


func _publish_che_telemetry():
	if _canvas == null or _selected_location.is_empty():
		return
	var now_ms = Time.get_ticks_msec()
	for che in _canvas.get_che_operating_data():
		var details: Dictionary = che.get("equipment_type_details", {})
		var rate_lph := 0.0
		var is_straddle = str(che.get("equipment_type_code", "")) == "SC"
		var is_diesel = str(details.get("energy_source", "")) == "Diesel"
		if bool(che.get("powered_on", false)) and is_straddle and is_diesel:
			if bool(che.get("simulation_is_moving", false)):
				rate_lph = SC_FUEL_DRIVING_LOADED_LPH if bool(che.get("twistlock_closed", false)) else SC_FUEL_DRIVING_EMPTY_LPH
			else:
				rate_lph = SC_FUEL_IDLE_LPH
		var tank_capacity = float(details.get("fuel_tank_capacity", 0.0))
		var consumed = rate_lph * _telemetry_timer.wait_time / 3600.0
		var fuel_level = float(che.get("fuel_level", 100.0))
		if tank_capacity > 0.0 and consumed > 0.0:
			fuel_level = max(0.0, fuel_level - consumed / tank_capacity * 100.0)
		var total_consumed = float(che.get("fuel_consumed_liters", 0.0)) + consumed
		var che_id = int(che.get("pk", che.get("id", 0)))
		var updated = _canvas.update_che_fuel(che_id, fuel_level, rate_lph, total_consumed)
		if not updated.is_empty():
			che = updated
		_update_terminal_che(che)
		var powered_on = bool(che.get("powered_on", false))
		var working = bool(che.get("working", false))
		var last_publish_ms = int(_last_che_mqtt_publish_ms.get(che_id, 0))
		if CheIntegrationPolicy.should_publish_data(powered_on, working, last_publish_ms, now_ms):
			if _mqtt_node.publish_che_data(che):
				_last_che_mqtt_publish_ms[che_id] = now_ms


func _area_badge(area_type: String) -> String:
	match area_type.to_lower():
		"water": return "SEA"
		"land": return "LND"
		"quay": return "QAY"
		"yard": return "YRD"
		"yard block": return "BLK"
		"building": return "BLD"
		"parking": return "PRK"
		_: return "ZNE"


func _refresh_terminal_layout():
	if _selected_location.is_empty():
		return
	_refresh_button.disabled = true
	_set_status(_save_status, "Refreshing areas, roads, and CHEs...", null)
	_minitos.refresh_terminal(str(_selected_location.get("pk", _selected_location.get("id", ""))))


func _on_mode_selected(index: int):
	_canvas.set_design_enabled(index == 0)


func _on_tool_selected(index: int):
	var tools = ["yard", "quay", "water", "road", "building", "parking", "yard block"]
	_canvas.set_tool(tools[index])
	_object_name.text = _tool_button.get_item_text(index)


func _on_draft_changed(draft: Dictionary):
	_draft_object = draft
	if draft.is_empty():
		return
	_save_status.text = "Draft %.0f x %.0f at %.6f, %.6f" % [
		draft.get("width", 0),
		draft.get("height", 0),
		draft.get("latitude", 0),
		draft.get("longitude", 0)
	]


func _on_save_object_pressed():
	if _selected_location.is_empty():
		_set_status(_save_status, "Select a terminal first", false)
		return
	if _draft_object.is_empty():
		_set_status(_save_status, "Draw an object on the map first", false)
		return

	var payload = _draft_object.duplicate()
	payload["name"] = _object_name.text
	payload["max_bays"] = int(_bays.value)
	payload["max_rows"] = int(_rows.value)
	payload["max_tier"] = int(_tiers.value)
	payload["code"] = _object_name.text.replace(" ", "_").left(20).to_upper()
	payload["is_visible"] = true
	_set_status(_save_status, "Saving to Minitos...", null)
	_minitos.create_design_object(str(_selected_location.get("pk", _selected_location.get("id", ""))), payload)


func _on_server_checked(ok: bool, message: String):
	_set_status(_server_status, message, ok)


func _on_authenticated(ok: bool, message: String):
	_minitos_connected = ok
	if ok and _remember_credentials.button_pressed:
		_save_credentials()
	_update_location_gate()
	_set_status(_auth_status, message, ok)


func _on_broker_checked(ok: bool, message: String):
	_mqtt_connected = ok
	_mqtt_connect_button.disabled = false
	if ok and not _terminal_ches.is_empty():
		_publish_initial_che_state(_terminal_ches)
	_update_location_gate()
	_set_status(_mqtt_status, message, ok)


func _on_mqtt_message_rejected(message_type: String, device_id: String, reason: String):
	_set_status(_save_status, "%s for CHE %s was not sent: %s" % [message_type, device_id, reason], false)


func _on_locations_loaded(ok: bool, locations: Array, message: String):
	_set_status(_locations_status, message, ok)
	_locations = locations
	_locations_list.clear()
	for location in _locations:
		var location_id = location.get("pk", location.get("id", "?"))
		_locations_list.add_item("%s  #%s" % [location.get("name", "Unnamed terminal"), location_id])


func _on_areas_loaded(ok: bool, areas: Array, message: String):
	_set_status(_save_status, message, ok)
	if ok:
		_canvas.set_areas(areas)


func _on_terminal_layout_loaded(ok: bool, location_data: Dictionary, areas: Array, roads: Array, ches: Array, message: String):
	_refresh_button.disabled = false
	var status_message = message
	if ok:
		status_message = "%s: %s areas, %s roads, %s CHEs | origin %.6f, %.6f | angle %.1f deg" % [
			message,
			areas.size(),
			roads.size(),
			ches.size(),
			float(location_data.get("map_origin_latitude", location_data.get("latitude", 0.0))),
			float(location_data.get("map_origin_longitude", location_data.get("longitude", 0.0))),
			float(location_data.get("angle", 0.0)),
		]
	_set_status(_save_status, status_message, ok)
	if ok:
		_terminal_ches = ches.duplicate(true)
		_last_che_mqtt_publish_ms.clear()
		_selected_location = location_data
		_apply_location_time(_selected_location)
		_canvas.set_map_definition(_selected_location)
		_canvas.set_terminal_data(areas, roads, ches)
		_publish_initial_che_state(_terminal_ches)


func _publish_initial_che_state(ches: Array):
	if not _mqtt_connected:
		return
	# Sparkplug requires every DBIRTH in the session before the first DDATA.
	for che in ches:
		if bool(che.get("powered_on", false)):
			_mqtt_node.publish_che_birth(che)
	for che in ches:
		if bool(che.get("powered_on", false)):
			var che_id = int(che.get("pk", che.get("id", 0)))
			if _mqtt_node.publish_che_data(che):
				_last_che_mqtt_publish_ms[che_id] = Time.get_ticks_msec()


func _apply_location_time(location_data: Dictionary):
	_location_timezone = str(location_data.get("timezone", "UTC"))
	_location_utc_offset_minutes = int(location_data.get("timezone_utc_offset_minutes", 0))
	_last_clock_second = -1


func _on_object_saved(ok: bool, object_data: Dictionary, message: String):
	_set_status(_save_status, message, ok)
	if ok:
		if object_data is Dictionary and object_data.has("coordinates"):
			object_data["type"] = object_data.get("area_type", object_data.get("type", "yard"))
			_canvas.add_saved_object(object_data)
		else:
			var saved = _draft_object.duplicate()
			saved["name"] = _object_name.text
			_canvas.add_saved_object(saved)


func _set_status(label: Label, message: String, ok):
	label.text = message
	if ok == null:
		label.modulate = Color(0.82, 0.76, 0.46)
	elif ok:
		label.modulate = Color(0.45, 0.82, 0.58)
	else:
		label.modulate = Color(0.94, 0.42, 0.38)


func _server_base_url() -> String:
	var host = _server_host.text.strip_edges().trim_suffix("/")
	var host_without_scheme = host
	var scheme_index = host.find("://")
	if scheme_index != -1:
		host_without_scheme = host.substr(scheme_index + 3)
	if host_without_scheme.find(":") != -1:
		return host
	return "%s:%s" % [host, int(_server_port.value)]


func _update_location_gate():
	if _locations_button == null:
		return
	var ready = _minitos_connected and _mqtt_connected
	_locations_button.disabled = not ready
	if ready:
		_locations_status.text = "Ready to load terminals"
	else:
		_locations_status.text = "Connect Minitos and MQTT first"


func _on_remember_credentials_toggled(enabled: bool):
	if not enabled:
		var config = ConfigFile.new()
		config.erase_section("minitos")
		config.save(CREDENTIALS_PATH)


func _load_saved_credentials():
	var config = ConfigFile.new()
	if config.load(CREDENTIALS_PATH) != OK:
		return
	if not bool(config.get_value("minitos", "remember", false)):
		return
	_remember_credentials.button_pressed = true
	_server_host.text = str(config.get_value("minitos", "host", _server_host.text))
	_server_port.value = int(config.get_value("minitos", "port", _server_port.value))
	_username.text = str(config.get_value("minitos", "username", ""))
	_password.text = str(config.get_value("minitos", "password", ""))


func _save_credentials():
	var config = ConfigFile.new()
	config.set_value("minitos", "remember", true)
	config.set_value("minitos", "host", _server_host.text)
	config.set_value("minitos", "port", int(_server_port.value))
	config.set_value("minitos", "username", _username.text)
	config.set_value("minitos", "password", _password.text)
	config.save(CREDENTIALS_PATH)
